"""Tests for interactive setup and project add questions."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.projects import load_agents, load_projects
from agentcoop.wizard import Prompter, add_project_flow, setup_board, suggest_driver


def _answers(*lines: str):
    queue = list(lines)

    def _input(_prompt: str = "") -> str:
        if not queue:
            raise AssertionError("unexpected prompt: " + _prompt)
        return queue.pop(0)

    return _input


class SetupAskTests(unittest.TestCase):
    def test_setup_chooses_two_agents_and_one_ollama_model(self) -> None:
        with support.TempEnv() as env:
            found = [
                {"tool": "cursor", "display": "Cursor", "reason": "found ~/.cursor"},
                {"tool": "claude-code", "display": "Claude Code", "reason": "found ~/.claude"},
                {
                    "tool": "ollama",
                    "display": "Ollama",
                    "reason": "found local server",
                    "models": ["llava", "llama3"],
                },
            ]
            out = io.StringIO()
            prompter = Prompter(
                input_fn=_answers("1,2", "", "", "1", "n", "n", "n"),
                output_fn=out.write,
            )
            with patch("agentcoop.wizard.detect_installed", return_value=found):
                result = setup_board(prompter=prompter, yes=True)
            agents = load_agents(env.board)["agents"]
            self.assertIn("cursor", agents)
            self.assertIn("claude_code", agents)
            self.assertIn("ollama_llava", agents)
            self.assertEqual(set(result["connected"]), {"cursor", "claude_code", "ollama_llava"})

    def test_setup_accepts_a_tool_name_instead_of_a_number(self) -> None:
        with support.TempEnv() as env:
            found = [{"tool": "cursor", "display": "Cursor", "reason": "found ~/.cursor"}]
            out = io.StringIO()
            prompter = Prompter(
                input_fn=_answers("claude-code", "", "n", "n", "n"),
                output_fn=out.write,
            )
            with patch("agentcoop.wizard.detect_installed", return_value=found):
                result = setup_board(prompter=prompter, yes=True)
            self.assertIn("claude_code", result["connected"])
            self.assertIn("claude_code", load_agents(env.board)["agents"])

    def test_project_add_accepts_every_default(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor,claude-code", "--yes"]), 0)
            folder = env.workspace / "demo"
            folder.mkdir()
            prompter = Prompter(
                input_fn=_answers(
                    str(folder),
                    "",
                    "",
                    "1",
                    "",
                    "",
                    "",
                ),
                output_fn=lambda _text: None,
            )
            add_project_flow(prompter=prompter, yes=True)
            project = load_projects(env.board)["projects"]["demo"]
            self.assertEqual(project["driver"], "cursor")
            self.assertEqual(project["helpers"].get("claude_code"), "reviews code")

    def test_driver_preselect_from_cursor_folder(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor,claude-code", "--yes"]), 0)
            folder = env.workspace / "app"
            (folder / ".cursor").mkdir(parents=True)
            agents = load_agents(env.board)
            self.assertEqual(suggest_driver(agents, folder), "cursor")

    def test_driver_preselect_from_agents_md(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "codex,cursor", "--yes"]), 0)
            folder = env.workspace / "app"
            folder.mkdir()
            (folder / "AGENTS.md").write_text("# hi\n", encoding="utf-8")
            agents = load_agents(env.board)
            self.assertEqual(suggest_driver(agents, folder), "codex")

    def test_driver_falls_back_to_cursor(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "claude-code,cursor", "--yes"]), 0)
            folder = env.workspace / "plain"
            folder.mkdir()
            agents = load_agents(env.board)
            self.assertEqual(suggest_driver(agents, folder), "cursor")

    def test_one_invalid_answer_reasks_two_cancel(self) -> None:
        with support.TempEnv() as env:
            found = [{"tool": "cursor", "display": "Cursor", "reason": "found ~/.cursor"}]
            out = io.StringIO()
            prompter = Prompter(
                input_fn=_answers("99", "0"),
                output_fn=lambda text: out.write(text + "\n"),
            )
            with patch("agentcoop.wizard.detect_installed", return_value=found):
                with self.assertRaises(Exception) as raised:
                    setup_board(prompter=prompter, yes=True)
            self.assertEqual(getattr(raised.exception, "code", 1), 1)
            self.assertIn("Cancelled", str(raised.exception))
            self.assertEqual(load_agents(env.board).get("agents"), {})

    def test_rerun_summary_lists_existing_agents(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor", "--yes"]), 0)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["setup", "--connect", "claude-code", "--yes"]), 0)
            text = out.getvalue()
            self.assertIn("cursor", text)
            self.assertIn("claude_code", text)
            self.assertIn("(new)", text)
            self.assertNotIn("No agents connected yet", text)

    def test_no_terminal_without_flags_gives_exit_2(self) -> None:
        with support.TempEnv():
            err = io.StringIO()
            with patch("sys.stdin.isatty", return_value=False), patch(
                "sys.stdout.isatty", return_value=False
            ), redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(["project", "add"])
            self.assertEqual(code, 2)
            self.assertIn("Run this in a terminal", err.getvalue())
            self.assertIn("--driver cursor", err.getvalue())


if __name__ == "__main__":
    unittest.main()
