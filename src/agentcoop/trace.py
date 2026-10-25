"""Append one JSON line per command when tracing is on."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

_BODY_FLAGS = {
    "--body",
    "--body-file",
    "--note",
    "--text",
    "--reason",
    "--describe",
}
_SECRET = re.compile(r"(BEGIN [A-Z ]*PRIVATE|sk-|AKIA[0-9A-Z]{8,}|ghp_[A-Za-z0-9]{8,})", re.I)
_RESULT_COMMANDS = {"check", "watch", "wait", "cursor-loop"}
_RESULT_LIMIT = 2000


class Tee:
    """Write to the real stream and keep a copy."""

    def __init__(self, primary, extra) -> None:
        self.primary = primary
        self.extra = extra

    def write(self, text: str) -> int:
        self.primary.write(text)
        self.extra.write(text)
        return len(text)

    def flush(self) -> None:
        self.primary.flush()
        self.extra.flush()

    def __getattr__(self, name: str):
        return getattr(self.primary, name)


def tracing(board: Path) -> bool:
    """Return True when the environment or the board setting asks for a trace."""
    if os.environ.get("AGENTCOOP_TRACE") == "1":
        return True
    from agentcoop.projects import board_is_ready, load_config

    if not board_is_ready(board):
        return False
    try:
        return load_config(board).get("trace") is True
    except Exception:
        return False


def redact_args(argv: list[str]) -> list[str]:
    """Return arguments with message bodies and secret-looking values left out."""
    cleaned: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            cleaned.append("<body>")
            skip = False
            continue
        name, _, value = arg.partition("=")
        if name in _BODY_FLAGS and value:
            cleaned.append(name + "=<body>")
            continue
        if arg in _BODY_FLAGS:
            cleaned.append(arg)
            skip = True
            continue
        if _SECRET.search(arg):
            cleaned.append("<secret>")
            continue
        cleaned.append(arg)
    return cleaned


def note_command(
    board: Path,
    argv: list[str],
    *,
    agent: str,
    command: str,
    code: int,
    started: float,
    printed: str = "",
) -> None:
    """Append one trace line. Failures here never change the command result."""
    if not tracing(board):
        return
    import time

    elapsed = int((time.perf_counter() - started) * 1000)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
    record: dict = {
        "time": stamp,
        "agent": agent,
        "command": command,
        "args": redact_args(argv),
        "exit": int(code),
        "ms": elapsed,
    }
    if command in _RESULT_COMMANDS and printed:
        text = printed.replace("\r\n", "\n")
        if len(text) > _RESULT_LIMIT:
            text = text[-_RESULT_LIMIT:]
        record["result"] = text
    folder = board / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "trace.jsonl"
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def agent_from_args(argv: list[str]) -> str:
    """Return --as NAME, or AGENTCOOP_AGENT, without reading a message body."""
    for index, arg in enumerate(argv):
        if arg == "--as" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--as="):
            return arg.split("=", 1)[1]
    return str(os.environ.get("AGENTCOOP_AGENT") or "")
