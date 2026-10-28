"""Stall lines, outbox posting from cursor-loop, and clearing rejected answers."""

from __future__ import annotations

import io
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone

import support

from agentcoop.cli import main
from agentcoop.core import new_messages
from agentcoop.cursorloop import run_cursor_loop
from agentcoop.outbox import clear_rejected, write_new_md
from agentcoop.paths import ensure_outbox_dirs, inbox_new_md, outbox_dir
from agentcoop.progress import stall_line, write_progress
from agentcoop.projects import connect_agent_record, init_board_files
from agentcoop.rounds import schedule_path
from agentcoop.statusfile import last_round_line, render_status


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 28, 0, 40, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _ready(env: support.TempEnv) -> None:
    assert main(["setup", "--connect", "cursor,claude-desktop", "--schedule", "no", "--yes"]) == 0
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
                "claude_desktop=helps",
                "--gitignore",
                "no",
                "--yes",
            ]
        )
        == 0
    )


class StallTests(unittest.TestCase):
    def test_cursor_loop_posts_a_helper_nudge(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            from agentcoop.paths import ensure_outbox_dirs

            ensure_outbox_dirs(env.board, "demo", "claude_desktop")
            box = outbox_dir(env.board, "demo", "claude_desktop")
            path = box / "nudge.20261028T003122Z.md"
            path.write_text("Phase D stopped after the first frame.\n", encoding="utf-8")
            when = time.time() - 30
            os.utime(path, (when, when))
            code = run_cursor_loop(
                env.board,
                "demo",
                "cursor",
                sleep=lambda _seconds: None,
                out=io.StringIO(),
                max_passes=1,
            )
            self.assertEqual(code, 0)
            messages, _issues = new_messages(env.board, "demo", "cursor")
            self.assertTrue(any(item.type == "nudge" for item in messages))

    def test_an_ended_turn_posts_mail_and_does_not_tick(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            from agentcoop.activity import note_turn

            write_progress(
                env.board,
                "demo",
                "cursor",
                "Status: working\nNow: Phase D. No click yet.\n",
            )
            note_turn(env.board, "cursor", "ended", "hook")
            ensure_outbox_dirs(env.board, "demo", "claude_desktop")
            box = outbox_dir(env.board, "demo", "claude_desktop")
            path = box / "nudge.20261028T003122Z.md"
            path.write_text("Phase D stopped after the first frame.\n", encoding="utf-8")
            when = time.time() - 30
            os.utime(path, (when, when))
            out = io.StringIO()
            code = run_cursor_loop(
                env.board,
                "demo",
                "cursor",
                sleep=lambda _seconds: None,
                out=out,
                max_passes=1,
            )
            self.assertEqual(code, 0)
            self.assertNotIn("continue your current task", out.getvalue())
            messages, _issues = new_messages(env.board, "demo", "cursor")
            self.assertTrue(any(item.type == "nudge" for item in messages))

    def test_a_heading_progress_file_still_wakes_the_task(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            write_progress(
                env.board,
                "demo",
                "cursor",
                "# Progress: demo\n\n## Working on now\nPhase D. No click yet.\n",
            )
            clock = _Clock()
            out = io.StringIO()
            code = run_cursor_loop(
                env.board,
                "demo",
                "cursor",
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
                out=out,
                max_passes=1,
            )
            self.assertEqual(code, 0)
            self.assertIn("continue your current task", out.getvalue())
            self.assertIn("Phase D", out.getvalue())

    def test_old_working_progress_says_the_turn_ended(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            name = write_progress(
                env.board,
                "demo",
                "cursor",
                "Status: working\nNow: Phase D. No click yet.\n",
            )
            path = env.board / "projects" / "demo" / "progress" / name
            old = time.time() - 15 * 60
            os.utime(path, (old, old))
            from agentcoop.activity import note_turn

            note_turn(env.board, "cursor", "ended", "hook")
            text = render_status(env.board)
            self.assertIn("idle, turn ended", text)
            self.assertIn("Phase D", text)
            note_turn(env.board, "cursor", "running", "prompt")
            fresh = render_status(env.board)
            self.assertNotIn("idle, turn ended", fresh)
            self.assertIn("working", fresh)

    def test_new_md_names_a_quiet_driver(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            name = write_progress(
                env.board,
                "demo",
                "cursor",
                "Status: working\nNow: Phase D. No click yet.\n",
            )
            path = env.board / "projects" / "demo" / "progress" / name
            old = time.time() - 24 * 60
            os.utime(path, (old, old))
            write_new_md(env.board, "demo", "claude_desktop")
            text = inbox_new_md(env.board, "demo", "claude_desktop").read_text(encoding="utf-8")
            self.assertIn("No new messages.", text)
            self.assertIn("Driver progress unchanged for", text)
            self.assertIn("min during a task.", text)
            self.assertIn(stall_line(env.board, "demo"), text)

    def test_a_late_round_is_named_on_the_status_page(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor")
            schedule_path(env.board).parent.mkdir(parents=True, exist_ok=True)
            schedule_path(env.board).write_text(
                json.dumps({"interval": 5, "files": []}) + "\n",
                encoding="utf-8",
            )
            old = (datetime.now(timezone.utc) - timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
            (env.board / ".state" / "last-tick").write_text(old + "\n", encoding="utf-8")
            self.assertIn("background round is late", last_round_line(env.board))
            self.assertIn("agentcoop start", render_status(env.board))

    def test_clearing_rejected_answers_drops_them_from_new_md(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            from agentcoop.paths import outbox_rejected_dir

            rejected = outbox_rejected_dir(env.board, "demo", "claude_desktop")
            rejected.mkdir(parents=True, exist_ok=True)
            (rejected / "old.md").write_text("stale\n", encoding="utf-8")
            (rejected / "old.md.reason.txt").write_text("Too old.\n", encoding="utf-8")
            write_new_md(env.board, "demo", "claude_desktop")
            before = inbox_new_md(env.board, "demo", "claude_desktop").read_text(encoding="utf-8")
            self.assertIn("old.md", before)
            self.assertEqual(
                main(["rejected", "clear", "--as", "claude_desktop", "--project", "demo"]),
                0,
            )
            after = inbox_new_md(env.board, "demo", "claude_desktop").read_text(encoding="utf-8")
            self.assertNotIn("old.md", after)
            self.assertEqual(clear_rejected(env.board, "demo", "claude_desktop"), 0)


if __name__ == "__main__":
    unittest.main()
