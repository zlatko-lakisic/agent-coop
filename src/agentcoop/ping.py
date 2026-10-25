"""Pings and the heartbeat that answers them. A nudge stays a helper ping."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.projects import load_config, load_projects

PING_TYPES = ("ping", "nudge")
HEARTBEAT_STATUS = ("working", "idle", "blocked", "waiting")


def gap_minutes(board: Path) -> int:
    """How long a ping waits before the next one, and before an automatic heartbeat."""
    return int(load_config(board).get("progress_every_minutes") or 10)


def _parse_ts(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _moment(now: datetime | None) -> datetime:
    from agentcoop.core import utc_now

    return utc_now(now)


def _messages_for(board: Path, project: str, agent: str):
    from agentcoop.core import iter_project_messages

    return iter_project_messages(board, project, agent)


def heartbeat_for(board: Path, project: str, ping) -> object | None:
    """Return the heartbeat that answers this ping, if one was posted."""
    for message in _messages_for(board, project, ping.sender):
        if message.type == "heartbeat" and message.re == ping.id and message.sender == ping.recipient:
            return message
    return None


def ping_violation(board: Path, project: str, message, driver: str, helpers: dict) -> str | None:
    """Return a reason when this ping is not allowed."""
    from agentcoop.core import NUDGE_CHARS, file_id, format_ts

    if len(message.body) > NUDGE_CHARS:
        return "A ping can be at most 500 characters."
    if message.re:
        return "A ping does not answer another message."
    sender_is_driver = message.sender == driver
    if sender_is_driver:
        if message.recipient not in helpers:
            return "The driver can only ping a helper."
    else:
        if message.recipient != driver:
            return "A helper can only ping the driver."
        if message.sender not in helpers:
            return "The sender or recipient is not a member of this project."
    this_id = None if message.path is None else file_id(message)
    stored = [
        item
        for item in _messages_for(board, project, message.recipient)
        if item.type == "ping" and item.sender == message.sender and file_id(item) != this_id
    ]
    open_ones = [item for item in stored if heartbeat_for(board, project, item) is None]
    moment = _parse_ts(message.ts) or _moment(None)
    earlier = []
    for item in stored:
        when = _parse_ts(item.ts)
        if when is not None and when <= moment:
            earlier.append(when)
    latest = max(earlier, default=None)
    wait = timedelta(minutes=gap_minutes(board))
    if open_ones:
        until = latest + wait if latest is not None else moment
        return (
            "You already have an open ping to "
            + message.recipient
            + ". The next ping is allowed at "
            + format_ts(until)
            + ", after a heartbeat."
        )
    if latest is not None and moment < latest + wait:
        return "The next ping is allowed at " + format_ts(latest + wait) + "."
    return None


def heartbeat_violation(board: Path, project: str, message) -> str | None:
    """Return a reason when this heartbeat does not answer a ping."""
    from agentcoop.core import load_message
    from agentcoop.paths import inbox_msgs

    if not message.re:
        return "A heartbeat answers a ping. Use --re with the ping id."
    if message.status and message.status not in HEARTBEAT_STATUS:
        return "Heartbeat status must be working, idle, blocked, or waiting."
    original = load_message(inbox_msgs(board, project, message.sender) / (message.re + ".json"))
    if original is None or original.type not in PING_TYPES:
        return "A heartbeat answers a ping."
    if original.recipient != message.sender or original.sender != message.recipient:
        return "A heartbeat goes back to the agent who pinged you."
    return None


def _last_progress(board: Path, project: str, agent: str) -> str:
    from agentcoop.progress import helper_progress_dir, latest_progress

    folder = helper_progress_dir(board, project, agent)
    newest = None
    if folder.is_dir():
        files = [path for path in folder.glob("*.md") if path.is_file()]
        if files:
            newest = max(files, key=lambda path: path.name)
    if newest is None:
        newest = latest_progress(board, project)
    if newest is None:
        return "none"
    try:
        first = newest.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return "none"
    return first[0] if first else "none"


def _last_message(board: Path, project: str, agent: str) -> str:
    found = _messages_for(board, project, agent)
    if not found:
        return "none"
    latest = max(found, key=lambda item: item.ts)
    return latest.ts + " " + latest.subject


def _watch_text(board: Path, project: str, agent: str, now: datetime) -> str:
    from agentcoop.outbox import is_outbox_agent
    from agentcoop.watch import agent_watch_line

    return agent_watch_line(board, project, agent, now, outbox=is_outbox_agent(board, agent))


def post_due_heartbeats(board: Path, project: str, now: datetime | None = None) -> None:
    """Post an automatic heartbeat when a ping has had none in time."""
    from agentcoop.core import file_id, send

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    driver = str(record.get("driver") or "")
    names = [driver] + [str(name) for name in (record.get("helpers") or {})]
    moment = _moment(now)
    wait = timedelta(minutes=gap_minutes(board))
    for name in names:
        if not name:
            continue
        for ping in _messages_for(board, project, name):
            if ping.type != "ping":
                continue
            if heartbeat_for(board, project, ping) is not None:
                continue
            sent_at = _parse_ts(ping.ts)
            if sent_at is None or moment < sent_at + wait:
                continue
            body = (
                ping.recipient
                + " did not answer. "
                + _watch_text(board, project, ping.recipient, moment)
                + " Latest progress: "
                + _last_progress(board, project, ping.recipient)
                + ". Last message: "
                + _last_message(board, project, ping.recipient)
                + "."
            )
            status = "idle"
            try:
                send(
                    board,
                    project=project,
                    sender=ping.recipient,
                    recipient=ping.sender,
                    subject="Heartbeat",
                    body=body[:500],
                    type="heartbeat",
                    re=file_id(ping),
                    auto=True,
                    status=status,
                    next_text=ping.recipient + " did not answer.",
                    now=moment,
                )
            except CoopError:
                continue


def quiet_since(board: Path, project: str, agent: str) -> str:
    """Return the time an agent stopped answering, after two automatic heartbeats."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    names = [str(record.get("driver") or "")]
    names.extend(str(name) for name in (record.get("helpers") or {}))
    pings = []
    for name in names:
        if not name:
            continue
        for message in _messages_for(board, project, name):
            if message.type == "ping" and message.recipient == agent:
                pings.append(message)
    pings.sort(key=lambda item: item.ts)
    if len(pings) < 2:
        return ""
    last_two = pings[-2:]
    for ping in last_two:
        beat = heartbeat_for(board, project, ping)
        if beat is None or not beat.auto:
            return ""
    return last_two[0].ts


def not_answering_lines(board: Path, project: str) -> list[str]:
    """One line per agent who missed two pings in a row."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    names = []
    driver = str(record.get("driver") or "")
    if driver:
        names.append(driver)
    names.extend(str(name) for name in (record.get("helpers") or {}))
    lines = []
    for name in names:
        since = quiet_since(board, project, name)
        if since:
            lines.append(name + " is not answering since " + since + ".")
    return lines
