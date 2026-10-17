"""Tests for the viewer printing its link when stdout is a pipe."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import unittest
import urllib.request

import support


class UiFlushTests(unittest.TestCase):
    def test_link_appears_on_a_pipe_and_shutdown_stops_it(self) -> None:
        with support.TempEnv() as env:
            from agentcoop.cli import main

            self.assertEqual(main(["setup", "--yes"]), 0)
            child_env = os.environ.copy()
            child_env["HOME"] = str(env.home)
            child_env["USERPROFILE"] = str(env.home)
            child_env["AGENTCOOP_BOARD"] = str(env.board)
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            child_env["PYTHONPATH"] = os.path.join(root, "src")
            child_env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                [sys.executable, "-c", "from agentcoop.ui import serve; raise SystemExit(serve(open_browser=False))"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
            )
            box: list[bytes] = []

            def _read() -> None:
                assert proc.stdout is not None
                box.append(proc.stdout.readline())

            thread = threading.Thread(target=_read, daemon=True)
            thread.start()
            try:
                thread.join(5)
                self.assertTrue(box, "the link was not printed within 5 seconds")
                line = box[0].decode("utf-8").strip()
                self.assertTrue(line.startswith("http://127.0.0.1:"))
                self.assertIn("?t=", line)
                token = line.split("?t=", 1)[1]
                request = urllib.request.Request(
                    line.split("/?t=")[0] + "/api/shutdown?t=" + token,
                    data=b"{}",
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                self.assertEqual(proc.wait(timeout=5), 0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout is not None:
                    proc.stdout.close()
                if proc.stderr is not None:
                    proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
