"""Tests for atomic project add and project repair."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import support

from agentcoop.cli import main
from agentcoop.errors import CoopError
from agentcoop.projects import load_projects
from agentcoop.wizard import add_project_flow


class ProjectAddTests(unittest.TestCase):
    def test_failed_install_rolls_back(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor", "--yes"]), 0)
            folder = env.workspace / "demo"
            folder.mkdir()

            def boom(*args, **kwargs):
                raise OSError("disk full")

            with mock.patch("agentcoop.install._write_content", side_effect=boom):
                with self.assertRaises(CoopError) as raised:
                    add_project_flow(
                        path=folder,
                        name="demo",
                        driver="cursor",
                        helpers={},
                        yes=True,
                        gitignore=False,
                    )
            self.assertTrue(str(raised.exception).startswith("Could not finish adding demo."))
            self.assertNotIn("demo", load_projects(env.board)["projects"])
            self.assertFalse((folder / ".cursor").exists())
            self.assertFalse((env.board / "projects" / "demo").exists())

    def test_doctor_and_repair_missing_driver_files(self) -> None:
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
                        "no",
                        "--yes",
                    ]
                ),
                0,
            )
            rule = folder / ".cursor" / "rules" / "agent-coop.mdc"
            self.assertTrue(rule.is_file())
            rule.unlink()
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["doctor"])
            self.assertEqual(code, 1)
            self.assertIn("demo: cursor is missing its project files", out.getvalue())
            self.assertIn("agentcoop project repair demo", out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["project", "repair", "demo"]), 0)
            self.assertIn(str(rule), out.getvalue())
            self.assertTrue(rule.is_file())
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["doctor"])
            self.assertNotIn("missing its project files", out.getvalue())


if __name__ == "__main__":
    unittest.main()
