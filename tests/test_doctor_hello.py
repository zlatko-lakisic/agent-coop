"""Tests for doctor levels, project filter, and the exact hello check."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.core import send
from agentcoop.doctor import format_doctor, run_doctor
from agentcoop.install import file_sha256, render_template
from agentcoop.projects import load_agents, mutate_agents


class DoctorHelloTests(unittest.TestCase):
    def test_project_hides_other_projects(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor,claude-code", "--yes"]), 0)
            for name in ("one", "two"):
                folder = env.workspace / name
                folder.mkdir()
                self.assertEqual(
                    main(
                        [
                            "project",
                            "add",
                            str(folder),
                            "--name",
                            name,
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
            items = run_doctor(env.board, project="one")
            text = format_doctor(items)
            self.assertIn("one", text)
            self.assertNotIn("two: folder exists", text)

    def test_notes_exit_0_and_fix_exit_1(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "claude-code", "--yes"]), 0)
            skill = env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
            items = run_doctor(env.board)
            self.assertTrue(any(item.level == "note" and "changed after" in item.text for item in items))
            # A note is not a fix, so it alone does not make doctor fail.
            self.assertFalse(
                any(item.level == "fix" and "changed after" in item.text for item in items)
            )
            (env.board / "bin" / "VERSION").unlink()
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["doctor"])
            self.assertEqual(code, 1)
            self.assertIn("Fix:", out.getvalue())
            self.assertIn("not on the board yet", out.getvalue())

    def test_unreadable_file_text(self) -> None:
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
            bad = env.board / "projects" / "demo" / "inbox" / "claude_code" / "msgs" / "broken.json"
            bad.write_text("{not-json", encoding="utf-8")
            import os
            import time

            old = time.time() - 60
            os.utime(bad, (old, old))
            items = run_doctor(env.board)
            text = format_doctor(items)
            self.assertIn("a message file could not be read and was skipped", text)
            self.assertIn(str(bad), text)
            self.assertNotIn("Run: agentcoop doctor", text)

    def test_hello_ignores_other_subjects(self) -> None:
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
            send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="hello there",
                body="not the hello test",
                needs_reply=True,
            )
            item = next(i for i in run_doctor(env.board, project="demo") if "hello test" in i.text)
            self.assertEqual(item.level, "fix")
            self.assertIn("No hello test yet", item.text)

    def test_templates_include_hello_lines(self) -> None:
        with support.TempEnv() as env:
            init = env.board
            from agentcoop.projects import init_board_files
            from agentcoop.install import stage_runtime

            init_board_files(init)
            stage_runtime(init)
            for name in (
                "SKILL.md.tmpl",
                "cursor-rule.mdc.tmpl",
                "AGENTS.md.tmpl",
                "GEMINI.md.tmpl",
            ):
                text = render_template(name, agent_name="cursor", board=init)
                self.assertIn(
                    'send each helper a message with --type question --needs-reply --subject "Agent Co-op hello"',
                    text,
                )
                self.assertIn(
                    'reply with --type ack and --subject "Re: Agent Co-op hello"',
                    text,
                )


if __name__ == "__main__":
    unittest.main()
