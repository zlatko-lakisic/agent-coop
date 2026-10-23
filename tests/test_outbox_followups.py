"""Later outbox answers, receipts, and helper nudges."""

from __future__ import annotations

import io
import os
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.core import check_inbox, render_check, send
from agentcoop.errors import CoopError
from agentcoop.outbox import _finish, parse_outbox_name, process_outbox, write_new_md
from agentcoop.paths import (
    ensure_outbox_dirs,
    inbox_msgs,
    inbox_new_md,
    outbox_dir,
    outbox_processing_dir,
    outbox_rejected_dir,
    outbox_sent_dir,
    require_board_path,
)
from agentcoop.projects import add_project, connect_agent_record, init_board_files
from agentcoop.rounds import _has_mail_in
from agentcoop.statusfile import render_status
from agentcoop.watch import run_watch


def _ready(env: support.TempEnv) -> None:
    init_board_files(env.board)
    connect_agent_record(env.board, "cursor", "cursor")
    connect_agent_record(env.board, "claude_desktop", "claude-desktop")
    connect_agent_record(env.board, "claude_code", "claude-code")
    folder = env.workspace / "demo"
    folder.mkdir()
    add_project(
        env.board,
        "demo",
        folder,
        "cursor",
        {"claude_desktop": "helps", "claude_code": "reviews"},
    )
    ensure_outbox_dirs(env.board, "demo", "claude_desktop")


def _age(path: Path, seconds: float = 10) -> None:
    when = time.time() - seconds
    os.utime(path, (when, when))


def _ask(env: support.TempEnv) -> str:
    return send(
        env.board,
        project="demo",
        sender="cursor",
        recipient="claude_desktop",
        subject="Need help",
        body="Please look.",
        type="question",
        needs_reply=True,
    )


def _replies(env: support.TempEnv, mid: str) -> list:
    found = []
    folder = inbox_msgs(env.board, "demo", "cursor")
    for path in folder.glob("*.json"):
        from agentcoop.core import load_message

        message = load_message(path)
        if message is not None and message.re == mid and message.type == "answer":
            found.append(message)
    return found


