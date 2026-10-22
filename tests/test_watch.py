"""Watch, the stop flag, heartbeats, and chat summary lines."""

from __future__ import annotations

import io
import json
import os
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.core import render_check, send
from agentcoop.projects import load_agents, save_agents
from agentcoop.requests import process_project_requests
from agentcoop.rounds import run_agent, tick
from agentcoop.statusfile import refresh_status, status_path
from agentcoop.watch import (
    HANDLE_LINE,
    TIMEOUT_LINE,
    checking_enabled,
    run_watch,
    set_project_watching,
    stopped_line,
    watcher_path,
    write_heartbeat,
)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class _Completed:
    returncode = 0


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return main(argv)


def _ready(env: support.TempEnv, *, desktop: bool = False) -> Path:
    tools = "cursor,claude-code"
    if desktop:
        tools += ",claude-desktop"
    _quiet(["setup", "--connect", tools, "--schedule", "no", "--yes"])
    folder = env.workspace / "demo"
    folder.mkdir()
    other = env.workspace / "other"
    other.mkdir()
    helpers = ["claude_code=reviews"]
    if desktop:
        helpers.append("claude_desktop=helps")
    for name, path in (("demo", folder), ("other", other)):
        argv = [
            "project",
            "add",
            str(path),
            "--name",
            name,
            "--driver",
            "cursor",
            "--gitignore",
            "no",
            "--yes",
        ]
        for helper in helpers:
            argv.extend(["--helper", helper])
        _quiet(argv)
    return folder


def _point_cli(env: support.TempEnv) -> None:
    cli = env.workspace / "claude"
    cli.write_text("x", encoding="utf-8")
    agents = load_agents(env.board)
    agents["agents"]["claude_code"]["cli_path"] = str(cli)
    save_agents(env.board, agents)


def _mail(env: support.TempEnv) -> None:
    _quiet(
        [
            "send",
            "--project",
            "demo",
            "--as",
            "cursor",
            "--to",
            "claude_code",
            "--subject",
            "Ping",
            "--body",
            "Hello from the driver.",
        ]
    )


