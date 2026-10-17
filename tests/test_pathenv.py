"""Tests for the launcher, wrapper, PATH changes, and refresh on re-run."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.install import file_sha256, refresh_installed_files, stage_runtime
from agentcoop.pathenv import (
    WINDOWS_PATH_KEY,
    agentcoop_command,
    apply_path_change,
    pick_launcher,
    undo_path_changes,
    write_command_wrapper,
)
from agentcoop.projects import init_board_files, load_agents, load_config, mutate_agents
class PathEnvTests(unittest.TestCase):
    def test_launcher_order_on_posix(self) -> None:
        calls = []

        def which(name: str):
            calls.append(name)
            if name == "python3":
                return "/usr/bin/python3"
            return None

        path = pick_launcher(which=which, name="posix", executable="/fallback")
        # resolve() follows the real python3 symlink, which may be python3.12.
        expected = "/usr/bin/python3"
        if Path(expected).exists():
            expected = str(Path(expected).resolve())
        self.assertEqual(path, expected)
        self.assertEqual(calls, ["python3"])

    def test_launcher_order_on_windows(self) -> None:
        calls = []

        def which(name: str):
            calls.append(name)
            if name == "python":
                return r"C:\Python\python.exe"
            return None

        path = pick_launcher(which=which, name="nt", executable=r"C:\fallback.exe")
        self.assertEqual(calls[0], "py")
        self.assertIn(path, (r"C:\Python\python.exe", str(Path(r"C:\Python\python.exe").resolve())))

    def test_launcher_falls_back_to_sys_executable(self) -> None:
        path = pick_launcher(which=lambda _name: None, name="posix", executable="/opt/python")
        self.assertTrue(path.endswith("python") or path == "/opt/python" or "python" in path)

    def test_wrapper_content_and_mode(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            stage_runtime(env.board)
            wrapper = env.board / "bin" / "agentcoop"
            if os.name == "nt":
                wrapper = env.board / "bin" / "agentcoop.cmd"
                self.assertTrue(wrapper.is_file())
                text = wrapper.read_text(encoding="utf-8")
                self.assertIn("agentcoop.pyz", text)
                self.assertIn("%*", text)
            else:
                self.assertTrue(wrapper.is_file())
                text = wrapper.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("#!/bin/sh\n"))
                self.assertIn('exec "', text)
                self.assertIn("agentcoop.pyz", text)
                self.assertEqual(oct(wrapper.stat().st_mode & 0o777), "0o700")
            command = agentcoop_command(env.board)
            self.assertIn(str(wrapper), command.replace('"', "").replace("'", ""))

    def test_rc_block_added_once_and_removed_by_uninstall(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX PATH files")
        with support.TempEnv() as env:
            init_board_files(env.board)
            stage_runtime(env.board)
            zshrc = env.home / ".zshrc"
            zshrc.write_text("# keep me\n", encoding="utf-8")
            with patch.dict(os.environ, {"SHELL": "/bin/zsh"}):
                apply_path_change(env.board, True)
                apply_path_change(env.board, True)
            text = zshrc.read_text(encoding="utf-8")
            self.assertEqual(text.count("# agentcoop:start"), 1)
            self.assertIn(str(env.board / "bin"), text)
            self.assertTrue(text.startswith("# keep me\n"))
            config = load_config(env.board)
            self.assertIn(str(zshrc), config.get("path_changes") or [])
            undo_path_changes(env.board)
            after = zshrc.read_text(encoding="utf-8")
            self.assertEqual(after, "# keep me\n")
            self.assertNotIn("agentcoop:start", after)

    def test_fish_prints_a_line_and_changes_nothing(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX PATH files")
        with support.TempEnv() as env:
            init_board_files(env.board)
            stage_runtime(env.board)
            config_path = env.home / ".config" / "fish" / "config.fish"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("# fish\n", encoding="utf-8")
            with patch.dict(os.environ, {"SHELL": "/usr/bin/fish"}):
                changed = apply_path_change(env.board, True)
            self.assertEqual(changed, [])
            self.assertEqual(config_path.read_text(encoding="utf-8"), "# fish\n")

    def test_windows_path_append_and_removal_with_fake_winreg(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            stage_runtime(env.board)
            store = {"Path": ("C:\\Windows\\System32", 2)}

            class FakeWinreg:
                HKEY_CURRENT_USER = object()
                KEY_READ = 1
                KEY_SET_VALUE = 2
                REG_EXPAND_SZ = 2
                REG_SZ = 1

                @staticmethod
                def OpenKey(_root, _path, _reserved, _access):
                    return object()

                @staticmethod
                def QueryValueEx(_key, name):
                    if name not in store:
                        raise FileNotFoundError(name)
                    return store[name]

                @staticmethod
                def SetValueEx(_key, name, _reserved, value_type, value):
                    store[name] = (value, value_type)

                @staticmethod
                def CloseKey(_key):
                    return None

            from agentcoop import pathenv

            changed = pathenv._windows_add_path(env.board, winreg_module=FakeWinreg)
            self.assertEqual(changed, [WINDOWS_PATH_KEY])
            path_value = store["Path"][0]
            self.assertIn(str(env.board / "bin"), path_value)
            pathenv._windows_remove_path(env.board, winreg_module=FakeWinreg)
            self.assertNotIn(str(env.board / "bin"), store["Path"][0])

    def test_rerun_refreshes_unedited_skill_and_leaves_edited(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "claude-code", "--yes"]), 0)
            skill = env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
            self.assertTrue(skill.is_file())
            original = skill.read_text(encoding="utf-8")
            agents = load_agents(env.board)
            entry = next(
                item
                for item in agents["agents"]["claude_code"]["installed"]
                if item.get("path") == str(skill)
            )
            self.assertEqual(file_sha256(skill), entry["sha256"])
            skill.write_text(original + "\n# edited by me\n", encoding="utf-8")
            left = refresh_installed_files(env.board)
            self.assertTrue(any(str(skill) == path for path in left))
            self.assertIn("# edited by me", skill.read_text(encoding="utf-8"))

            skill.write_text(original, encoding="utf-8")

            def restore_hash(data: dict) -> None:
                for item in data["agents"]["claude_code"]["installed"]:
                    if item.get("path") == str(skill):
                        item["sha256"] = file_sha256(skill)

            mutate_agents(env.board, restore_hash)
            left = refresh_installed_files(env.board)
            self.assertNotIn(str(skill), left)
            refreshed = skill.read_text(encoding="utf-8")
            self.assertIn("Agent Co-op", refreshed)


if __name__ == "__main__":
    unittest.main()
