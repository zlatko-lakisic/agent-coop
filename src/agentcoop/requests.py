"""Apply join, leave, and watching requests an agent wrote into the board."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import project_dir
from agentcoop.projects import validate_agent_name

OTHER_ACTION = (
    "This needs a person. Ask an agent that can run Agent Co-op, such as Cursor, "
    "or use the Agent Co-op page."
)
UNREADABLE = "This request could not be read."


def requests_dir(board: Path, project: str, agent: str) -> Path:
    """Return the folder where one agent writes requests."""
    return project_dir(board, project) / "requests" / agent


def _is_young(path: Path, now: datetime | None) -> bool:
    from agentcoop.core import YOUNG_SECONDS

    try:
        modified = path.stat().st_mtime
    except OSError:
        return True
    clock = now.timestamp() if now is not None else datetime.now().timestamp()
    return clock - modified < YOUNG_SECONDS


def _move(path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    os.replace(path, dest)
    return dest


def _reject(path: Path, reason: str) -> None:
    dest = _move(path, path.parent / "rejected")
    (dest.parent / (dest.name + ".reason.txt")).write_text(reason + "\n", encoding="utf-8")


def _finish(path: Path, line: str) -> None:
    first = line.splitlines()[0] if line else "Done."
    dest = _move(path, path.parent / "done")
    (dest.parent / (dest.name + ".result.txt")).write_text(first + "\n", encoding="utf-8")


def process_project_requests(board: Path, project: str, now: datetime | None = None) -> None:
    """Apply request files for one project. Skip a file younger than 5 seconds."""
    from agentcoop.membership import join_named, leave_named

    root = project_dir(board, project) / "requests"
    if not root.is_dir():
        return
    for agent_dir in sorted(root.iterdir()):
        if not agent_dir.is_dir() or agent_dir.name.startswith("."):
            continue
        if agent_dir.name in ("done", "rejected"):
            continue
        try:
            validate_agent_name(agent_dir.name)
        except CoopError:
            continue
        agent = agent_dir.name
        for path in sorted(agent_dir.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() != ".json":
                continue
            if _is_young(path, now):
                continue
            _process_one(board, project, agent, path, join_named, leave_named)


def _process_one(board: Path, project: str, agent: str, path: Path, join_named, leave_named) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _reject(path, UNREADABLE)
        return
    if not isinstance(data, dict):
        _reject(path, UNREADABLE)
        return
    action = data.get("action")
    if action in ("stop_watching", "start_watching"):
        _apply_watching(board, project, agent, path, action == "start_watching")
        return
    if action not in ("join", "leave"):
        _reject(path, OTHER_ACTION)
        return
    note = data.get("note") if isinstance(data.get("note"), str) else None
    try:
        if action == "join":
            text = join_named(agent, project, note=note)
            accepts = data.get("accepts")
            if isinstance(accepts, list) and all(isinstance(item, str) and item.strip() for item in accepts):
                from agentcoop.share import set_accepts

                set_accepts(board, project, agent, [str(item) for item in accepts])
        else:
            text = leave_named(agent, project)
    except CoopError as exc:
        text = str(exc)
    _finish(path, text)


def _apply_watching(board: Path, project: str, agent: str, path: Path, watching: bool) -> None:
    """Set or clear the project stop flag for a member, then file the request."""
    from agentcoop.projects import load_projects, role_of
    from agentcoop.watch import set_project_watching

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    if not role_of(record, agent):
        _reject(path, agent + " is not a member of " + project + ".")
        return
    set_project_watching(board, project, watching, agent)
    if watching:
        text = "Checking is on for " + project + "."
    else:
        text = "Checking is stopped for " + project + "."
    _finish(path, text)


def process_board_requests(board: Path, now: datetime | None = None) -> None:
    """Apply request files in every project, including ones with no members checking."""
    root = board / "projects"
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            process_project_requests(board, child.name, now=now)
