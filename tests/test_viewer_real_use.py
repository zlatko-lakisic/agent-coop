"""Viewer real-use fixes: severity, stall snapshot, totals, encoding, files, order."""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone

import support

from agentcoop.core import send
from agentcoop.progress import stall_line, stall_snapshot, waiting_line, write_progress
from agentcoop.timeline import SEVERITY, clear_cache, events_for, query_events, severity_for, status_view
from agentcoop.ui import files_view


class ViewerRealUseTests(unittest.TestCase):
    def test_every_kind_has_severity(self) -> None:
        for kind in SEVERITY:
            self.assertIn(severity_for(kind), ("info", "attention", "problem"), kind)
        self.assertEqual(severity_for("update.installed"), "info")
        self.assertEqual(severity_for("stall.progress"), "problem")
        self.assertEqual(severity_for("message.nudge"), "attention")
        self.assertEqual(severity_for("message.review_verdict", verdict="changes"), "attention")

    def test_stall_snapshot_freezes_minutes_and_skips_waiting(self) -> None:
        with support.TempEnv() as env:
            from agentcoop.cli import main

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
            written = datetime(2026, 10, 28, 11, 36, tzinfo=timezone.utc)
            name = write_progress(
                env.board,
                "demo",
                "cursor",
                "Status: working\nNow: Phase D.\n",
                now=written,
            )
            path = env.board / "projects" / "demo" / "progress" / name
            os.utime(path, (written.timestamp(), written.timestamp()))
            moment = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
            first = stall_snapshot(env.board, "demo", now=moment)
            self.assertIsNotNone(first)
            assert first is not None
            self.assertEqual(first["minutes"], 24)
            later = moment + timedelta(minutes=10)
            second = stall_snapshot(env.board, "demo", now=later)
            assert second is not None
            self.assertEqual(second["minutes"], 24)
            self.assertEqual(stall_line(env.board, "demo", now=later), first["text"])
            write_progress(
                env.board,
                "demo",
                "cursor",
                "Status: waiting\nNow: you\n",
                now=later,
            )
            self.assertIsNone(stall_snapshot(env.board, "demo", now=later + timedelta(minutes=30)))
            self.assertIn("Waiting on", waiting_line(env.board, "demo"))
            status = status_view(env.board)
            self.assertTrue(any(item["kind"] == "progress.waiting" for item in status["items"]))
            self.assertFalse(any(item["kind"] == "stall.progress" for item in status["items"]))

    def test_query_events_returns_total_and_day_counts(self) -> None:
        with support.TempEnv() as env:
            from agentcoop.cli import main

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
            start = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
            for index in range(5):
                send(
                    env.board,
                    project="demo",
                    sender="cursor",
                    recipient="claude_desktop",
                    subject="Item " + str(index),
                    body="Body " + str(index),
                    type="status",
                    now=start + timedelta(minutes=index),
                )
            clear_cache()
            page = query_events(env.board, project="demo", kinds=["message."], limit=2)
            self.assertEqual(page["total"], 5)
            self.assertTrue(page["next"])
            self.assertEqual(len(page["events"]), 2)
            self.assertIn("2026-10-27", page["day_counts"])
            self.assertEqual(page["day_counts"]["2026-10-27"], 5)
            self.assertGreaterEqual(query_events(env.board, project="demo")["total"], 5)

    def test_mixed_order_and_utf8_replace(self) -> None:
        with support.TempEnv() as env:
            from agentcoop.cli import main

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
            when = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)
            send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_desktop",
                subject="Same second A",
                body="café ≠ 🤖",
                type="status",
                now=when,
            )
            send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_desktop",
                subject="Same second B",
                body="accents",
                type="status",
                now=when,
            )
            progress = env.board / "projects" / "demo" / "progress"
            progress.mkdir(parents=True, exist_ok=True)
            bad = progress / "20261028T140000Z_cursor.md"
            bad.write_bytes(b"Status: working\nNow: bad \xff bytes\n")
            clear_cache()
            rows = events_for(env.board, "demo")
            subjects = [event["title"] for event in rows if event["kind"].startswith("message.")]
            self.assertLess(subjects.index("Same second A"), subjects.index("Same second B"))
            text_events = [event for event in rows if "bad" in (event.get("body") or event.get("summary") or "")]
            self.assertTrue(text_events)
            page = query_events(env.board, project="demo", query="café")
            self.assertTrue(page["events"])

    def test_files_view_covers_shared_held_and_drafts(self) -> None:
        with support.TempEnv() as env:
            from agentcoop.cli import main

            assert main(["setup", "--connect", "cursor", "--schedule", "no", "--yes"]) == 0
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
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                )
                == 0
            )
            shared = env.board / "projects" / "demo" / "files" / "shared"
            shared.mkdir(parents=True, exist_ok=True)
            (shared / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (shared / "tool.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (shared / "index.json").write_text(
                json.dumps(
                    {
                        "files": [
                            {
                                "name": "shot.png",
                                "kind": "image",
                                "bytes": 8,
                                "sender": "cursor",
                                "message": "m1",
                                "shared_on": "2026-10-26T12:00:00Z",
                                "path": str(shared / "shot.png"),
                            },
                            {
                                "name": "tool.sh",
                                "kind": "program",
                                "bytes": 10,
                                "sender": "cursor",
                                "shared_on": "2026-10-26T12:03:00Z",
                                "held": True,
                                "path": str(shared / "tool.sh"),
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            draft = env.board / "projects" / "demo" / "files" / "cursor"
            draft.mkdir(parents=True, exist_ok=True)
            (draft / "note.txt").write_text("draft\n", encoding="utf-8")
            out = env.board / "projects" / "demo" / "outbox" / "cursor" / "answer.files"
            out.mkdir(parents=True, exist_ok=True)
            (out / "extra.txt").write_text("outbox\n", encoding="utf-8")
            rows = files_view(env.board, "demo", "tok")
            names = {row["name"] for row in rows}
            self.assertIn("shot.png", names)
            self.assertIn("tool.sh", names)
            self.assertIn("note.txt", names)
            self.assertIn("extra.txt", names)
            held = next(row for row in rows if row["name"] == "tool.sh")
            self.assertFalse(held["served"])
            self.assertEqual(held["source"], "held")
            served = next(row for row in rows if row["name"] == "shot.png")
            self.assertTrue(served["served"])
            self.assertTrue(served["url"])


if __name__ == "__main__":
    unittest.main()
