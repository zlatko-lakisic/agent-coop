"""Tests for the A4 small fixes."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

import support

from agentcoop.cli import main
from agentcoop.core import send
from agentcoop.projects import load_projects


class SmallFixTests(unittest.TestCase):
    def test_folder_contains_project_message(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor", "--yes"]), 0)
            child = env.workspace / "parent" / "child"
            child.mkdir(parents=True)
            self.assertEqual(
                main(
                    [
                        "project",
                        "add",
                        str(child),
                        "--name",
                        "child",
                        "--driver",
                        "cursor",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                ),
                0,
            )
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "project",
                        "add",
                        str(env.workspace / "parent"),
                        "--name",
                        "parent",
                        "--driver",
                        "cursor",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn(
                "This folder contains the child project. Projects cannot be inside each other.",
                err.getvalue(),
            )

    def test_runner_helper_text(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor", "--yes"]), 0)
            self.assertEqual(
                main(["connect", "ollama", "--model", "llava", "--as", "ollama_llava", "--yes"]),
                0,
            )
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
            out = io.StringIO()
            with support.person("demo"), redirect_stdout(out):
                code = main(
                    [
                        "project",
                        "helper",
                        "add",
                        "demo",
                        "ollama_llava",
                        "--note",
                        "checks screenshots",
                    ]
                )
            self.assertEqual(code, 0)
            text = out.getvalue()
            self.assertIn(
                "ollama_llava is a local model runner. It only answers questions and cannot edit files.",
                text,
            )
            self.assertNotIn("skill", text.lower())

    def test_send_returns_only_the_id(self) -> None:
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
            mid = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="hi",
                body="hi",
            )
            self.assertIsInstance(mid, str)
            self.assertTrue(mid)


if __name__ == "__main__":
    unittest.main()
