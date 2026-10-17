"""Schedule install, stop, and the git shim."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.gitshim import blocked_action
from agentcoop.projects import init_board_files
from agentcoop.rounds import load_schedule, start_schedule, stop_schedule, write_shims


class Completed:
    def __init__(self, code: int = 0) -> None:
        self.returncode = code


class ScheduleTests(unittest.TestCase):
    def test_shim_blocks_and_allows(self) -> None:
        self.assertEqual(blocked_action(["push", "origin"]), "push")
        self.assertEqual(blocked_action(["-C", "/tmp", "push"]), "push")
        self.assertEqual(blocked_action(["switch", "main"]), "switch")
        self.assertEqual(blocked_action(["checkout", "main"]), "checkout")
        self.assertEqual(blocked_action(["branch", "-D", "old"]), "branch -D")
        self.assertEqual(blocked_action(["branch", "-d", "old"]), "branch -d")
        self.assertEqual(blocked_action(["reset", "--hard"]), "reset --hard")
        self.assertEqual(blocked_action(["rebase", "main"]), "rebase")
        self.assertEqual(blocked_action(["worktree", "list"]), "worktree")
        self.assertIsNone(blocked_action(["status"]))
        self.assertIsNone(blocked_action(["add", "-A"]))
        self.assertIsNone(blocked_action(["commit", "-m", "hi"]))
        self.assertIsNone(blocked_action(["-c", "user.name=A", "status"]))

    def test_shim_script_respects_the_variable(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            write_shims(env.board)
            shim = env.board / "bin" / "shims" / ("git.cmd" if os.name == "nt" else "git")
            self.assertTrue(shim.is_file())
            if os.name == "nt":
                return
            env_on = dict(os.environ)
            env_on["AGENTCOOP_UNATTENDED"] = "1"
            blocked = subprocess.run([str(shim), "push"], capture_output=True, text=True, env=env_on)
            self.assertEqual(blocked.returncode, 1)
            self.assertIn("push is blocked", blocked.stderr)
            allowed = subprocess.run([str(shim), "status"], capture_output=True, text=True, env=env_on)
            self.assertEqual(allowed.returncode, 0)
            env_off = dict(os.environ)
            env_off.pop("AGENTCOOP_UNATTENDED", None)
            plain = subprocess.run(
                [str(shim), "status", "--porcelain"],
                capture_output=True,
                text=True,
                env=env_off,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            self.assertEqual(plain.returncode, 0)

    def test_start_stop_and_failed_scheduler(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            calls = []

            def runner(argv, capture_output=True, text=True):
                calls.append(list(argv))
                return Completed(0)

            with patch("agentcoop.rounds.sys.platform", "darwin"):
                start_schedule(env.board, 5, runner=runner)
            record = load_schedule(env.board)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record["interval"], 5)
            self.assertTrue(any("launchctl" in part for part in calls[0]))
            calls.clear()
            with patch("agentcoop.rounds.sys.platform", "darwin"):
                start_schedule(env.board, 9, runner=runner)
            self.assertEqual(load_schedule(env.board)["interval"], 9)
            with patch("agentcoop.rounds.sys.platform", "darwin"):
                stop_schedule(env.board, runner=runner)
            self.assertIsNone(load_schedule(env.board))

            def fail(argv, capture_output=True, text=True):
                return Completed(1)

            with patch("agentcoop.rounds.sys.platform", "darwin"):
                with self.assertRaises(Exception):
                    start_schedule(env.board, 5, runner=fail)
            self.assertIsNone(load_schedule(env.board))

    def test_setup_schedule_flag(self) -> None:
        with support.TempEnv() as env:
            code = main(["setup", "--connect", "cursor", "--schedule", "no", "--yes"])
            self.assertEqual(code, 0)
            self.assertIsNone(load_schedule(env.board))
            calls = []

            def runner(argv, capture_output=True, text=True):
                calls.append(argv)
                return Completed(0)

            with patch("agentcoop.rounds.subprocess.run", side_effect=runner):
                with patch("agentcoop.rounds.sys.platform", "darwin"):
                    code = main(["setup", "--schedule", "yes", "--every", "4", "--yes"])
            self.assertEqual(code, 0)
            self.assertEqual(load_schedule(env.board)["interval"], 4)

    def test_commands_work_without_a_terminal(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            self.assertEqual(main(["status"]), 0)
            code = main(["draft", "--as", "cursor", "--name", "a.md", "--body", "hi"])
            self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
