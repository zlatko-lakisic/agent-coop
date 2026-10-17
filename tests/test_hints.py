"""Tests that check hints use the installed command path."""

from __future__ import annotations

import unittest

import support

from agentcoop.core import check_inbox, render_check, send
from agentcoop.paths import agentcoop_command
from agentcoop.projects import add_project, connect_agent_record, init_board_files


class HintTests(unittest.TestCase):
    def test_hints_use_agentcoop_command(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            (env.board / "bin" / "agentcoop.pyz").write_bytes(b"PK\x03\x04fake")
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            connect_agent_record(env.board, "claude_code", "claude-code", display="Claude Code")
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {"claude_code": "reviews"})
            send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="hi",
                body="hi",
            )
            expected = agentcoop_command(env.board)
            report = check_inbox(env.board, "demo", "claude_code")
            full = render_check(report)
            summary = render_check(report, summary=True)
            self.assertIn(expected + " done --as claude_code", full)
            self.assertIn(expected + " check --as claude_code", summary)


if __name__ == "__main__":
    unittest.main()
