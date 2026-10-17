"""Tests for project context documents and context_request messages."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.context import (
    archive_context,
    latest_version,
    list_versions,
    open_context_requests,
    read_marker,
    write_context,
)
from agentcoop.core import check_inbox, protocol_violation, send
from agentcoop.errors import CoopError
from agentcoop.paths import inbox_msgs
from agentcoop.projects import (
    add_project,
    connect_agent_record,
    init_board_files,
    load_projects,
    set_driver,
)


def _ready(env: support.TempEnv) -> Path:
    init_board_files(env.board)
    connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
    connect_agent_record(env.board, "claude_code", "claude-code", display="Claude Code")
    folder = env.workspace / "frontend"
    folder.mkdir(exist_ok=True)
    add_project(
        env.board,
        "frontend",
        folder,
        "cursor",
        {"claude_code": "reviews code"},
    )
    return folder


class ContextTests(unittest.TestCase):
    def test_write_for_driver_only(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            name = write_context(env.board, "frontend", "cursor", "Hello context")
            self.assertTrue(name.endswith("_cursor.md"))
            self.assertTrue((env.board / "projects" / "frontend" / "context" / name).is_file())
            with self.assertRaises(CoopError) as helper_err:
                write_context(env.board, "frontend", "claude_code", "nope")
            self.assertIn("Only the driver of frontend writes the project context.", str(helper_err.exception))
            connect_agent_record(env.board, "opencode", "opencode", display="OpenCode")
            with self.assertRaises(CoopError) as outsider_err:
                write_context(env.board, "frontend", "opencode", "nope")
            self.assertIn("Only the driver of frontend writes the project context.", str(outsider_err.exception))

    def test_size_and_secret_limits(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            with self.assertRaises(CoopError) as size_err:
                write_context(env.board, "frontend", "cursor", "x" * 70000)
            self.assertIn("too long", str(size_err.exception))
            with self.assertRaises(CoopError) as secret_err:
                write_context(env.board, "frontend", "cursor", "api_key=sk-abcdefghijklmnopqrstuvwxyz")
            self.assertIn("password or API key", str(secret_err.exception))
            name = write_context(
                env.board,
                "frontend",
                "cursor",
                "api_key=sk-abcdefghijklmnopqrstuvwxyz",
                allow_secret=True,
            )
            self.assertTrue(name.endswith(".md"))

    def test_show_records_read_and_headers(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["context", "show", "--project", "frontend", "--as", "claude_code"])
            self.assertEqual(code, 0)
            self.assertIn("There is no project context yet.", out.getvalue())
            self.assertIn("context_request", out.getvalue())
            name = write_context(env.board, "frontend", "cursor", "## Goal\nShip it.\n")
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(
                    [
                        "context",
                        "show",
                        "--project",
                        "frontend",
                        "--as",
                        "claude_code",
                    ]
                )
            self.assertEqual(code, 0)
            text = out.getvalue()
            self.assertIn("Project context for frontend, version " + name, text)
            self.assertIn("Ship it.", text)
            self.assertEqual(read_marker(env.board, "frontend", "claude_code"), name)
            report = check_inbox(env.board, "frontend", "claude_code")
            from agentcoop.context import format_context_header

            lines = format_context_header(
                env.board,
                "frontend",
                "claude_code",
                "helper",
                report.driver,
                report.helpers,
            )
            self.assertEqual(lines, ["Project context: read (version " + name + ")."])
            later = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=2)
            new_name = write_context(
                env.board,
                "frontend",
                "cursor",
                "## Goal\nUpdated.\n",
                now=later,
            )
            lines = format_context_header(
                env.board,
                "frontend",
                "claude_code",
                "helper",
                report.driver,
                report.helpers,
            )
            self.assertTrue(lines[0].startswith("Project context: updated "))
            self.assertIn("context show --as claude_code", lines[0])
            self.assertNotEqual(new_name, name)

    def test_one_context_request_then_block_then_allow(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = send(
                env.board,
                project="frontend",
                sender="claude_code",
                recipient="cursor",
                type="context_request",
                subject="Context for frontend",
                body="Need the goal",
            )
            self.assertTrue(mid)
            path = inbox_msgs(env.board, "frontend", "cursor") / (mid + ".json")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data["needs_reply"])
            with self.assertRaises(CoopError) as err:
                send(
                    env.board,
                    project="frontend",
                    sender="claude_code",
                    recipient="cursor",
                    type="context_request",
                    subject="Again",
                    body="More",
                )
            self.assertIn("You already asked the driver for context.", str(err.exception))
            reply = send(
                env.board,
                project="frontend",
                sender="cursor",
                recipient="claude_code",
                type="answer",
                re=mid,
                subject="Re: Context",
                body="Wrote it",
            )
            self.assertTrue(reply)
            self.assertEqual(open_context_requests(env.board, "frontend", helper="claude_code"), [])
            again = send(
                env.board,
                project="frontend",
                sender="claude_code",
                recipient="cursor",
                type="context_request",
                subject="Context again",
                body="Need more",
            )
            self.assertTrue(again)

    def test_helper_question_without_re_still_refused(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            with self.assertRaises(CoopError) as err:
                send(
                    env.board,
                    project="frontend",
                    sender="claude_code",
                    recipient="cursor",
                    type="question",
                    subject="hi",
                    body="hi",
                )
            self.assertIn("Helpers can only reply to the driver.", str(err.exception))

    def test_bare_mode_context_request_rules(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            folder = inbox_msgs(env.board, "frontend", "cursor")
            folder.mkdir(parents=True, exist_ok=True)
            first = {
                "v": 1,
                "id": "20260101T000000Z_claude_code_aaaa",
                "ts": "2026-01-01T00:00:00Z",
                "project": "frontend",
                "sender": "claude_code",
                "recipient": "cursor",
                "type": "context_request",
                "re": None,
                "subject": "Context",
                "body": "need it",
                "attachments": [],
                "needs_reply": True,
            }
            path = folder / (first["id"] + ".json")
            path.write_text(json.dumps(first), encoding="utf-8")
            from agentcoop.core import load_message

            message = load_message(path)
            projects = load_projects(env.board)
            reason = protocol_violation(
                projects["projects"]["frontend"],
                message,
                env.board,
                "frontend",
            )
            self.assertIsNone(reason)
            second = dict(first)
            second["id"] = "20260101T000001Z_claude_code_bbbb"
            second_path = folder / (second["id"] + ".json")
            second_path.write_text(json.dumps(second), encoding="utf-8")
            second_msg = load_message(second_path)
            reason2 = protocol_violation(
                projects["projects"]["frontend"],
                second_msg,
                env.board,
                "frontend",
            )
            self.assertIsNotNone(reason2)
            self.assertIn("You already asked the driver for context.", reason2 or "")

    def test_needs_reply_forced(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = send(
                env.board,
                project="frontend",
                sender="claude_code",
                recipient="cursor",
                type="context_request",
                subject="Context",
                body="need",
                needs_reply=False,
            )
            data = json.loads(
                (inbox_msgs(env.board, "frontend", "cursor") / (mid + ".json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(data["needs_reply"])

    def test_open_requests_listed_first_for_driver(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            send(
                env.board,
                project="frontend",
                sender="cursor",
                recipient="claude_code",
                subject="Please review",
                body="look",
            )
            send(
                env.board,
                project="frontend",
                sender="claude_code",
                recipient="cursor",
                type="context_request",
                subject="Context for frontend",
                body="need context",
            )
            from agentcoop.context import format_context_header

            lines = format_context_header(
                env.board,
                "frontend",
                "cursor",
                "driver",
                "cursor",
                {"claude_code": "reviews"},
            )
            self.assertTrue(lines)
            self.assertIn("claude_code is waiting for project context.", lines[0])

    def test_driver_change_keeps_context(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            name = write_context(env.board, "frontend", "cursor", "Keep me\n")
            set_driver(env.board, "frontend", "claude_code")
            latest = latest_version(env.board, "frontend")
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.name, name)
            self.assertIn("Keep me", latest.read_text(encoding="utf-8"))

    def test_archive_keeps_latest_five(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            for index in range(8):
                write_context(
                    env.board,
                    "frontend",
                    "cursor",
                    "v" + str(index) + "\n",
                    now=base + timedelta(seconds=index),
                )
            self.assertEqual(len(list_versions(env.board, "frontend")), 8)
            removed = archive_context(
                env.board,
                "frontend",
                older_than_days=0,
                now=base + timedelta(days=1),
            )
            self.assertEqual(removed, 3)
            versions = list_versions(env.board, "frontend")
            self.assertEqual(len(versions), 5)
            self.assertTrue(versions[-1].name.startswith("20260101T000007Z"))

    def test_cli_commands_without_terminal(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["context", "template"]), 0)
            self.assertIn("# Project context:", out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(
                    main(
                        [
                            "context",
                            "write",
                            "--as",
                            "cursor",
                            "--project",
                            "frontend",
                            "--body",
                            "Demo context",
                        ]
                    ),
                    0,
                )
            name = out.getvalue().strip()
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["context", "list", "--project", "frontend"]), 0)
            self.assertIn(name, out.getvalue())
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(
                    [
                        "context",
                        "write",
                        "--as",
                        "claude_code",
                        "--project",
                        "frontend",
                        "--body",
                        "nope",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("Only the driver", err.getvalue())


if __name__ == "__main__":
    unittest.main()
