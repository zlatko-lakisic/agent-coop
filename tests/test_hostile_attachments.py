"""Tests for attachment checks on the real file."""

from __future__ import annotations

import io
import os
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.core import write_message_file
from agentcoop.errors import CoopError
from agentcoop.paths import inbox_msgs
from agentcoop.projects import add_project, connect_agent_record, init_board_files
from agentcoop.safety import open_attachment


def _ready(env: support.TempEnv) -> Path:
    init_board_files(env.board)
    connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
    connect_agent_record(env.board, "claude_code", "claude-code", display="Claude Code")
    folder = env.workspace / "frontend"
    folder.mkdir()
    add_project(env.board, "frontend", folder, "cursor", {"claude_code": "reviews"})
    return folder


def _kwargs(env: support.TempEnv, folder: Path) -> dict:
    return {
        "project_path": folder,
        "board": env.board,
        "project": "frontend",
        "allowed_roots": [],
    }


def _send(folder: Path, attach: str) -> int:
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
                "see file",
                "--project",
                "frontend",
                "--attach",
                attach,
            ]
        )
    return code


class HostileAttachmentTests(unittest.TestCase):
    def test_symlink_to_env_is_refused_and_blocked(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            secret = folder / ".env"
            secret.write_text("TOKEN=1\n", encoding="utf-8")
            link = folder / "review.txt"
            try:
                link.symlink_to(secret)
            except OSError:
                self.skipTest("symlinks are not available")
            self.assertEqual(_send(folder, str(link)), 1)
            mid = "20261017T000000Z_cursor_aaaa"
            write_message_file(
                inbox_msgs(env.board, "frontend", "claude_code"),
                {
                    "v": 1,
                    "id": mid,
                    "ts": "2026-10-17T00:00:00Z",
                    "project": "frontend",
                    "sender": "cursor",
                    "recipient": "claude_code",
                    "type": "question",
                    "re": None,
                    "subject": "hi",
                    "body": "see file",
                    "attachments": [str(link)],
                    "needs_reply": False,
                },
            )
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["check", "--as", "claude_code", "--project", "frontend"]), 0)
            self.assertIn("BLOCKED", out.getvalue())

    def test_secret_names_are_case_insensitive(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            for name in (".ENV.local", "key.PEM", "Credentials.json"):
                path = folder / name
                path.write_text("x", encoding="utf-8")
                self.assertEqual(_send(folder, str(path)), 1, name)

    def test_folder_is_refused(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            nested = folder / "notes"
            nested.mkdir()
            self.assertEqual(_send(folder, str(nested)), 1)

    @unittest.skipIf(os.name == "nt", "named pipes are not available on Windows")
    def test_named_pipe_is_refused(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            pipe = folder / "pipe.txt"
            os.mkfifo(pipe)
            self.assertEqual(_send(folder, str(pipe)), 1)

    def test_symlink_to_normal_file_inside_project_is_allowed(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            target = folder / "notes.txt"
            target.write_text("hello", encoding="utf-8")
            link = folder / "review.txt"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks are not available")
            self.assertEqual(_send(folder, str(link)), 0)

    def test_open_attachment_size_limit_and_clip(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            big = folder / "big.bin"
            big.write_bytes(b"a" * (1024 * 1024))
            kwargs = _kwargs(env, folder)
            with self.assertRaises(CoopError):
                open_attachment(str(big), max_bytes=1000, **kwargs)
            clipped = open_attachment(str(big), max_bytes=1000, clip=True, **kwargs)
            self.assertEqual(len(clipped), 1000)

    @unittest.skipIf(os.name == "nt", "named pipes are not available on Windows")
    def test_open_attachment_on_a_pipe_does_not_block(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            pipe = folder / "pipe.txt"
            os.mkfifo(pipe)
            box: list = []

            def _call() -> None:
                try:
                    box.append(
                        open_attachment(str(pipe), max_bytes=1000, **_kwargs(env, folder))
                    )
                except Exception as exc:
                    box.append(exc)

            thread = threading.Thread(target=_call)
            thread.start()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertTrue(box)
            self.assertIsInstance(box[0], CoopError)


if __name__ == "__main__":
    unittest.main()