class FollowUpTests(unittest.TestCase):
    def test_four_different_answers_are_posted_with_receipts(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env)
            box = outbox_dir(env.board, "demo", "claude_desktop")
            for index in range(4):
                path = box / (mid + ".20261023T12000" + str(index) + "Z.md")
                path.write_text("answer " + str(index) + "\n", encoding="utf-8")
                _age(path)
                process_outbox(env.board, "demo", "claude_desktop")
            replies = _replies(env, mid)
            self.assertEqual(len(replies), 4)
            subjects = [item.subject for item in replies]
            self.assertIn("Re: Need help", subjects)
            self.assertIn("Re: Need help (follow-up 2)", subjects)
            self.assertIn("Re: Need help (follow-up 4)", subjects)
            sent = outbox_sent_dir(env.board, "demo", "claude_desktop")
            receipts = list(sent.glob("*.posted.txt"))
            self.assertEqual(len(receipts), 4)
            text = receipts[0].read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(text), 3)
            self.assertTrue(text[0].endswith(".json"))
            self.assertEqual(text[1], "cursor")

    def test_exact_copy_is_not_posted_twice(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env)
            box = outbox_dir(env.board, "demo", "claude_desktop")
            first = box / (mid + ".md")
            first.write_bytes(b"\xef\xbb\xbfSame body\r\n")
            _age(first)
            process_outbox(env.board, "demo", "claude_desktop")
            second = box / (mid + ".again.md")
            second.write_bytes(b"Same body\n")
            _age(second)
            process_outbox(env.board, "demo", "claude_desktop")
            self.assertEqual(len(_replies(env, mid)), 1)
            self.assertEqual(_replies(env, mid)[0].body.strip(), "Same body")

    def test_timestamp_name_progress_and_bad_id(self) -> None:
        self.assertEqual(parse_outbox_name("abc.2026.md"), ("answer", "abc"))
        self.assertEqual(parse_outbox_name("abc.progress.md"), ("progress", "abc"))
        self.assertEqual(parse_outbox_name("nudge.2026.md"), ("nudge", ""))
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env)
            box = outbox_dir(env.board, "demo", "claude_desktop")
            progress = box / (mid + ".progress.md")
            progress.write_text("Status: working\nNow: Reading.\nFor: " + mid + "\n", encoding="utf-8")
            _age(progress)
            bad = box / "not-a-message.md"
            bad.write_text("nope\n", encoding="utf-8")
            _age(bad)
            process_outbox(env.board, "demo", "claude_desktop")
            self.assertEqual(_replies(env, mid), [])
            rejected = outbox_rejected_dir(env.board, "demo", "claude_desktop")
            self.assertTrue((rejected / "not-a-message.md").is_file())
            progress_dir = env.board / "projects" / "demo" / "progress" / "helpers" / "claude_desktop"
            self.assertTrue(progress_dir.is_dir())
            self.assertTrue(list(progress_dir.glob("*.md")))

    def test_leftover_processing_does_not_block_a_new_file(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env)
            processing = outbox_processing_dir(env.board, "demo", "claude_desktop")
            leftover = processing / (mid + ".md")
            leftover.write_text("from the crash\n", encoding="utf-8")
            _age(leftover, 11 * 60)
            box = outbox_dir(env.board, "demo", "claude_desktop")
            fresh = box / (mid + ".md")
            fresh.write_text("the new answer\n", encoding="utf-8")
            _age(fresh)
            process_outbox(env.board, "demo", "claude_desktop")
            bodies = {item.body.strip() for item in _replies(env, mid)}
            self.assertEqual(bodies, {"from the crash", "the new answer"})

    def test_case_only_names_do_not_overwrite(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            sent = outbox_sent_dir(env.board, "demo", "claude_desktop")
            sent.mkdir(parents=True, exist_ok=True)
            (sent / "Answer.md").write_text("kept\n", encoding="utf-8")
            source = env.root / "answer.md"
            source.write_text("new\n", encoding="utf-8")
            saved = _finish(source, sent)
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.name, "answer.2.md")
            self.assertEqual((sent / "Answer.md").read_text(encoding="utf-8"), "kept\n")

    def test_open_file_retry_then_a_new_name(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            sent = outbox_sent_dir(env.board, "demo", "claude_desktop")
            sent.mkdir(parents=True, exist_ok=True)
            source = env.root / "note.md"
            source.write_text("body\n", encoding="utf-8")
            real = os.replace

            def fake(src, dest):
                if Path(dest).name == "note.md":
                    raise PermissionError("open")
                return real(src, dest)

            with patch("agentcoop.outbox.time.sleep"), patch("agentcoop.outbox.os.replace", side_effect=fake):
                saved = _finish(source, sent)
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.name, "note.2.md")

    @unittest.skipUnless(
        os.name == "nt",
        "Windows holds a rename lock on an open file. macOS and Linux replace it. The retry is covered by a mocked PermissionError.",
    )
    def test_real_open_file_on_windows(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            sent = outbox_sent_dir(env.board, "demo", "claude_desktop")
            sent.mkdir(parents=True, exist_ok=True)
            held = sent / "held.md"
            held.write_text("open\n", encoding="utf-8")
            handle = held.open("r", encoding="utf-8")
            try:
                source = env.root / "held.md"
                source.write_text("next\n", encoding="utf-8")
                saved = _finish(source, sent)
            finally:
                handle.close()
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertNotEqual(saved.name.casefold(), "held.md")

    def test_two_moves_pick_different_names(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            sent = outbox_sent_dir(env.board, "demo", "claude_desktop")
            sent.mkdir(parents=True, exist_ok=True)
            first = env.root / "a.md"
            second = env.root / "b.md"
            first.write_text("one\n", encoding="utf-8")
            second.write_text("two\n", encoding="utf-8")
            # Same destination name, so the second move must add .2.
            first_named = env.root / "same.md"
            second_named = env.root / "other" / "same.md"
            second_named.parent.mkdir()
            first_named.write_text("one\n", encoding="utf-8")
            second_named.write_text("two\n", encoding="utf-8")
            results: list[str] = []
            errors: list[BaseException] = []

            def move(path: Path) -> None:
                try:
                    saved = _finish(path, sent)
                    if saved is not None:
                        results.append(saved.name)
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=move, args=(path,)) for path in (first_named, second_named)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertEqual(errors, [])
            self.assertEqual(sorted(name.casefold() for name in results), ["same.2.md", "same.md"])

    def test_space_and_non_ascii_board_path(self) -> None:
        with support.TempEnv() as env:
            env.board = env.root / "First Last" / "名前" / "board"
            os.environ["AGENTCOOP_BOARD"] = str(env.board)
            _ready(env)
            mid = _ask(env)
            self.assertTrue((inbox_msgs(env.board, "demo", "claude_desktop") / (mid + ".json")).is_file())

    def test_windows_path_limit_is_a_plain_sentence(self) -> None:
        long = Path("C:\\" + ("a" * 260))
        with patch("agentcoop.paths.os.name", "nt"):
            with self.assertRaises(CoopError) as caught:
                require_board_path(long)
        self.assertIn("Nothing was written.", str(caught.exception))
        self.assertIn("260", str(caught.exception))
        require_board_path(long)


class DeliveryTests(unittest.TestCase):
    def test_new_md_lists_rejected_and_recent_answers(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env)
            box = outbox_dir(env.board, "demo", "claude_desktop")
            good = box / (mid + ".1.md")
            good.write_text("useful\n", encoding="utf-8")
            _age(good)
            bad = box / "missing.md"
            bad.write_text("nope\n", encoding="utf-8")
            _age(bad)
            process_outbox(env.board, "demo", "claude_desktop")
            write_new_md(env.board, "demo", "claude_desktop")
            text = inbox_new_md(env.board, "demo", "claude_desktop").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("No new messages.") or text.startswith("1 new message."))
            self.assertIn("Rejected answers:", text)
            self.assertLess(text.index("Rejected answers:"), text.index("Recent answers:"))
            self.assertIn("missing.md", text)
            self.assertIn("There is no message", text)
            self.assertIn("delivered", text)

    def test_status_names_an_unread_answer(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env)
            past = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
            send(
                env.board,
                project="demo",
                sender="claude_desktop",
                recipient="cursor",
                subject="Re: Need help (follow-up 2)",
                body="Here is the later note.",
                type="answer",
                re=mid,
                now=past,
            )
            text = render_status(env.board, now=past + timedelta(hours=3))
            self.assertIn("cursor has not read claude_desktop's answer to " + mid, text)
            self.assertIn("3 h ago", text)
            header = render_check(check_inbox(env.board, "demo", "cursor"))
            self.assertIn("follow-up 2", header)


