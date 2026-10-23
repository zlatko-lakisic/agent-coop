"""Tests for the board lock and strict settings formats."""

from __future__ import annotations

import io
import json
import multiprocessing
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.errors import CoopError
from agentcoop.projects import (
    add_helper,
    add_project,
    atomic_write_bytes,
    connect_agent_record,
    init_board_files,
    load_projects,
)


def _worker(board: str, project: str, agent: str, error_path: str) -> None:
    codes = []
    for index in range(10):
        try:
            add_helper(Path(board), project, agent, "round-" + str(index))
        except CoopError as exc:
            codes.append(str(exc.code))
    Path(error_path).write_text("\n".join(codes), encoding="utf-8")


class LockTests(unittest.TestCase):
    def test_replace_retries_when_the_file_is_busy(self) -> None:
        with support.TempEnv() as env:
            target = env.board / "projects.json"
            calls = {"n": 0}
            real = os.replace

            def flaky(src: str, dst: str) -> None:
                calls["n"] += 1
                if calls["n"] < 3:
                    raise PermissionError("busy")
                real(src, dst)

            with patch("agentcoop.projects.os.replace", flaky), patch("agentcoop.projects.time.sleep"):
                atomic_write_bytes(target, b'{"ok": true}\n')
            self.assertGreaterEqual(calls["n"], 3)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"ok": true}\n')

    def test_concurrent_helpers_are_all_kept(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            names = ["helper" + str(index) for index in range(5)]
            for name in names:
                connect_agent_record(env.board, name, "claude-code", display=name)
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {})
            ctx = multiprocessing.get_context("spawn")
            procs = []
            try:
                for name in names:
                    proc = ctx.Process(
                        target=_worker,
                        args=(
                            str(env.board),
                            "demo",
                            name,
                            str(env.root / (name + ".errors")),
                        ),
                    )
                    procs.append(proc)
                    proc.start()
                for proc in procs:
                    proc.join(60)
                    self.assertEqual(proc.exitcode, 0)
            finally:
                for proc in procs:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(5)
            codes = []
            for name in names:
                text = (env.root / (name + ".errors")).read_text(encoding="utf-8")
                if text.strip():
                    codes.extend(text.splitlines())
            self.assertEqual(codes, [])
            helpers = load_projects(env.board)["projects"]["demo"]["helpers"]
            for name in names:
                self.assertEqual(helpers.get(name), "round-9")

    def test_incomplete_projects_file_is_refused(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            path = env.board / "projects.json"
            path.write_text('{"version": 1}\n', encoding="utf-8")
            err = io.StringIO()
            with support.person("review"), redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(["project", "review", "--accept"])
            self.assertEqual(code, 1)
            self.assertIn("expected format", err.getvalue())
            folder = env.workspace / "demo"
            folder.mkdir()
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "project",
                        "add",
                        str(folder),
                        "--name",
                        "demo",
                        "--driver",
                        "cursor",
                        "--yes",
                    ]
                )
            self.assertEqual(code, 6)
            self.assertIn("expected format", err.getvalue())
            self.assertNotIn("KeyError", err.getvalue())

    def test_helpers_must_be_an_object(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            raw = {
                "version": 1,
                "projects": {
                    "demo": {
                        "path": str(env.workspace),
                        "driver": "cursor",
                        "helpers": ["claude_code"],
                        "created": "2026-10-17T00:00:00Z",
                        "installed": [],
                    }
                },
            }
            (env.board / "projects.json").write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(CoopError) as raised:
                load_projects(env.board)
            self.assertEqual(raised.exception.code, 6)
            self.assertIn("expected format", str(raised.exception))

    def test_summary_reports_damaged_settings(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            (env.board / "agents.json").write_text('{"version": 1}\n', encoding="utf-8")
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["check", "--as", "cursor", "--summary"])
            self.assertEqual(code, 0)
            self.assertEqual(err.getvalue(), "")
            self.assertIn("board settings are damaged", out.getvalue())
            self.assertEqual(out.getvalue().strip().count("\n"), 0)


if __name__ == "__main__":
    unittest.main()
