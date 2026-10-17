"""Tests for judging a message by the roles in effect when it was sent."""

from __future__ import annotations

import io
import json
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

import support

from agentcoop.cli import main
from agentcoop.doctor import format_doctor, run_doctor
from agentcoop.projects import load_projects, save_projects


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


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


class RolesHistoryTests(unittest.TestCase):
    def test_old_driver_messages_stay_in_protocol(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            self.assertEqual(
                main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--to",
                        "claude_code",
                        "--subject",
                        "Look",
                        "--body",
                        "please",
                        "--project",
                        "demo",
                    ]
                ),
                0,
            )
            time.sleep(1.1)
            with support.person("demo"), redirect_stdout(io.StringIO()):
                self.assertEqual(main(["project", "driver", "demo", "claude_code", "--yes"]), 0)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["check", "--as", "claude_code", "--project", "demo"]), 0)
            text = out.getvalue()
            self.assertIn("Look", text)
            self.assertNotIn("OUT OF PROTOCOL", text)
            stem = "20261017T000000Z_cursor_late"
            folder = env.board / "projects" / "demo" / "inbox" / "claude_code" / "msgs"
            payload = {
                "v": 1,
                "id": stem,
                "ts": _now(),
                "project": "demo",
                "sender": "cursor",
                "recipient": "claude_code",
                "type": "question",
                "re": None,
                "subject": "After",
                "body": "too late",
                "attachments": [],
                "needs_reply": False,
            }
            (folder / (stem + ".json")).write_text(json.dumps(payload), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["check", "--as", "claude_code", "--project", "demo"]), 0)
            self.assertIn("OUT OF PROTOCOL", out.getvalue())
            self.assertIn(stem, out.getvalue())

    def test_doctor_ignores_a_done_out_of_protocol_message(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            stem = "20260101T000000Z_cursor_bad"
            folder = env.board / "projects" / "demo" / "inbox" / "claude_code" / "msgs"
            payload = {
                "v": 1,
                "id": stem,
                "ts": "2026-01-01T00:00:00Z",
                "project": "demo",
                "sender": "stranger",
                "recipient": "claude_code",
                "type": "question",
                "re": None,
                "subject": "nope",
                "body": "nope",
                "attachments": [],
                "needs_reply": False,
            }
            (folder / (stem + ".json")).write_text(json.dumps(payload), encoding="utf-8")
            self.assertIn(stem, format_doctor(run_doctor(env.board)))
            self.assertEqual(main(["done", "--as", "claude_code", "--project", "demo", stem]), 0)
            self.assertNotIn(stem, format_doctor(run_doctor(env.board)))

    def test_missing_history_uses_current_roles(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            data = load_projects(env.board)
            data["projects"]["demo"].pop("roles_history", None)
            save_projects(env.board, data)
            self.assertEqual(
                main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--to",
                        "claude_code",
                        "--subject",
                        "Still",
                        "--body",
                        "ok",
                        "--project",
                        "demo",
                    ]
                ),
                0,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["check", "--as", "claude_code", "--project", "demo"]), 0)
            self.assertIn("Still", out.getvalue())
            self.assertNotIn("OUT OF PROTOCOL", out.getvalue())
            self.assertIsNone(load_projects(env.board)["projects"]["demo"].get("roles_history"))


if __name__ == "__main__":
    unittest.main()
