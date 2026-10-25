"""Helper lines rank by grade, then availability, then cost."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone

import support

from agentcoop.cli import main
from agentcoop.core import check_inbox, render_check, send
from agentcoop.grades import write_grade
from agentcoop.profiles import set_profile
from agentcoop.watch import write_heartbeat


def _quiet(argv: list[str]) -> str:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = main(argv)
    if code != 0:
        raise AssertionError(out.getvalue() or "command failed")
    return out.getvalue()


class ChoiceTests(unittest.TestCase):
    def test_ranking_new_label_and_availability(self) -> None:
        with support.TempEnv() as env:
            _quiet(["setup", "--connect", "cursor,claude-code,claude-desktop", "--schedule", "no", "--yes"])
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
                    "claude_code=edits",
                    "--helper",
                    "claude_desktop=thinks",
                    "--gitignore",
                    "no",
                    "--yes",
                ]
            )
            set_profile(env.board, "demo", "claude_code", cost="low", tags=["reasoning"], can=["read"])
            set_profile(env.board, "demo", "claude_desktop", cost="high")
            when = datetime(2026, 10, 25, 16, 0, tzinfo=timezone.utc)
            for offset in range(3):
                asked = send(
                    env.board,
                    project="demo",
                    sender="cursor",
                    recipient="claude_code",
                    subject="Look",
                    body="Please look.",
                    type="question",
                    needs_reply=True,
                    now=when + timedelta(minutes=offset),
                )
                answer = send(
                    env.board,
                    project="demo",
                    sender="claude_code",
                    recipient="cursor",
                    subject="Re: Look",
                    body="Done.",
                    type="answer",
                    re=asked,
                    now=when + timedelta(minutes=offset, seconds=30),
                )
                write_grade(
                    env.board,
                    "demo",
                    "cursor",
                    answer,
                    score=5,
                    outcome="used",
                    note="It helped.",
                    tags=["reasoning"],
                    now=when + timedelta(hours=1, minutes=offset),
                )
            send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="Next",
                body="One more.",
                type="question",
                needs_reply=True,
                now=when + timedelta(hours=2),
            )
            write_heartbeat(
                env.board,
                "demo",
                "claude_code",
                since=datetime.now(timezone.utc),
                seen=datetime.now(timezone.utc),
            )
            text = _quiet(["helpers", "--as", "cursor", "--project", "demo", "--for", "reasoning"])
            self.assertLess(text.index("claude_code"), text.index("claude_desktop"))
            self.assertIn("watching, idle", text)
            self.assertIn("reasoning 5.0 (3)", text)
            self.assertIn("1 open request", text)
            self.assertIn("cost low", text)
            self.assertIn("claude_desktop: offline", text)
            self.assertIn("new", text)
            self.assertNotIn("0.0", text)
            header = render_check(check_inbox(env.board, "demo", "cursor"))
            self.assertIn("claude_code: watching, idle", header)
            self.assertIn("tags:", text)
            self.assertNotIn("tags:", header)
