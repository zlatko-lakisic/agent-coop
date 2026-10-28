"""Timeline events and the read-only viewer API."""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import support

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "sim"))

import viewer_fixture

from agentcoop.core import send
from agentcoop.timeline import agents_view, clear_cache, event_detail, events_for, histogram, query_events, rules_view, status_view
from agentcoop.ui import make_server

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)


def _kinds(board: Path) -> set[str]:
    clear_cache()
    return {event["kind"] for event in events_for(board, "demo")}


class TimelineTests(unittest.TestCase):
    def test_event_kinds_and_order(self) -> None:
        with support.TempEnv() as env:
            viewer_fixture.build_demo(env.board, env.workspace)
            kinds = _kinds(env.board)
            for kind in (
                "message.question",
                "message.answer",
                "message.task",
                "message.status",
                "message.ack",
                "message.patch",
                "message.context_request",
                "message.nudge",
                "message.ping",
                "message.heartbeat",
                "message.decline",
                "message.cancel",
                "message.review_request",
                "message.review_verdict",
                "message.move",
                "progress.update",
                "progress.review",
                "progress.waiting",
                "progress.done",
                "context.version",
                "membership.join",
                "membership.helper_add",
                "settings.watch",
                "settings.review",
                "settings.notice",
                "settings.approval",
                "settings.override",
                "file.shared",
                "file.held",
                "outbox.sent",
                "outbox.rejected",
                "grade.late",
                "round.log",
                "stall.mail",
                "update.check",
                "update.installed",
            ):
                self.assertIn(kind, kinds, kind)
            low = query_events(env.board, project="demo", include_low=True)
            self.assertTrue(any(event["kind"] == "trace.line" for event in low["events"]) or "trace.line" in kinds)
            page = query_events(env.board, project="demo", query="Same second", limit=10)
            subjects = [event["title"] for event in page["events"]]
            self.assertLess(subjects.index("Same second B"), subjects.index("Same second A"))
            secret = [event for event in events_for(env.board, "demo") if event["title"] == "Secret note" or "hidden" in event["summary"]]
            self.assertTrue(secret)
            self.assertIn("[hidden: looks like a secret]", secret[0]["summary"] + secret[0]["body"])
            agents = agents_view(env.board, "demo")
            self.assertTrue(any(row["name"] == "cursor" and row["role"] == "driver" for row in agents))
            self.assertEqual(len(agents[0]["activity"]), 7)
            rules = rules_view(env.board, "demo")
            self.assertEqual(rules["review"], "changes")
            self.assertEqual(rules["checking"], "stopped")
            status = status_view(env.board)
            self.assertTrue(any(item["kind"] == "stall.mail" for item in status["items"]))

    def test_filters_paging_thread_histogram_cache(self) -> None:
        with support.TempEnv() as env:
            viewer_fixture.build_demo(env.board, env.workspace)
            clear_cache()
            found = query_events(env.board, project="demo", query="first scene", limit=10)
            self.assertTrue(found["events"])
            actors = query_events(env.board, project="demo", actors=["gemini"], limit=10)
            self.assertTrue(actors["events"])
            self.assertTrue(all(event["actor"] == "gemini" for event in actors["events"]))
            first = query_events(env.board, project="demo", limit=2)
            self.assertEqual(len(first["events"]), 2)
            second = query_events(env.board, project="demo", cursor=first["next"], limit=2)
            self.assertNotEqual(first["events"][0]["id"], second["events"][0]["id"])
            question = next(event for event in events_for(env.board, "demo") if event["kind"] == "message.question")
            detail = event_detail(env.board, question["id"])
            self.assertIsNotNone(detail)
            self.assertGreaterEqual(len(detail["thread"]), 2)
            buckets = histogram(env.board, project="demo", buckets=4)
            self.assertTrue(buckets["buckets"])
            self.assertTrue(any(slot["markers"] for slot in buckets["buckets"]))
            path = env.board / "projects" / "demo" / "progress" / "20261025T150000Z_cursor.md"
            path.write_text(path.read_text(encoding="utf-8") + "\nExtra line for the cache.\n", encoding="utf-8")
            again = events_for(env.board, "demo")
            self.assertTrue(any("Extra line" in event.get("body", "") for event in again))

    def test_large_board_page(self) -> None:
        with support.TempEnv() as env:
            viewer_fixture.build_large(env.board, env.workspace, 20000)
            clear_cache()
            query_events(env.board, project="wide", limit=50)
            started = time.perf_counter()
            page = query_events(env.board, project="wide", limit=50)
            elapsed = time.perf_counter() - started
            self.assertEqual(len(page["events"]), 50)
            self.assertLess(elapsed, 0.3, "page took " + str(round(elapsed, 3)) + "s")

    def test_api_security(self) -> None:
        with support.TempEnv() as env:
            viewer_fixture.build_demo(env.board, env.workspace)
            server = make_server(env.board, "secret")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            base = "http://127.0.0.1:" + str(port)
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(base + "/api/v2/events?project=demo", timeout=5)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                request = urllib.request.Request(base + "/api/v2/events?project=demo&limit=5", headers={"X-Agentcoop-Token": "wrong"})
                with self.assertRaises(urllib.error.HTTPError) as wrong:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(wrong.exception.code, 403)
                wrong.exception.close()
                request = urllib.request.Request(base + "/api/v2/events?project=demo&limit=5", headers={"X-Agentcoop-Token": "secret"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                    self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
                    self.assertEqual(response.headers["Content-Security-Policy"], CSP)
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(payload["events"])
                with urllib.request.urlopen(base + "/?t=secret", timeout=5) as response:
                    self.assertIn("no-referrer", response.headers["Referrer-Policy"])
                    self.assertIn("unsafe-inline", response.headers["Content-Security-Policy"])
                for path in ("/files/demo/icon.svg?t=secret", "/files/demo/page.html?t=secret"):
                    with urllib.request.urlopen(base + path, timeout=5) as response:
                        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
                held = urllib.request.Request(base + "/files/demo/tool.sh?t=secret")
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    urllib.request.urlopen(held, timeout=5)
                self.assertEqual(blocked.exception.code, 403)
                blocked.exception.close()
                post = urllib.request.Request(
                    base + "/api/driver?t=secret",
                    data=b"{}",
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    urllib.request.urlopen(post, timeout=5)
                self.assertEqual(missing.exception.code, 400)
                missing.exception.close()
            finally:
                server.shutdown()
                server.server_close()


class SameSecondTests(unittest.TestCase):
    def test_seq_not_id(self) -> None:
        with support.TempEnv() as env:
            from agentcoop.projects import add_project, connect_agent_record, init_board_files

            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            connect_agent_record(env.board, "claude_code", "claude-code", display="Claude Code")
            folder = env.workspace / "order"
            folder.mkdir()
            add_project(env.board, "order", folder, "cursor", {"claude_code": "helps"})
            when = datetime(2026, 10, 27, 15, 22, 57, tzinfo=timezone.utc)
            first = send(env.board, project="order", sender="cursor", recipient="claude_code", subject="zzz-later-name", body="one", type="task", now=when)
            second = send(env.board, project="order", sender="claude_code", recipient="cursor", subject="aaa-earlier-name", body="two", type="answer", re=first, now=when)
            clear_cache()
            rows = [event for event in events_for(env.board, "order") if event["kind"].startswith("message.")]
            self.assertEqual(rows[0]["title"], "zzz-later-name")
            self.assertEqual(rows[1]["title"], "aaa-earlier-name")
            self.assertLess(rows[0]["order"], rows[1]["order"])
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
