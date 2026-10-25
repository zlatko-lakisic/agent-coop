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
from agentcoop.cursorloop import (
    TICK_PREFIX,
    chat_is_watching,
    cursor_stop_followup,
    hook_fresh,
    idle_mode,
    leave_cursor_idle,
    maybe_notify,
    notify_command,
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
            self.assertIn("end your turn", result["followup_message"])
            self.assertIn("start cursor-loop again", result["followup_message"])
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
            self.assertEqual(result, {"followup_message": "End your turn."})
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
            self.assertNotEqual(result["followup_message"], "End your turn.")


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
            self.assertEqual(data["hooks"]["stop"][0]["timeout"], 600)
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
