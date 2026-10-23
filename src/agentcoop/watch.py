"""Wait for mail without using a model, and record who is watching."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop.core import check_inbox, format_ts, render_check, utc_now
from agentcoop.errors import CoopError
from agentcoop.paths import outbox_dir, outbox_sent_dir, project_dir
from agentcoop.projects import atomic_write_bytes, board_is_ready, dump_json, load_projects, role_of

POLL_SECONDS = 5
HEARTBEAT_SECONDS = 30
STALE_SECONDS = 3 * 60
DEFAULT_TIMEOUT_MINUTES = 10
MIN_TIMEOUT_MINUTES = 1
MAX_TIMEOUT_MINUTES = 30

HANDLE_LINE = "Handle these, then run agentcoop watch again."
TIMEOUT_LINE = "No new messages. Run agentcoop watch again."


def stopped_line(project: str) -> str:
    """Return the line watch prints when the person has stopped checking."""
    return "The person stopped the co-op for " + project + ". Stop watching and tell them."


def watch_path(board: Path, project: str) -> Path:
    """Return the project stop flag."""
    return project_dir(board, project) / "watch.json"


def watcher_path(board: Path, project: str, agent: str) -> Path:
    """Return one agent's heartbeat file."""
    return project_dir(board, project) / "watchers" / (agent + ".json")


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def checking_enabled(board: Path, project: str) -> bool:
    """Return False only when the project flag says checking is stopped."""
    data = _load_json(watch_path(board, project))
    if not data:
        return True
    return data.get("watching") is not False


def assert_member(board: Path, project: str, agent: str) -> dict:
    """Raise when this agent is not the driver or a helper."""
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ". Run: agentcoop project list")
    if not role_of(record, agent):
        raise CoopError(agent + " is not a member of " + project + ".")
    return record


def set_project_watching(board: Path, project: str, watching: bool, by: str, now: datetime | None = None) -> None:
    """Set or clear one project's stop flag. Clearing deletes the file."""
    path = watch_path(board, project)
    if watching:
        if path.is_file():
            path.unlink()
    else:
        payload = {
            "watching": False,
            "by": by,
            "ts": format_ts(utc_now(now)),
        }
        atomic_write_bytes(path, dump_json(payload))
    _refresh(board)


def set_all_projects(board: Path, watching: bool, by: str) -> None:
    """Set or clear the stop flag for every project."""
    if not board_is_ready(board):
        return
    projects = load_projects(board).get("projects") or {}
    for name in projects:
        set_project_watching(board, name, watching, by)


def write_heartbeat(
    board: Path,
    project: str,
    agent: str,
    *,
    since: datetime,
    seen: datetime,
) -> None:
    """Write the heartbeat atomically."""
    payload = {
        "agent": agent,
        "since": format_ts(since),
        "last_seen": format_ts(seen),
        "how": "watch",
    }
    atomic_write_bytes(watcher_path(board, project, agent), dump_json(payload))
    _refresh(board)


def end_heartbeat(board: Path, project: str, agent: str) -> None:
    """Remove the heartbeat so status shows this agent is not watching."""
    path = watcher_path(board, project, agent)
    if path.is_file():
        path.unlink()
    _refresh(board)


def _refresh(board: Path) -> None:
    from agentcoop.statusfile import refresh_status

    refresh_status(board)


def _parse_stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def heartbeat_seen(board: Path, project: str, agent: str) -> datetime | None:
    """Return the heartbeat time, or None when the agent is not watching."""
    data = _load_json(watcher_path(board, project, agent))
    if not data or data.get("ended"):
        return None
    return _parse_stamp(data.get("last_seen"))


def heartbeat_fresh(board: Path, project: str, agent: str, now: datetime | None = None) -> bool:
    """Return True when the heartbeat is under 3 minutes old."""
    seen = heartbeat_seen(board, project, agent)
    if seen is None:
        return False
    moment = utc_now(now)
    return (moment - seen).total_seconds() < STALE_SECONDS


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _emit(line: str, stream) -> None:
    stream.write(line)
    if not line.endswith("\n"):
        stream.write("\n")
    stream.flush()


def run_watch(
    board: Path,
    project: str,
    agent: str,
    *,
    timeout_seconds: float,
    poll_seconds: float = POLL_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    now=None,
    sleep=None,
    out=None,
) -> int:
    """Block until there is mail, the stop flag is set, the program changes, or the timeout passes.

    Exit 0 is new mail, 3 is a timeout, 4 means checking is stopped, and 5 means
    Agent Co-op was updated. The heartbeat file is removed when this returns.
    """
    from agentcoop.version import read_installed

    assert_member(board, project, agent)
    clock = now or (lambda: datetime.now(timezone.utc))
    pause = sleep or time.sleep
    stream = out if out is not None else sys.stdout
    from agentcoop.updatecheck import header_line

    notice = header_line(board, agent, record=True)
    if notice:
        _emit(notice, stream)
    started_version = read_installed(board)
    started = _aware(clock())
    deadline = started + timedelta(seconds=timeout_seconds)
    last_beat: datetime | None = None
    try:
        while True:
            current_version = read_installed(board)
            if current_version != started_version:
                _emit(
                    "Agent Co-op was updated to " + current_version + ". Run agentcoop watch again.",
                    stream,
                )
                return 5
            current = _aware(clock())
            if last_beat is None or (current - last_beat).total_seconds() >= heartbeat_seconds:
                write_heartbeat(board, project, agent, since=started, seen=current)
                last_beat = current
            report = check_inbox(board, project, agent, now=current.timestamp())
            if not checking_enabled(board, project):
                _emit(stopped_line(project), stream)
                return 4
            if report.messages:
                text = render_check(report)
                if text:
                    _emit(text if text.endswith("\n") else text + "\n", stream)
                _emit(HANDLE_LINE, stream)
                return 0
            if current >= deadline:
                _emit(TIMEOUT_LINE, stream)
                return 3
            remaining = (deadline - current).total_seconds()
            pause(min(poll_seconds, remaining) if remaining > 0 else 0)
    finally:
        end_heartbeat(board, project, agent)


def latest_desktop_seen(board: Path, project: str, agent: str) -> datetime | None:
    """Return the newest outbox answer or request time for an outbox helper."""
    from agentcoop.requests import requests_dir

    folders = (
        outbox_dir(board, project, agent),
        outbox_sent_dir(board, project, agent),
        requests_dir(board, project, agent),
    )
    newest: float | None = None
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in (".md", ".json"):
                continue
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or modified > newest:
                newest = modified
    if newest is None:
        return None
    return datetime.fromtimestamp(newest, timezone.utc)


def ago_phrase(seconds: float) -> str:
    """Return a short age, such as 'just now' or '2 minutes ago'."""
    minutes = int(max(seconds, 0) // 60)
    if minutes < 1:
        return "just now"
    if minutes == 1:
        return "1 minute ago"
    return str(minutes) + " minutes ago"


def agent_watch_line(board: Path, project: str, agent: str, now: datetime, *, outbox: bool) -> str:
    """Return one status line for an agent."""
    moment = utc_now(now)
    if outbox:
        seen = latest_desktop_seen(board, project, agent)
    else:
        seen = heartbeat_seen(board, project, agent)
    if seen is None:
        return agent + ": not watching."
    age = (moment - seen).total_seconds()
    if age >= STALE_SECONDS:
        return agent + ": not watching."
    return agent + ": watching, last check " + ago_phrase(age) + "."
