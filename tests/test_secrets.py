"""Tests for secret blocking on send."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

import support

from agentcoop.cli import main
from agentcoop.paths import inbox_msgs, sent_dir
from agentcoop.projects import add_project, connect_agent_record, init_board_files


def _ready(env: support.TempEnv) -> None:
    init_board_files(env.board)
    connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
    connect_agent_record(env.board, "claude_code", "claude-code", display="Claude Code")
    folder = env.workspace / "frontend"
    folder.mkdir()
    add_project(env.board, "frontend", folder, "cursor", {"claude_code": "reviews code"})


class SecretSendTests(unittest.TestCase):
    def test_secret_in_body_is_refused(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--to",
                        "claude_code",
                        "--subject",
                        "hi",
                        "--body",
                        "key sk-abcdefghijklmnop here",
                        "--project",
                        "frontend",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("was not sent", err.getvalue())
            self.assertIn("--allow-secret", err.getvalue())
            msgs = inbox_msgs(env.board, "frontend", "claude_code")
            self.assertFalse(list(msgs.glob("*.json")) if msgs.is_dir() else False)
            sent = sent_dir(env.board, "frontend", "cursor")
            self.assertFalse(list(sent.glob("*.json")) if sent.is_dir() else False)

    def test_allow_secret_sends(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--to",
                        "claude_code",
                        "--subject",
                        "hi",
                        "--body",
                        "key sk-abcdefghijklmnop here",
                        "--project",
                        "frontend",
                        "--allow-secret",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue(out.getvalue().strip())
            msgs = inbox_msgs(env.board, "frontend", "claude_code")
            self.assertEqual(len(list(msgs.glob("*.json"))), 1)

    def test_secret_in_subject_is_refused(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--to",
                        "claude_code",
                        "--subject",
                        "sk-abcdefghijklmnop",
                        "--body",
                        "plain body",
                        "--project",
                        "frontend",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("was not sent", err.getvalue())
            msgs = inbox_msgs(env.board, "frontend", "claude_code")
            self.assertFalse(list(msgs.glob("*.json")) if msgs.is_dir() else False)


if __name__ == "__main__":
    unittest.main()
