"""Progress fields and helper progress documents."""

from __future__ import annotations

import io
import os
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.core import check_inbox, render_check, send
from agentcoop.errors import CoopError
from agentcoop.outbox import process_outbox
from agentcoop.paths import outbox_dir
from agentcoop.progress import wait_for_reply
from agentcoop.progressmeta import format_fields, parse_fields, parse_percent, parse_status, parse_step
from agentcoop.projects import add_project, connect_agent_record, init_board_files, load_config, save_config
from agentcoop.rounds import _has_mail_in
from agentcoop.watch import TIMEOUT_LINE, run_watch


class FieldTests(unittest.TestCase):
    def test_valid_and_invalid_values(self) -> None:
        self.assertEqual(parse_status("working"), "working")
        self.assertEqual(parse_percent("0"), 0)
        self.assertEqual(parse_percent("100"), 100)
        self.assertEqual(parse_step("3 of 7"), "3 of 7")
        for bad in ("120", "-1", "about half", ""):
            with self.assertRaises(CoopError):
                parse_percent(bad)
        with self.assertRaises(CoopError):
            parse_step("3/7")
        with self.assertRaises(CoopError):
            parse_status("busy")
        text = format_fields(status="working", step="3 of 7", percent="43", now="Reading the logs.", for_id="abc", rest="## Done so far\nOne.")
        fields = parse_fields(text)
        self.assertEqual(fields["Status"], "working")
        self.assertEqual(fields["Step"], "3 of 7")
        self.assertEqual(fields["Percent"], "43")
        self.assertEqual(fields["Now"], "Reading the logs.")
        self.assertEqual(fields["For"], "abc")
        self.assertIn("Done so far", fields["rest"])


