"""Tests for doctor."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

import support

from agentcoop.cli import main


class DoctorTests(unittest.TestCase):
    def test_deleted_skill_and_integrity(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "claude-code", "--yes"]), 0)
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
                        "claude_code",
                        "--yes",
                    ]
                ),
                0,
            )
            skill = env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            skill.unlink()
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["doctor"])
            self.assertEqual(code, 1)
            self.assertIn("agentcoop connect claude-code", out.getvalue())
            path = env.board / "projects.json"
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["doctor", "--strict"])
            self.assertEqual(code, 5)
            self.assertIn("changed outside agentcoop", out.getvalue())

    def test_hello_status(self) -> None:
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
                        "--yes",
                    ]
                ),
                0,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                main(["doctor", "--project", "frontend"])
            self.assertIn("No hello test yet", out.getvalue())
            self.assertEqual(
                main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--project",
                        "frontend",
                        "--to",
                        "claude_code",
                        "--subject",
                        "Hello",
                        "--body",
                        "Say hello",
                    ]
                ),
                0,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                main(["doctor", "--project", "frontend"])
            text = out.getvalue()
            self.assertIn("sent to claude_code", text)
            self.assertIn("Still waiting: claude_code", text)


if __name__ == "__main__":
    unittest.main()
