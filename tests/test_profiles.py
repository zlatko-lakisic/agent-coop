"""Helper profiles: self-description, a person's confirmation, and the integrity hash."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.profiles import profile_of
from agentcoop.projects import integrity_ok, load_projects


def _quiet(argv: list[str]) -> str:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = main(argv)
    if code != 0:
        raise AssertionError(out.getvalue())
    return out.getvalue()


def _ready(env: support.TempEnv) -> Path:
    _quiet(["setup", "--connect", "cursor,claude-code", "--schedule", "no", "--yes"])
    folder = env.workspace / "demo"
    folder.mkdir()
    _quiet(
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
    return folder


class ProfileTests(unittest.TestCase):
    def test_join_describes_the_helper_and_keeps_the_hash(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            text = _quiet(
                [
                    "join",
                    "--as",
                    "claude_code",
                    "--project",
                    "demo",
                    "--tags",
                    "code-review,reasoning",
                    "--can",
                    "read,run",
                    "--cost",
                    "low",
                ]
            )
            self.assertIn("joined", text)
            record = (load_projects(env.board).get("projects") or {})["demo"]
            profile = profile_of(record, "claude_code")
            self.assertEqual(profile["tags"], ["code-review", "reasoning"])
            self.assertEqual(profile["can"], ["read", "run"])
            self.assertEqual(profile["cost"], "low")
            self.assertIsInstance(record["helpers"]["claude_code"], str)
            self.assertTrue(integrity_ok(env.board))
            shown = _quiet(["profile", "--as", "cursor", "--project", "demo"])
            self.assertIn("code-review", shown)
            self.assertIn("cost low", shown)

    def test_changing_another_agent_needs_confirmation(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            _quiet(["join", "--as", "claude_code", "--project", "demo"])
            with support.person("nope"):
                code = main(
                    [
                        "profile",
                        "--as",
                        "cursor",
                        "--project",
                        "demo",
                        "claude_code",
                        "--tags",
                        "vision",
                    ]
                )
            self.assertNotEqual(code, 0)
            record = (load_projects(env.board).get("projects") or {})["demo"]
            self.assertEqual(profile_of(record, "claude_code")["tags"], [])
            with support.person("claude_code"):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(
                        main(
                            [
                                "profile",
                                "--as",
                                "cursor",
                                "--project",
                                "demo",
                                "claude_code",
                                "--tags",
                                "vision",
                                "--cost",
                                "high",
                            ]
                        ),
                        0,
                    )
            record = (load_projects(env.board).get("projects") or {})["demo"]
            self.assertEqual(profile_of(record, "claude_code")["tags"], ["vision"])
            self.assertEqual(profile_of(record, "claude_code")["cost"], "high")
            self.assertTrue(integrity_ok(env.board))
            raw = json.loads((env.board / "projects.json").read_text(encoding="utf-8"))
            self.assertIn("profiles", raw["projects"]["demo"])

    def test_a_join_request_can_carry_the_same_fields(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            _quiet(["connect", "claude-desktop", "--yes"])
            box = env.board / "projects" / "demo" / "requests" / "claude_desktop"
            box.mkdir(parents=True)
            (box / "join.json").write_text(
                json.dumps(
                    {
                        "action": "join",
                        "note": "sees pictures",
                        "tags": ["vision"],
                        "can": ["read", "vision"],
                        "cost": "medium",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            import os
            import time

            when = time.time() - 30
            os.utime(box / "join.json", (when, when))
            _quiet(["check", "--as", "cursor", "--project", "demo"])
            record = (load_projects(env.board).get("projects") or {})["demo"]
            profile = profile_of(record, "claude_desktop")
            self.assertEqual(profile["tags"], ["vision"])
            self.assertEqual(profile["can"], ["read", "vision"])
            self.assertEqual(profile["notes"], "sees pictures")
            self.assertTrue(integrity_ok(env.board))