class HelperProgressTests(unittest.TestCase):
    def test_only_an_open_driver_request(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor")
            connect_agent_record(env.board, "claude_code", "claude-code")
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {"claude_code": "reviews"})
            sent = io.StringIO()
            with redirect_stdout(sent), redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--project",
                        "demo",
                        "--to",
                        "claude_code",
                        "--subject",
                        "Look",
                        "--body",
                        "Please look.",
                        "--needs-reply",
                    ]
                )
            self.assertEqual(code, 0)
            message_id = ""
            for line in sent.getvalue().splitlines():
                if line.strip():
                    message_id = line.split()[0]
            self.assertTrue(message_id)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "progress",
                        "write",
                        "--as",
                        "claude_code",
                        "--project",
                        "demo",
                        "--for",
                        message_id,
                        "--percent",
                        "50",
                        "--step",
                        "2 of 4",
                        "--now",
                        "Reading the training logs.",
                        "--body",
                        "Done so far: one file.",
                    ]
                )
            self.assertEqual(code, 0, out.getvalue())
            folder_path = env.board / "projects" / "demo" / "progress" / "helpers" / "claude_code"
            files = list(folder_path.glob("*.md"))
            self.assertEqual(len(files), 1)
            body = files[0].read_text(encoding="utf-8")
            self.assertIn("Percent: 50", body)
            self.assertIn("For: " + message_id, body)
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = main(
                    [
                        "progress",
                        "write",
                        "--as",
                        "claude_code",
                        "--project",
                        "demo",
                        "--for",
                        "not-a-message",
                        "--body",
                        "no",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("not an open request", err.getvalue())
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = main(
                    [
                        "progress",
                        "write",
                        "--as",
                        "claude_code",
                        "--project",
                        "demo",
                        "--for",
                        message_id,
                        "--percent",
                        "120",
                        "--body",
                        "no",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("Percent", err.getvalue())


class DesktopProgressTests(unittest.TestCase):
    def test_progress_file_is_not_the_answer(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor")
            connect_agent_record(env.board, "claude_desktop", "claude-desktop")
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {"claude_desktop": "writes"})
            mid = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_desktop",
                subject="Train",
                body="Please train.",
                type="question",
                needs_reply=True,
            )
            box = outbox_dir(env.board, "demo", "claude_desktop")
            progress = box / (mid + ".progress.md")
            progress.write_text(
                "Status: working\nStep: 2 of 4\nPercent: 50\nNow: Reading the training logs.\nFor: "
                + mid
                + "\n\nDone so far: logs.\n",
                encoding="utf-8",
            )
            answer = box / (mid + ".md")
            answer.write_text("The run finished.\n", encoding="utf-8")
            when = time.time() - 30
            os.utime(progress, (when, when))
            os.utime(answer, (when, when))
            process_outbox(env.board, "demo", "claude_desktop")
            saved = list((env.board / "projects" / "demo" / "progress" / "helpers" / "claude_desktop").glob("*.md"))
            self.assertEqual(len(saved), 1)
            self.assertIn("Percent: 50", saved[0].read_text(encoding="utf-8"))
            self.assertFalse(progress.exists())
            report = check_inbox(env.board, "demo", "cursor")
            bodies = [item.message.body for item in report.messages]
            self.assertEqual([body.strip() for body in bodies], ["The run finished."])
            self.assertFalse(_has_mail_in(env.board, "cursor", "demo") and any("training logs" in body for body in bodies))
            text = render_check(check_inbox(env.board, "demo", "cursor"))
            self.assertIn("1 new message", text)
            self.assertNotIn("2 new message", text)


class WakeTests(unittest.TestCase):
    def test_progress_does_not_wake_watch_or_the_round(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor")
            connect_agent_record(env.board, "claude_code", "claude-code")
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {"claude_code": "reviews"})
            mid = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="Look",
                body="Please look.",
                needs_reply=True,
            )
            from agentcoop.core import mark_done

            mark_done(env.board, "demo", "cursor", [])
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(
                [
                    "progress",
                    "write",
                    "--as",
                    "claude_code",
                    "--project",
                    "demo",
                    "--for",
                    mid,
                    "--percent",
                    "50",
                    "--now",
                    "Reading the training logs.",
                    "--body",
                    "Still going.",
                ]
                )
            self.assertFalse(_has_mail_in(env.board, "cursor", "demo"))

            class Clock:
                def __init__(self) -> None:
                    from datetime import datetime, timedelta, timezone

                    self.now = datetime(2026, 10, 23, tzinfo=timezone.utc)
                    self.delta = timedelta(seconds=0)

                def __call__(self):
                    return self.now

                def advance(self, seconds: float) -> None:
                    from datetime import timedelta

                    self.now = self.now + timedelta(seconds=seconds)

            clock = Clock()

            def pause(seconds: float) -> None:
                clock.advance(seconds)

            out = io.StringIO()
            code = run_watch(
                env.board,
                "demo",
                "cursor",
                timeout_seconds=1,
                now=clock,
                sleep=pause,
                out=out,
            )
            self.assertEqual(code, 3)
            self.assertIn(TIMEOUT_LINE, out.getvalue())
            self.assertNotIn("training logs", out.getvalue())
            header = render_check(check_inbox(env.board, "demo", "cursor"))
            self.assertIn("claude_code on " + mid + ":", header)
            self.assertIn("50%", header)
            self.assertIn("0 new message", header)


class WaitTests(unittest.TestCase):
    def test_fresh_progress_slides_until_the_hour(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor")
            connect_agent_record(env.board, "claude_code", "claude-code")
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {"claude_code": "reviews"})
            mid = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="Look",
                body="Please look.",
                needs_reply=True,
            )
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(
                [
                    "progress",
                    "write",
                    "--as",
                    "claude_code",
                    "--project",
                    "demo",
                    "--for",
                    mid,
                    "--step",
                    "2 of 4",
                    "--percent",
                    "50",
                    "--now",
                    "Reading the training logs.",
                    "--body",
                    "Still going.",
                ]
                )
            saved = list((env.board / "projects" / "demo" / "progress" / "helpers" / "claude_code").glob("*.md"))
            self.assertEqual(len(saved), 1)

            class Clock:
                def __init__(self) -> None:
                    self.mono = 0.0
                    self.wall = time.time()

                def monotonic(self) -> float:
                    return self.mono

                def time(self) -> float:
                    return self.wall

                def sleep(self, seconds: float) -> None:
                    self.mono += seconds
                    self.wall += seconds
                    os.utime(saved[0], (self.wall, self.wall))

            clock = Clock()
            os.utime(saved[0], (clock.wall, clock.wall))
            out = io.StringIO()
            with redirect_stdout(out):
                code = wait_for_reply(
                    env.board,
                    "demo",
                    "cursor",
                    mid,
                    1,
                    sleep=clock.sleep,
                    monotonic=clock.monotonic,
                    wall=clock.time,
                )
            self.assertEqual(code, 3)
            text = out.getvalue()
            self.assertIn("claude_code: step 2 of 4 (50%). Reading the training logs.", text)
            self.assertIn("has been working for", text)
            self.assertGreaterEqual(clock.mono, 3600)

            reply = send(
                env.board,
                project="demo",
                sender="claude_code",
                recipient="cursor",
                subject="Re: Look",
                body="Done.",
                re=mid,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                code = wait_for_reply(env.board, "demo", "cursor", mid, 1, sleep=lambda _s: None, monotonic=lambda: 0, wall=time.time)
            self.assertEqual(code, 0)
            self.assertIn(reply, out.getvalue())


class StatusTests(unittest.TestCase):
    def test_lines_stale_note_and_done_drops_after_a_day(self) -> None:
        from datetime import datetime, timedelta, timezone

        from agentcoop.progress import write_helper_progress, write_progress
        from agentcoop.rounds import format_status
        from agentcoop.statusfile import render_status

        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor")
            connect_agent_record(env.board, "claude_code", "claude-code")
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {"claude_code": "reviews"})
            mid = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="Look",
                body="Please look.",
                needs_reply=True,
            )
            write_progress(
                env.board,
                "demo",
                "cursor",
                format_fields(status="working", step="3 of 7", percent="43", now="Reading the logs.", rest="## Working on now\nReading the logs."),
            )
            write_helper_progress(
                env.board,
                "demo",
                "claude_code",
                mid,
                "Done so far: one.",
                status="working",
                percent="50",
                now="Reading the training logs.",
            )
            text = render_status(env.board)
            self.assertIn("cursor: working, step 3 of 7, 43%", text)
            self.assertIn("claude_code on " + mid + ": working, 50%", text)
            self.assertIn("post every 10 minutes", format_status(env.board))
            helper_file = next((env.board / "projects" / "demo" / "progress" / "helpers" / "claude_code").glob("*.md"))
            old = time.time() - 25 * 60 - 30
            os.utime(helper_file, (old, old))
            stale = render_status(env.board)
            self.assertIn("no update for 25 min", stale)
            config = load_config(env.board)
            config["progress_every_minutes"] = 30
            config["progress_long_task_minutes"] = 8
            save_config(env.board, config)
            self.assertIn("post every 30 minutes", format_status(env.board))
            self.assertNotIn("no update for 25 min", render_status(env.board))
            moment = datetime.now(timezone.utc) + timedelta(seconds=2)
            write_helper_progress(
                env.board,
                "demo",
                "claude_code",
                mid,
                "Finished.",
                status="done",
                percent="100",
                now="Finished the run.",
                moment=moment,
            )
            done_file = sorted((env.board / "projects" / "demo" / "progress" / "helpers" / "claude_code").glob("*.md"))[-1]
            ancient = time.time() - 2 * 86400
            os.utime(done_file, (ancient, ancient))
            self.assertNotIn("Finished the run", render_status(env.board))


class SettingTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            config = load_config(env.board)
            self.assertEqual(config["progress_every_minutes"], 10)
            self.assertEqual(config["progress_long_task_minutes"], 5)
            self.assertTrue((Path(env.board) / "config.json").is_file())


if __name__ == "__main__":
    unittest.main()
