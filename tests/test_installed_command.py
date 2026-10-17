"""The installed agentcoop command stays executable after setup."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.install import build_zipapp
from agentcoop.projects import init_board_files, tighten_board_modes


def _env(env: support.TempEnv) -> dict:
    data = dict(os.environ)
    data["HOME"] = str(env.home)
    data["USERPROFILE"] = str(env.home)
    data["AGENTCOOP_BOARD"] = str(env.board)
    data.pop("PYTHONPATH", None)
    return data


@unittest.skipIf(os.name == "nt", "POSIX execute bits")
class InstalledCommandTests(unittest.TestCase):
    def test_zipapp_setup_leaves_a_runnable_command(self) -> None:
        with support.TempEnv() as env:
            pyz = env.root / "agentcoop.pyz"
            build_zipapp(pyz)
            data = _env(env)
            folder = env.workspace / "demo"
            folder.mkdir()
            setup = subprocess.run(
                [sys.executable, str(pyz), "setup", "--connect", "claude-code", "--schedule", "no", "--yes"],
                env=data,
                capture_output=True,
                text=True,
            )
            self.assertEqual(setup.returncode, 0, setup.stderr)
            command = env.board / "bin" / "agentcoop"
            self.assertTrue(os.access(command, os.X_OK))
            version = subprocess.run([str(command), "version"], env=data, capture_output=True, text=True)
            self.assertEqual(version.returncode, 0, version.stderr)
            again = subprocess.run(
                [sys.executable, str(pyz), "setup", "--connect", "claude-code", "--schedule", "no", "--yes"],
                env=data,
                capture_output=True,
                text=True,
            )
            self.assertEqual(again.returncode, 0, again.stderr)
            version = subprocess.run([str(command), "version"], env=data, capture_output=True, text=True)
            self.assertEqual(version.returncode, 0, version.stderr)
            settings = env.home / ".claude" / "settings.json"
            self.assertTrue(settings.is_file())
            import json

            payload = json.loads(settings.read_text(encoding="utf-8"))
            hook = payload["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            ran = subprocess.run(hook, env=data, shell=True, capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_frozen_binary_keeps_mode_700(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            binary = env.workspace / "agentcoop"
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(0o700)
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(binary)):
                from agentcoop.install import stage_runtime

                stage_runtime(env.board)
            tighten_board_modes(env.board)
            installed = env.board / "bin" / "agentcoop"
            self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o700)
