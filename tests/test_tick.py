"""Scheduled helper and driver runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.context import latest_version
from agentcoop.core import file_id, new_messages
from agentcoop.projects import load_agents, save_agents
from agentcoop.rounds import run_agent, tick
from agentcoop.schedule import build_argv


def _script(path: Path, body: str) -> None:
    path.write_text("#!" + sys.executable + "\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _board_ready(env: support.TempEnv, helper_tool: str = "claude-code") -> Path:
    folder = env.workspace / "demo"
    folder.mkdir()
    main(["setup", "--connect", helper_tool + ",cursor", "--schedule", "no", "--yes"])
    helper_name = helper_tool.replace("-", "_")
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
            helper_name + "=reviews",
            "--gitignore",
            "no",
            "--yes",
        ]
    )
    return folder


def _point_cli(board: Path, name: str, cli: Path) -> None:
    agents = load_agents(board)
    agents["agents"][name]["cli_path"] = str(cli)
    save_agents(board, agents)


class TickTests(unittest.TestCase):
    def test_helper_posts_the_reply_and_marks_context_read(self) -> None:
        with support.TempEnv() as env:
            _board_ready(env)
            log = env.workspace / "seen.txt"
            script = env.workspace / "claude"
            _script(
                script,
                "import os, pathlib, sys\n"
                "text = '\\n'.join(sys.argv) + '\\n' + os.environ.get('PATH','') + '\\n' + os.environ.get('AGENTCOOP_UNATTENDED','')\n"
                "pathlib.Path(" + repr(str(log)) + ").write_text(text, encoding='utf-8')\n"
                "print('The helper reply')\n",
            )
            _point_cli(env.board, "claude_code", script)
            main(["context", "write", "--project", "demo", "--as", "cursor", "--body", "The goal is a demo."])
            main(
                [
                    "send",
                    "--project",
                    "demo",
                    "--as",
                    "cursor",
                    "--to",
                    "claude_code",
                    "--subject",
                    "Look",
                    "--body",
                    "Please look.",
                ]
            )
            self.assertEqual(run_agent(env.board, "claude_code", "demo"), 0)
            seen = log.read_text(encoding="utf-8")
            self.assertIn("The goal is a demo.", seen)
            self.assertIn("Please look.", seen)
            self.assertIn(str(env.board / "bin" / "shims"), seen)
            self.assertTrue(seen.rstrip().endswith("1"))
            messages, _issues = new_messages(env.board, "demo", "cursor")
            answers = [item for item in messages if item.type == "answer"]
            self.assertEqual(len(answers), 1)
            self.assertTrue(answers[0].re)
            left, _issues = new_messages(env.board, "demo", "claude_code")
            self.assertEqual(left, [])
            latest = latest_version(env.board, "demo")
            marker = env.board / "projects" / "demo" / "inbox" / "claude_code" / "context-read"
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), latest.name)

    def test_need_context_once(self) -> None:
        with support.TempEnv() as env:
            _board_ready(env)
            script = env.workspace / "claude"
            _script(script, "print('NEED CONTEXT: the goal')\n")
            _point_cli(env.board, "claude_code", script)
            main(["send", "--project", "demo", "--as", "cursor", "--to", "claude_code", "--subject", "A", "--body", "one"])
            run_agent(env.board, "claude_code", "demo")
            main(["send", "--project", "demo", "--as", "cursor", "--to", "claude_code", "--subject", "B", "--body", "two"])
            run_agent(env.board, "claude_code", "demo")
            messages, _issues = new_messages(env.board, "demo", "cursor")
            requests = [item for item in messages if item.type == "context_request"]
            self.assertEqual(len(requests), 1)

    def test_long_or_diff_answer_is_attached(self) -> None:
        with support.TempEnv() as env:
            _board_ready(env)
            script = env.workspace / "claude"
            _script(script, "print('```diff\\n+line\\n```')\n")
            _point_cli(env.board, "claude_code", script)
            main(["send", "--project", "demo", "--as", "cursor", "--to", "claude_code", "--subject", "A", "--body", "one"])
            run_agent(env.board, "claude_code", "demo")
            folder = env.board / "projects" / "demo" / "files" / "claude_code"
            self.assertTrue(any(path.name.endswith("-answer.md") for path in folder.iterdir()))

    def test_three_failures_then_a_notice(self) -> None:
        with support.TempEnv() as env:
            _board_ready(env)
            script = env.workspace / "claude"
            _script(script, "raise SystemExit(1)\n")
            _point_cli(env.board, "claude_code", script)
            main(["send", "--project", "demo", "--as", "cursor", "--to", "claude_code", "--subject", "A", "--body", "one"])
            run_agent(env.board, "claude_code", "demo")
            left, _issues = new_messages(env.board, "demo", "claude_code")
            self.assertEqual(len(left), 1)
            run_agent(env.board, "claude_code", "demo")
            run_agent(env.board, "claude_code", "demo")
            left, _issues = new_messages(env.board, "demo", "claude_code")
            self.assertEqual(left, [])
            notices, _issues = new_messages(env.board, "demo", "cursor")
            self.assertTrue(any(item.type == "status" for item in notices))

    def test_at_most_three_messages_per_round(self) -> None:
        with support.TempEnv() as env:
            _board_ready(env)
            script = env.workspace / "claude"
            _script(script, "print('ok')\n")
            _point_cli(env.board, "claude_code", script)
            for subject in ("A", "B", "C", "D"):
                main(
                    [
                        "send",
                        "--project",
                        "demo",
                        "--as",
                        "cursor",
                        "--to",
                        "claude_code",
                        "--subject",
                        subject,
                        "--body",
                        subject,
                    ]
                )
            run_agent(env.board, "claude_code", "demo")
            left, _issues = new_messages(env.board, "demo", "claude_code")
            self.assertEqual(len(left), 1)

    def test_driver_argv_includes_the_board(self) -> None:
        with support.TempEnv() as env:
            folder = _board_ready(env, "claude-code")
            claude = env.workspace / "claude"
            claude.write_text("x", encoding="utf-8")
            _point_cli(env.board, "claude_code", claude)
            argv = build_argv(
                env.board,
                "claude_code",
                role="driver",
                prompt="go",
                cwd=folder,
                project_path=folder,
            )
            self.assertIn("--add-dir", argv)
            self.assertIn(str(env.board), argv)
            self.assertNotIn("todo", argv)

    def test_no_mail_starts_nothing(self) -> None:
        with support.TempEnv() as env:
            _board_ready(env)
            started = []

            def fake(board, args):
                started.append(args)

            import agentcoop.rounds as rounds

            original = rounds._start_detached
            rounds._start_detached = fake
            try:
                self.assertEqual(tick(env.board), 0)
            finally:
                rounds._start_detached = original
            self.assertEqual(started, [])

    def test_touch_and_daily_limit_skip_the_round(self) -> None:
        with support.TempEnv() as env:
            _board_ready(env)
            main(["send", "--project", "demo", "--as", "cursor", "--to", "claude_code", "--subject", "A", "--body", "one"])
            from datetime import datetime, timezone

            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            active = env.board / ".state" / "active-claude_code"
            active.parent.mkdir(parents=True, exist_ok=True)
            active.write_text(stamp + "\n", encoding="utf-8")
            started = []
            import agentcoop.rounds as rounds

            original = rounds._start_detached
            rounds._start_detached = lambda board, args: started.append(args)
            try:
                tick(env.board)
                self.assertEqual(started, [])
                active.unlink()
                runs = {"agents": {"claude_code": {"days": {datetime.now(timezone.utc).strftime("%Y-%m-%d"): 48}}}}
                (env.board / ".state" / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
                tick(env.board)
                self.assertEqual(started, [])
            finally:
                rounds._start_detached = original

    def test_timeout_does_not_mark_done(self) -> None:
        with support.TempEnv() as env:
            _board_ready(env)
            script = env.workspace / "claude"
            _script(script, "import time\ntime.sleep(3)\n")
            _point_cli(env.board, "claude_code", script)
            main(["send", "--project", "demo", "--as", "cursor", "--to", "claude_code", "--subject", "A", "--body", "one"])
            run_agent(env.board, "claude_code", "demo", timeout_minutes=0.01)
            left, _issues = new_messages(env.board, "demo", "claude_code")
            self.assertEqual(len(left), 1)

    def test_templates_include_the_command_rule(self) -> None:
        from agentcoop.install import read_template

        sentence = "You may run agentcoop start, stop, now, and status"
        for name in ("SKILL.md.tmpl", "cursor-rule.mdc.tmpl", "AGENTS.md.tmpl", "GEMINI.md.tmpl"):
            self.assertIn(sentence, read_template(name))
            self.assertIn("{{AGENTCOOP}} draft", read_template(name))
            self.assertIn("agentcoop/auto", read_template(name))


if __name__ == "__main__":
    unittest.main()