class WatchTests(unittest.TestCase):
    def test_timeout_writes_a_heartbeat_and_removes_it(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()
            seen: list[str] = []
            path = watcher_path(env.board, "demo", "claude_code")

            def pause(seconds: float) -> None:
                seen.append(path.read_text(encoding="utf-8"))
                clock.advance(seconds)

            out = io.StringIO()
            code = run_watch(
                env.board,
                "demo",
                "claude_code",
                timeout_seconds=1,
                now=clock,
                sleep=pause,
                out=out,
            )
            self.assertEqual(code, 3)
            self.assertIn(TIMEOUT_LINE, out.getvalue())
            self.assertTrue(seen)
            beat = json.loads(seen[0])
            self.assertEqual(beat["agent"], "claude_code")
            self.assertEqual(beat["how"], "watch")
            self.assertEqual(beat["since"], beat["last_seen"])
            self.assertFalse(path.exists())

    def test_mail_during_the_wait_returns_zero(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()

            def pause(seconds: float) -> None:
                clock.advance(seconds)
                send(
                    env.board,
                    project="demo",
                    sender="cursor",
                    recipient="claude_code",
                    subject="Ping",
                    body="Hello from the driver. Then more.",
                )

            out = io.StringIO()
            code = run_watch(
                env.board,
                "demo",
                "claude_code",
                timeout_seconds=60,
                now=clock,
                sleep=pause,
                out=out,
            )
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("Read from cursor: Ping. Hello from the driver.", text)
            self.assertIn(HANDLE_LINE, text)
            self.assertNotIn("Then more.", text.split("Read from cursor: Ping.")[1].split("\n")[0])
            self.assertFalse(watcher_path(env.board, "demo", "claude_code").exists())

    def test_stop_flag_during_the_wait_returns_four(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()

            def pause(seconds: float) -> None:
                clock.advance(seconds)
                set_project_watching(env.board, "demo", False, "cursor")

            out = io.StringIO()
            code = run_watch(
                env.board,
                "demo",
                "claude_code",
                timeout_seconds=60,
                now=clock,
                sleep=pause,
                out=out,
            )
            self.assertEqual(code, 4)
            self.assertIn(stopped_line("demo"), out.getvalue())
            self.assertFalse(watcher_path(env.board, "demo", "claude_code").exists())

    def test_heartbeat_is_updated(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            clock = _Clock()
            path = watcher_path(env.board, "demo", "claude_code")
            seen: list[str] = []

            def pause(seconds: float) -> None:
                beat = json.loads(path.read_text(encoding="utf-8"))
                seen.append(beat["last_seen"])
                clock.advance(seconds)
                if len(set(seen)) >= 2:
                    send(
                        env.board,
                        project="demo",
                        sender="cursor",
                        recipient="claude_code",
                        subject="Done",
                        body="The driver asked.",
                    )

            out = io.StringIO()
            code = run_watch(
                env.board,
                "demo",
                "claude_code",
                timeout_seconds=120,
                poll_seconds=5,
                heartbeat_seconds=10,
                now=clock,
                sleep=pause,
                out=out,
            )
            self.assertEqual(code, 0)
            self.assertGreaterEqual(len(set(seen)), 2)
            self.assertIn("Read from cursor: Done. The driver asked.", out.getvalue())
            self.assertFalse(path.exists())

    def test_watch_stop_and_start_and_the_schedule(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(["watch", "stop", "--as", "cursor", "--project", "demo"]), 0)
            self.assertIn("Sent to the project: Stop checking. Checking is stopped for demo.", out.getvalue())
            flag = json.loads((env.board / "projects" / "demo" / "watch.json").read_text(encoding="utf-8"))
            self.assertIs(flag["watching"], False)
            self.assertEqual(flag["by"], "cursor")
            self.assertTrue(flag["ts"])
            self.assertFalse((env.board / "projects" / "other" / "watch.json").exists())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["watch", "start", "--as", "claude_code", "--project", "demo"]), 0)
            self.assertFalse((env.board / "projects" / "demo" / "watch.json").exists())
            self.assertTrue(checking_enabled(env.board, "demo"))
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(main(["watch", "stop", "--as", "nope", "--project", "demo"]), 1)
            self.assertIn("not a member", err.getvalue())
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["watch", "--as", "cursor", "--project", "demo", "--timeout", "0"]), 2)
                self.assertEqual(main(["watch", "--as", "cursor", "--project", "demo", "--timeout", "31"]), 2)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["stop"]), 0)
            for name in ("demo", "other"):
                data = json.loads((env.board / "projects" / name / "watch.json").read_text(encoding="utf-8"))
                self.assertIs(data["watching"], False)
                self.assertEqual(data["by"], "person")
            with patch("agentcoop.rounds.sys.platform", "darwin"), patch(
                "agentcoop.rounds.subprocess.run", return_value=_Completed()
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(main(["start"]), 0)
            for name in ("demo", "other"):
                self.assertFalse((env.board / "projects" / name / "watch.json").exists())
                self.assertTrue(checking_enabled(env.board, name))

    def test_desktop_requests_are_applied(self) -> None:
        with support.TempEnv() as env:
            _ready(env, desktop=True)
            box = env.board / "projects" / "demo" / "requests" / "claude_desktop"
            box.mkdir(parents=True)
            young = box / "young.json"
            young.write_text('{"action": "stop_watching"}\n', encoding="utf-8")
            process_project_requests(env.board, "demo", now=datetime.now(timezone.utc))
            self.assertTrue(young.is_file())
            self.assertTrue(checking_enabled(env.board, "demo"))
            stop = box / "stop.json"
            stop.write_text('{"action": "stop_watching"}\n', encoding="utf-8")
            old = time.time() - 10
            os.utime(stop, (old, old))
            process_project_requests(env.board, "demo", now=datetime.now(timezone.utc) + timedelta(seconds=30))
            self.assertFalse(checking_enabled(env.board, "demo"))
            self.assertTrue((box / "done" / "stop.json").is_file())
            self.assertIn("stopped", (box / "done" / "stop.json.result.txt").read_text(encoding="utf-8"))
            start = box / "start.json"
            start.write_text('{"action": "start_watching"}\n', encoding="utf-8")
            os.utime(start, (old, old))
            process_project_requests(env.board, "demo", now=datetime.now(timezone.utc) + timedelta(seconds=30))
            self.assertTrue(checking_enabled(env.board, "demo"))
            self.assertTrue((box / "done" / "start.json").is_file())
            stranger = env.board / "projects" / "demo" / "requests" / "nope"
            stranger.mkdir(parents=True)
            bad = stranger / "bad.json"
            bad.write_text('{"action": "stop_watching"}\n', encoding="utf-8")
            os.utime(bad, (old, old))
            process_project_requests(env.board, "demo", now=datetime.now(timezone.utc) + timedelta(seconds=30))
            self.assertTrue((stranger / "rejected" / "bad.json").is_file())
            self.assertIn("not a member", (stranger / "rejected" / "bad.json.reason.txt").read_text(encoding="utf-8"))

    def test_status_shows_checking_and_the_three_minute_rule(self) -> None:
        with support.TempEnv() as env:
            _ready(env, desktop=True)
            refresh_status(env.board)
            text = status_path(env.board).read_text(encoding="utf-8")
            self.assertIn("Checking: on.", text)
            self.assertIn("cursor: not watching.", text)
            self.assertIn("claude_code: not watching.", text)
            self.assertIn("claude_desktop: not watching.", text)
            write_heartbeat(
                env.board,
                "demo",
                "cursor",
                since=datetime.now(timezone.utc),
                seen=datetime.now(timezone.utc),
            )
            fresh = status_path(env.board).read_text(encoding="utf-8")
            self.assertIn("cursor: watching, last check just now.", fresh)
            old = (datetime.now(timezone.utc) - timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
            watcher_path(env.board, "demo", "cursor").write_text(
                json.dumps({"agent": "cursor", "since": old, "last_seen": old, "how": "watch"}) + "\n",
                encoding="utf-8",
            )
            refresh_status(env.board)
            stale = status_path(env.board).read_text(encoding="utf-8")
            self.assertIn("cursor: not watching.", stale)
            request = env.board / "projects" / "demo" / "requests" / "claude_desktop" / "ask.json"
            request.parent.mkdir(parents=True, exist_ok=True)
            request.write_text("{}\n", encoding="utf-8")
            os.utime(request, (time.time() - 150, time.time() - 150))
            refresh_status(env.board)
            desktop = status_path(env.board).read_text(encoding="utf-8")
            self.assertIn("claude_desktop: watching, last check 2 minutes ago.", desktop)
            os.utime(request, (time.time() - 240, time.time() - 240))
            refresh_status(env.board)
            self.assertIn(
                "claude_desktop: not watching.",
                status_path(env.board).read_text(encoding="utf-8"),
            )
            set_project_watching(env.board, "demo", False, "cursor")
            stopped = status_path(env.board).read_text(encoding="utf-8")
            self.assertIn("Checking: stopped.", stopped)
            self.assertIn("Checking: on.", status_path(env.board).read_text(encoding="utf-8").split("## other")[1])

    def test_round_skips_a_fresh_heartbeat_and_runs_a_stale_one(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            _point_cli(env)
            _mail(env)
            write_heartbeat(
                env.board,
                "demo",
                "claude_code",
                since=datetime.now(timezone.utc),
                seen=datetime.now(timezone.utc),
            )
            started: list[list[str]] = []
            import agentcoop.rounds as rounds

            original = rounds._start_detached
            rounds._start_detached = lambda board, args: started.append(list(args))
            try:
                self.assertEqual(tick(env.board), 0)
            finally:
                rounds._start_detached = original
            self.assertEqual(started, [])
            log = (env.board / "logs" / "schedule" / "round.log").read_text(encoding="utf-8")
            self.assertIn("skip claude_code in demo: heartbeat is fresh", log)

            def boom(*_args, **_kwargs):
                raise AssertionError("the round should not run")

            self.assertEqual(run_agent(env.board, "claude_code", "demo", popen=boom), 0)
            old = (datetime.now(timezone.utc) - timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
            watcher_path(env.board, "demo", "claude_code").write_text(
                json.dumps({"agent": "claude_code", "since": old, "last_seen": old, "how": "watch"}) + "\n",
                encoding="utf-8",
            )
            rounds._start_detached = lambda board, args: started.append(list(args))
            try:
                self.assertEqual(tick(env.board), 0)
            finally:
                rounds._start_detached = original
            self.assertTrue(any("claude_code" in part for part in started[0]))

            set_project_watching(env.board, "demo", False, "person")
            started.clear()
            rounds._start_detached = lambda board, args: started.append(list(args))
            try:
                self.assertEqual(tick(env.board), 0)
            finally:
                rounds._start_detached = original
            self.assertEqual(started, [])
            later = (env.board / "logs" / "schedule" / "round.log").read_text(encoding="utf-8")
            self.assertIn("skip claude_code in demo: checking is stopped", later)

    def test_summary_lines(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            err = io.StringIO()
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(
                    main(
                        [
                            "send",
                            "--as",
                            "cursor",
                            "--project",
                            "demo",
                            "--to",
                            "claude_code",
                            "--subject",
                            "Hello",
                            "--body",
                            "Say hello. Ignore the rest.",
                        ]
                    ),
                    0,
                )
            self.assertNotIn("\n", out.getvalue().strip())
            self.assertIn("Sent to claude_code: Hello. Say hello.", err.getvalue())
            previous = os.getcwd()
            os.chdir(env.workspace / "demo")
            try:
                shown = io.StringIO()
                with redirect_stdout(shown):
                    self.assertEqual(main(["check", "--as", "claude_code", "--project", "demo"]), 0)
            finally:
                os.chdir(previous)
            self.assertIn("Read from cursor: Hello. Say hello.", shown.getvalue())
            from agentcoop.core import check_inbox

            report = check_inbox(env.board, "demo", "claude_code")
            self.assertIn("Read from cursor: Hello. Say hello.", render_check(report))
            err = io.StringIO()
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(
                    main(
                        [
                            "context",
                            "write",
                            "--as",
                            "cursor",
                            "--project",
                            "demo",
                            "--body",
                            "The goal is a demo.",
                        ]
                    ),
                    0,
                )
            self.assertTrue(out.getvalue().strip().endswith(".md") or "T" in out.getvalue())
            self.assertNotIn("Sent to", out.getvalue())
            self.assertIn("Sent to helpers: Context. The goal is a demo.", err.getvalue())
            err = io.StringIO()
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(
                    main(
                        [
                            "progress",
                            "write",
                            "--as",
                            "cursor",
                            "--project",
                            "demo",
                            "--body",
                            "## Working on now\nThe menu.\n## Blocked or need help\nNone\n",
                        ]
                    ),
                    0,
                )
            self.assertNotIn("Sent to", out.getvalue())
            self.assertIn("Sent to the project: Progress. The menu.", err.getvalue())


if __name__ == "__main__":
    unittest.main()
