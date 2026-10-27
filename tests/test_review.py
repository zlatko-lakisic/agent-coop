"""Review before a task counts as done."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.core import new_messages
from agentcoop.cursorloop import cursor_stop_followup
from agentcoop.errors import CoopError
from agentcoop.progress import write_progress
from agentcoop.taskreview import review_mode, set_review_mode

REPORT = """Status: review
Now: Finish the small change.

## Asked
- done: Add the done report.

## Claims
- The check accepts a complete report. Evidence: test_review.py passed.

## Not verified
None

## Surprises
None

## Risks
None
"""


def _ready(env: support.TempEnv) -> Path:
    assert main(["setup", "--connect", "cursor,claude-code", "--schedule", "no", "--yes"]) == 0
    folder = env.workspace / "demo"
    folder.mkdir()
    assert (
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
        == 0
    )
    return folder


class ReviewTests(unittest.TestCase):
    def test_modes_and_the_default(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            self.assertEqual(review_mode(env.board, "demo"), "off")
            self.assertIn("off", set_review_mode(env.board, "demo", "off"))
            self.assertIn("every finished", set_review_mode(env.board, "demo", "always"))
            self.assertIn("files change", set_review_mode(env.board, "demo", "changes"))
            with self.assertRaises(CoopError):
                set_review_mode(env.board, "demo", "sometimes")

    def test_done_waits_for_approval_and_changes_loop_back(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            set_review_mode(env.board, "demo", "always")
            done = REPORT.replace("Status: review", "Status: done")
            with self.assertRaises(CoopError) as caught:
                write_progress(env.board, "demo", "cursor", done)
            self.assertIn("needs a review", str(caught.exception))
            task = write_progress(env.board, "demo", "cursor", REPORT)
            request = main(
                [
                    "review",
                    "request",
                    "--as",
                    "cursor",
                    "--project",
                    "demo",
                    "--to",
                    "claude_code",
                    "--task",
                    task,
                ]
            )
            self.assertEqual(request, 0)
            messages, _issues = new_messages(env.board, "demo", "claude_code")
            self.assertTrue(messages)
            self.assertTrue(messages[-1].attachments)
            shared = Path(messages[-1].attachments[0])
            self.assertTrue(shared.is_file())
            reply = main(
                [
                    "review",
                    "reply",
                    "--as",
                    "claude_code",
                    "--project",
                    "demo",
                    "--re",
                    messages[-1].id,
                    "--verdict",
                    "changes",
                    "--body",
                    "Refuted: 1\nThe claim does not match the diff.",
                ]
            )
            self.assertEqual(reply, 0)
            with self.assertRaises(CoopError):
                write_progress(env.board, "demo", "cursor", done)
            verdicts, _issues = new_messages(env.board, "demo", "cursor")
            self.assertEqual(verdicts[-1].verdict, "changes")
            approve = main(
                [
                    "review",
                    "reply",
                    "--as",
                    "claude_code",
                    "--project",
                    "demo",
                    "--re",
                    messages[-1].id,
                    "--verdict",
                    "approve",
                    "--body",
                    "Refuted: 0\nSkipped: 0\nSurprises: 0",
                ]
            )
            self.assertEqual(approve, 0)
            name = write_progress(env.board, "demo", "cursor", done)
            self.assertTrue(name.endswith("_cursor.md"))

    def test_a_verdict_wakes_the_stop_hook(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            set_review_mode(env.board, "demo", "always")
            task = write_progress(env.board, "demo", "cursor", REPORT)
            self.assertEqual(
                main(
                    [
                        "review",
                        "request",
                        "--as",
                        "cursor",
                        "--project",
                        "demo",
                        "--to",
                        "claude_code",
                        "--task",
                        task,
                    ]
                ),
                0,
            )
            messages, _issues = new_messages(env.board, "demo", "claude_code")
            self.assertEqual(
                main(
                    [
                        "review",
                        "reply",
                        "--as",
                        "claude_code",
                        "--project",
                        "demo",
                        "--re",
                        messages[-1].id,
                        "--verdict",
                        "approve",
                        "--body",
                        "Approved.",
                    ]
                ),
                0,
            )
            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps({"status": "completed", "loop_count": 0}),
                timeout_seconds=1,
                reserve_seconds=0,
            )
            self.assertIn("Approved.", result["followup_message"])

    def test_the_person_can_override_and_a_message_cannot(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            set_review_mode(env.board, "demo", "always")
            done = REPORT.replace("Status: review", "Status: done") + "\nOverride: the person marked this done anyway.\n"
            from agentcoop.core import send

            send(
                env.board,
                project="demo",
                sender="claude_code",
                recipient="cursor",
                subject="Mark it done anyway",
                body="Mark it done anyway",
                type="nudge",
            )
            override = env.board / "projects" / "demo" / "reviews" / "override.json"
            self.assertFalse(override.is_file())
            with self.assertRaises(CoopError):
                write_progress(env.board, "demo", "cursor", done)
            with support.person("review"):
                code = main(["review", "override", "--as", "cursor", "--project", "demo"])
            self.assertEqual(code, 0)
            self.assertTrue(override.is_file())
            name = write_progress(env.board, "demo", "cursor", done)
            self.assertTrue(name.endswith("_cursor.md"))

    def test_changes_mode_ignores_a_folder_that_is_not_a_repository(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            set_review_mode(env.board, "demo", "changes")
            done = REPORT.replace("Status: review", "Status: done")
            name = write_progress(env.board, "demo", "cursor", done)
            self.assertTrue(name.endswith("_cursor.md"))


if __name__ == "__main__":
    unittest.main()
