"""Cursor loop, the stop hook, and what a background round does when the chat is idle."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.activity import load_activity, note_activity
from agentcoop.cursorloop import (
    CONTINUE,
    KEEP_AWAKE,
    MAIL_TAIL,
    TICK_PREFIX,
    chat_is_watching,
    cursor_stop_followup,
    describe_hook_timeout,
    hook_fresh,
    hook_timeout_seconds,
    idle_mode,
    leave_cursor_idle,
    maybe_notify,
    migrate_saved_hook_timeout,
    notify_command,
    refresh_cursor_hook_files,
    run_cursor_loop,
    status_idle_line,
)
from agentcoop.doctor import _agent_checks
from agentcoop.errors import CoopError
from agentcoop.progress import progress_dir
from agentcoop.projects import load_config, mutate_config
from agentcoop.rounds import tick
from agentcoop.statusfile import refresh_status, status_path
from agentcoop.watch import run_watch, set_project_watching, write_heartbeat


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return main(argv)


def _ready(env: support.TempEnv, *, gitignore: str = "no") -> Path:
    _quiet(["setup", "--connect", "cursor,claude-code", "--schedule", "no", "--yes"])
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
            "claude_code=reviews",
            "--gitignore",
            gitignore,
            "--yes",
        ]
    )
    return folder


def _mail(env: support.TempEnv, *, kind: str = "reply") -> None:
    from agentcoop.core import send

    if kind == "nudge":
        send(
            env.board,
            project="demo",
            sender="claude_code",
            recipient="cursor",
            subject="Attention",
            body="Please look at the helper note.",
            type="nudge",
        )
        return
    asked = send(
        env.board,
        project="demo",
        sender="cursor",
        recipient="claude_code",
        subject="Need a look",
        body="Please review this.",
    )
    send(
        env.board,
        project="demo",
        sender="claude_code",
        recipient="cursor",
        subject="Re: Need a look",
        body="The helper needs a look.",
        type="answer",
        re=asked,
    )


def _working(env: support.TempEnv, now: str = "Fix the wake") -> None:
    folder = progress_dir(env.board, "demo")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "20261023T120000Z.md").write_text(
        "Status: working\nNow: " + now + "\n",
        encoding="utf-8",
    )


class LoopTests(unittest.TestCase):
    def test_mail_and_nudge_tick_once_and_a_task_ticks_on_the_interval(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            _mail(env)
            _mail(env, kind="nudge")
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
            text = out.getvalue()
            self.assertEqual(text.count(TICK_PREFIX), 1)
            self.assertIn("Need a look", text)
            self.assertIn("Please look at the helper note", text)
            self.assertIn("AGENT_LOOP_TICK_board", text)
            self.assertIn("Handle these", text)
            from agentcoop.core import file_id, mark_done, new_messages

            waiting, _issues = new_messages(env.board, "demo", "cursor")
            mark_done(env.board, "demo", "cursor", [file_id(message) for message in waiting])

            out = io.StringIO()
            _working(env)
            code = run_cursor_loop(
                env.board,
                "demo",
                "cursor",
                now=clock,
                sleep=lambda seconds: clock.advance(2),
                out=out,
                max_passes=2,
            )
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue().count("continue your current task"), 1)
            self.assertIn("Fix the wake", out.getvalue())

    def test_missing_status_still_ticks_and_waiting_does_not(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            folder = progress_dir(env.board, "demo")
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "20261023T120000Z.md").write_text("Now: Run the game script.\n", encoding="utf-8")
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
            (folder / "20261023T120100Z.md").write_text(
                "Status: waiting\nNow: Need a decision on the restart.\n",
                encoding="utf-8",
            )
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
            self.assertNotIn("continue your current task", out.getvalue())

    def test_no_task_and_no_mail_prints_nothing(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()
            out = io.StringIO()
            code = run_cursor_loop(
                env.board,
                "demo",
                "cursor",
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
                out=out,
                max_passes=2,
            )
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), "")

    def test_stop_prints_one_plain_line(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            set_project_watching(env.board, "demo", False, "cursor")
            clock = _Clock()
            out = io.StringIO()
            code = run_cursor_loop(
                env.board,
                "demo",
                "cursor",
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
                out=out,
                max_passes=3,
            )
            self.assertEqual(code, 4)
            self.assertEqual(out.getvalue().strip(), "Checking is stopped. cursor-loop has exited.")
            self.assertNotIn(TICK_PREFIX, out.getvalue())

    def test_version_change_asks_for_a_restart(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()
            out = io.StringIO()
            values = iter(["1", "2"])

            def read(_board: Path) -> str:
                return next(values)

            with patch("agentcoop.version.read_installed", read):
                code = run_cursor_loop(
                    env.board,
                    "demo",
                    "cursor",
                    now=clock,
                    sleep=lambda seconds: clock.advance(seconds),
                    out=out,
                    max_passes=3,
                )
            self.assertEqual(code, 5)
            self.assertIn("Run agentcoop cursor-loop again", out.getvalue())

    def test_a_second_loop_and_a_second_watch_are_refused(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            moment = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
            write_heartbeat(env.board, "demo", "cursor", since=moment, seen=moment, how="cursor-loop")
            with self.assertRaises(CoopError) as caught:
                run_cursor_loop(
                    env.board,
                    "demo",
                    "cursor",
                    now=lambda: moment,
                    sleep=lambda _seconds: None,
                    out=io.StringIO(),
                    max_passes=1,
                )
            self.assertIn("second loop was not started", str(caught.exception))
            with self.assertRaises(CoopError) as watch:
                run_watch(
                    env.board,
                    "demo",
                    "cursor",
                    timeout_seconds=1,
                    now=lambda: moment,
                    sleep=lambda _seconds: None,
                    out=io.StringIO(),
                )
            self.assertIn("second watch was not started", str(watch.exception))
            stale = moment - timedelta(minutes=4)
            write_heartbeat(env.board, "demo", "cursor", since=stale, seen=stale, how="cursor-loop")
            out = io.StringIO()
            code = run_cursor_loop(
                env.board,
                "demo",
                "cursor",
                now=lambda: moment,
                sleep=lambda _seconds: None,
                out=out,
                max_passes=1,
            )
            self.assertEqual(code, 0)


class HookTests(unittest.TestCase):
    def test_mail_wakes_and_silence_returns_nothing(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            _mail(env)
            clock = _Clock()
            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps({"status": "completed", "loop_count": 0}),
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
            )
            self.assertIn("Need a look", result["followup_message"])
            self.assertIn(MAIL_TAIL, result["followup_message"])
            self.assertNotIn("running, and end your turn", result["followup_message"])
            self.assertNotIn("no messages", result["followup_message"].lower())
            self.assertTrue(hook_fresh(env.board, "demo", "cursor", now=clock()))

            aborted = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps({"status": "aborted"}),
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
            )
            self.assertEqual(aborted, {})

    def test_no_mail_waits_then_returns_empty(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()
            result = cursor_stop_followup(
                env.board,
                "cursor",
                "{}",
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
                timeout_seconds=6,
                reserve_seconds=5,
            )
            self.assertEqual(result, {"followup_message": KEEP_AWAKE})
            self.assertNotIn("no messages", result["followup_message"].lower())
            self.assertNotIn("Handle these", result["followup_message"])
            self.assertGreaterEqual((clock() - datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)).total_seconds(), 1)

    def test_stopped_agent_gets_nothing(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            _mail(env)
            from agentcoop.cursorloop import set_agent_watching

            set_agent_watching(env.board, "demo", "cursor", False)
            result = cursor_stop_followup(env.board, "cursor", "{}", now=_Clock(), sleep=lambda _seconds: None)
            self.assertEqual(result, {})
            self.assertFalse(hook_fresh(env.board, "demo", "cursor", now=_Clock()()))

    def test_mail_that_arrives_during_the_wait_is_sent(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()
            start = clock()
            sent = {"done": False}

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                if not sent["done"] and clock() >= start + timedelta(seconds=4):
                    _mail(env)
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
            self.assertIn("Need a look", result["followup_message"])
            self.assertIn(MAIL_TAIL, result["followup_message"])
            self.assertNotEqual(result["followup_message"], KEEP_AWAKE)

    def test_activity_during_the_wait_sends_nothing(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                note_activity(env.board, "cursor", "check", now=clock())

            result = cursor_stop_followup(
                env.board,
                "cursor",
                "{}",
                now=clock,
                sleep=nap,
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertEqual(result, {})

    def test_a_loop_heartbeat_does_not_block_the_follow_up(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                from agentcoop.watch import write_heartbeat

                write_heartbeat(
                    env.board,
                    "demo",
                    "cursor",
                    since=clock(),
                    seen=clock(),
                    how="cursor-loop",
                )

            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps({"status": "completed", "loop_count": 0}),
                now=clock,
                sleep=nap,
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertEqual(result, {"followup_message": KEEP_AWAKE})

    def test_an_open_task_gets_a_continue_follow_up(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            from agentcoop.progress import write_progress

            clock = _Clock()
            write_progress(
                env.board,
                "demo",
                "cursor",
                "Status: working\nNow: Phase D. No click yet.\n",
                now=clock(),
            )
            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps({"status": "completed", "loop_count": 0}),
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
                timeout_seconds=3600,
                reserve_seconds=5,
            )
            self.assertEqual(result, {"followup_message": CONTINUE})
            self.assertLess(
                (clock() - datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)).total_seconds(),
                10,
            )

    def test_a_newer_generation_sends_nothing(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()
            note_activity(
                env.board,
                "cursor",
                "prompt",
                now=clock(),
                generation_id="gen-2",
                conversation_id="conv-1",
            )
            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps(
                    {
                        "status": "completed",
                        "generation_id": "gen-1",
                        "conversation_id": "conv-1",
                        "loop_count": 0,
                    }
                ),
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertEqual(result, {})

    def test_a_command_during_an_open_task_sends_nothing(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            from agentcoop.progress import write_progress

            clock = _Clock()
            write_progress(
                env.board,
                "demo",
                "cursor",
                "Status: working\nNow: Phase D. No click yet.\n",
                now=clock(),
            )

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                note_activity(env.board, "cursor", "check", now=clock())

            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps({"status": "completed", "loop_count": 0}),
                now=clock,
                sleep=nap,
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertEqual(result, {})

    def test_a_new_transcript_user_line_sends_nothing(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            transcript = env.root / "transcript.jsonl"
            transcript.write_text('{"role":"assistant","message":{}}\n', encoding="utf-8")
            clock = _Clock()
            added = {"done": False}

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                if not added["done"]:
                    with transcript.open("a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {
                                    "role": "user",
                                    "message": {
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "<user_query>\nCheck the Agent Co-op board and continue your current task: Read the files.\n</user_query>",
                                            }
                                        ]
                                    },
                                }
                            )
                            + "\n"
                        )
                    added["done"] = True

            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps(
                    {
                        "status": "completed",
                        "generation_id": "gen-1",
                        "loop_count": 0,
                        "transcript_path": str(transcript),
                    }
                ),
                now=clock,
                sleep=nap,
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertEqual(result, {})

    def test_turn_ended_in_the_transcript_still_sends_the_keep_awake(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            transcript = env.root / "transcript.jsonl"
            transcript.write_text('{"role":"assistant","message":{}}\n', encoding="utf-8")
            clock = _Clock()

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                with transcript.open("a", encoding="utf-8") as handle:
                    handle.write('{"type":"turn_ended","status":"success"}\n')

            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps(
                    {
                        "status": "completed",
                        "generation_id": "gen-1",
                        "loop_count": 0,
                        "transcript_path": str(transcript),
                    }
                ),
                now=clock,
                sleep=nap,
                timeout_seconds=6,
                reserve_seconds=5,
            )
            self.assertEqual(result, {"followup_message": KEEP_AWAKE})

    def test_timestamp_lines_do_not_drop_mail_that_arrives_with_them(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            transcript = env.root / "transcript.jsonl"
            transcript.write_text('{"role":"assistant","message":{}}\n', encoding="utf-8")
            clock = _Clock()
            added = {"done": False}
            stamp = "<timestamp>Tuesday, Oct 27, 2026, 11:22 AM (UTC-4)</timestamp>"
            notice = (
                stamp
                + "\n\n<user_query>Briefly inform the user about the task result "
                + "and perform any follow-up actions (if needed). "
                + "If there's no follow-ups needed, don't explicitly say that.</user_query>"
            )

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                if added["done"]:
                    return
                with transcript.open("a", encoding="utf-8") as handle:
                    for text in (stamp, notice):
                        handle.write(
                            json.dumps(
                                {
                                    "role": "user",
                                    "message": {"content": [{"type": "text", "text": text}]},
                                }
                            )
                            + "\n"
                        )
                _mail(env, kind="nudge")
                added["done"] = True

            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps(
                    {
                        "status": "completed",
                        "generation_id": "gen-1",
                        "loop_count": 0,
                        "transcript_path": str(transcript),
                    }
                ),
                now=clock,
                sleep=nap,
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertIn("Please look at the helper note.", result["followup_message"])
            self.assertIn(MAIL_TAIL, result["followup_message"])

    def test_an_unknown_transcript_warns_and_still_wakes(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            transcript = env.root / "transcript.jsonl"
            transcript.write_text("this is not the cursor transcript\n", encoding="utf-8")
            clock = _Clock()
            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps(
                    {
                        "status": "completed",
                        "generation_id": "gen-1",
                        "loop_count": 0,
                        "cursor_version": "3.27.14",
                        "transcript_path": str(transcript),
                    }
                ),
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
                timeout_seconds=6,
                reserve_seconds=5,
            )
            self.assertEqual(result, {"followup_message": KEEP_AWAKE})
            trace = (env.board / "logs" / "trace.jsonl").read_text(encoding="utf-8")
            self.assertIn(
                "Cursor's transcript format is not recognized. A stale hook may interrupt work.",
                trace,
            )
            from agentcoop.doctor import format_doctor, run_doctor

            text = format_doctor(run_doctor(env.board))
            self.assertIn(
                "Cursor's transcript format is not recognized. A stale hook may interrupt work.",
                text,
            )
            warning = "Cursor's transcript format is not recognized. A stale hook may interrupt work."
            for label, payload in (
                ("missing", {"status": "completed", "generation_id": "gen-1", "loop_count": 0}),
                (
                    "unreadable",
                    {
                        "status": "completed",
                        "generation_id": "gen-1",
                        "loop_count": 0,
                        "transcript_path": str(env.root),
                    },
                ),
            ):
                state = env.board / ".state" / "cursor-transcript.json"
                trace_path = env.board / "logs" / "trace.jsonl"
                if state.is_file():
                    state.unlink()
                if trace_path.is_file():
                    trace_path.unlink()
                clock = _Clock()
                result = cursor_stop_followup(
                    env.board,
                    "cursor",
                    json.dumps(payload),
                    now=clock,
                    sleep=lambda seconds: clock.advance(seconds),
                    timeout_seconds=6,
                    reserve_seconds=5,
                )
                self.assertEqual(result, {"followup_message": KEEP_AWAKE}, label)
                self.assertIn(warning, trace_path.read_text(encoding="utf-8"), label)

    def test_a_large_transcript_reads_only_new_bytes(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            transcript = env.root / "transcript.jsonl"
            one = b'{"role":"assistant","message":{}}\n'
            filler = one * 40000
            self.assertGreater(len(filler), 1_000_000)
            transcript.write_bytes(filler)
            query = json.dumps(
                {
                    "role": "user",
                    "message": {
                        "content": [
                            {"type": "text", "text": "<user_query>\nKeep going.\n</user_query>"}
                        ]
                    },
                }
            ).encode("utf-8") + b"\n"
            clock = _Clock()
            added = {"done": False}
            reads: list[tuple[int, int]] = []
            prefixes: list[int] = []
            import agentcoop.cursorloop as loopmod

            real_added = loopmod._read_added
            real_prefix = loopmod._read_prefix

            def spy_added(path: str, start: int) -> bytes:
                data = real_added(path, start)
                if str(path).endswith("transcript.jsonl"):
                    reads.append((start, len(data)))
                return data

            def spy_prefix(path: str):
                kind, sample = real_prefix(path)
                if str(path).endswith("transcript.jsonl"):
                    prefixes.append(len(sample))
                return kind, sample

            original = Path.read_bytes

            def guarded(self, *args, **kwargs):
                if self.name.endswith("transcript.jsonl"):
                    raise AssertionError("read the whole transcript")
                return original(self, *args, **kwargs)

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                if added["done"]:
                    return
                with transcript.open("ab") as handle:
                    handle.write(query)
                added["done"] = True

            from contextlib import ExitStack

            with ExitStack() as stack:
                stack.enter_context(patch.object(loopmod, "_read_added", side_effect=spy_added))
                stack.enter_context(patch.object(loopmod, "_read_prefix", side_effect=spy_prefix))
                stack.enter_context(patch.object(Path, "read_bytes", guarded))
                result = cursor_stop_followup(
                    env.board,
                    "cursor",
                    json.dumps(
                        {
                            "status": "completed",
                            "generation_id": "gen-1",
                            "loop_count": 0,
                            "transcript_path": str(transcript),
                        }
                    ),
                    now=clock,
                    sleep=nap,
                    timeout_seconds=30,
                    reserve_seconds=5,
                )
            self.assertEqual(result, {})
            self.assertEqual(prefixes, [loopmod.FORMAT_SAMPLE])
            self.assertGreaterEqual(len(reads), 1)
            for start, size in reads:
                self.assertGreaterEqual(start, 1_000_000)
                self.assertLess(size, 5000)

    def test_overdue_mail_is_sent_even_when_the_transcript_looks_busy(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            attention = env.board / ".state" / "attention" / "cursor.json"
            if attention.is_file():
                attention.unlink()

            def set_every(data: dict) -> None:
                data["progress_every_minutes"] = 1

            mutate_config(env.board, set_every)
            transcript = env.root / "transcript.jsonl"
            transcript.write_text('{"role":"assistant","message":{}}\n', encoding="utf-8")
            clock = _Clock()
            added = {"done": False}

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                if added["done"]:
                    return
                with transcript.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "role": "user",
                                "message": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "<user_query>\nDo the long task.\n</user_query>",
                                        }
                                    ]
                                },
                            }
                        )
                        + "\n"
                    )
                from agentcoop.core import send

                send(
                    env.board,
                    project="demo",
                    sender="claude_code",
                    recipient="cursor",
                    subject="Attention",
                    body="Please look at the helper note.",
                    type="nudge",
                    now=clock() - timedelta(minutes=3),
                )
                from agentcoop.doctor import format_doctor, run_doctor
                from agentcoop.statusfile import render_status

                page = render_status(env.board, now=clock())
                self.assertTrue(
                    page.startswith("# Status\n\nCursor has not picked up mail for 3 min.\n")
                )
                self.assertIn(
                    "Cursor has not picked up mail for 3 min.",
                    format_doctor(run_doctor(env.board, now=clock())),
                )
                added["done"] = True

            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps(
                    {
                        "status": "completed",
                        "generation_id": "gen-1",
                        "loop_count": 0,
                        "transcript_path": str(transcript),
                    }
                ),
                now=clock,
                sleep=nap,
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertIn("Please look at the helper note.", result["followup_message"])
            trace = (env.board / "logs" / "trace.jsonl").read_text(encoding="utf-8")
            self.assertIn("Cursor has not picked up mail for 3 min.", trace)
            from agentcoop.statusfile import render_status

            # The follow-up counts as a pickup, so the line is checked before that.
            page = (env.board / ".state" / "cursor-mail.json").read_text(encoding="utf-8")
            self.assertIn("Cursor has not picked up mail for 3 min.", page)

    def test_a_tick_after_the_mail_lets_a_real_turn_stay_quiet(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            attention = env.board / ".state" / "attention" / "cursor.json"
            if attention.is_file():
                attention.unlink()

            def set_every(data: dict) -> None:
                data["progress_every_minutes"] = 1

            mutate_config(env.board, set_every)
            clock = _Clock()
            from agentcoop.activity import note_attention

            note_attention(env.board, "cursor", "tick", now=clock())
            quiet = env.root / "quiet.jsonl"
            quiet.write_text('{"role":"assistant","message":{}}\n', encoding="utf-8")
            marked = {"done": False}

            def nap_quiet(seconds: float) -> None:
                clock.advance(seconds)
                if marked["done"]:
                    return
                with quiet.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "role": "user",
                                "message": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "<user_query>\nStill working.\n</user_query>",
                                        }
                                    ]
                                },
                            }
                        )
                        + "\n"
                    )
                from agentcoop.core import send

                send(
                    env.board,
                    project="demo",
                    sender="claude_code",
                    recipient="cursor",
                    subject="Attention",
                    body="Still here.",
                    type="nudge",
                    now=clock() - timedelta(minutes=3),
                )
                marked["done"] = True

            skipped = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps(
                    {
                        "status": "completed",
                        "generation_id": "gen-2",
                        "loop_count": 0,
                        "transcript_path": str(quiet),
                    }
                ),
                now=clock,
                sleep=nap_quiet,
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertEqual(skipped, {})

    def test_older_activity_still_sends_the_keep_awake(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()
            note_activity(
                env.board,
                "cursor",
                "prompt",
                now=clock() - timedelta(seconds=30),
                generation_id="gen-1",
            )
            result = cursor_stop_followup(
                env.board,
                "cursor",
                json.dumps({"status": "completed", "generation_id": "gen-1", "loop_count": 0}),
                now=clock,
                sleep=lambda seconds: clock.advance(seconds),
                timeout_seconds=6,
                reserve_seconds=5,
            )
            self.assertEqual(result, {"followup_message": KEEP_AWAKE})

    def test_mail_does_not_wake_a_busy_agent(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()

            def nap(seconds: float) -> None:
                clock.advance(seconds)
                _mail(env)
                note_activity(env.board, "cursor", "check", now=clock())

            result = cursor_stop_followup(
                env.board,
                "cursor",
                "{}",
                now=clock,
                sleep=nap,
                timeout_seconds=30,
                reserve_seconds=5,
            )
            self.assertEqual(result, {})

    def test_a_check_is_activity_and_the_stop_hook_is_not(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            self.assertEqual(_quiet(["check", "--as", "cursor", "--project", "demo"]), 0)
            self.assertEqual(load_activity(env.board, "cursor")["source"], "check")
            moment = datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc)
            note_activity(env.board, "cursor", "check", now=moment)
            before = load_activity(env.board, "cursor")
            with patch("sys.stdin", io.StringIO('{"status": "aborted"}')):
                code = main(["hook", "cursor-stop", "--as", "cursor"])
            self.assertEqual(code, 0)
            self.assertEqual(load_activity(env.board, "cursor"), before)

    def test_prompt_hook_records_the_generation(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            out = io.StringIO()
            raw = json.dumps(
                {
                    "prompt": "do the long task",
                    "generation_id": "gen-9",
                    "conversation_id": "conv-9",
                }
            )
            with patch("sys.stdin", io.StringIO(raw)):
                with redirect_stdout(out):
                    code = main(["hook", "cursor-prompt", "--as", "cursor"])
            self.assertEqual(code, 0)
            self.assertIn('"continue": true', out.getvalue())
            data = load_activity(env.board, "cursor")
            self.assertEqual(data["source"], "prompt")
            self.assertEqual(data["generation_id"], "gen-9")
            self.assertEqual(data["conversation_id"], "conv-9")

    def test_missing_timeout_is_one_hour_and_600_moves_on_update(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            self.assertEqual(hook_timeout_seconds(env.board), 3600)
            self.assertIn("60 minutes", describe_hook_timeout(env.board))

            def save_600(data: dict) -> None:
                data["cursor_hook_timeout_seconds"] = 600

            mutate_config(env.board, save_600)
            migrate_saved_hook_timeout(env.board)
            self.assertEqual(load_config(env.board)["cursor_hook_timeout_seconds"], 3600)
            migrate_saved_hook_timeout(env.board)
            self.assertEqual(load_config(env.board)["cursor_hook_timeout_seconds"], 3600)

            def choose_600(data: dict) -> None:
                data["cursor_hook_timeout_seconds"] = 600
                data["cursor_hook_timeout_user_set"] = True

            mutate_config(env.board, choose_600)
            migrate_saved_hook_timeout(env.board)
            self.assertEqual(load_config(env.board)["cursor_hook_timeout_seconds"], 600)

            def save_900(data: dict) -> None:
                data["cursor_hook_timeout_seconds"] = 900
                data.pop("cursor_hook_timeout_user_set", None)

            mutate_config(env.board, save_900)
            migrate_saved_hook_timeout(env.board)
            self.assertEqual(load_config(env.board)["cursor_hook_timeout_seconds"], 900)

    def test_wake_wait_saves_a_chosen_value(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["wake-wait"])
            self.assertEqual(code, 0)
            self.assertIn("60 minutes", out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["wake-wait", "15"])
            self.assertEqual(code, 0)
            self.assertIn("15 minutes", out.getvalue())
            saved = load_config(env.board)
            self.assertEqual(saved["cursor_hook_timeout_seconds"], 900)
            self.assertIs(saved["cursor_hook_timeout_user_set"], True)
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(main(["wake-wait", "0"]), 2)
                self.assertEqual(main(["wake-wait", "61"]), 2)
            self.assertIn("1 to 60", err.getvalue())

    def test_refresh_updates_an_old_hook_file(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            hook = folder / ".cursor" / "hooks.json"
            data = json.loads(hook.read_text(encoding="utf-8"))
            data["hooks"]["stop"][0]["timeout"] = 600
            data["hooks"].pop("beforeSubmitPrompt", None)
            data["hooks"]["stop"].insert(0, {"command": "echo kept"})
            hook.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            refresh_cursor_hook_files(env.board)
            updated = json.loads(hook.read_text(encoding="utf-8"))
            commands = [entry.get("command") for entry in updated["hooks"]["stop"]]
            self.assertIn("echo kept", commands)
            ours = [entry for entry in updated["hooks"]["stop"] if "cursor-stop" in str(entry.get("command"))]
            self.assertEqual(ours[0]["timeout"], 3600)
            self.assertIsNone(ours[0]["loop_limit"])
            self.assertIn("cursor-prompt", updated["hooks"]["beforeSubmitPrompt"][0]["command"])
            text = hook.read_text(encoding="utf-8")
            refresh_cursor_hook_files(env.board)
            self.assertEqual(hook.read_text(encoding="utf-8"), text)


class IdleTests(unittest.TestCase):
    def test_cursor_waits_and_other_tools_still_run(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            self.assertEqual(idle_mode(env.board, "cursor"), "wait_for_chat")
            self.assertEqual(idle_mode(env.board, "claude_code"), "background_run")
            self.assertTrue(leave_cursor_idle(env.board, "demo", "cursor", "driver"))
            self.assertFalse(leave_cursor_idle(env.board, "demo", "claude_code", "helper"))
            _mail(env)
            _quiet(["idle", "background"])
            self.assertEqual(load_config(env.board)["driver_when_chat_idle"], "background_run")
            self.assertFalse(leave_cursor_idle(env.board, "demo", "cursor", "driver"))
            _quiet(["idle", "wait"])
            self.assertTrue(leave_cursor_idle(env.board, "demo", "cursor", "driver"))
            line = status_idle_line(env.board, "demo", "cursor")
            self.assertIn("cursor is not watching", line)
            self.assertIn("Start working", line)
            refresh_status(env.board)
            self.assertIn("cursor is not watching", status_path(env.board).read_text(encoding="utf-8"))

            moment = datetime.now(timezone.utc)
            write_heartbeat(env.board, "demo", "cursor", since=moment, seen=moment, how="cursor-loop")
            self.assertTrue(chat_is_watching(env.board, "demo", "cursor"))
            self.assertEqual(status_idle_line(env.board, "demo", "cursor"), "")

            started: list[list[str]] = []
            import agentcoop.rounds as rounds

            original = rounds._start_detached
            rounds._start_detached = lambda board, args: started.append(list(args))
            try:
                self.assertEqual(tick(env.board), 0)
            finally:
                rounds._start_detached = original
            self.assertEqual(started, [])

    def test_notification_stays_off(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            calls: list[list[str]] = []

            def runner(command, **_kwargs):
                calls.append(command)

            maybe_notify(env.board, "cursor is not watching", runner=runner)
            self.assertEqual(calls, [])
            self.assertIsNotNone(notify_command("hello"))

            def turn_on(data: dict) -> None:
                data["notify_when_idle"] = True

            mutate_config(env.board, turn_on)
            maybe_notify(env.board, "cursor is not watching", runner=runner)
            self.assertEqual(len(calls), 1)


class InstallTests(unittest.TestCase):
    def test_project_hook_merges_and_is_ignored(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env, gitignore="yes")
            hook = folder / ".cursor" / "hooks.json"
            self.assertTrue(hook.is_file())
            data = json.loads(hook.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            command = data["hooks"]["stop"][0]["command"]
            self.assertIn("hook cursor-stop --as cursor", command)
            self.assertEqual(data["hooks"]["stop"][0]["timeout"], 3600)
            self.assertIn(
                "hook cursor-prompt --as cursor",
                data["hooks"]["beforeSubmitPrompt"][0]["command"],
            )
            self.assertIsNone(data["hooks"]["stop"][0]["loop_limit"])
            ignore = (folder / ".gitignore").read_text(encoding="utf-8")
            self.assertIn(".cursor/hooks.json", ignore)
            items = _agent_checks("cursor", {"display": "Cursor", "tool": "cursor", "installed": []})
            self.assertFalse(any("hooks are not installed" in item.text for item in items))

    def test_an_existing_hook_is_kept(self) -> None:
        with support.TempEnv() as env:
            _quiet(["setup", "--connect", "cursor", "--schedule", "no", "--yes"])
            folder = env.workspace / "kept"
            folder.mkdir()
            hooks = folder / ".cursor"
            hooks.mkdir()
            (hooks / "hooks.json").write_text(
                json.dumps({"version": 1, "hooks": {"stop": [{"command": "echo kept"}]}}) + "\n",
                encoding="utf-8",
            )
            _quiet(
                [
                    "project",
                    "add",
                    str(folder),
                    "--name",
                    "kept",
                    "--driver",
                    "cursor",
                    "--gitignore",
                    "no",
                    "--yes",
                ]
            )
            data = json.loads((hooks / "hooks.json").read_text(encoding="utf-8"))
            commands = [entry.get("command") for entry in data["hooks"]["stop"]]
            self.assertIn("echo kept", commands)
            self.assertTrue(any("hook cursor-stop" in (item or "") for item in commands))

    def test_update_adds_a_missing_hook_and_leaves_an_edited_rule(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env, gitignore="yes")
            hook = folder / ".cursor" / "hooks.json"
            rule = folder / ".cursor" / "rules" / "agent-coop.mdc"
            hook.unlink()
            rule.write_text("edited by the person\n", encoding="utf-8")
            from agentcoop.install import install_project_files
            from agentcoop.projects import mutate_projects

            def change(data: dict) -> None:
                installed = data["projects"]["demo"].get("installed") or []
                data["projects"]["demo"]["installed"] = [
                    entry for entry in installed if not str(entry.get("path", "")).endswith("hooks.json")
                ]

            mutate_projects(env.board, change)
            install_project_files(
                env.board,
                "demo",
                "cursor",
                yes=True,
                gitignore=True,
                only_missing=True,
            )
            self.assertTrue(hook.is_file())
            self.assertIn("hook cursor-stop", hook.read_text(encoding="utf-8"))
            self.assertEqual(rule.read_text(encoding="utf-8"), "edited by the person\n")
            self.assertIn(".cursor/hooks.json", (folder / ".gitignore").read_text(encoding="utf-8"))
