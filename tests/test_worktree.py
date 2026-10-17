"""Git worktrees for scheduled driver runs."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.projects import load_agents, save_agents
from agentcoop.rounds import format_status, run_agent
from agentcoop.worktree import current_branch, finish_worktree, prepare_worktree, remove_worktree, worktree_path


def _git(folder: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True)


def _repo(path: Path) -> None:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "person@localhost")
    _git(path, "config", "user.name", "Person")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "start")


class WorktreeTests(unittest.TestCase):
    def test_worktree_is_created_once_and_keeps_the_person_checkout(self) -> None:
        with support.TempEnv() as env:
            from agentcoop.projects import init_board_files

            init_board_files(env.board)
            repo = env.workspace / "demo"
            _repo(repo)
            branch = subprocess.run(
                ["git", "-C", str(repo), "branch", "--show-current"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            dest, person, note = prepare_worktree(env.board, "demo", repo)
            self.assertEqual(person, branch)
            self.assertEqual(note, "")
            self.assertTrue((dest / "README.md").is_file())
            again, _person, _note = prepare_worktree(env.board, "demo", repo)
            self.assertEqual(again, dest)
            (repo / "README.md").write_text("changed by the person\n", encoding="utf-8")
            head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            (dest / "NOTES.md").write_text("from the run\n", encoding="utf-8")
            finish_worktree(dest)
            after = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(head, after)
            self.assertIn("changed by the person", (repo / "README.md").read_text(encoding="utf-8"))
            log = subprocess.run(
                ["git", "-C", str(dest), "log", "-1", "--format=%s"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("Agent Co-op scheduled run", log)
            kept = remove_worktree(env.board, "demo", repo)
            self.assertIn("agentcoop/auto", kept or "")
            self.assertFalse(dest.exists())
            branches = subprocess.run(
                ["git", "-C", str(repo), "branch"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("agentcoop/auto", branches)

    def test_a_side_branch_is_put_back_on_the_auto_branch(self) -> None:
        with support.TempEnv() as env:
            from agentcoop.projects import init_board_files

            init_board_files(env.board)
            repo = env.workspace / "demo"
            _repo(repo)
            main(["setup", "--connect", "claude-code,cursor", "--schedule", "no", "--yes"])
            main(
                [
                    "project",
                    "add",
                    str(repo),
                    "--name",
                    "demo",
                    "--driver",
                    "claude_code",
                    "--helper",
                    "cursor=edits",
                    "--gitignore",
                    "no",
                    "--yes",
                ]
            )
            cli = env.workspace / "claude"
            cli.write_text("x", encoding="utf-8")
            agents = load_agents(env.board)
            agents["agents"]["claude_code"]["cli_path"] = str(cli)
            save_agents(env.board, agents)
            import io
            from contextlib import redirect_stdout

            sent = io.StringIO()
            with redirect_stdout(sent):
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
                        "cursor",
                        "--project",
                        "demo",
                        "--to",
                        "claude_code",
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

            def launch(argv, **kwargs):
                cwd = kwargs["cwd"]
                subprocess.run(["git", "-C", cwd, "checkout", "-b", "sneaky"], check=True, capture_output=True)
                (Path(cwd) / "NEW.txt").write_text("from the run\n", encoding="utf-8")

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

            self.assertEqual(run_agent(env.board, "claude_code", "demo", popen=launch), 0)
            dest = worktree_path(env.board, "demo")
            self.assertEqual(current_branch(dest), "agentcoop/auto")
            text = format_status(env.board)
            self.assertIn("switched to the branch sneaky", text)
            self.assertIn("git diff agentcoop/auto...sneaky", text)
            log = list((env.board / "logs" / "claude_code").glob("*.log"))
            self.assertTrue(log)
            self.assertIn("sneaky", log[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
