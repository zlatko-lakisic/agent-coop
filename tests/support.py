"""Shared test helpers. Import this before agentcoop."""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TempEnv:
    """A fake home and a temp board. Never touches the real home folder."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.board = self.root / "board"
        self.workspace = self.root / "workspace"
        self.home.mkdir()
        self.workspace.mkdir()
        self.fakes = self.root / "fakes"
        self.fakes.mkdir()
        self._old: dict[str, str | None] = {}

    def __enter__(self) -> "TempEnv":
        for key, value in {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "AGENTCOOP_BOARD": str(self.board),
            "PATH": str(self.fakes) + os.pathsep + os.environ.get("PATH", ""),
            "AGENTCOOP_TEST_OFFLINE": "1",
        }.items():
            self._old[key] = os.environ.get(key)
            os.environ[key] = value
        for key in ("AGENTCOOP_AGENT", "CLAUDECODE", "GEMINI_CLI", "CODEX_SANDBOX", "CODEX_THREAD_ID"):
            self._old[key] = os.environ.get(key)
            os.environ.pop(key, None)
        for name in ("launchctl", "systemctl", "crontab", "schtasks", "taskkill"):
            path = self.fakes / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)
        return self

    def __exit__(self, *args: object) -> bool:
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()
        return False


@contextmanager
def person(answer: str):
    """Pretend the command is running in a terminal and type one confirmation."""

    def _input(_prompt: str = "") -> str:
        return answer

    with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=_input):
        yield
