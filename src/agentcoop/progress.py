"""Driver progress documents. A document, not a message."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import private_dir, project_dir
from agentcoop.projects import load_config, load_projects, require_board, role_of, validate_agent_name
from agentcoop.safety import secret_warning

VERSION_RE_PARTS = 2


def progress_dir(board: Path, project: str) -> Path:
    """Return the progress folder for a project."""
    return project_dir(board, project) / "progress"


def latest_progress(board: Path, project: str) -> Path | None:
    """Return the newest progress file, or None."""
    versions = list_progress(board, project)
    return versions[-1] if versions else None


def list_progress(board: Path, project: str) -> list[Path]:
    """Return progress files, oldest first."""
    folder = progress_dir(board, project)
    if not folder.is_dir():
        return []
    files = [path for path in folder.iterdir() if path.is_file() and path.suffix == ".md"]
    return sorted(files, key=lambda path: path.name)


def parse_progress_name(name: str) -> tuple[str, str] | None:
    """Return (stamp, driver) from a progress file name."""
    if not name.endswith(".md"):
        return None
    stem = name[:-3]
    stamp, sep, driver = stem.partition("_")
    if not sep or not stamp or not driver:
        return None
    return stamp, driver


def progress_time(name: str) -> datetime | None:
    """Return the UTC time encoded in a progress file name."""
    parsed = parse_progress_name(name)
    if parsed is None:
        return None
    try:
        return datetime.strptime(parsed[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def section_text(body: str, heading: str) -> str:
    """Return the text under one ## heading."""
    capture = False
    lines = []
    for line in body.splitlines():
        if line.startswith("## "):
            if capture:
                break
            capture = line.strip() == "## " + heading
            continue
        if capture:
            lines.append(line)
    return "\n".join(lines).strip()


def write_progress(
    board: Path,
    project: str,
    driver: str,
    body: str,
    *,
    allow_secret: bool = False,
    now: datetime | None = None,
) -> str:
    """Write a new progress version. Returns the file name."""
    require_board(board)
    validate_agent_name(driver)
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ".")
    if role_of(record, driver) != "driver":
        raise CoopError("Only the driver of " + project + " writes progress updates.")
    if not allow_secret and secret_warning(body):
        raise CoopError(
            "This progress update looks like it contains a password or API key, so it was "
            "not saved. Remove the secret and write again. If this is a false alarm, "
            "add --allow-secret."
        )
    limit = int(load_config(board).get("progress_limit_bytes") or 16384)
    if len(body.encode("utf-8")) > limit:
        raise CoopError(
            "The progress update is too long. Keep it under 16 KB and put details in a file you attach to a message."
        )
    moment = now or datetime.now(timezone.utc).replace(microsecond=0)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = stamp + "_" + driver + ".md"
    folder = progress_dir(board, project)
    private_dir(folder)
    path = folder / name
    payload = body if body.endswith("\n") else body + "\n"
    mode = 0o600 if os.name != "nt" else 0o666
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), mode)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return name


def show_progress(board: Path, project: str, version: str | None = None) -> str:
    """Return one progress version with a header."""
    require_board(board)
    if version:
        path = progress_dir(board, project) / version
        if path.name != version or not path.is_file():
            raise CoopError("There is no progress version named " + version + ".")
    else:
        path = latest_progress(board, project)
        if path is None:
            return "There is no progress update yet.\n"
    parsed = parse_progress_name(path.name)
    driver = parsed[1] if parsed else "?"
    when = progress_time(path.name)
    shown = when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else "?"
    header = "Progress for " + project + ", updated " + shown + " by " + driver + ".\n"
    return header + path.read_text(encoding="utf-8")


def list_progress_text(board: Path, project: str, limit: int | None = None) -> str:
    """List versions, newest first."""
    versions = list(reversed(list_progress(board, project)))
    if limit is not None:
        versions = versions[:limit]
    if not versions:
        return "No progress versions yet.\n"
    lines = []
    for path in versions:
        when = progress_time(path.name)
        shown = when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else "?"
        lines.append(path.name + "  " + shown + "  " + str(path.stat().st_size) + " bytes")
    return "\n".join(lines) + "\n"


def progress_template_text() -> str:
    """Return the blank progress template."""
    from agentcoop.install import read_template

    text = read_template("progress.md.tmpl")
    return text if text.endswith("\n") else text + "\n"


def archive_progress(board: Path, project: str, older_than_days: int, now: datetime | None = None) -> int:
    """Remove old progress versions, always keeping the latest 20."""
    require_board(board)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = moment - timedelta(days=older_than_days)
    versions = list_progress(board, project)
    if len(versions) <= 20:
        return 0
    keep = {path.name for path in versions[-20:]}
    removed = 0
    for path in versions:
        if path.name in keep:
            continue
        when = progress_time(path.name)
        if when is None or when > cutoff:
            continue
        path.unlink()
        removed += 1
    return removed


def progress_attachment(board: Path, project: str) -> Path:
    """Return the latest progress file, or raise when there is none."""
    path = latest_progress(board, project)
    if path is None:
        raise CoopError(
            "Post a progress update first with agentcoop progress write, so the helper knows where you are."
        )
    return path.resolve()


