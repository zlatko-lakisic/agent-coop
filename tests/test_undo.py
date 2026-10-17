"""Tests for undo leaving edited files and keeping user gitignore lines."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

import support

from agentcoop.cli import main


def _add(folder, *, gitignore: str = "yes") -> None:
    main(["setup", "--connect", "cursor", "--yes"])
    code = main(
        [
            "project",
            "add",
            str(folder),
            "--name",
            "demo",
            "--driver",
            "cursor",
            "--gitignore",
            gitignore,
            "--yes",
        ]
    )
    if code != 0:
        raise AssertionError("project add failed")


class UndoTests(unittest.TestCase):
    def test_edited_rule_is_left_in_place(self) -> None:
        with support.TempEnv() as env:
            folder = env.workspace / "demo"
            folder.mkdir()
            _add(folder, gitignore="no")
            rule = folder / ".cursor" / "rules" / "agent-coop.mdc"
            rule.write_text(rule.read_text(encoding="utf-8") + "\nextra line\n", encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["project", "remove", "demo", "--yes"])
            self.assertEqual(code, 0)
            self.assertTrue(rule.is_file())
            self.assertIn("Left", out.getvalue())

    def test_unedited_rule_is_deleted(self) -> None:
        with support.TempEnv() as env:
            folder = env.workspace / "demo"
            folder.mkdir()
            _add(folder, gitignore="no")
            rule = folder / ".cursor" / "rules" / "agent-coop.mdc"
            self.assertTrue(rule.is_file())
            self.assertEqual(main(["project", "remove", "demo", "--yes"]), 0)
            self.assertFalse(rule.exists())

    def test_existing_gitignore_line_survives_remove(self) -> None:
        with support.TempEnv() as env:
            folder = env.workspace / "demo"
            folder.mkdir()
            (folder / ".git").mkdir()
            ignore = folder / ".gitignore"
            ignore.write_text(".cursor/rules/agent-coop.mdc\n", encoding="utf-8")
            _add(folder, gitignore="yes")
            self.assertEqual(main(["project", "remove", "demo", "--yes"]), 0)
            self.assertIn(".cursor/rules/agent-coop.mdc", ignore.read_text(encoding="utf-8"))

    def test_backup_is_not_left_in_the_project(self) -> None:
        with support.TempEnv() as env:
            folder = env.workspace / "demo"
            folder.mkdir()
            (folder / ".git").mkdir()
            (folder / ".gitignore").write_text("*.log\n", encoding="utf-8")
            _add(folder, gitignore="yes")
            leftovers = list(folder.rglob("*agentcoop-backup*"))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
