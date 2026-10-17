"""Tests for the command line exit codes and check output."""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

import support

from agentcoop.cli import main
from agentcoop.projects import add_project, connect_agent_record, init_board_files


def _ready(env: support.TempEnv) -> None:
    init_board_files(env.board)
    connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
    connect_agent_record(env.board, "claude_code", "claude-code", display="Claude Code")
    folder = env.workspace / "frontend"
    folder.mkdir()
    add_project(env.board, "frontend", folder, "cursor", {"claude_code": "reviews code"})


class CliTests(unittest.TestCase):
    def test_exit_codes(self) -> None:
        with support.TempEnv() as env:
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(main(["whoami"]), 2)
            self.assertIn("--as NAME", err.getvalue())
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(main(["check", "--as", "cursor"]), 3)
            self.assertIn("agentcoop setup", err.getvalue())
            _ready(env)
            previous = os.getcwd()
            try:
                os.chdir(env.home)
                err = io.StringIO()
                with redirect_stderr(err):
                    self.assertEqual(main(["check", "--as", "cursor"]), 4)
            finally:
                os.chdir(previous)
            self.assertIn("--project NAME", err.getvalue())

    def test_check_summary_and_whoami(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            previous = os.getcwd()
            try:
                os.chdir(env.workspace / "frontend")
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main(["check", "--as", "cursor", "--summary"]), 0)
                self.assertEqual(out.getvalue(), "")
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(
                        main(
                            [
                                "send",
                                "--as",
                                "cursor",
                                "--to",
                                "claude_code",
                                "--subject",
                                "Hello",
                                "--body",
                                "Say hello",
                                "--needs-reply",
                            ]
                        ),
                        0,
                    )
                mid = out.getvalue().strip()
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main(["check", "--as", "claude_code", "--summary"]), 0)
                self.assertIn("1 new message for claude_code", out.getvalue())
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main(["whoami", "--as", "cursor"]), 0)
                text = out.getvalue()
                self.assertIn("Role: driver", text)
                self.assertIn("claude_code: reviews code", text)
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main(["version"]), 0)
                self.assertIn("spec 0.1", out.getvalue())
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main(["done", "--as", "claude_code", mid]), 0)
                self.assertIn("Marked 1 message done.", out.getvalue())
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
