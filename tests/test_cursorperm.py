"""Cursor permission file and the unscheduled driver."""

from __future__ import annotations

import json
import os
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
            self.assertIn("Shell(agentcoop)", allow)
            self.assertIn("Shell(npm)", allow)
            self.assertIn("Shell(pytest)", allow)
            self.assertIn("Write(**/.env)", deny)
            self.assertIn("Write(" + str(env.board.resolve()) + "/projects.json)", deny)
            self.assertIn("Write(" + str(env.board.resolve()) + "/.state/**)", deny)
            self.assertIn("Write(" + str(env.board.resolve()) + "/bin/**)", deny)
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
        from agentcoop.projects import load_agents, save_agents
        from agentcoop.rounds import run_agent, write_shims
        from agentcoop.schedule import build_argv
        from agentcoop.worktree import current_branch, worktree_path

        tool = get_tool("cursor")
        driver = tool.headless["driver"]
        helper = tool.headless["helper"]
        self.assertIn("--trust", driver)
        self.assertIn("--trust", helper)
        for flag in ("--force", "-f", "--yolo"):
            self.assertNotIn(flag, driver)
            self.assertNotIn(flag, helper)
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
            main(["setup", "--connect", "cursor,claude-code", "--schedule", "no", "--yes"])
            main(
                [
                    "project",
                    "add",
                    str(repo),
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
            )
            cli = env.workspace / "agent"
            cli.write_text("x", encoding="utf-8")
            agents = load_agents(env.board)
            agents["agents"]["cursor"]["cli_path"] = str(cli)
            claude = env.workspace / "claude"
            claude.write_text("x", encoding="utf-8")
            agents["agents"]["claude_code"]["cli_path"] = str(claude)
            save_agents(env.board, agents)
            text = format_status(env.board)
            self.assertNotIn("cannot be scheduled as the driver", text)
            argv = build_argv(
                env.board,
                "cursor",
                role="driver",
                prompt="go",
                cwd=repo,
                project_path=repo,
            )
            self.assertEqual(argv[0], str(cli))
            self.assertIn("--trust", argv)
            self.assertIn("--workspace", argv)
            for flag in ("--force", "-f", "--yolo"):
                self.assertNotIn(flag, argv)
            import io
            from contextlib import redirect_stdout

            sent = io.StringIO()
            with redirect_stdout(sent):
                self.assertEqual(
                    main(
                        [
                            "send",
                            "--as",
                            "cursor",
                            "--project",
                            "demo",
                            "--to",
                            "claude_code",
                            "--subject",
                            "Go",
                            "--body",
                            "start",
                        ]
                    ),
                    0,
                )
            mid = sent.getvalue().strip().splitlines()[-1]
            self.assertEqual(
                main(
                    [
                        "send",
                        "--as",
                        "claude_code",
                        "--project",
                        "demo",
                        "--to",
                        "cursor",
                        "--re",
                        mid,
                        "--type",
                        "answer",
                        "--subject",
                        "Re: Go",
                        "--body",
                        "done",
                    ]
                ),
                0,
            )
            write_shims(env.board)
            seen = {}

            def launch(argv, **kwargs):
                seen["argv"] = argv
                seen["env"] = kwargs["env"]
                import shutil

                git_bin = shutil.which("git", path=kwargs["env"]["PATH"])
                seen["git"] = git_bin or ""
                pushed = subprocess.run(
                    [git_bin or "git", "push"],
                    cwd=kwargs["cwd"],
                    env=kwargs["env"],
                    capture_output=True,
                    text=True,
                )
                seen["push"] = (pushed.returncode, (pushed.stderr or "") + (pushed.stdout or ""))

                class Proc:
                    returncode = 0
                    pid = 424242
                    stdout = None

                    def communicate(self, timeout=None):
                        return "done\n", ""

                    def kill(self):
                        return None

                    def wait(self, timeout=None):
                        return 0

                return Proc()

            self.assertEqual(run_agent(env.board, "cursor", "demo", popen=launch), 0)
            dest = worktree_path(env.board, "demo")
            self.assertEqual(current_branch(dest), "agentcoop/auto")
            self.assertIn("shims", seen["git"].replace("\\", "/"))
            self.assertNotEqual(seen["push"][0], 0)
            self.assertIn("blocked", seen["push"][1])
            parts = seen["env"]["PATH"].split(os.pathsep)
            self.assertEqual(parts[0], str(env.board / "bin" / "shims"))
            self.assertEqual(parts[1], str(env.board / "bin"))
            prompt = seen["argv"][-1]
            self.assertIn("agentcoop check", prompt)
            self.assertNotIn(str(env.board / "bin"), prompt)
            data = json.loads((dest / ".cursor" / "cli.json").read_text(encoding="utf-8"))
            self.assertEqual(
                data["permissions"]["allow"][:5],
                [
                    "Read(**)",
                    "Write(" + str(dest.resolve()) + "/**)",
                    "Write(" + str(env.board.resolve()) + "/**)",
                    "Shell(git)",
                    "Shell(agentcoop)",
                ],
            )
            self.assertIn(
                "Write(" + str(env.board.resolve()) + "/bin/**)",
                data["permissions"]["deny"],
            )
            helper_dir = env.board / "projects" / "demo"
            from agentcoop.cursorperm import write_cursor_helper_cli

            helper_path = write_cursor_helper_cli(helper_dir)
            helper = json.loads(helper_path.read_text(encoding="utf-8"))
            self.assertEqual(helper["permissions"]["allow"], ["Read(**)"])
            self.assertEqual(helper["permissions"]["deny"], ["Write(**)", "Shell(*)"])


if __name__ == "__main__":
    unittest.main()
