"""Existing-install detection and the desktop folder phrases."""

from __future__ import annotations

import json
import unittest

import support

from agentcoop.errors import CoopError
from agentcoop.projects import existing_install
from agentcoop.update import install_this

ROOT = support.ROOT

LINUX_INSTALL = (
    "Install Agent Co-op from github.com/zlatko-lakisic/agent-coop "
    "and connect my ~/.agent_board/projects folder"
)
WINDOWS_INSTALL = (
    "Install Agent Co-op from github.com/zlatko-lakisic/agent-coop "
    "and connect my %USERPROFILE%\\.agent_board\\projects folder"
)
LINUX_YES = "Yes, request access to ~/.agent_board/projects"
WINDOWS_YES = "Yes, request access to %USERPROFILE%\\.agent_board\\projects"

DESKTOP_SKILLS = (
    ROOT / "skills" / "agent-coop" / "SKILL.md",
    ROOT / "src" / "agentcoop" / "data" / "agent-coop" / "SKILL.md",
)


class ExistingInstallTests(unittest.TestCase):
    def test_empty_folder_is_not_installed(self) -> None:
        with support.TempEnv() as env:
            env.board.mkdir()
            self.assertFalse(existing_install(env.board))

    def test_projects_json_alone_is_installed(self) -> None:
        with support.TempEnv() as env:
            env.board.mkdir()
            (env.board / "projects.json").write_text("{}\n", encoding="utf-8")
            self.assertTrue(existing_install(env.board))

    def test_agents_json_alone_is_installed(self) -> None:
        with support.TempEnv() as env:
            env.board.mkdir()
            (env.board / "agents.json").write_text("{}\n", encoding="utf-8")
            self.assertTrue(existing_install(env.board))

    def test_version_file_alone_is_installed(self) -> None:
        with support.TempEnv() as env:
            bindir = env.board / "bin"
            bindir.mkdir(parents=True)
            (bindir / "VERSION").write_text("0.1.0\n", encoding="utf-8")
            self.assertTrue(existing_install(env.board))

    def test_config_json_alone_is_not_installed(self) -> None:
        with support.TempEnv() as env:
            env.board.mkdir()
            (env.board / "config.json").write_text("{}\n", encoding="utf-8")
            self.assertFalse(existing_install(env.board))

    def test_second_check_sees_the_other_agent(self) -> None:
        with support.TempEnv() as env:
            env.board.mkdir()
            self.assertFalse(existing_install(env.board))
            (env.board / "projects.json").write_text("{}\n", encoding="utf-8")
            self.assertTrue(existing_install(env.board))

    def test_install_this_fills_in_an_old_board(self) -> None:
        with support.TempEnv() as env:
            env.board.mkdir()
            (env.board / "agents.json").write_text(
                '{"version": 1, "agents": {}}\n',
                encoding="utf-8",
            )
            self.assertEqual(install_this(env.board), 0)
            agents = json.loads((env.board / "agents.json").read_text(encoding="utf-8"))
            self.assertEqual(agents.get("agents"), {})
            self.assertTrue((env.board / "projects.json").is_file())
            self.assertTrue((env.board / "config.json").is_file())
            self.assertTrue((env.board / "bin" / "VERSION").is_file())

    def test_install_this_refuses_a_missing_board(self) -> None:
        with support.TempEnv() as env:
            with self.assertRaises(CoopError) as caught:
                install_this(env.board)
            self.assertEqual(caught.exception.code, 3)
            self.assertFalse((env.board / "projects.json").exists())
            self.assertFalse((env.board / "agents.json").exists())


class PhraseTests(unittest.TestCase):
    def test_phrase_data_names_the_folder(self) -> None:
        raw = json.loads(
            (ROOT / "src" / "agentcoop" / "data" / "phrases.json").read_text(encoding="utf-8")
        )
        rows = [row for group in raw["groups"] for row in group["rows"]]
        by_say = {row["say"]: row for row in rows}
        install = by_say[LINUX_INSTALL]
        self.assertEqual(install["also"], WINDOWS_INSTALL)
        self.assertEqual(install["added"], "0.2.9")
        self.assertEqual(install["in"], ["desktop"])
        yes = by_say[LINUX_YES]
        self.assertEqual(yes["also"], WINDOWS_YES)
        self.assertEqual(yes["added"], "0.2.9")
        self.assertEqual(yes["in"], ["desktop"])
        for path in DESKTOP_SKILLS:
            text = path.read_text(encoding="utf-8")
            self.assertLess(len(text.splitlines()), 150, path.name)
            for needle in (LINUX_INSTALL, WINDOWS_INSTALL, LINUX_YES, WINDOWS_YES):
                self.assertIn(needle, text, str(path))

    def test_setup_page_uses_a_release_and_contributors_keep_the_checkout(self) -> None:
        text = (ROOT / "docs" / "agent-setup.md").read_text(encoding="utf-8")
        self.assertIn("published release", text)
        self.assertIn("Never clone, build, or run from source to install.", text)
        self.assertNotIn("PYTHONPATH", text)
        self.assertIn("projects.json", text)
        self.assertIn("agents.json", text)
        self.assertIn("Check again right before installing.", text)
        self.assertIn("another agent installed meanwhile", text)
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(
            "From a checkout, run commands as `PYTHONPATH=src python3 -m agentcoop`.",
            agents,
        )
