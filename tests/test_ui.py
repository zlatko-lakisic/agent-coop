"""Tests for the local viewer. No browser is opened."""

from __future__ import annotations

import threading
import unittest
import urllib.error
import urllib.request

import support

from agentcoop.errors import CoopError
from agentcoop.projects import init_board_files
from agentcoop.ui import make_server


class UiTests(unittest.TestCase):
    def test_token_and_localhost_only(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            with self.assertRaises(CoopError):
                make_server(env.board, "secret", host="0.0.0.0")
            server = make_server(env.board, "secret")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen("http://127.0.0.1:" + str(port) + "/api/state", timeout=2)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                with urllib.request.urlopen(
                    "http://127.0.0.1:" + str(port) + "/api/state?t=secret",
                    timeout=2,
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"projects", response.read())
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
