"""Tests for send refusals and private board file modes."""

from __future__ import annotations

import io
import json
import os
import stat
import unittest
from contextlib import redirect_stderr

import support

from agentcoop.cli import main
from agentcoop.doctor import format_doctor, run_doctor


class SendGuardTests(unittest.TestCase):
    def test_send_refuses_an_agent_that_is_not_connected(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor,claude-code", "--yes"]), 0)
            folder = env.workspace / "demo"
            folder.mkdir()
            self.assertEqual(
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
                        "claude_code=reviews",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                ),
                0,
            )
            agents_path = env.board / "agents.json"
            agents = json.loads(agents_path.read_text(encoding="utf-8"))
            del agents["agents"]["claude_code"]
            agents_path.write_text(json.dumps(agents, indent=2) + "\n", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--to",
                        "claude_code",
                        "--subject",
                        "hi",
                        "--body",
                        "hi",
                        "--project",
                        "demo",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("not connected", err.getvalue())

    def test_send_refuses_tampered_roles(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor,claude-code", "--yes"]), 0)
            folder = env.workspace / "demo"
            folder.mkdir()
            self.assertEqual(
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
                        "claude_code=reviews",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                ),
                0,
            )
            path = env.board / "projects.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["projects"]["demo"]["helpers"]["claude_code"] = "changed"
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--to",
                        "claude_code",
                        "--subject",
                        "hi",
                        "--body",
                        "hi",
                        "--project",
                        "demo",
                    ]
                )
            self.assertEqual(code, 5)
            self.assertIn("agentcoop project review", err.getvalue())


@unittest.skipIf(os.name == "nt", "POSIX file modes")
class BoardModeTests(unittest.TestCase):
    def test_setup_makes_the_board_private(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor,claude-code", "--yes"]), 0)
            folder = env.workspace / "demo"
            folder.mkdir()
            self.assertEqual(
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
                        "claude_code=reviews",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                ),
                0,
            )
            self.assertEqual(stat.S_IMODE(env.board.stat().st_mode), 0o700)
            code = main(
                [
                    "send",
                    "--as",
                    "cursor",
                    "--to",
                    "claude_code",
                    "--subject",
                    "hi",
                    "--body",
                    "hi",
                    "--project",
                    "demo",
                ]
            )
            self.assertEqual(code, 0)
            messages = list((env.board / "projects" / "demo" / "inbox" / "claude_code" / "msgs").glob("*.json"))
            self.assertEqual(len(messages), 1)
            self.assertEqual(stat.S_IMODE(messages[0].stat().st_mode), 0o600)

    def test_doctor_reports_a_loose_board_and_setup_fixes_it(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor", "--yes"]), 0)
            os.chmod(env.board, 0o755)
            text = format_doctor(run_doctor(env.board))
            self.assertIn(
                "Fix: other accounts on this computer can read the board. Run: agentcoop setup",
                text,
            )
            self.assertEqual(main(["setup", "--yes"]), 0)
            text = format_doctor(run_doctor(env.board))
            self.assertNotIn("other accounts on this computer can read the board", text)


if __name__ == "__main__":
    unittest.main()
