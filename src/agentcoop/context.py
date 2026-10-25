"""Project context documents that helpers read and the driver writes."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop.core import message_sort_key
from agentcoop.errors import CoopError
from agentcoop.paths import private_dir, project_dir
from agentcoop.projects import (
    load_config,
    load_projects,
    require_board,
    role_of,
    validate_agent_name,
)
from agentcoop.safety import secret_warning

VERSION_RE = re.compile(r"^(\d{8}T\d{6}Z)_([a-z][a-z0-9_]{1,31})\.md$")

CONTEXT_TEMPLATE = """# Project context: <project name>

## What this project is
Two or three sentences: what it does, who uses it, and why it exists.

## Current goal
What we are working on right now, and what done looks like.

## Where things are
The folders and files that matter, with one line each. Absolute paths for anything a helper should read.

## How to build, run, and test
The exact commands, and what a passing result looks like.

## Conventions
Code style, naming, formatting, commit messages, and rules from the project's own docs (link them; do not copy them).

## Decisions already made
Choices that are settled and must not be reopened, each with a one-line reason.

## Current state
What works, what is in progress, what is broken, and known issues.

## What each helper is for here
One line per helper: what I will ask them, and what a useful answer looks like (format, length, patch or explanation).

## Do not
Things helpers must not suggest or do in this project.

## Where to learn more
Links to the project's README, design docs, issues, or anything else worth reading.