def record_urgent(board: Path, project: str, message_id: str, when: datetime | None = None) -> None:
    """Remember an urgent help request so doctor can notice a missing reply."""
    path = board / ".state" / "urgent.json"
    data = _load_urgent(board)
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    rows = data.setdefault("requests", [])
    rows.append(
        {
            "project": project,
            "id": message_id,
            "time": moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    private_dir(path.parent)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def urgent_requests(board: Path) -> list[dict]:
    """Return recorded urgent help requests."""
    return list(_load_urgent(board).get("requests") or [])


def reminder_line(board: Path, project: str, agent: str, *, now: datetime | None = None) -> str:
    """Return the check reminder when progress is stale and the driver was active."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    if role_of(record, agent) != "driver":
        return ""
    minutes = int(load_config(board).get("progress_every_minutes") or 10)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    latest = latest_progress(board, project)
    if latest is None:
        return ""
    when = progress_time(latest.name)
    if when is None:
        return ""
    age = (moment - when).total_seconds() / 60
    if age <= minutes:
        return ""
    if not _driver_was_active(board, project, agent, moment, minutes):
        return ""
    from agentcoop.pathenv import agentcoop_command

    command = agentcoop_command(board)
    return (
        "Your last progress update was "
        + str(int(age))
        + " minutes ago. Post one now: "
        + command
        + " progress template, then "
        + command
        + " progress write --as "
        + agent
        + " --body -"
    )


def status_lines(board: Path, project: str) -> list[str]:
    """Return the Working on now and Blocked lines for status."""
    path = latest_progress(board, project)
    if path is None:
        return []
    body = path.read_text(encoding="utf-8")
    when = progress_time(path.name)
    shown = when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else "?"
    working = section_text(body, "Working on now").replace("\n", " ")
    blocked = section_text(body, "Blocked or need help").replace("\n", " ")
    return [
        "Working on now (" + shown + "): " + (working or "(empty)"),
        "Blocked or need help: " + (blocked or "(empty)"),
    ]


def _driver_was_active(board: Path, project: str, agent: str, moment: datetime, minutes: int) -> bool:
    window = timedelta(minutes=minutes)
    touch = board / ".state" / ("active-" + agent)
    if touch.is_file():
        try:
            when = datetime.strptime(touch.read_text(encoding="utf-8").strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            if moment - when <= window:
                return True
        except ValueError:
            pass
    runs = board / ".state" / "runs.json"
    if runs.is_file():
        try:
            data = json.loads(runs.read_text(encoding="utf-8"))
            last = str(((data.get("agents") or {}).get(agent) or {}).get("last") or "")
            when = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if moment - when <= window:
                return True
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    from agentcoop.core import log_messages

    for item in log_messages(board, project):
        if item.sender != agent:
            continue
        try:
            when = datetime.strptime(item.ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if moment - when <= window:
            return True
    return False


def helper_progress_dir(board: Path, project: str, agent: str) -> Path:
    """Return the folder of one helper's progress documents."""
    return progress_dir(board, project) / "helpers" / agent


def write_helper_progress(
    board: Path,
    project: str,
    agent: str,
    message_id: str,
    body: str,
    *,
    status: str = "working",
    step: str = "",
    percent: str = "",
    now: str = "",
    allow_secret: bool = False,
    moment: datetime | None = None,
) -> str:
    """Write helper progress for one open request from the driver. Returns the file name."""
    from agentcoop.core import file_id, new_messages
    from agentcoop.progressmeta import format_fields

    require_board(board)
    validate_agent_name(agent)
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ".")
    if role_of(record, agent) != "helper":
        raise CoopError("Only a helper writes progress for a request. The driver writes project progress.")
    driver = str(record.get("driver") or "")
    messages, _issues = new_messages(board, project, agent)
    open_request = False
    for item in messages:
        if file_id(item) == message_id and item.sender == driver:
            open_request = True
            break
    if not open_request:
        raise CoopError(message_id + " is not an open request from the driver to " + agent + ".")
    text = format_fields(status=status or "working", step=step, percent=percent, now=now, for_id=message_id, rest=body)
    if not allow_secret and secret_warning(text):
        raise CoopError(
            "This progress update looks like it contains a password or API key, so it was "
            "not saved. Remove the secret and write again. If this is a false alarm, "
            "add --allow-secret."
        )
    when = moment or datetime.now(timezone.utc).replace(microsecond=0)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = helper_progress_dir(board, project, agent)
    private_dir(folder)
    name = stamp + "_" + message_id + ".md"
    path = folder / name
    payload = text if text.endswith("\n") else text + "\n"
    mode = 0o600 if os.name != "nt" else 0o666
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), mode)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return name


def _load_urgent(board: Path) -> dict:
    path = board / ".state" / "urgent.json"
    if not path.is_file():
        return {"requests": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"requests": []}
    if not isinstance(data, dict):
        return {"requests": []}
    data.setdefault("requests", [])
    return data
