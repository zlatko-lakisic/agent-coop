"""Tests for connect refusing before it changes anything."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.doctor import format_doctor, run_doctor


class ConnectAtomicTests(unittest.TestCase):
    def test_user_owned_skill_is_left_alone(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--yes"]), 0)
            skill = env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("mine\n", encoding="utf-8")
            before = (env.board / "agents.json").read_bytes()
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(["connect", "claude-code"])
            self.assertEqual(code, 1)
            self.assertEqual((env.board / "agents.json").read_bytes(), before)
            self.assertEqual(skill.read_text(encoding="utf-8"), "mine\n")
            self.assertNotIn("claude_code", (env.board / "agents.json").read_text(encoding="utf-8"))

    def test_yes_keeps_the_old_file_in_board_backups(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--yes"]), 0)
            skill = env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("mine\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["connect", "claude-code", "--yes"]), 0)
            self.assertIn("Agent Co-op", skill.read_text(encoding="utf-8"))
            backups = list((env.board / ".state" / "backups").rglob("SKILL.md.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "mine\n")

    def test_a_failed_write_leaves_nothing(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--yes"]), 0)
            before = (env.board / "agents.json").read_bytes()
            skill = env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            original = Path.write_text

            def fail_settings(self, text, *args, **kwargs):  # type: ignore[no-untyped-def]
                if self.name == "settings.json":
                    raise OSError("disk full")
                return original(self, text, *args, **kwargs)

            err = io.StringIO()
            with patch.object(Path, "write_text", fail_settings), redirect_stderr(err):
                code = main(["connect", "claude-code", "--yes"])
            self.assertEqual(code, 1)
            self.assertIn("Nothing was changed", err.getvalue())
            self.assertFalse(skill.exists())
            self.assertFalse((env.home / ".claude" / "settings.json").exists())
            self.assertEqual((env.board / "agents.json").read_bytes(), before)

    def test_doctor_names_a_file_that_belongs_to_the_user(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "claude-code", "--yes"]), 0)
            skill = env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            skill.write_text("mine\n", encoding="utf-8")
            text = format_doctor(run_doctor(env.board))
            self.assertIn(
                str(skill) + " was changed after it was installed. Agent Co-op will leave it alone.",
                text,
            )


if __name__ == "__main__":
    unittest.main()
