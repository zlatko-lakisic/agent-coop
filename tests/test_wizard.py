"""Tests for setup and project commands."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import support

from agentcoop.cli import main


class WizardTests(unittest.TestCase):
    def test_setup_is_idempotent(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "claude-code", "--yes"]), 0)
            keep = env.board / "keep.txt"
            keep.write_text("keep", encoding="utf-8")
            projects = (env.board / "projects.json").read_bytes()
            self.assertEqual(main(["setup", "--connect", "claude-code", "--yes"]), 0)
            self.assertEqual(keep.read_text(encoding="utf-8"), "keep")
            self.assertEqual((env.board / "projects.json").read_bytes(), projects)
            self.assertTrue((env.board / "bin" / "VERSION").is_file())

    def test_project_add_change_driver_and_remove(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor,claude-code", "--yes"]), 0)
            folder = env.workspace / "frontend"
            folder.mkdir()
            self.assertEqual(
                main(
                    [
                        "project",
                        "add",
                        str(folder),
                        "--name",
                        "frontend",
                        "--driver",
                        "cursor",
                        "--helper",
                        "claude_code=reviews code",
                        "--gitignore",
                        "yes",
                        "--lock-helpers",
                        "yes",
                        "--yes",
                    ]
                ),
                0,
            )
            rule = folder / ".cursor" / "rules" / "agent-coop.mdc"
            self.assertIn("cursor", rule.read_text(encoding="utf-8"))
            self.assertIn(".cursor/rules/agent-coop.mdc", (folder / ".gitignore").read_text(encoding="utf-8"))
            previous = os.getcwd()
            try:
                os.chdir(folder)
                self.assertEqual(
                    main(
                        [
                            "send",
                            "--as",
                            "cursor",
                            "--to",
                            "claude_code",
                            "--subject",
                            "Look",
                            "--body",
                            "please",
                            "--needs-reply",
                        ]
                    ),
                    0,
                )
                self.assertEqual(main(["project", "driver", "frontend", "claude_code", "--yes"]), 0)
            finally:
                os.chdir(previous)
            self.assertFalse(rule.exists())
            self.assertTrue((folder / ".claude" / "skills" / "agent-coop" / "SKILL.md").is_file())
            inbox = env.board / "projects" / "frontend" / "inbox" / "claude_code" / "msgs"
            bodies = [json.loads(path.read_text(encoding="utf-8"))["body"] for path in inbox.glob("*.json")]
            self.assertTrue(any("Driver changed to claude_code" in body for body in bodies))
            self.assertEqual(main(["project", "remove", "frontend", "--yes"]), 0)
            self.assertFalse((env.board / "projects" / "frontend").exists())
            self.assertTrue(any(Path(env.board / "removed").iterdir()))
            self.assertFalse((folder / ".claude" / "skills" / "agent-coop" / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
