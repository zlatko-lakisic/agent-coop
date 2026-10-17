"""Tests for refusing project folders that are too broad."""

from __future__ import annotations

import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.ui import make_server


def _post(port: int, folder: str) -> tuple[int, str]:
    request = urllib.request.Request(
        "http://127.0.0.1:" + str(port) + "/api/project?t=secret",
        data=json.dumps(
            {"path": folder, "name": "demo", "driver": "cursor", "helpers": {}}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        exc.close()
        return exc.code, body


class BroadFolderTests(unittest.TestCase):
    def test_cli_and_ui_refuse_broad_folders(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor", "--yes"]), 0)
            server = make_server(env.board, "secret")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            cases = [
                (env.home, "Your home folder cannot be a project"),
                (Path(env.home.resolve().anchor), "filesystem root"),
                (env.home.parent, "contains your home folder"),
                (env.board, "contains the Agent Co-op board"),
            ]
            try:
                for folder, snippet in cases:
                    err = io.StringIO()
                    with redirect_stderr(err):
                        code = main(
                            [
                                "project",
                                "add",
                                str(folder),
                                "--name",
                                "demo",
                                "--driver",
                                "cursor",
                                "--yes",
                            ]
                        )
                    self.assertEqual(code, 1, str(folder))
                    self.assertIn(snippet, err.getvalue())
                    status, body = _post(port, str(folder))
                    self.assertEqual(status, 400, body)
                    self.assertIn(snippet, body)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_normal_folder_under_home_works(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor", "--yes"]), 0)
            folder = env.home / "projects" / "frontend"
            folder.mkdir(parents=True)
            code = main(
                [
                    "project",
                    "add",
                    str(folder),
                    "--name",
                    "frontend",
                    "--driver",
                    "cursor",
                    "--gitignore",
                    "no",
                    "--yes",
                ]
            )
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