## Glossary
Project terms and abbreviations a newcomer would not know.
"""


def context_dir(board: Path, project: str) -> Path:
    """Return the folder of immutable context versions."""
    return project_dir(board, project) / "context"


def context_read_path(board: Path, project: str, agent: str) -> Path:
    """Return the file that records which context version an agent last read."""
    return project_dir(board, project) / "inbox" / agent / "context-read"


def list_versions(board: Path, project: str) -> list[Path]:
    """Return context version files, oldest first."""
    folder = context_dir(board, project)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        if path.is_file() and VERSION_RE.fullmatch(path.name):
            found.append(path)
    return sorted(found, key=lambda item: item.name)


def latest_version(board: Path, project: str) -> Path | None:
    """Return the latest context file, or None when there is none."""
    versions = list_versions(board, project)
    if not versions:
        return None
    return versions[-1]


def parse_version_name(name: str) -> tuple[str, str] | None:
    """Return (utc stamp, driver) from a version file name."""
    match = VERSION_RE.fullmatch(name)
    if not match:
        return None
    return match.group(1), match.group(2)


def version_time(name: str) -> datetime | None:
    """Parse the UTC time from a version file name."""
    parsed = parse_version_name(name)
    if parsed is None:
        return None
    try:
        return datetime.strptime(parsed[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_marker(board: Path, project: str, agent: str) -> str | None:
    """Return the last context version name this agent recorded as read."""
    path = context_read_path(board, project, agent)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def write_marker(board: Path, project: str, agent: str, version_name: str) -> None:
    """Record that an agent read a context version."""
    path = context_read_path(board, project, agent)
    private_dir(path.parent)
    payload = (version_name + "\n").encode("utf-8")
    tmp = path.with_name("." + path.name + "." + str(os.getpid()) + ".tmp")
    mode = 0o600 if os.name != "nt" else 0o666
    fd = os.open(
        str(tmp),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
        mode,
    )
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def write_context(
    board: Path,
    project: str,
    driver: str,
    body: str,
    *,
    allow_secret: bool = False,
    now: datetime | None = None,
) -> str:
    """Write a new context version. Returns the file name."""
    require_board(board)
    validate_agent_name(driver)
    projects = load_projects(board)
    record = (projects.get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ". Run: agentcoop project list")
    if role_of(record, driver) != "driver":
        raise CoopError("Only the driver of " + project + " writes the project context.")
    if not allow_secret and secret_warning(body):
        raise CoopError(
            "This context looks like it contains a password or API key, so it was "
            "not saved. Remove the secret and write again. If this is a false alarm, "
            "add --allow-secret."
        )
    config = load_config(board)
    limit = int(config.get("context_limit_bytes") or 65536)
    if len(body.encode("utf-8")) > limit:
        raise CoopError(
            "The context is too long. Keep it under 64 KB and link to files in the "
            "project for details."
        )
    moment = now or datetime.now(timezone.utc).replace(microsecond=0)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = stamp + "_" + driver + ".md"
    folder = context_dir(board, project)
    private_dir(folder)
    path = folder / name
    if path.exists():
        moment = moment + timedelta(seconds=1)
        stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = stamp + "_" + driver + ".md"
        path = folder / name
    payload = body if body.endswith("\n") else body + "\n"
    data = payload.encode("utf-8")
    tmp = path.with_name("." + path.name + ".tmp")
    mode = 0o600 if os.name != "nt" else 0o666
    fd = os.open(
        str(tmp),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
        mode,
    )
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return name


def show_context(
    board: Path,
    project: str,
    *,
    version: str | None = None,
    as_agent: str | None = None,
) -> str:
    """Return the text to print for context show."""
    require_board(board)
    projects = load_projects(board)
    record = (projects.get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ". Run: agentcoop project list")
    if version:
        path = context_dir(board, project) / version
        if not path.is_file():
            raise CoopError("There is no context version named " + version + ".")
    else:
        path = latest_version(board, project)
    if path is None:
        lines = ["There is no project context yet."]
        if as_agent and role_of(record, as_agent) == "helper":
            from agentcoop.paths import agentcoop_command

            cmd = agentcoop_command(board)
            driver = str(record.get("driver") or "")
            lines.append(
                "Ask the driver with: "
                + cmd
                + " send --as "
                + as_agent
                + " --to "
                + driver
                + ' --type context_request --subject "Context for '
                + project
                + '" --body "<what you need>"'
            )
        return "\n".join(lines) + "\n"
    parsed = parse_version_name(path.name)
    author = parsed[1] if parsed else "?"
    when = version_time(path.name)
    time_text = when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else path.name
    header = (
        "Project context for "
        + project
        + ", version "
        + path.name
        + ", written by "
        + author
        + " at "
        + time_text
        + "."
    )
    body = path.read_text(encoding="utf-8")
    if as_agent:
        validate_agent_name(as_agent)
        if role_of(record, as_agent) == "":
            raise CoopError(as_agent + " is not a member of " + project + ".")
        write_marker(board, project, as_agent, path.name)
    return header + "\n\n" + body


def list_context(board: Path, project: str) -> str:
    """Return a listing of context versions."""
    require_board(board)
    projects = load_projects(board)
    if project not in (projects.get("projects") or {}):
        raise CoopError("There is no project named " + project + ". Run: agentcoop project list")
    versions = list_versions(board, project)
    if not versions:
        return "No context versions yet.\n"
    lines = []
    for path in versions:
        parsed = parse_version_name(path.name)
        author = parsed[1] if parsed else "?"
        when = version_time(path.name)
        time_text = when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else "?"
        size = path.stat().st_size
        lines.append(path.name + "  " + time_text + "  " + author + "  " + str(size) + " bytes")
    return "\n".join(lines) + "\n"


def context_template_text() -> str:
    """Return the blank context template."""
    try:
        from agentcoop.install import read_template

        text = read_template("context.md.tmpl")
    except Exception:
        text = CONTEXT_TEMPLATE
    return text if text.endswith("\n") else text + "\n"


def archive_context(board: Path, project: str, older_than_days: int, now: datetime | None = None) -> int:
    """Remove old context versions, always keeping the latest 5."""
    require_board(board)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = moment - timedelta(days=older_than_days)
    versions = list_versions(board, project)
    if len(versions) <= 5:
        return 0
    keep = set(path.name for path in versions[-5:])
    removed = 0
    for path in versions:
        if path.name in keep:
            continue
        when = version_time(path.name)
        if when is None or when > cutoff:
            continue
        path.unlink()
        removed += 1
    return removed


def open_context_requests(board: Path, project: str, helper: str | None = None) -> list:
    """Return open context_request messages for helpers in a project.

    Requests live in the driver's inbox. A request is open until a message from
    the driver with re equal to its id is in the helper's msgs folder.
    """
    from agentcoop.core import file_id, load_message, list_message_paths
    from agentcoop.paths import inbox_msgs

    projects = load_projects(board)
    record = (projects.get("projects") or {}).get(project)
    if record is None:
        return []
    driver = str(record.get("driver") or "")
    helpers = list((record.get("helpers") or {}).keys())
    if helper is not None:
        helpers = [helper] if helper in helpers else []
    open_ones = []
    driver_msgs = list_message_paths(inbox_msgs(board, project, driver))
    requests_by_helper: dict[str, list] = {name: [] for name in helpers}
    for path in driver_msgs:
        message = load_message(path)
        if message is None:
            continue
        if (
            message.type == "context_request"
            and message.recipient == driver
            and message.sender in requests_by_helper
            and not message.re
        ):
            requests_by_helper[message.sender].append(message)
    for name in helpers:
        replies = set()
        for path in list_message_paths(inbox_msgs(board, project, name)):
            message = load_message(path)
            if message is None:
                continue
            if message.sender == driver and message.re:
                replies.add(message.re)
        for item in requests_by_helper[name]:
            if file_id(item) not in replies:
                open_ones.append(item)
    open_ones.sort(key=message_sort_key)
    return open_ones


def helper_has_open_context_request(board: Path, project: str, helper: str) -> bool:
    """Return True when this helper already has an unanswered context request."""
    return bool(open_context_requests(board, project, helper=helper))


def context_status(
    board: Path,
    project: str,
    agent: str,
    role: str,
) -> dict:
    """Build the context block for check --json and headers."""
    latest = latest_version(board, project)
    latest_name = latest.name if latest else None
    read = read_marker(board, project, agent) if role == "helper" else None
    open_reqs = []
    if role == "driver":
        for item in open_context_requests(board, project):
            open_reqs.append({"id": item.id, "from": item.sender, "subject": item.subject})
    return {
        "latest": latest_name,
        "read": read,
        "open_requests": open_reqs,
    }


def format_context_header(
    board: Path,
    project: str,
    agent: str,
    role: str,
    driver: str,
    helpers: dict,
) -> list[str]:
    """Return check-header lines about project context."""
    from agentcoop.paths import agentcoop_command

    cmd = agentcoop_command(board)
    lines: list[str] = []
    latest = latest_version(board, project)
    if role == "helper":
        if latest is None:
            lines.append(
                "Project context: none yet. Ask the driver before you start "
                "(see agentcoop context show)."
            )
            return lines
        read = read_marker(board, project, agent)
        if read != latest.name:
            when = version_time(latest.name)
            time_text = when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else latest.name
            lines.append(
                "Project context: updated "
                + time_text
                + ". Read it first: "
                + cmd
                + " context show --as "
                + agent
            )
        else:
            lines.append("Project context: read (version " + latest.name + ").")
        return lines
    # driver
    for item in open_context_requests(board, project):
        lines.append(
            item.sender
            + " is waiting for project context. Write it with agentcoop context write, "
            "then reply to the request."
        )
    if helpers and latest is None:
        lines.append(
            "Project context: none yet. Helpers will ask for it. Start from: "
            + cmd
            + " context template"
        )
    return lines
