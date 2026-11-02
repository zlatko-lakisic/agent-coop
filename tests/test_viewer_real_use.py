"""Viewer real-use fixes: severity, stall snapshot, totals, encoding, files, order."""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import support

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "sim"))

import viewer_fixture

from agentcoop.core import send
from agentcoop.progress import stall_line, stall_snapshot, waiting_line, write_progress
from agentcoop.timeline import SEVERITY, clear_cache, events_for, histogram, query_events, severity_for, status_view
from agentcoop.ui import file_children, files_view, make_server


class ViewerRealUseTests(unittest.TestCase):
    def test_histogram_spans_full_range_including_empty_days(self) -> None:
        with support.TempEnv() as env:
            viewer_fixture.build_demo(env.board, env.workspace)
            clear_cache()
            start = "2026-10-22T00:00:00Z"
            end = "2026-11-01T23:59:59Z"
            result = histogram(env.board, project="demo", start=start, end=end, buckets=20)
            buckets = result["buckets"]
            self.assertEqual(len(buckets), 20)
            self.assertEqual(buckets[0]["start"], "2026-10-22T00:00:00Z")
            self.assertTrue(buckets[-1]["end"] >= "2026-11-01T00:00:00Z")
            # Empty hours stay; not every slot needs events.
            self.assertTrue(any(not slot["actors"] for slot in buckets))
            self.assertTrue(any(slot["actors"] for slot in buckets))
            days = {slot["start"][:10] for slot in buckets}
            self.assertIn("2026-10-22", days)
            self.assertIn("2026-11-01", days)

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
            rows = files_view(env.board, "demo", "tok", local_open=True)
            names = {row["name"] for row in rows}
            self.assertIn("shot.png", names)
            self.assertIn("tool.sh", names)
            self.assertIn("note.txt", names)
            self.assertIn("extra.txt", names)
            held = next(row for row in rows if row["name"] == "tool.sh")
            self.assertFalse(held["served"])
            self.assertEqual(held["source"], "held")
            self.assertIn("Program", held["note"])
            served = next(row for row in rows if row["name"] == "shot.png")
            self.assertTrue(served["served"])
            self.assertTrue(served["url"])
            draft = next(row for row in rows if row["name"] == "note.txt")
            self.assertTrue(draft["served"])
            self.assertIn("/local-file", draft["url"])
            outbox = next(row for row in rows if row["name"] == "extra.txt")
            self.assertTrue(outbox["served"])
            self.assertIn("/local-file", outbox["url"])
            phone_rows = files_view(env.board, "demo", "tok", local_open=False)
            phone_draft = next(row for row in phone_rows if row["name"] == "note.txt")
            self.assertFalse(phone_draft["served"])

    def test_files_view_nests_referenced_project_paths(self) -> None:
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
            screens = folder / "data" / "board" / "screens"
            screens.mkdir(parents=True, exist_ok=True)
            shot = screens / "r12-campaign-open.png"
            shot.write_bytes(b"\x89PNG\r\n\x1a\n")
            draft = env.board / "projects" / "demo" / "files" / "cursor"
            draft.mkdir(parents=True, exist_ok=True)
            (draft / "catalog.md").write_text(
                "# Catalog\n\nOpen client was `data/board/screens/r12-campaign-open.png`.\n",
                encoding="utf-8",
            )
            rows = files_view(env.board, "demo", "tok", local_open=True)
            parent = next(row for row in rows if row["name"] == "catalog.md")
            self.assertTrue(parent.get("expandable"))
            self.assertTrue(parent.get("local_path"))
            self.assertEqual(parent.get("children") or [], [])
            children = file_children(
                env.board,
                "demo",
                "tok",
                str(parent["local_path"]),
                local_open=True,
                parent_id=str(parent["id"]),
                parent_name=str(parent["name"]),
            )
            self.assertEqual(len(children), 1)
            child = children[0]
            self.assertEqual(child["name"], "r12-campaign-open.png")
            self.assertEqual(child["source"], "reference")
            self.assertEqual(child["bucket"], "image")
            self.assertTrue(child["served"])
            self.assertIn("/local-file", child["url"])
            eager = files_view(env.board, "demo", "tok", local_open=True, expand_refs=True)
            eager_parent = next(row for row in eager if row["name"] == "catalog.md")
            self.assertEqual(len(eager_parent.get("children") or []), 1)

    def test_files_view_nests_json_manifest_paths_recursively(self) -> None:
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
            screens = folder / "data" / "board" / "screens"
            screens.mkdir(parents=True, exist_ok=True)
            (screens / "r11-a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (screens / "r11-b.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            runtime = folder / "data" / "runtime" / "negotiations"
            runtime.mkdir(parents=True, exist_ok=True)
            (runtime / "frame_manifest.json").write_text(
                json.dumps(
                    {
                        "held_out": ["data/board/screens/r11-a.png"],
                        "frames": [{"file": "data/board/screens/r11-b.png", "screen": "negotiations"}],
                    }
                ),
                encoding="utf-8",
            )
            draft = env.board / "projects" / "demo" / "files" / "cursor"
            draft.mkdir(parents=True, exist_ok=True)
            (draft / "note.md").write_text(
                "See `data/runtime/negotiations/frame_manifest.json`.\n",
                encoding="utf-8",
            )
            rows = files_view(env.board, "demo", "tok", local_open=True)
            parent = next(row for row in rows if row["name"] == "note.md")
            self.assertTrue(parent.get("expandable"))
            level1 = file_children(
                env.board,
                "demo",
                "tok",
                str(parent["local_path"]),
                local_open=True,
                parent_id=str(parent["id"]),
                parent_name=str(parent["name"]),
            )
            self.assertEqual(len(level1), 1)
            manifest = level1[0]
            self.assertEqual(manifest["name"], "frame_manifest.json")
            self.assertTrue(manifest.get("expandable"))
            self.assertEqual(manifest.get("children") or [], [])
            nested = {
                child["name"]
                for child in file_children(
                    env.board,
                    "demo",
                    "tok",
                    str(manifest["local_path"]),
                    local_open=True,
                    parent_id=str(manifest["id"]),
                    parent_name=str(manifest["name"]),
                )
            }
            self.assertIn("r11-a.png", nested)
            self.assertIn("r11-b.png", nested)

    def test_local_file_serves_draft_refuses_program(self) -> None:
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
            draft = env.board / "projects" / "demo" / "files" / "cursor"
            draft.mkdir(parents=True, exist_ok=True)
            note = draft / "note.txt"
            note.write_bytes(b"draft\n")
            script = draft / "run.sh"
            script.write_bytes(b"#!/bin/sh\n")
            server = make_server(env.board, "secret")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            base = "http://127.0.0.1:" + str(port)
            try:
                ok = (
                    base
                    + "/local-file?project=demo&p="
                    + urllib.parse.quote(str(note))
                    + "&t=secret"
                )
                with urllib.request.urlopen(ok, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b"draft\n")
                blocked = (
                    base
                    + "/local-file?project=demo&p="
                    + urllib.parse.quote(str(script))
                    + "&t=secret"
                )
                with self.assertRaises(urllib.error.HTTPError) as refused:
                    urllib.request.urlopen(blocked, timeout=5)
                self.assertEqual(refused.exception.code, 403)
                refused.exception.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