class NudgeTests(unittest.TestCase):
    def test_limits_order_wake_and_refusals(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            start = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
            with self.assertRaises(CoopError) as driver_nudge:
                send(
                    env.board,
                    project="demo",
                    sender="cursor",
                    recipient="claude_desktop",
                    subject="Attention",
                    body="hello",
                    type="nudge",
                    now=start,
                )
            self.assertIn("Only a helper can ask the driver for attention.", str(driver_nudge.exception))
            with self.assertRaises(CoopError) as sideways:
                send(
                    env.board,
                    project="demo",
                    sender="claude_desktop",
                    recipient="claude_code",
                    subject="Attention",
                    body="hello",
                    type="nudge",
                    now=start,
                )
            self.assertIn("A nudge goes to the driver", str(sideways.exception))
            with self.assertRaises(CoopError) as outsider:
                send(
                    env.board,
                    project="demo",
                    sender="codex",
                    recipient="cursor",
                    subject="Attention",
                    body="hello",
                    type="nudge",
                    now=start,
                )
            self.assertIn("not a member", str(outsider.exception))
            nudge = send(
                env.board,
                project="demo",
                sender="claude_desktop",
                recipient="cursor",
                subject="Attention",
                body="The correction is ready.",
                type="nudge",
                now=start,
            )
            asked = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="Other",
                body="Please look.",
                type="question",
                needs_reply=True,
                now=start,
            )
            send(
                env.board,
                project="demo",
                sender="claude_code",
                recipient="cursor",
                subject="Re: Other",
                body="A normal note.",
                type="answer",
                re=asked,
                now=start,
            )
            self.assertTrue(nudge)
            header = render_check(check_inbox(env.board, "demo", "cursor", now=time.time() + 30))
            self.assertIn("claude_desktop asks for your attention: The correction is ready.", header)
            self.assertLess(header.index("asks for your attention"), header.index("Re: Other"))
            self.assertTrue(_has_mail_in(env.board, "cursor", "demo"))

            class Clock:
                def __init__(self) -> None:
                    self.now = datetime.now(timezone.utc)

                def __call__(self) -> datetime:
                    return self.now

                def advance(self, seconds: float) -> None:
                    self.now = self.now + timedelta(seconds=seconds)

            clock = Clock()
            out = io.StringIO()
            code = run_watch(
                env.board,
                "demo",
                "cursor",
                timeout_seconds=30,
                poll_seconds=5,
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
                out=out,
            )
            self.assertEqual(code, 0)
            self.assertIn("asks for your attention", out.getvalue())
            with self.assertRaises(CoopError) as again:
                send(
                    env.board,
                    project="demo",
                    sender="claude_desktop",
                    recipient="cursor",
                    subject="Attention",
                    body="Again.",
                    type="nudge",
                    now=start + timedelta(minutes=31),
                )
            self.assertIn("next nudge is allowed at", str(again.exception))
            send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_desktop",
                subject="Status",
                body="I saw it.",
                type="status",
                now=start + timedelta(minutes=1),
            )
            with self.assertRaises(CoopError) as too_soon:
                send(
                    env.board,
                    project="demo",
                    sender="claude_desktop",
                    recipient="cursor",
                    subject="Attention",
                    body="Too soon.",
                    type="nudge",
                    now=start + timedelta(minutes=10),
                )
            self.assertIn("next nudge is allowed at", str(too_soon.exception))
            later = send(
                env.board,
                project="demo",
                sender="claude_desktop",
                recipient="cursor",
                subject="Attention",
                body="Now it is time.",
                type="nudge",
                now=start + timedelta(minutes=31),
            )
            self.assertTrue(later)

    def test_outbox_nudge_file(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            path = outbox_dir(env.board, "demo", "claude_desktop") / "nudge.20261023T120000Z.md"
            path.write_text("Please look at the correction.\n", encoding="utf-8")
            _age(path)
            process_outbox(env.board, "demo", "claude_desktop")
            header = render_check(check_inbox(env.board, "demo", "cursor"))
            self.assertIn("asks for your attention: Please look at the correction.", header)
            sent = outbox_sent_dir(env.board, "demo", "claude_desktop")
            self.assertTrue(list(sent.glob("nudge*.posted.txt")))


if __name__ == "__main__":
    unittest.main()
