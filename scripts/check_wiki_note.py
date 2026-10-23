"""Fail a pull request that changes user-facing files and forgets the wiki line."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def needs_note(path: str) -> bool:
    """Return True when this path can change what a person sees or does."""
    text = path.replace("\\", "/").strip()
    if text.startswith("./"):
        text = text[2:]
    if text.startswith("src/") or text.startswith("docs/") or text.startswith("skills/"):
        return True
    if text.startswith("templates/") or "/templates/" in text:
        return True
    return False


def body_has_note(body: str) -> bool:
    """Return True when a line starts with Wiki: or No wiki change:."""
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Wiki:") or stripped.startswith("No wiki change:"):
            return True
    return False


def event_body(payload: dict) -> str:
    """Return the pull request body from a GitHub event payload."""
    pr = payload.get("pull_request") if isinstance(payload, dict) else None
    if not isinstance(pr, dict):
        return ""
    body = pr.get("body")
    return body if isinstance(body, str) else ""


def load_event(path: str | None = None) -> dict:
    """Read the event payload. A missing file is an empty object."""
    raw = path if path is not None else os.environ.get("GITHUB_EVENT_PATH") or ""
    if not raw:
        return {}
    file_path = Path(raw)
    if not file_path.is_file():
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def evaluate(paths: list[str], body: str) -> str:
    """Return an error message, or an empty string when the note is fine."""
    triggered = [item for item in paths if needs_note(item)]
    if not triggered:
        return ""
    if body_has_note(body):
        return ""
    shown = ", ".join(triggered[:12])
    return (
        "This pull request changes "
        + shown
        + " and its body has no Wiki: or No wiki change: line. "
        + "Follow .cursor/skills/wiki/SKILL.md."
    )


def main(argv: list[str] | None = None) -> int:
    """Read changed paths on stdin and the event payload from the environment."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--skip" in args:
        return 0
    paths = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    message = evaluate(paths, event_body(load_event()))
    if message:
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
