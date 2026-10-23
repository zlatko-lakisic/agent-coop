"""Progress fields and helper progress documents."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.errors import CoopError
from agentcoop.progressmeta import format_fields, parse_fields, parse_percent, parse_status, parse_step
from agentcoop.projects import add_project, connect_agent_record, init_board_files, load_config


class FieldTests(unittest.TestCase):
    def test_valid_and_invalid_values(self) -> None:
        self.assertEqual(parse_status("working"), "working")
        self.assertEqual(parse_percent("0"), 0)
        self.assertEqual(parse_percent("100"), 100)
        self.assertEqual(parse_step("3 of 7"), "3 of 7")
        for bad in ("120", "-1", "about half", ""):
            with self.assertRaises(CoopError):
                parse_percent(bad)
        with self.assertRaises(CoopError):
            parse_step("3/7")
        with self.assertRaises(CoopError):
            parse_status("busy")
        text = format_fields(status="working", step="3 of 7", percent="43", now="Reading the logs.", for_id="abc", rest="## Done so far\nOne.")
        fields = parse_fields(text)
        self.assertEqual(fields["Status"], "working")
        self.assertEqual(fields["Step"], "3 of 7")
        self.assertEqual(fields["Percent"], "43")
        self.assertEqual(fields["Now"], "Reading the logs.")
        self.assertEqual(fields["For"], "abc")
        self.assertIn("Done so far", fields["rest"])


class HelperProgressTests(unittest.TestCase):
    def test_only_an_open_driver_request(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor")
            connect_agent_record(env.board, "claude_code", "claude-code")
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {"claude_code": "reviews"})
            sent = io.StringIO()
            with redirect_stdout(sent), redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--project",
                        "demo",
                        "--to",
                        "claude_code",
                        "--subject",
                        "Look",
                        "--body",
                        "Please look.",
                        "--needs-reply",
                    ]
                )
            self.assertEqual(code, 0)
            message_id = ""
            for line in sent.getvalue().splitlines():
                if line.strip():
                    message_id = line.split()[0]
            self.assertTrue(message_id)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "progress",
                        "write",
                        "--as",
                        "claude_code",
                        "--project",
                        "demo",
                        "--for",
                        message_id,
                        "--percent",
                        "50",
                        "--step",
                        "2 of 4",
                        "--now",
                        "Reading the training logs.",
                        "--body",
                        "Done so far: one file.",
                    ]
                )
            self.assertEqual(code, 0, out.getvalue())
            folder_path = env.board / "projects" / "demo" / "progress" / "helpers" / "claude_code"
            files = list(folder_path.glob("*.md"))
            self.assertEqual(len(files), 1)
            body = files[0].read_text(encoding="utf-8")
            self.assertIn("Percent: 50", body)
            self.assertIn("For: " + message_id, body)
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = main(
                    [
                        "progress",
                        "write",
                        "--as",
                        "claude_code",
                        "--project",
                        "demo",
                        "--for",
                        "not-a-message",
                        "--body",
                        "no",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("not an open request", err.getvalue())
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = main(
                    [
                        "progress",
                        "write",
                        "--as",
                        "claude_code",
                        "--project",
                        "demo",
                        "--for",
                        message_id,
                        "--percent",
                        "120",
                        "--body",
                        "no",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("Percent", err.getvalue())


class SettingTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            config = load_config(env.board)
            self.assertEqual(config["progress_every_minutes"], 10)
            self.assertEqual(config["progress_long_task_minutes"], 5)
            self.assertTrue((Path(env.board) / "config.json").is_file())


if __name__ == "__main__":
    unittest.main()
