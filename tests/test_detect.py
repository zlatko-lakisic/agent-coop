"""Tests for agent detection rules."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import support

from agentcoop.detect import detect_tools


class DetectToolsTests(unittest.TestCase):
    def test_home_dirs_detect_claude_and_codex(self) -> None:
        with support.TempEnv() as env:
            (env.home / ".claude").mkdir()
            (env.home / ".codex").mkdir()
            found = detect_tools(env.home, "darwin", which=lambda _name: None)
            by_tool = {item["tool"]: item for item in found}
            self.assertIn("claude-code", by_tool)
            self.assertEqual(by_tool["claude-code"]["reason"], "found ~/.claude")
            self.assertIn("codex", by_tool)
            self.assertEqual(by_tool["codex"]["reason"], "found ~/.codex")

    def test_command_detects_gemini(self) -> None:
        with support.TempEnv() as env:
            def which(name: str) -> str | None:
                if name == "gemini":
                    return "/usr/bin/gemini"
                return None

            found = detect_tools(env.home, "darwin", which=which)
            by_tool = {item["tool"]: item for item in found}
            self.assertIn("gemini-cli", by_tool)
            self.assertEqual(by_tool["gemini-cli"]["reason"], "found command gemini")

    def test_win32_app_detects_claude_desktop(self) -> None:
        with support.TempEnv() as env:
            local = env.root / "LocalAppData"
            target = local / "AnthropicClaude"
            target.mkdir(parents=True)
            previous = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = str(local)
            try:
                found = detect_tools(env.home, "win32", which=lambda _name: None)
            finally:
                if previous is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = previous
            by_tool = {item["tool"]: item for item in found}
            self.assertIn("claude-desktop", by_tool)
            self.assertIn("AnthropicClaude", by_tool["claude-desktop"]["reason"])

    def test_empty_home_finds_nothing(self) -> None:
        with support.TempEnv() as env:
            # linux has no app paths in the table, so real /Applications cannot match.
            found = detect_tools(env.home, "linux", which=lambda _name: None)
            self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
