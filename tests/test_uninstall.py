"""Tests for agentcoop uninstall."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.install import render_template
from agentcoop.projects import init_board_files
from agentcoop.uninstall import _exec_start_runs_runner

RULE = (
    "Run agentcoop uninstall --agent only when the person asks you to uninstall "
    "in your own chat."
)
ROOT = Path(__file__).resolve().parents[1]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _must(argv: list[str]) -> None:
    code, out, err = _run(argv)
    if code != 0:
        raise AssertionError(" ".join(argv) + " failed\n" + out + err)


def _prepare(env: support.TempEnv) -> tuple[Path, Path]:
    settings = env.home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "theme": "dark",
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
                    ]
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _must(["setup", "--connect", "cursor,claude-code,codex,chatgpt-codex", "--yes"])
    frontend = env.workspace / "frontend"
    api = env.workspace / "api"
    frontend.mkdir()
    api.mkdir()
    (frontend / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (frontend / "AGENTS.md").write_text("# My notes\n", encoding="utf-8")
    _must(
        [
            "project",
            "add",
            str(frontend),
            "--name",
            "frontend",
            "--driver",
            "cursor",
            "--helper",
            "chatgpt_codex=notes",
            "--gitignore",
            "yes",
            "--yes",
        ]
    )
    _must(
        [
            "project",
            "add",
            str(api),
            "--name",
            "api",
            "--driver",
            "claude_code",
            "--helper",
            "codex=help",
            "--gitignore",
            "no",
            "--yes",
        ]
    )
    return frontend, api


def _files(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    if not root.exists():
        return found
    for path in root.rglob("*"):
        if path.is_file():
            found[str(path.resolve())] = path.read_bytes()
    return found


def _snapshot(env: support.TempEnv, frontend: Path, api: Path) -> dict[str, bytes]:
    found = {}
    found.update(_files(env.home))
    found.update(_files(env.board))
    found.update(_files(frontend))
    found.update(_files(api))
    return found


class UninstallTests(unittest.TestCase):
    def test_agent_without_flag_changes_nothing(self) -> None:
        with support.TempEnv() as env:
            frontend, api = _prepare(env)
            before = _snapshot(env, frontend, api)
            with patch("sys.stdin.isatty", return_value=False):
                code, _out, err = _run(["uninstall"])
            self.assertEqual(code, 2)
            self.assertIn(
                "To uninstall from an agent, add --agent. The board will be moved, not deleted.",
                err,
            )
            self.assertEqual(before, _snapshot(env, frontend, api))

    def test_dry_run_changes_nothing(self) -> None:
        with support.TempEnv() as env:
            frontend, api = _prepare(env)
            before = _snapshot(env, frontend, api)

            def _refuse(_prompt: str = "") -> str:
                raise AssertionError("dry-run asked for confirmation")

            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=_refuse):
                code, out, err = _run(["uninstall", "--dry-run"])
            self.assertEqual(code, 0, err)
            self.assertIn("Project frontend", out)
            self.assertIn("Project api", out)
            self.assertIn("will be deleted", out)
            self.assertEqual(before, _snapshot(env, frontend, api))

            with patch("sys.stdin.isatty", return_value=False):
                code, out, err = _run(["uninstall", "--agent", "--dry-run"])
            self.assertEqual(code, 0, err)
            self.assertIn("will be moved to", out)
            self.assertIn("agent-coop-uninstalled-", out)
            self.assertEqual(before, _snapshot(env, frontend, api))

    def test_person_confirm_removes_agent_coop_and_keeps_user_files(self) -> None:
        with support.TempEnv() as env:
            frontend, _api = _prepare(env)
            note = env.board / "projects" / "frontend" / "keep.txt"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text("keep-this-message\n", encoding="utf-8")
            bindir = env.board / "bin"
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = str(bindir) + os.pathsep + old_path
            try:
                with patch("sys.stdin.isatty", return_value=True), patch(
                    "builtins.input", return_value="uninstall"
                ) as asked:
                    code, out, err = _run(["uninstall"])
            finally:
                os.environ["PATH"] = old_path
            self.assertEqual(code, 0, err)
            asked.assert_called_once_with("Type uninstall to confirm: ")
            self.assertFalse(env.board.exists())
            self.assertFalse((env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md").exists())
            self.assertFalse((env.home / ".codex" / "skills" / "agent-coop" / "SKILL.md").exists())
            self.assertFalse((frontend / ".cursor" / "rules" / "agent-coop.mdc").exists())
            self.assertFalse((frontend / ".codex" / "skills" / "agent-coop" / "SKILL.md").exists())
            self.assertIn("node_modules/", (frontend / ".gitignore").read_text(encoding="utf-8"))
            self.assertNotIn("agent-coop.mdc", (frontend / ".gitignore").read_text(encoding="utf-8"))
            agents = (frontend / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("# My notes", agents)
            self.assertNotIn("agentcoop:start", agents)
            settings = json.loads((env.home / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["theme"], "dark")
            self.assertEqual(settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "echo mine")
            self.assertNotIn("SessionStart", settings["hooks"])
            self.assertNotIn("UserPromptSubmit", settings["hooks"])
            self.assertTrue(all(settings["hooks"].values()))
            self.assertIn("Agent Co-op is uninstalled.", out)
            self.assertNotIn("Files you changed, left in place:", out)
            self.assertIn("Remove " + str(bindir) + " from your PATH.", out)

    def test_person_wrong_answer_changes_nothing(self) -> None:
        with support.TempEnv() as env:
            frontend, api = _prepare(env)
            before = _snapshot(env, frontend, api)
            with support.person("no"):
                code, _out, err = _run(["uninstall"])
            self.assertEqual(code, 1)
            self.assertIn("Cancelled.", err)
            self.assertEqual(before, _snapshot(env, frontend, api))

    def test_keep_board_drops_bin_and_keeps_messages(self) -> None:
        with support.TempEnv() as env:
            _prepare(env)
            note = env.board / "projects" / "frontend" / "keep.txt"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text("keep-this-message\n", encoding="utf-8")
            self.assertTrue((env.board / "bin").is_dir())
            with support.person("uninstall"):
                code, out, err = _run(["uninstall", "--keep-board"])
            self.assertEqual(code, 0, err)
            self.assertTrue(env.board.is_dir())
            self.assertFalse((env.board / "bin").exists())
            kept = [path.read_text(encoding="utf-8") for path in env.board.rglob("keep.txt")]
            self.assertEqual(kept, ["keep-this-message\n"])
            self.assertIn("will be kept", out)

    def test_agent_mode_moves_the_board(self) -> None:
        with support.TempEnv() as env:
            os.environ["AGENTCOOP_BOARD"] = str(env.home / ".agent_board")
            frontend, _api = _prepare(env)
            board = env.home / ".agent_board"
            note = board / "projects" / "frontend" / "keep.txt"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text("keep-this-message\n", encoding="utf-8")
            with patch("sys.stdin.isatty", return_value=False):
                code, out, err = _run(["uninstall", "--agent"])
            self.assertEqual(code, 0, err)
            self.assertFalse(board.exists())
            self.assertFalse((env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md").exists())
            self.assertFalse((frontend / ".cursor" / "rules" / "agent-coop.mdc").exists())
            self.assertIn("# My notes", (frontend / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertIn("node_modules/", (frontend / ".gitignore").read_text(encoding="utf-8"))
            moved = list(env.home.glob("agent-coop-uninstalled-*"))
            self.assertEqual(len(moved), 1)
            self.assertTrue(moved[0].is_dir())
            kept = [path.read_text(encoding="utf-8") for path in moved[0].rglob("keep.txt")]
            self.assertEqual(kept, ["keep-this-message\n"])
            self.assertIn(str(moved[0]), out)
            self.assertIn("Delete that folder when you no longer need it.", out)

    def test_edited_cursor_rule_is_listed_and_left_in_place(self) -> None:
        with support.TempEnv() as env:
            frontend, _api = _prepare(env)
            rule = frontend / ".cursor" / "rules" / "agent-coop.mdc"
            rule.write_text(rule.read_text(encoding="utf-8") + "\nextra line\n", encoding="utf-8")
            with support.person("uninstall"):
                code, out, err = _run(["uninstall"])
            self.assertEqual(code, 0, err)
            self.assertTrue(rule.is_file())
            self.assertIn("extra line", rule.read_text(encoding="utf-8"))
            self.assertIn("Files you changed, left in place:", out)
            self.assertIn("agent-coop.mdc", out)
            self.assertIn("in place", out)

    @unittest.skipUnless(sys.platform == "linux", "systemd user units are only removed on Linux")
    def test_systemd_unit_is_removed(self) -> None:
        with support.TempEnv() as env:
            _prepare(env)
            folder = env.home / ".config" / "systemd" / "user"
            folder.mkdir(parents=True, exist_ok=True)
            unit = folder / "agentcoop-runner.service"
            unit.write_text(
                "[Service]\nExecStart=/usr/bin/agentcoop runner --as ollama_llava\n",
                encoding="utf-8",
            )
            other = folder / "unrelated.service"
            other.write_text("[Service]\nExecStart=/usr/bin/nginx\n", encoding="utf-8")
            mocked = patch(
                "agentcoop.uninstall.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0),
            )
            with mocked as calls, support.person("uninstall"):
                code, out, err = _run(["uninstall"])
            self.assertEqual(code, 0, err)
            self.assertFalse(unit.exists())
            self.assertTrue(other.is_file())
            self.assertIn("agentcoop-runner.service", out)
            commands = [call.args[0] for call in calls.call_args_list]
            self.assertIn(["systemctl", "--user", "disable", "--now", "agentcoop-runner.service"], commands)

    @unittest.skipUnless(sys.platform == "darwin", "launchd plists are only removed on macOS")
    def test_launchd_plist_is_removed(self) -> None:
        with support.TempEnv() as env:
            _prepare(env)
            plist = env.home / "Library" / "LaunchAgents" / "com.agentcoop.runner.plist"
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text("agentcoop\n", encoding="utf-8")
            mocked = patch(
                "agentcoop.uninstall.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0),
            )
            with mocked as calls, support.person("uninstall"):
                code, out, err = _run(["uninstall"])
            self.assertEqual(code, 0, err)
            self.assertFalse(plist.exists())
            self.assertIn("com.agentcoop.runner.plist", out)
            commands = [call.args[0] for call in calls.call_args_list]
            self.assertTrue(any(item[:2] == ["launchctl", "unload"] for item in commands))

    def test_exec_start_matcher(self) -> None:
        self.assertTrue(_exec_start_runs_runner("ExecStart=/usr/bin/agentcoop runner --as x\n"))
        self.assertFalse(_exec_start_runs_runner("ExecStart=/usr/bin/agentcoop check\n"))
        self.assertFalse(_exec_start_runs_runner("Description=agentcoop runner helper\n"))

    def test_templates_include_the_uninstall_rule(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            names = (
                "SKILL.md.tmpl",
                "cursor-rule.mdc.tmpl",
                "AGENTS.md.tmpl",
                "GEMINI.md.tmpl",
                "board-README.md.tmpl",
            )
            for name in names:
                text = render_template(name, agent_name="cursor", board=env.board)
                self.assertIn(RULE, text, name)
        setup = (ROOT / "docs" / "agent-setup.md").read_text(encoding="utf-8")
        self.assertIn(RULE, setup)

    def test_readme_uninstall_links_exist(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn(".png", readme)
        section = readme.split("## Uninstall", 1)[1].split("\n## ", 1)[0]
        links = re.findall(r"\]\(([^)]+)\)", section)
        self.assertTrue(links)
        for link in links:
            self.assertFalse(link.startswith("http"))
            self.assertTrue((ROOT / link).is_file(), link)


if __name__ == "__main__":
    unittest.main()
