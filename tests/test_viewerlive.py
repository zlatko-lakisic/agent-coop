"""Viewer WebSocket protocol: hello, ops, subscribe, resume, file cookie."""

from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

import support

from agentcoop.timeline import clear_cache
from agentcoop.ui import make_server
from agentcoop.wsserver import OPCODE_CLOSE, connect_client
from agentcoop import viewerlive


def _ws(port: int, *, origin: str | None = None, path: str = "/"):
    if origin is None:
        origin = "http://127.0.0.1:" + str(port)
    return connect_client("127.0.0.1", port, path, origin=origin)


def _hello(conn, token: str) -> dict:
    conn.send_text(json.dumps({"type": "hello", "token": token}))
    opcode, payload = conn.recv_message(timeout=5)
    assert opcode != OPCODE_CLOSE, payload
    return json.loads(payload.decode("utf-8"))


def _req(conn, req_id: int, op: str, params: dict | None = None) -> dict:
    conn.send_text(json.dumps({"id": req_id, "op": op, "params": params or {}}))
    while True:
        opcode, payload = conn.recv_message(timeout=5)
        if opcode == OPCODE_CLOSE:
            raise AssertionError("socket closed: " + payload.decode("utf-8", "replace"))
        message = json.loads(payload.decode("utf-8"))
        if message.get("id") == req_id:
            return message
        # live push; ignore and keep waiting


class ViewerLiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = support.TempEnv()
        self.env.__enter__()
        from agentcoop.projects import add_project, connect_agent_record, init_board_files

        init_board_files(self.env.board)
        connect_agent_record(self.env.board, "cursor", "cursor", display="Cursor")
        connect_agent_record(self.env.board, "claude_code", "claude-code", display="Claude Code")
        folder = self.env.workspace / "demo"
        folder.mkdir()
        add_project(self.env.board, "demo", folder, "cursor", {"claude_code": "helps"})
        self.server = make_server(self.env.board, "secret-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def _write_progress(self, subject: str) -> None:
        folder = self.env.board / "projects" / "demo" / "progress"
        folder.mkdir(parents=True, exist_ok=True)
        # Filenames must stay stamp_agent.md for the timeline parser.
        while True:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            path = folder / (stamp + "_cursor.md")
            if not path.exists():
                break
            time.sleep(1)
        path.write_text(
            "# "
            + subject
            + "\n\nStatus: working\n\n## Working on now\n\n"
            + subject
            + "\n",
            encoding="utf-8",
        )

    def _wait_baseline(self) -> None:
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.server.live._baseline_ready:
                return
            time.sleep(0.05)
        self.fail("watcher did not take a baseline")

    def _wait_event_added(self, conn, needle: str, seconds: float = 8) -> dict:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                opcode, payload = conn.recv_message(timeout=0.5)
            except Exception:
                continue
            if opcode == OPCODE_CLOSE:
                break
            message = json.loads(payload.decode("utf-8"))
            if message.get("type") == "event.added" and needle in json.dumps(message):
                return message
        self.fail("no event.added containing " + needle)
        return {}

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.env.__exit__(None, None, None)

    def test_origin_refused(self) -> None:
        with self.assertRaises(Exception):
            _ws(self.port, origin="http://evil.example")

    def test_hello_timeout(self) -> None:
        old = viewerlive.HELLO_DEADLINE_SECONDS
        viewerlive.HELLO_DEADLINE_SECONDS = 0.4
        try:
            conn = _ws(self.port)
            time.sleep(0.6)
            opcode, _payload = conn.recv_message(timeout=2)
            self.assertEqual(opcode, OPCODE_CLOSE)
            conn.close()
        finally:
            viewerlive.HELLO_DEADLINE_SECONDS = old

    def test_wrong_token(self) -> None:
        conn = _ws(self.port)
        conn.send_text(json.dumps({"type": "hello", "token": "nope"}))
        opcode, _payload = conn.recv_message(timeout=5)
        self.assertEqual(opcode, OPCODE_CLOSE)
        conn.close()

    def test_request_reply_and_projects(self) -> None:
        conn = _ws(self.port)
        hello = _hello(conn, "secret-token")
        self.assertEqual(hello.get("type"), "hello.ok")
        reply = _req(conn, 1, "projects")
        self.assertTrue(reply.get("ok"))
        names = [row["name"] for row in reply["data"]["projects"]]
        self.assertIn("demo", names)
        conn.close()

    def test_subscribe_pushes_event_added(self) -> None:
        conn = _ws(self.port)
        _hello(conn, "secret-token")
        reply = _req(conn, 2, "subscribe", {"project": "demo"})
        self.assertTrue(reply.get("ok"))
        self._wait_baseline()
        self._write_progress("Live note")
        clear_cache()
        found = self._wait_event_added(conn, "Live note")
        self.assertIn("seq", found)
        conn.close()

    def test_resume_replays(self) -> None:
        conn = _ws(self.port)
        _hello(conn, "secret-token")
        _req(conn, 3, "subscribe", {"project": "demo"})
        self._wait_baseline()
        self._write_progress("First live")
        clear_cache()
        first = self._wait_event_added(conn, "First live")
        seq = int(first["seq"])
        reply = _req(conn, 4, "resume", {"seq": seq - 1})
        self.assertTrue(reply.get("ok"))
        messages = reply["data"]["messages"]
        self.assertTrue(any(int(row.get("seq") or 0) == seq for row in messages))
        conn.close()

    def test_file_cookie(self) -> None:
        # Put a shared image on the board.
        from agentcoop.share import share_root

        root = share_root(self.env.board, "demo")
        root.mkdir(parents=True, exist_ok=True)
        image = root / "shot.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        index = root / "index.json"
        index.write_text(
            json.dumps(
                {
                    "files": [
                        {
                            "name": "shot.png",
                            "path": str(image),
                            "kind": "image",
                            "media_type": "image/png",
                            "bytes": image.stat().st_size,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        conn = _ws(self.port)
        hello = _hello(conn, "secret-token")
        cookie = hello.get("file_session")
        self.assertTrue(cookie)
        # Without cookie or token, denied.
        url = "http://127.0.0.1:" + str(self.port) + "/files/demo/shot.png"
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(url, timeout=5)
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        request = urllib.request.Request(url, headers={"Cookie": "agentcoop_files=" + cookie})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(response.read().startswith(b"\x89PNG"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
