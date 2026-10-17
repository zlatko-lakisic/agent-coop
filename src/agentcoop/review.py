"""Review and repair projects.json when it was changed outside agentcoop."""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.projects import (
    good_copy_path,
    integrity_ok,
    load_projects,
    save_projects,
    validate_all,
)


def _pretty(raw: bytes) -> list[str]:
    data = json.loads(raw.decode("utf-8"))
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").splitlines(keepends=True)


def review_projects(board: Path, *, restore: bool = False, accept: bool = False) -> str:
    """Diff, restore, or accept projects.json. Returns the text to print."""
    if integrity_ok(board):
        return "projects.json matches the last saved roles. Nothing to review."
    good = good_copy_path(board)
    current = board / "projects.json"
    if not good.is_file():
        raise CoopError(
            "There is no saved copy to compare with. Check the file by hand, then run: "
            "agentcoop project review --accept"
        )
    if restore:
        data = json.loads(good.read_text(encoding="utf-8"))
        save_projects(board, data)
        return "Restored the last roles saved by agentcoop."
    if accept:
        load_projects(board)
        validate_all(board)
        data = json.loads(current.read_text(encoding="utf-8"))
        save_projects(board, data)
        return "Accepted the current projects.json."
    left = _pretty(good.read_bytes())
    right = _pretty(current.read_bytes())
    diff = "".join(
        difflib.unified_diff(
            left,
            right,
            fromfile="last saved by agentcoop",
            tofile="current file",
        )
    )
    lines = [diff.rstrip(), ""]
    if sys.stdin.isatty():
        lines.append("Restore the last saved roles (r), accept the current file (a), or cancel (c)?")
        print("\n".join(lines))
        choice = input().strip().lower()
        if choice == "r":
            return review_projects(board, restore=True)
        if choice == "a":
            return review_projects(board, accept=True)
        return "Cancelled."
    lines.append("Run agentcoop project review --restore or --accept")
    return "\n".join(lines)
