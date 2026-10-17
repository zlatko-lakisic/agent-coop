"""Tests for install, removal, and hook merging."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support

from agentcoop.errors import CoopError
from agentcoop.install import (
    claude_code_hooks,
    connect_agent,
    disconnect_agent,
    install_project_files,
    merge_hook_settings,
    remove_hook_command,
    remove_project_files,
)
from agentcoop.projects import add_project, init_board_files, load_agents, load_projects


class InstallTests(unittest.TestCase):
    def test_skill_paths_and_disconnect(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent(env.board, "claude-code", yes=True)
            skill = env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            text = skill.read_text(encoding="utf-8")
            self.assertIn("`claude_code`", text)
            self.assertIn(str(env.board), text)
            manifest = load_agents(env.board)["agents"]["claude_code"]["installed"]
            self.assertTrue(manifest)
            self.assertTrue(all(item.get("sha256") for item in manifest))
            disconnect_agent(env.board, "claude_code")
            self.assertFalse(skill.exists())
            self.assertNotIn("claude_code", load_agents(env.board)["agents"])

    def test_refuse_overwrite_without_yes(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            skill = env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("mine\n", encoding="utf-8")
            with self.assertRaises(CoopError):
                connect_agent(env.board, "claude-code", yes=False)
            self.assertEqual(skill.read_text(encoding="utf-8"), "mine\n")
            connect_agent(env.board, "claude-code", yes=True)
            self.assertIn("Agent Co-op", skill.read_text(encoding="utf-8"))
            backups = list(skill.parent.glob("SKILL.md.agentcoop-backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "mine\n")

    def test_agents_md_section_round_trip(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent(env.board, "codex", yes=True)
            folder = env.workspace / "frontend"
            folder.mkdir()
            add_project(env.board, "frontend", folder, "codex", {})
            agents_md = folder / "AGENTS.md"
            agents_md.write_text("# Notes\n\nKeep this.\n", encoding="utf-8")
            install_project_files(env.board, "frontend", "codex", yes=True)
            text = agents_md.read_text(encoding="utf-8")
            self.assertIn("Keep this.", text)
            self.assertEqual(text.count("<!-- agentcoop:start -->"), 1)
            install_project_files(env.board, "frontend", "codex", yes=True)
            text = agents_md.read_text(encoding="utf-8")
            self.assertEqual(text.count("<!-- agentcoop:start -->"), 1)
            self.assertIn("Keep this.", text)
            remove_project_files(env.board, "frontend")
            text = agents_md.read_text(encoding="utf-8")
            self.assertIn("Keep this.", text)
            self.assertNotIn("agentcoop:start", text)
            self.assertNotIn("frontend", load_projects(env.board)["projects"])

    def test_hook_merge_keeps_existing(self) -> None:
        existing = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
                ]
            }
        }
        addition = {
            "hooks": {
                "SessionStart": [
                    {"matcher": "*", "hooks": [{"type": "command", "command": "agentcoop check --summary"}]}
                ]
            }
        }
        merged = merge_hook_settings(existing, addition)
        self.assertEqual(merged["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "echo hi")
        self.assertIn("SessionStart", merged["hooks"])
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent(env.board, "claude-code", yes=True)
            folder = env.workspace / "frontend"
            folder.mkdir()
            add_project(
                env.board,
                "frontend",
                folder,
                "claude_code",
                {},
            )
            settings = folder / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
            install_project_files(env.board, "frontend", "claude_code", yes=True)
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "echo hi")
            self.assertIn("SessionStart", data["hooks"])
            self.assertIn("UserPromptSubmit", data["hooks"])
            remove_project_files(env.board, "frontend")
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertIn("PreToolUse", data["hooks"])
            self.assertEqual(data["hooks"].get("SessionStart"), [])
            self.assertFalse((folder / ".cursor").exists())

    def test_hook_paths_escape_windows_backslashes(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            board = Path(r"C:\Users\you\.agent_board")
            hooks = claude_code_hooks(board, "claude_code")
            # Round-trip through JSON the same way install writes settings.
            encoded = json.dumps(hooks)
            loaded = json.loads(encoded)
            command = loaded["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            self.assertIn("claude_code", command)
            self.assertIn("check --as", command)
            self.assertIn("agent_board", command.replace("\\\\", "\\"))
            # Removal must match the real command string, not a JSON-escaped copy.
            cleaned = remove_hook_command(loaded, command)
            self.assertEqual(cleaned["hooks"]["SessionStart"], [])
            self.assertEqual(cleaned["hooks"]["UserPromptSubmit"], [])

    def test_disconnect_refuses_driver(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent(env.board, "cursor", yes=True)
            folder = env.workspace / "frontend"
            folder.mkdir()
            add_project(env.board, "frontend", folder, "cursor", {})
            with self.assertRaises(CoopError) as refused:
                disconnect_agent(env.board, "cursor")
            self.assertIn("driver", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
