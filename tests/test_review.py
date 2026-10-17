"""Tests for projects.json integrity pause and project review."""

from __future__ import annotations

import hashlib
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

import support

from agentcoop.cli import main
from agentcoop.projects import integrity_ok, load_projects


def _tamper(board) -> None:
    path = board / "projects.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    # Keep structure valid but change a helper note so validation can still pass.
    for project in data.get("projects", {}).values():
        helpers = project.get("helpers") or {}
        if helpers:
            name = next(iter(helpers))
            helpers[name] = "hand edited note"
            break
        else:
            project["helpers"] = {}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class ReviewTests(unittest.TestCase):
    def test_helper_add_paused_after_tamper(self) -> None:
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
                        "--yes",
                    ]
                ),
                0,
            )
            before_hash = (env.board / ".state" / "projects.sha256").read_text(encoding="utf-8")
            _tamper(env.board)
            self.assertFalse(integrity_ok(env.board))
            err = io.StringIO()
            with support.person("demo"), redirect_stderr(err):
                code = main(["project", "helper", "add", "demo", "claude_code", "--note", "new"])
            self.assertEqual(code, 5)
            self.assertIn("role changes are paused", err.getvalue())
            self.assertIn("agentcoop project review", err.getvalue())
            self.assertEqual(
                (env.board / ".state" / "projects.sha256").read_text(encoding="utf-8"),
                before_hash,
            )
            note = load_projects(env.board)["projects"]["demo"]["helpers"]["claude_code"]
            self.assertEqual(note, "hand edited note")

    def test_review_restore(self) -> None:
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
                        "--yes",
                    ]
                ),
                0,
            )
            _tamper(env.board)
            out = io.StringIO()
            with support.person("review"), redirect_stdout(out):
                self.assertEqual(main(["project", "review", "--restore"]), 0)
            self.assertIn("Restored", out.getvalue())
            self.assertTrue(integrity_ok(env.board))
            note = load_projects(env.board)["projects"]["demo"]["helpers"]["claude_code"]
            self.assertEqual(note, "reviews")

    def test_review_accept_valid(self) -> None:
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
                        "--yes",
                    ]
                ),
                0,
            )
            _tamper(env.board)
            out = io.StringIO()
            with support.person("review"), redirect_stdout(out):
                self.assertEqual(main(["project", "review", "--accept"]), 0)
            self.assertIn("Accepted", out.getvalue())
            self.assertTrue(integrity_ok(env.board))
            note = load_projects(env.board)["projects"]["demo"]["helpers"]["claude_code"]
            self.assertEqual(note, "hand edited note")

    def test_review_accept_invalid(self) -> None:
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
                        "--yes",
                    ]
                ),
                0,
            )
            path = env.board / "projects.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["projects"]["demo"]["driver"] = "missing_agent"
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            err = io.StringIO()
            with support.person("review"), redirect_stderr(err):
                code = main(["project", "review", "--accept"])
            self.assertEqual(code, 1)
            self.assertFalse(integrity_ok(env.board))

    def test_review_nothing_to_do(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--yes"]), 0)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["project", "review"]), 0)
            self.assertIn("Nothing to review", out.getvalue())

    def test_doctor_mentions_project_review(self) -> None:
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
                        "--yes",
                    ]
                ),
                0,
            )
            path = env.board / "projects.json"
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                main(["doctor"])
            self.assertIn("project review", out.getvalue())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertNotEqual(
                digest,
                (env.board / ".state" / "projects.sha256").read_text(encoding="utf-8").strip(),
            )


if __name__ == "__main__":
    unittest.main()
