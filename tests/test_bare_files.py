"""Tests for bare-mode files that disagree with the folder they live in."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

import support

from agentcoop.cli import main

BODY = "x" * (5 * 1024 * 1024)


def _ready(env: support.TempEnv) -> None:
    main(["setup", "--connect", "cursor,claude-code", "--yes"])
    folder = env.workspace / "demo"
    folder.mkdir()
    main(
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


def _write(env: support.TempEnv, stem: str, **overrides: object) -> None:
    data = {
        "v": 1,
        "id": stem,
        "ts": "2026-01-01T00:00:00Z",
        "project": "demo",
        "sender": "cursor",
        "recipient": "claude_code",
        "type": "question",
        "re": None,
        "subject": "hi",
        "body": BODY,
        "attachments": [],
        "needs_reply": False,
    }
    data.update(overrides)
    folder = env.board / "projects" / "demo" / "inbox" / "claude_code" / "msgs"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (stem + ".json")).write_text(json.dumps(data), encoding="utf-8")


def _check(reason: str) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(["check", "--as", "claude_code", "--project", "demo"])
    text = out.getvalue()
    assert code == 0
    assert "OUT OF PROTOCOL" in text
    assert reason in text
    assert len(text.encode("utf-8")) < 2048
    return text


class BareFileTests(unittest.TestCase):
    def test_id_must_match_the_file_name(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            _write(env, "20260101T000000Z_cursor_bbbb", id="20260101T000000Z_cursor_aaaa")
            text = _check("The id inside the file does not match the file name.")
            self.assertIn("20260101T000000Z_cursor_bbbb", text)
            self.assertNotIn("20260101T000000Z_cursor_aaaa", text)
            self.assertEqual(main(["done", "--as", "claude_code", "--project", "demo", "20260101T000000Z_cursor_bbbb"]), 0)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["check", "--as", "claude_code", "--project", "demo"]), 0)
            self.assertNotIn("OUT OF PROTOCOL", out.getvalue())

    def test_recipient_must_match_the_inbox(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            stem = "20260101T000000Z_cursor_rcpt"
            _write(env, stem, recipient="cursor")
            _check("This message is in the wrong inbox.")
            self.assertEqual(main(["done", "--as", "claude_code", "--project", "demo", stem]), 0)

    def test_project_must_match_the_folder(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            stem = "20260101T000000Z_cursor_proj"
            _write(env, stem, project="other")
            _check("This message names a different project.")
            self.assertEqual(main(["done", "--as", "claude_code", "--project", "demo", stem]), 0)

    def test_oversized_body_is_out_of_protocol(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            stem = "20260101T000000Z_cursor_huge"
            _write(env, stem)
            _check("This message is larger than the board allows.")
            self.assertEqual(main(["done", "--as", "claude_code", "--project", "demo", stem]), 0)


if __name__ == "__main__":
    unittest.main()
