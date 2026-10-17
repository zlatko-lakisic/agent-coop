"""Git worktrees for scheduled driver runs."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import support

from agentcoop.worktree import finish_worktree, prepare_worktree, remove_worktree


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


if __name__ == "__main__":
    unittest.main()
