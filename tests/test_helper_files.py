"""Tests for project instruction files installed for helpers."""

from __future__ import annotations

import unittest

import support

from agentcoop.cli import main
from agentcoop.install import install_project_files
from agentcoop.projects import add_project, init_board_files
from agentcoop.wizard import helper_add, helper_remove


class HelperFileTests(unittest.TestCase):
    def test_cursor_helper_gets_a_project_rule(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "claude-code,cursor", "--yes"]), 0)
            folder = env.workspace / "paper"
            folder.mkdir()
            self.assertEqual(
                main(
                    [
                        "project",
                        "add",
                        str(folder),
                        "--name",
                        "paper",
                        "--driver",
                        "claude_code",
                        "--helper",
                        "cursor=edits",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                ),
                0,
            )
            rule = folder / ".cursor" / "rules" / "agent-coop.mdc"
            self.assertTrue(rule.is_file())
            self.assertIn("cursor", rule.read_text(encoding="utf-8"))
            helper_remove("paper", "cursor")
            self.assertFalse(rule.exists())

    def test_edited_helper_rule_is_left_in_place(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "claude-code,cursor", "--yes"]), 0)
            folder = env.workspace / "paper"
            folder.mkdir()
            self.assertEqual(
                main(
                    [
                        "project",
                        "add",
                        str(folder),
                        "--name",
                        "paper",
                        "--driver",
                        "claude_code",
                        "--helper",
                        "cursor=edits",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                ),
                0,
            )
            rule = folder / ".cursor" / "rules" / "agent-coop.mdc"
            rule.write_text(rule.read_text(encoding="utf-8") + "\nextra\n", encoding="utf-8")
            text = helper_remove("paper", "cursor")
            self.assertTrue(rule.is_file())
            self.assertIn("Left", text)

    def test_two_agents_md_sections_stay_independent(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            self.assertEqual(main(["setup", "--connect", "copilot,opencode", "--yes"]), 0)
            folder = env.workspace / "paper"
            folder.mkdir()
            add_project(env.board, "paper", folder, "copilot", {"opencode": "local"})
            install_project_files(env.board, "paper", "copilot", yes=True)
            install_project_files(env.board, "paper", "opencode", yes=True)
            text = (folder / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("<!-- agentcoop:start copilot -->", text)
            self.assertIn("<!-- agentcoop:start opencode -->", text)
            helper_remove("paper", "opencode")
            text = (folder / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("<!-- agentcoop:start copilot -->", text)
            self.assertNotIn("opencode", text)


if __name__ == "__main__":
    unittest.main()
