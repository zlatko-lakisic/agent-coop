"""Tests that the installed zipapp can load templates and run commands."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

import support

from agentcoop.install import build_zipapp, read_template


def _clean_env(home: Path, board: Path) -> dict:
    """Environment for running the zipapp without the source tree on the path."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "AGENTCOOP_BOARD", "AGENTCOOP_AGENT")
    }
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["AGENTCOOP_BOARD"] = str(board)
    return env


class ZipappTests(unittest.TestCase):
    def test_read_template_from_source(self) -> None:
        skill = read_template("SKILL.md.tmpl")
        self.assertIn("Agent Co-op", skill)
        hooks = read_template("hooks/claude-code.json.tmpl")
        self.assertIn("SessionStart", hooks)
        html = read_template("ui.html")
        self.assertIn("Agent Co-op", html)

    def test_zipapp_loads_templates(self) -> None:
        with support.TempEnv() as env:
            pyz = env.root / "agentcoop.pyz"
            build_zipapp(pyz)
            script = (
                "import sys\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "from agentcoop.install import read_template\n"
                "print(read_template('hooks/claude-code.json.tmpl')[:20])\n"
                "print(read_template('ui.html')[:20])\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", script, str(pyz)],
                capture_output=True,
                text=True,
                env=_clean_env(env.home, env.board),
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("{", result.stdout)
            self.assertIn("<!", result.stdout)

    def test_zipapp_setup_project_send_check(self) -> None:
        with support.TempEnv() as env:
            pyz = env.root / "agentcoop.pyz"
            build_zipapp(pyz)
            project = env.workspace / "demo"
            project.mkdir()
            env_vars = _clean_env(env.home, env.board)
            commands = [
                ["setup", "--connect", "claude-code,cursor", "--yes"],
                [
                    "project",
                    "add",
                    str(project),
                    "--name",
                    "demo",
                    "--driver",
                    "cursor",
                    "--helper",
                    "claude_code=reviews",
                    "--lock-helpers",
                    "no",
                    "--gitignore",
                    "no",
                    "--yes",
                ],
                [
                    "send",
                    "--as",
                    "cursor",
                    "--to",
                    "claude_code",
                    "--subject",
                    "hi",
                    "--body",
                    "hi",
                    "--project",
                    "demo",
                ],
                ["check", "--as", "claude_code", "--project", "demo"],
            ]
            for args in commands:
                result = subprocess.run(
                    [sys.executable, str(pyz), *args],
                    capture_output=True,
                    text=True,
                    env=env_vars,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    "command failed: "
                    + " ".join(args)
                    + "\nstdout:\n"
                    + result.stdout
                    + "\nstderr:\n"
                    + result.stderr,
                )
            rule = project / ".cursor" / "rules" / "agent-coop.mdc"
            self.assertTrue(rule.is_file(), "Cursor rule was not installed")
            skill = env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            self.assertTrue(skill.is_file(), "Claude Code skill was not installed")

    def test_zipapp_desktop_prepare_includes_the_skill(self) -> None:
        import zipfile

        with support.TempEnv() as env:
            pyz = env.root / "agentcoop.pyz"
            build_zipapp(pyz)
            env_vars = _clean_env(env.home, env.board)
            setup = subprocess.run(
                [sys.executable, str(pyz), "setup", "--yes"],
                capture_output=True,
                text=True,
                env=env_vars,
                check=False,
            )
            self.assertEqual(setup.returncode, 0, setup.stderr)
            result = subprocess.run(
                [sys.executable, str(pyz), "desktop", "prepare"],
                capture_output=True,
                text=True,
                env=env_vars,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            zips = list((env.board / "exports").glob("*.zip"))
            self.assertEqual(len(zips), 1)
            with zipfile.ZipFile(zips[0]) as archive:
                self.assertIn("agent-coop/SKILL.md", archive.namelist())
                body = archive.read("agent-coop/SKILL.md").decode("utf-8")
            self.assertIn("name: agent-coop", body)


if __name__ == "__main__":
    unittest.main()
