"""Tests for which agents can be scheduled and how their commands are built."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import support

from agentcoop.projects import connect_agent_record, init_board_files
from agentcoop.schedule import build_argv, schedulable_reason
from agentcoop.tools import get_tool


class HeadlessTests(unittest.TestCase):
    def test_labels(self) -> None:
        self.assertIsInstance(get_tool("claude-code").headless, dict)
        self.assertIsInstance(get_tool("codex").headless, dict)
        self.assertIsInstance(get_tool("cursor").headless, dict)
        self.assertIsInstance(get_tool("gemini-cli").headless, dict)
        self.assertEqual(get_tool("opencode").headless, "todo")
        self.assertEqual(get_tool("goose").headless, "todo")
        self.assertEqual(get_tool("claude-desktop").headless, "none")
        self.assertEqual(get_tool("chatgpt-work").headless, "none")
        self.assertEqual(get_tool("cursor").cli_names, ("cursor-agent",))

    def test_missing_cli_message(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            reason = schedulable_reason(env.board, "cursor")
            self.assertIsNotNone(reason)
            assert reason is not None
            self.assertIn("cannot be started in the background", reason)
            self.assertIn("answers when you open it", reason)

    def test_claude_helper_argv(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            cli = env.workspace / "claude"
            cli.write_text("#!/bin/sh\n", encoding="utf-8")
            connect_agent_record(
                env.board,
                "claude_code",
                "claude-code",
                display="Claude Code",
                cli_path=str(cli),
                model="haiku",
            )
            argv = build_argv(
                env.board,
                "claude_code",
                role="helper",
                prompt="hello",
                cwd=env.board / "projects" / "demo",
                project_path=env.workspace / "demo",
            )
            self.assertEqual(argv[0], str(cli))
            self.assertIn("-p", argv)
            self.assertIn("hello", argv)
            self.assertIn("--permission-mode", argv)
            self.assertIn("plan", argv)
            self.assertIn("--model", argv)
            self.assertIn("haiku", argv)
            self.assertIn("Read,Grep,Glob", argv)
            self.assertNotIn("Bash", argv)

    def test_runner_uses_once(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(
                env.board,
                "ollama_llava",
                "ollama",
                display="Ollama",
                runner={"api": "ollama", "host": "http://localhost:11434", "model": "llava"},
            )
            argv = build_argv(
                env.board,
                "ollama_llava",
                role="helper",
                prompt="",
                cwd=env.board,
                project_path=env.workspace,
            )
            self.assertIn("runner", argv)
            self.assertIn("--once", argv)
            self.assertIn("--as", argv)

    def test_chatgpt_codex_needs_codex_cli(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(
                env.board,
                "chatgpt_codex",
                "chatgpt-codex",
                display="ChatGPT desktop (Codex)",
            )
            with mock.patch("agentcoop.schedule.shutil.which", return_value=None):
                reason = schedulable_reason(env.board, "chatgpt_codex")
            self.assertEqual(reason, "Answers when you open the ChatGPT desktop app.")

    def test_draft_refuses_paths(self) -> None:
        from agentcoop.errors import CoopError
        from agentcoop.projects import add_project
        from agentcoop.schedule import draft_file

        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor")
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {})
            with mock.patch("agentcoop.projects.Path.cwd", return_value=folder):
                path = draft_file(env.board, "cursor", "notes.md", "hello")
            self.assertTrue(str(path).endswith("files/cursor/notes.md") or str(path).endswith("files\\cursor\\notes.md"))
            self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")
            for bad in ("../x", "/tmp/x", "a/b"):
                with self.assertRaises(CoopError):
                    draft_file(env.board, "cursor", bad, "no")


if __name__ == "__main__":
    unittest.main()
