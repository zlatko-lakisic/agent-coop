"""Trace mode, standing approvals, the sim helper, and the fake driver."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.cursorperm import merge_app_permissions
from agentcoop.projects import load_config
from agentcoop.statusfile import refresh_status, status_path

ROOT = support.ROOT


def _load(name: str):
    path = ROOT / "scripts" / "sim" / (name + ".py")
    spec = importlib.util.spec_from_file_location("sim_" + name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _quiet(argv: list[str]) -> str:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    if code != 0:
        raise AssertionError(err.getvalue() or out.getvalue())
    return out.getvalue()


class TraceTests(unittest.TestCase):
    def test_trace_redacts_the_body_and_records_check(self) -> None:
        with support.TempEnv() as env:
            os.environ["AGENTCOOP_TRACE"] = "1"
            try:
                _quiet(["setup", "--connect", "cursor,claude-code", "--schedule", "no", "--yes"])
                folder = env.workspace / "demo"
                folder.mkdir()
                _quiet(
                    [
                        "project",
                        "add",
                        str(folder),
                        "--name",
                        "demo",
                        "--driver",
                        "cursor",
                        "--helper",
                        "claude_code=reviews",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                )
                _quiet(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--project",
                        "demo",
                        "--to",
                        "claude_code",
                        "--type",
                        "question",
                        "--subject",
                        "Note",
                        "--body",
                        "secret body text",
                    ]
                )
            finally:
                os.environ.pop("AGENTCOOP_TRACE", None)
            lines = (env.board / "logs" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertGreaterEqual(len(lines), 2)
            send = json.loads(lines[-1])
            self.assertEqual(send["command"], "send")
            self.assertEqual(send["agent"], "cursor")
            self.assertIn("<body>", send["args"])
            self.assertNotIn("secret body text", json.dumps(send))
            self.assertIn("exit", send)
            self.assertIn("ms", send)
            self.assertRegex(send["time"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
            _quiet(["trace", "on"])
            self.assertIs(load_config(env.board).get("trace"), True)
            text = _quiet(["check", "--as", "cursor", "--project", "demo"])
            traced = json.loads((env.board / "logs" / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(traced["command"], "check")
            self.assertIn("result", traced)
            self.assertIn("driver", traced["result"])
            self.assertIn("driver", text)

    def test_allow_shows_on_check_and_waiting_is_at_the_top(self) -> None:
        with support.TempEnv() as env:
            _quiet(["setup", "--connect", "cursor,claude-desktop", "--schedule", "no", "--yes"])
            folder = env.workspace / "demo"
            folder.mkdir()
            _quiet(
                [
                    "project",
                    "add",
                    str(folder),
                    "--name",
                    "demo",
                    "--driver",
                    "cursor",
                    "--helper",
                    "claude_desktop=helps",
                    "--gitignore",
                    "no",
                    "--yes",
                ]
            )
            saved = _quiet(
                [
                    "allow",
                    "--as",
                    "cursor",
                    "--project",
                    "demo",
                    "--text",
                    "restart the game and run the spy tests",
                ]
            )
            self.assertIn("Standing approval saved", saved)
            check = _quiet(["check", "--as", "cursor", "--project", "demo"])
            self.assertIn("Standing approvals: restart the game and run the spy tests.", check)
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(["allow", "--as", "claude_desktop", "--project", "demo", "--text", "anything"])
            self.assertEqual(code, 1)
            self.assertIn("Only the driver", err.getvalue())
            from agentcoop.progress import progress_dir

            progress = progress_dir(env.board, "demo")
            progress.mkdir(parents=True, exist_ok=True)
            (progress / "20261025T120000Z.md").write_text(
                "Status: waiting\nNow: Need a decision on the restart.\n",
                encoding="utf-8",
            )
            refresh_status(env.board)
            page = status_path(env.board).read_text(encoding="utf-8")
            self.assertTrue(page.startswith("# Status\n\nWaiting on you in demo: Need a decision on the restart.\n"))
            shown = _quiet(["allow", "show", "--as", "cursor", "--project", "demo"])
            self.assertIn("restart the game and run the spy tests", shown)
            cleared = _quiet(["allow", "clear", "--as", "cursor", "--project", "demo"])
            self.assertIn("cleared", cleared)
            path = env.workspace / "demo"
            written = merge_app_permissions(path)
            assert written is not None
            data = json.loads(written.read_text(encoding="utf-8"))
            self.assertIn("agentcoop.exe", data["terminalAllowlist"])
            again = merge_app_permissions(path)
            assert again is not None
            self.assertEqual(
                json.loads(again.read_text(encoding="utf-8"))["terminalAllowlist"].count("agentcoop"),
                1,
            )


class SimTests(unittest.TestCase):
    def test_helper_writes_crlf_bom_and_a_same_second_burst(self) -> None:
        helper = _load("helper")
        with support.TempEnv() as env:
            moment = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            paths = helper.run_timeline(
                env.board,
                "demo",
                "claude_desktop",
                [
                    {"at": 1, "kind": "answer", "text": "Review: restart the game.", "crlf": True, "bom": True},
                    {
                        "at": 0,
                        "kind": "burst",
                        "texts": [
                            "Review: restart the game, then rerun the suite.",
                            "Correction: do not restart. Start the spy tests.",
                            "Summary: leave the game running and start the spy tests.",
                        ],
                    },
                    {"at": 60, "kind": "nudge", "text": "The correction is the one to follow."},
                    {"at": 120, "kind": "silent"},
                ],
                "20261025T120000Z_cursor_ab12",
                moment,
            )
            self.assertEqual(len(paths), 5)
            raw = paths[0].read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"\r\n", raw)
            names = [path.name for path in paths]
            self.assertEqual(len(set(names)), len(names))
            self.assertTrue(any(name.startswith("nudge.") for name in names))

    def test_game_script_prints_epochs(self) -> None:
        game = _load("game")
        out = io.StringIO()
        with redirect_stdout(out):
            code = game.main(["--epochs", "3", "--interval", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(
            out.getvalue().splitlines(),
            ["epoch 1 of 3", "epoch 2 of 3", "epoch 3 of 3"],
        )

    def test_fake_driver_scenarios(self) -> None:
        with support.TempEnv() as env:
            report_dir = env.root / "reports"
            report_dir.mkdir()
            for scenario, checks in (
                ("s3", ("delivered",)),
                ("s4", ("standing",)),
                ("s12", ("ticked",)),
                ("s10", ("exit",)),
                ("s13", ("silent", "keep_awake")),
            ):
                report = report_dir / (scenario + ".json")
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "sim" / "run.py"),
                        "--scenario",
                        scenario,
                        "--driver",
                        "fake",
                        "--board",
                        str(env.root / scenario / "board"),
                        "--project",
                        str(env.root / scenario / "project"),
                        "--report",
                        str(report),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                data = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(data["scenario"], scenario)
                for key in checks:
                    self.assertIn(key, data)
            s3 = json.loads((report_dir / "s3.json").read_text(encoding="utf-8"))
            self.assertEqual(len(s3["delivered"]), 3)
            s4 = json.loads((report_dir / "s4.json").read_text(encoding="utf-8"))
            self.assertTrue(s4["standing"])
            s12 = json.loads((report_dir / "s12.json").read_text(encoding="utf-8"))
            self.assertTrue(s12["ticked"])
            s10 = json.loads((report_dir / "s10.json").read_text(encoding="utf-8"))
            self.assertEqual(s10["exit"], 5)
            s13 = json.loads((report_dir / "s13.json").read_text(encoding="utf-8"))
            self.assertTrue(s13["silent"])
            self.assertTrue(s13["keep_awake"])


if __name__ == "__main__":
    unittest.main()
