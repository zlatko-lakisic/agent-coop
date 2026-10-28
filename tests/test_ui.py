"""Tests for the local viewer. No browser is opened."""

from __future__ import annotations

import re
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

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

    def test_viewer_assets_use_session_cookie(self) -> None:
        """/?t= sets a cookie so ./assets load without the query token."""
        with support.TempEnv() as env:
            init_board_files(env.board)
            server = make_server(env.board, "secret")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            base = "http://127.0.0.1:" + str(port)
            try:
                jar = CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
                with opener.open(base + "/?t=secret", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    html = response.read().decode("utf-8")
                self.assertTrue(any(cookie.name == "agentcoop_ui" for cookie in jar))
                assets = re.findall(r'(?:src|href)="(\./assets/[^"]+)"', html)
                self.assertGreaterEqual(len(assets), 2)
                for relative in assets:
                    path = "/" + relative.lstrip("./")
                    with opener.open(base + path, timeout=5) as asset:
                        self.assertEqual(asset.status, 200)
                        ctype = asset.headers.get("Content-Type", "")
                        if path.endswith(".js"):
                            self.assertIn("javascript", ctype.lower())
                        elif path.endswith(".css"):
                            self.assertIn("text/css", ctype.lower())
                        asset.read()
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(base + assets[0].lstrip("."), timeout=5)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
