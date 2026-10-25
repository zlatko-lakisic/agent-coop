"""Pings, heartbeats, request states, and second opinions."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone

import support

from agentcoop.cli import main
from agentcoop.core import check_inbox, render_check, send
from agentcoop.cursorloop import cursor_stop_followup
from agentcoop.errors import CoopError
from agentcoop.outbox import outbox_dir, process_outbox
from agentcoop.paths import inbox_msgs
from agentcoop.ping import post_due_heartbeats, quiet_since
from agentcoop.tasks import move_request, request_state


def _quiet(argv: list[str]) -> str:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = main(argv)
    if code != 0:
        raise AssertionError(out.getvalue() or "command failed")
    return out.getvalue()


def _ready(env: support.TempEnv) -> None:
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


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class PingTests(unittest.TestCase):
    def test_pings_both_ways_and_nudge_still_works(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            when = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            nudge = send(
                env.board,
                project="demo",
                sender="claude_code",
                recipient="cursor",
                subject="Attention",
                body="Old nudge.",
                type="nudge",
                now=when,
            )
            self.assertTrue(nudge)
            with self.assertRaises(CoopError) as sideways:
                send(
                    env.board,
                    project="demo",
                    sender="claude_code",
                    recipient="claude_desktop",
                    subject="Attention",
                    body="No.",
                    type="ping",
                    now=when,
                )
            self.assertIn("only ping the driver", str(sideways.exception))
            ping = _quiet(
                [
                    "send",
                    "--as",
                    "cursor",
                    "--project",
                    "demo",
                    "--to",
                    "claude_desktop",
                    "--type",
                    "ping",
                    "--body",
                    "Are you still there?",
                ]
            ).strip().splitlines()[-1]
            beat = _quiet(
                [
                    "heartbeat",
                    "--as",
                    "claude_desktop",
                    "--project",
                    "demo",
                    "--re",
                    ping,
                    "--status",
                    "working",
                    "--on",
                    nudge,
                    "--next",
                    "Still reading.",
                ]
            ).strip()
            self.assertTrue(beat)
            with self.assertRaises(CoopError) as again:
                send(
                    env.board,
                    project="demo",
                    sender="cursor",
                    recipient="claude_desktop",
                    subject="Attention",
                    body="Again.",
                    type="ping",
                    now=datetime.now(timezone.utc),
                )
            self.assertIn("next ping is allowed", str(again.exception))

    def test_automatic_heartbeat_and_not_answering(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            start = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            first = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="Attention",
                body="First.",
                type="ping",
                now=start,
            )
            post_due_heartbeats(env.board, "demo", now=start + timedelta(minutes=10))
            replies = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (env.board / "projects" / "demo" / "inbox" / "cursor" / "msgs").glob("*.json")
                if json.loads(path.read_text(encoding="utf-8")).get("type") == "heartbeat"
            ]
            self.assertEqual(len(replies), 1)
            self.assertTrue(replies[0]["auto"])
            self.assertIn("did not answer", replies[0]["body"])
            second = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="Attention",
                body="Second.",
                type="ping",
                now=start + timedelta(minutes=11),
            )
            self.assertTrue(second)
            post_due_heartbeats(env.board, "demo", now=start + timedelta(minutes=22))
            self.assertTrue(quiet_since(env.board, "demo", "claude_code"))
            text = render_check(check_inbox(env.board, "demo", "cursor", now=(start + timedelta(minutes=22)).timestamp()))
            self.assertIn("not answering since", text)

    def test_ping_and_heartbeat_wake_the_stop_hook(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()
            sent = {"done": False}

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                if not sent["done"] and clock() >= datetime(2026, 10, 25, 12, 0, 4, tzinfo=timezone.utc):
                    send(
                        env.board,
                        project="demo",
                        sender="claude_code",
                        recipient="cursor",
                        subject="Attention",
                        body="Ping from the helper.",
                        type="ping",
                        now=clock(),
                    )
                    sent["done"] = True

            result = cursor_stop_followup(
                env.board,
                "cursor",
                "{}",
                now=clock,
                sleep=nap,
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertIn("Ping from the helper.", result["followup_message"])
            self.assertIn("end your turn", result["followup_message"])
            clock2 = _Clock()
            clock2.now = datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)
            ping = send(
                env.board,
                project="demo",
                sender="claude_desktop",
                recipient="cursor",
                subject="Attention",
                body="Waiting.",
                type="ping",
                now=clock2(),
            )
            sent_beat = {"done": False}

            def nap_beat(seconds: float) -> None:
                clock2.advance(seconds)
                if not sent_beat["done"] and clock2() >= datetime(2026, 10, 25, 13, 0, 4, tzinfo=timezone.utc):
                    send(
                        env.board,
                        project="demo",
                        sender="cursor",
                        recipient="claude_desktop",
                        subject="Heartbeat",
                        body="Back at the desk.",
                        type="heartbeat",
                        re=ping,
                        status="idle",
                        next_text="Back at the desk.",
                        now=clock2(),
                    )
                    sent_beat["done"] = True

            result = cursor_stop_followup(
                env.board,
                "claude_desktop",
                "{}",
                now=clock2,
                sleep=nap_beat,
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertIn("Back at the desk.", result["followup_message"])


class StateTests(unittest.TestCase):
    def test_decline_cancel_move_and_late(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            when = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)
            note = env.workspace / "demo" / "note.txt"
            note.write_text("hello\n", encoding="utf-8")
            asked = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="Look",
                body="Please look.",
                type="question",
                needs_reply=True,
                attachments=[str(note)],
                now=when,
            )
            self.assertEqual(request_state(env.board, "demo", _load(env, "claude_code", asked)), "open")
            _quiet(["decline", "--as", "claude_code", "--project", "demo", asked, "--reason", "Not my area."])
            self.assertEqual(request_state(env.board, "demo", _load(env, "claude_code", asked)), "declined")
            other = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="Next",
                body="One more.",
                type="question",
                needs_reply=True,
                attachments=[str(note)],
                now=when + timedelta(minutes=1),
            )
            moved = move_request(env.board, "demo", "cursor", other, "claude_desktop", "Better fit.", now=when + timedelta(minutes=2))
            moved_message = _load(env, "claude_desktop", moved)
            self.assertEqual(moved_message.moved_from, other)
            self.assertTrue(moved_message.attachments)
            self.assertEqual(request_state(env.board, "demo", _load(env, "claude_code", other)), "moved")
            send(
                env.board,
                project="demo",
                sender="claude_code",
                recipient="cursor",
                subject="Re: Next",
                body="Late note.",
                type="answer",
                re=other,
                now=when + timedelta(minutes=3),
            )
            text = render_check(check_inbox(env.board, "demo", "cursor"))
            self.assertIn("late", text)
            self.assertIn("Late note.", text)
            box = outbox_dir(env.board, "demo", "claude_desktop")
            path = box / (moved + ".md")
            path.write_bytes(b"\xef\xbb\xbfDECLINE: Too late.\r\n")
            import os

            old = (datetime.now() - timedelta(seconds=30)).timestamp()
            os.utime(path, (old, old))
            process_outbox(env.board, "demo", "claude_desktop")
            self.assertEqual(request_state(env.board, "demo", moved_message), "declined")


class OpinionTests(unittest.TestCase):
    def test_at_most_three_and_helpers_do_not_see_each_other(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            code = main(
                [
                    "send",
                    "--as",
                    "cursor",
                    "--project",
                    "demo",
                    "--to",
                    "claude_code,claude_desktop,claude_code,claude_desktop",
                    "--type",
                    "question",
                    "--subject",
                    "Too many",
                    "--body",
                    "No.",
                ]
            )
            self.assertNotEqual(code, 0)
            text = _quiet(
                [
                    "send",
                    "--as",
                    "cursor",
                    "--project",
                    "demo",
                    "--to",
                    "claude_code,claude_desktop",
                    "--type",
                    "question",
                    "--needs-reply",
                    "--subject",
                    "Which way",
                    "--body",
                    "Pick one.",
                ]
            )
            import re

            found = re.findall(r"\d{8}T\d{6}Z_[A-Za-z0-9_]+_[0-9a-f]{4}", text)
            self.assertEqual(len(found), 2)
            first = json.loads((inbox_msgs(env.board, "demo", "claude_code") / (found[0] + ".json")).read_text(encoding="utf-8"))
            second = json.loads((inbox_msgs(env.board, "demo", "claude_desktop") / (found[1] + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(first["group"], second["group"])
            self.assertIn("Answer on your own", first["body"])
            send(
                env.board,
                project="demo",
                sender="claude_code",
                recipient="cursor",
                subject="Re: Which way",
                body="Code says left.",
                type="answer",
                re=found[0],
            )
            send(
                env.board,
                project="demo",
                sender="claude_desktop",
                recipient="cursor",
                subject="Re: Which way",
                body="Desktop says right.",
                type="answer",
                re=found[1],
            )
            driver = render_check(check_inbox(env.board, "demo", "cursor"))
            self.assertIn("all 2 answers are in", driver)
            self.assertIn("Code says left.", driver)
            helper = render_check(check_inbox(env.board, "demo", "claude_code"))
            self.assertNotIn("Desktop says right.", helper)


def _load(env: support.TempEnv, agent: str, message_id: str):
    from agentcoop.core import load_message

    return load_message(inbox_msgs(env.board, "demo", agent) / (message_id + ".json"))
