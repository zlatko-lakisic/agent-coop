"""Tests for tool name aliases."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

import support

from agentcoop.cli import main
from agentcoop.projects import init_board_files
from agentcoop.tools import find_tool


class FindToolTests(unittest.TestCase):
    def test_aliases_resolve_to_same_tool(self) -> None:
        keys = set()
        for name in ("claude_code", "claude-code", "Claude Code", "CLAUDE-CODE"):
            tool = find_tool(name)
            self.assertIsNotNone(tool)
            assert tool is not None
            keys.add(tool.key)
        self.assertEqual(keys, {"claude-code"})

    def test_unknown_name_exits_1(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(["connect", "not-a-real-agent", "--yes"])
            self.assertEqual(code, 1)
            text = err.getvalue()
            self.assertIn("Unknown agent not-a-real-agent", text)
            self.assertIn("You can use:", text)
            self.assertIn("Claude Code (claude-code)", text)
            self.assertIn("Cursor (cursor)", text)


if __name__ == "__main__":
    unittest.main()
