"""Phone mode: private addresses, pairing, read-only manage."""

from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

import support

from agentcoop.phone import PairingCode, is_private_ip, new_pairing_code, refuse_public
from agentcoop.errors import CoopError
from agentcoop.projects import add_project, connect_agent_record, init_board_files
from agentcoop.ui import make_server
from agentcoop.wsserver import connect_client
from agentcoop.viewerlive import LiveHub


class PrivateAddressTests(unittest.TestCase):
    def test_ranges(self) -> None:
        self.assertTrue(is_private_ip("127.0.0.1"))
        self.assertTrue(is_private_ip("10.0.0.2"))
        self.assertTrue(is_private_ip("192.168.1.9"))
        self.assertTrue(is_private_ip("172.16.4.1"))
        self.assertTrue(is_private_ip("100.64.1.2"))
        self.assertFalse(is_private_ip("8.8.8.8"))
        with self.assertRaises(CoopError):
            refuse_public("8.8.8.8")

    def test_pairing_single_use_and_expiry(self) -> None:
        code = new_pairing_code()
        self.assertTrue(code.ok(code.code))
        code.used = True
        self.assertFalse(code.ok(code.code))
        expired = PairingCode(code="123456", expires=time.time() - 1)
        self.assertFalse(expired.ok("123456"))


class PhoneServerTests(unittest.TestCase):
    def test_pair_and_readonly_manage(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {})
            pairing = new_pairing_code()
            sessions: dict = {}
            hub = LiveHub(env.board, "desk-token")
            server = make_server(
                env.board,
                "desk-token",
                host="127.0.0.1",
                phone=True,
                live=hub,
                pairing=pairing,
                phone_sessions=sessions,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            try:
                with urllib.request.urlopen("http://127.0.0.1:" + str(port) + "/pair", timeout=5) as response:
                    body = response.read().decode("utf-8")
                    self.assertIn("Pairing code", body)
                bad = urllib.request.Request(
                    "http://127.0.0.1:" + str(port) + "/pair",
                    data=urllib.parse.urlencode({"code": "000000"}).encode(),
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(bad, timeout=5)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                good = urllib.request.Request(
                    "http://127.0.0.1:" + str(port) + "/pair",
                    data=urllib.parse.urlencode({"code": pairing.code}).encode(),
                    method="POST",
                )
                opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
                # Don't follow redirect; read Location.
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        return None

                opener = urllib.request.build_opener(NoRedirect)
                try:
                    opener.open(good, timeout=5)
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, 302)
                    loc = exc.headers.get("Location") or ""
                    self.assertIn("t=", loc)
                    phone_token = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["t"][0]
                    exc.close()
                else:
                    self.fail("expected redirect")
                # Reuse pairing code must fail.
                again = urllib.request.Request(
                    "http://127.0.0.1:" + str(port) + "/pair",
                    data=urllib.parse.urlencode({"code": pairing.code}).encode(),
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(again, timeout=5)

                conn = connect_client(
                    "127.0.0.1",
                    port,
                    "/",
                    origin="http://127.0.0.1:" + str(port),
                )
                conn.send_text(json.dumps({"type": "hello", "token": phone_token}))
                _op, payload = conn.recv_message(timeout=5)
                hello = json.loads(payload.decode("utf-8"))
                self.assertTrue(hello.get("readonly"))
                conn.send_text(json.dumps({"id": 1, "op": "shutdown", "params": {}}))
                _op, payload = conn.recv_message(timeout=5)
                reply = json.loads(payload.decode("utf-8"))
                self.assertFalse(reply.get("ok"))
                self.assertIn("read-only", reply.get("error", "").lower())
                conn.close()
            finally:
                server.shutdown()
                server.server_close()

    def test_public_host_refused(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            with self.assertRaises(CoopError):
                make_server(env.board, "tok", host="8.8.8.8", phone=True)


if __name__ == "__main__":
    unittest.main()
