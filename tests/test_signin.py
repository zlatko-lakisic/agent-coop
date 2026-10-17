"""A scheduled run that is not signed in stays unsigned and the message stays new."""

from __future__ import annotations

import unittest

import support

from agentcoop.cli import main
from agentcoop.core import new_messages
from agentcoop.doctor import format_doctor, run_doctor
from agentcoop.projects import load_agents, save_agents
from agentcoop.rounds import format_status, run_agent


CURSOR_AUTH = (
    "Error: Authentication required. Please run 'agent login' first, "
    "or set CURSOR_API_KEY environment variable.\n"
)


class _Proc:
    def __init__(self, output: str) -> None:
        self.returncode = 0
        self.pid = 424242
        self.stdout = None
        self._output = output

    def communicate(self, timeout=None):
        return self._output, ""

    def kill(self) -> None:
        return None

    def wait(self, timeout=None) -> int:
        return 0


class SignInTests(unittest.TestCase):
    def test_cursor_auth_message_stays_new(self) -> None:
        with support.TempEnv() as env:
            folder = env.workspace / "demo"
            folder.mkdir()
            main(["setup", "--connect", "cursor,claude-code", "--schedule", "no", "--yes"])
            main(
                [
                    "project",
                    "add",
                    str(folder),
                    "--name",
                    "demo",
                    "--driver",
                    "claude_code",
                    "--helper",
                    "cursor=edits",
                    "--gitignore",
                    "no",
                    "--yes",
                ]
            )
            cli = env.workspace / "agent"
            cli.write_text("x", encoding="utf-8")
            claude = env.workspace / "claude"
            claude.write_text("x", encoding="utf-8")
            agents = load_agents(env.board)
            agents["agents"]["cursor"]["cli_path"] = str(cli)
            agents["agents"]["claude_code"]["cli_path"] = str(claude)
            save_agents(env.board, agents)
            main(
                [
                    "send",
                    "--as",
                    "claude_code",
                    "--project",
                    "demo",
                    "--to",
                    "cursor",
                    "--subject",
                    "Look",
                    "--body",
                    "please look",
                ]
            )

            def launch(argv, **kwargs):
                return _Proc(CURSOR_AUTH)

            self.assertEqual(run_agent(env.board, "cursor", "demo", popen=launch), 0)
            left, _issues = new_messages(env.board, "demo", "cursor")
            self.assertEqual(len(left), 1)
            sentence = "Cursor is not signed in, so it cannot run in the background. Run: agent login"
            self.assertIn(sentence, format_status(env.board))
            self.assertIn("Fix: " + sentence, format_doctor(run_doctor(env.board)))

            def ok(argv, **kwargs):
                return _Proc("The file looks fine.\n")

            self.assertEqual(run_agent(env.board, "cursor", "demo", popen=ok), 0)
            self.assertNotIn("is not signed in", format_status(env.board))
            left, _issues = new_messages(env.board, "demo", "cursor")
            self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
