"""Read the compatibility table packaged with the program."""

from __future__ import annotations

import json
from pathlib import Path

from agentcoop.version import parse_version, version_text

DATA = Path(__file__).resolve().parent / "data" / "compat.json"
# The v field written on a new message. See core.py.
MESSAGE_FORMAT = 1


def load_rows(path: Path | None = None) -> list[dict]:
    """Return the table rows, Unreleased first."""
    raw = json.loads((path or DATA).read_text(encoding="utf-8"))
    rows = raw.get("rows") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def released_rows(rows: list[dict] | None = None) -> list[dict]:
    """Return rows that name a version, newest first."""
    found = []
    for row in rows if rows is not None else load_rows():
        version = str(row.get("version") or "")
        if version and version != "Unreleased":
            found.append(row)
    return found


def losses_on_downgrade(installed: str, target: str, rows: list[dict] | None = None) -> list[tuple[str, str]]:
    """Return Downgrade loses text for each version the move leaves behind.

    Includes the installed version and any version between it and the target.
    The target itself is not included.
    """
    start = parse_version(installed)
    stop = parse_version(target)
    if stop >= start:
        return []
    chosen: list[tuple[tuple[int, int, int], str, str]] = []
    for row in released_rows(rows):
        text = str(row.get("version") or "")
        try:
            number = parse_version(text)
        except Exception:
            continue
        if stop < number <= start:
            chosen.append((number, text, str(row.get("downgrade_loses") or "")))
    chosen.sort(reverse=True)
    return [(text, loss) for _number, text, loss in chosen]


def loss_lines(installed: tuple[int, int, int], target: tuple[int, int, int]) -> list[str]:
    """Return printable lines for a downgrade."""
    lines = []
    for version, text in losses_on_downgrade(version_text(installed), version_text(target)):
        if text:
            lines.append(version + ": " + text)
    return lines
