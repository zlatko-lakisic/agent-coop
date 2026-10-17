"""Cursor permission file and the unscheduled driver."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.cursorperm import write_cursor_cli
from agentcoop.projects import init_board_files, load_projects
from agentcoop.rounds import format_status
from agentcoop.tools import get_tool


def _git(folder: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True)


class CursorPermissionTests(unittest.TestCase):
    def test_cli_json_uses_absolute_paths_and_stays_out_of_git(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            repo = env.workspace / "demo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "person@localhost")
            _git(repo, "config", "user.name", "Person")
            (repo / "README.md").write_text("hi\n", encoding="utf-8")
            _git(repo, "add", "README.md")
            _git(repo, "commit", "-m", "start")
            main(["setup", "--connect", "cursor", "--schedule", "no", "--yes"])
            main(
                [
                    "project",
                    "add",
                    str(repo),
                    "--name",
                    "demo",
                    "--driver",
                    "cursor",
                    "--gitignore",
                    "no",
                    "--yes",
                ]
            )
            dest = env.board / "worktrees" / "demo"
            _git(repo, "worktree", "add", "-b", "agentcoop/auto", str(dest), "HEAD")
            path = write_cursor_cli(dest, env.board, ["npm", "pytest"])
            data = json.loads(path.read_text(encoding="utf-8"))
            allow = data["permissions"]["allow"]
            deny = data["permissions"]["deny"]
            self.assertIn("Read(**)", allow)
            self.assertIn("Write(" + str(dest.resolve()) + "/**)", allow)
            self.assertIn("Write(" + str(env.board.resolve()) + "/**)", allow)
            self.assertIn("Shell(git)", allow)
            self.assertIn("Shell(npm)", allow)
            self.assertIn("Shell(pytest)", allow)
            self.assertIn("Write(**/.env)", deny)
            self.assertIn("Write(" + str(env.board.resolve()) + "/projects.json)", deny)
            self.assertIn("Write(" + str(env.board.resolve()) + "/.state/**)", deny)
            check = subprocess.run(
                ["git", "-C", str(dest), "check-ignore", "-v", ".cursor/cli.json"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertIn(".cursor/cli.json", check.stdout)

    def test_driver_shell_needs_a_person(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            main(["setup", "--connect", "cursor", "--schedule", "no", "--yes"])
            folder = env.workspace / "demo"
            folder.mkdir()
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
            )
            import io
            from contextlib import redirect_stderr

            err = io.StringIO()
            with patch("sys.stdin.isatty", return_value=False), redirect_stderr(err):
                code = main(["project", "set", "demo", "--driver-shell", "npm,pytest"])
            self.assertEqual(code, 2)
            self.assertIn("a person has to confirm", err.getvalue())
            self.assertNotIn("driver_shell_allow", load_projects(env.board)["projects"]["demo"])

    def test_cursor_driver_argv_has_no_force(self) -> None:
        tool = get_tool("cursor")
        self.assertEqual(tool.headless["driver"], "todo")
        self.assertNotIn("--force", tool.headless["helper"])
        with support.TempEnv() as env:
            init_board_files(env.board)
            main(["setup", "--connect", "cursor", "--schedule", "no", "--yes"])
            folder = env.workspace / "demo"
            folder.mkdir()
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
            )
            from agentcoop.projects import load_agents, save_agents

            cli = env.workspace / "cursor-agent"
            cli.write_text("x", encoding="utf-8")
            agents = load_agents(env.board)
            agents["agents"]["cursor"]["cli_path"] = str(cli)
            save_agents(env.board, agents)
            text = format_status(env.board)
            self.assertIn(
                "Cursor cannot be scheduled as the driver yet, because its command-line agent "
                "cannot limit writes to the project copy and the board without asking.",
                text,
            )


if __name__ == "__main__":
    unittest.main()
