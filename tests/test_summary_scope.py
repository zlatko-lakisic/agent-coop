"""Tests for silent summary checks and one install copy per agent."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.doctor import format_doctor, run_doctor
from agentcoop.install import connect_agent
from agentcoop.projects import add_project, init_board_files, load_projects, record_installed, save_projects


class SummaryScopeTests(unittest.TestCase):
    def test_summary_silent_outside_project(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "claude-code,cursor", "--yes"]), 0)
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
            previous = os.getcwd()
            try:
                os.chdir(env.home)
                out = io.StringIO()
                err = io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(["check", "--as", "claude_code", "--summary"])
                self.assertEqual(code, 0)
                self.assertEqual(out.getvalue(), "")
                self.assertEqual(err.getvalue(), "")
            finally:
                os.chdir(previous)

    def test_summary_silent_without_board(self) -> None:
        with support.TempEnv() as env:
            missing = env.root / "no-board"
            os.environ["AGENTCOOP_BOARD"] = str(missing)
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["check", "--as", "claude_code", "--summary"])
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(err.getvalue(), "")

    def test_summary_silent_when_not_a_member(self) -> None:
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
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                ),
                0,
            )
            previous = os.getcwd()
            try:
                os.chdir(folder)
                out = io.StringIO()
                err = io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(["check", "--as", "claude_code", "--summary"])
                self.assertEqual(code, 0)
                self.assertEqual(out.getvalue(), "")
                self.assertEqual(err.getvalue(), "")
            finally:
                os.chdir(previous)

    def test_summary_still_needs_identity(self) -> None:
        with support.TempEnv() as env:
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(["check", "--summary"])
            self.assertEqual(code, 2)

    def test_cursor_project_installs_one_rule(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor", "--yes"]), 0)
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
                        "--gitignore",
                        "yes",
                        "--yes",
                    ]
                ),
                0,
            )
            rule = folder / ".cursor" / "rules" / "agent-coop.mdc"
            self.assertTrue(rule.is_file())
            self.assertFalse((folder / ".cursor" / "skills").exists())
            installed = load_projects(env.board)["projects"]["demo"]["installed"]
            kinds = [entry.get("kind") for entry in installed]
            self.assertEqual(kinds.count("rule"), 1)
            self.assertIn("gitignore", kinds)

    def test_claude_driver_installs_no_project_files(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "claude-code", "--yes"]), 0)
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
                        "claude_code",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                ),
                0,
            )
            self.assertFalse((folder / ".claude").exists())
            settings = env.home / ".claude" / "settings.json"
            data = json.loads(settings.read_text(encoding="utf-8"))
            session = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            prompt = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
            self.assertIn("check --as claude_code --summary", session)
            self.assertIn("touch --as claude_code", prompt)

    def test_doctor_reports_duplicate_skill(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent(env.board, "claude-code", yes=True)
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "claude_code", {})
            project_skill = folder / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            project_skill.parent.mkdir(parents=True)
            project_skill.write_text("duplicate\n", encoding="utf-8")
            projects = load_projects(env.board)
            from agentcoop.install import file_sha256

            record_installed(
                projects["projects"]["demo"].setdefault("installed", []),
                {
                    "path": str(project_skill),
                    "agent": "claude_code",
                    "kind": "skill",
                    "sha256": file_sha256(project_skill),
                    "created": True,
                },
            )
            save_projects(env.board, projects)
            text = format_doctor(run_doctor(env.board))
            self.assertIn("two copies of the Agent Co-op instructions", text)
            self.assertIn("agentcoop project repair demo", text)


if __name__ == "__main__":
    unittest.main()
