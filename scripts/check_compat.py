"""Fail when a compatibility change forgets the table, or the table drifts from the code."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentcoop import SPEC_VERSION, __version__  # noqa: E402
from agentcoop.compat import MESSAGE_FORMAT, load_rows, released_rows  # noqa: E402
from agentcoop.version import BOARD_FORMAT  # noqa: E402

TRIGGERS = (
    "SPEC.md",
    "src/agentcoop/core.py",
    "src/agentcoop/schema.py",
    "src/agentcoop/paths.py",
    "src/agentcoop/install.py",
    "src/agentcoop/statusfile.py",
    "src/agentcoop/outbox.py",
    "src/agentcoop/requests.py",
    "src/agentcoop/watch.py",
    "src/agentcoop/context.py",
    "src/agentcoop/progress.py",
    "src/agentcoop/projects.py",
    "src/agentcoop/migrate.py",
    "src/agentcoop/version.py",
    "src/agentcoop/rounds.py",
    "src/agentcoop/pathenv.py",
    "src/agentcoop/cli.py",
    "src/agentcoop/desktop.py",
    "src/agentcoop/update.py",
    "src/agentcoop/templates/",
    "skills/",
    "docs/agent-setup.md",
    "docs/agent-update.md",
)
TABLE = "src/agentcoop/data/compat.json"


def _clean(path: str) -> str:
    text = path.replace("\\", "/").strip()
    if text.startswith("./"):
        text = text[2:]
    return text


def triggered(path: str) -> bool:
    """Return True when this path can change what an older version can read."""
    text = _clean(path)
    for item in TRIGGERS:
        if item.endswith("/"):
            if text.startswith(item):
                return True
        elif text == item:
            return True
    return False


def body_opts_out(body: str) -> bool:
    """Return True when a line starts with No compatibility change:."""
    for line in (body or "").splitlines():
        if line.strip().startswith("No compatibility change:"):
            return True
    return False


def note_error(paths: list[str], body: str) -> str:
    """Return an error when the table was forgotten, else an empty string."""
    hits = [item for item in paths if triggered(item)]
    if not hits:
        return ""
    if any(_clean(item) == TABLE for item in paths):
        return ""
    if body_opts_out(body):
        return ""
    shown = ", ".join(hits[:12])
    return (
        "This pull request changes "
        + shown
        + " and does not change "
        + TABLE
        + ". Update the Unreleased row, or add a line No compatibility change: with a reason. "
        + "Follow .cursor/skills/compatibility/SKILL.md."
    )


def code_error(rows: list[dict] | None = None) -> str:
    """Return an error when the newest released row does not match the code."""
    released = released_rows(rows)
    if not released:
        return "The compatibility table has no released row."
    row = released[0]
    problems = []
    if str(row.get("version") or "") != __version__:
        problems.append("version " + str(row.get("version")) + " != " + __version__)
    if str(row.get("spec") or "") != SPEC_VERSION:
        problems.append("spec " + str(row.get("spec")) + " != " + SPEC_VERSION)
    if int(row.get("message_format") or 0) != MESSAGE_FORMAT:
        problems.append("message format != " + str(MESSAGE_FORMAT))
    if int(row.get("board_format") or 0) != BOARD_FORMAT:
        problems.append("board format != " + str(BOARD_FORMAT))
    if not problems:
        return ""
    return "The newest released compatibility row does not match the code: " + "; ".join(problems)


def event_body() -> str:
    """Return the pull request body from the GitHub event payload."""
    raw = os.environ.get("GITHUB_EVENT_PATH") or ""
    if not raw or not Path(raw).is_file():
        return ""
    try:
        data = json.loads(Path(raw).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    pr = data.get("pull_request") if isinstance(data, dict) else None
    if not isinstance(pr, dict):
        return ""
    body = pr.get("body")
    return body if isinstance(body, str) else ""


def main(argv: list[str] | None = None) -> int:
    """Check the code, or check a pull request note from paths on stdin."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--code" in args:
        message = code_error()
        if message:
            print(message, file=sys.stderr)
            return 1
        return 0
    paths = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    message = note_error(paths, event_body())
    if message:
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
