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
    fields_status = ""
    try:
        from agentcoop.progressmeta import parse_fields

        fields_status = str(parse_fields(body).get("Status") or "")
    except CoopError:
        fields_status = ""
    if fields_status in ("done", "review"):
        from agentcoop.donereport import check_done_report

        check_done_report(body)
    if fields_status == "done":
        from agentcoop.taskreview import assert_done_allowed

        assert_done_allowed(board, project, body)
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = stamp + "_" + driver + ".md"
    folder = progress_dir(board, project)
    private_dir(folder)
    path = folder / name
    while path.exists():
        moment = moment + timedelta(seconds=1)
        stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = stamp + "_" + driver + ".md"
        path = folder / name
    payload = body if body.endswith("\n") else body + "\n"
    mode = 0o600 if os.name != "nt" else 0o666
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), mode)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    from agentcoop.activity import note_activity

    note_activity(board, driver, "progress", now=moment)
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


def progress_template_text(*, helper: bool = False, done: bool = False) -> str:
    """Return the blank progress template. Helpers get the short one."""
    if done:
        from agentcoop.donereport import done_template

        return done_template()
    from agentcoop.install import read_template

    text = read_template("helper-progress.md.tmpl" if helper else "progress.md.tmpl")
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


def helper_tasks(board: Path, project: str, now: datetime | None = None) -> list[dict]:
    """Return the newest progress document for each helper request.

    A task with Status done is left out after one day.
    """
    from agentcoop.progressmeta import parse_fields

    root = progress_dir(board, project) / "helpers"
    if not root.is_dir():
        return []
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    newest: dict[tuple[str, str], tuple[str, Path]] = {}
    for agent_dir in sorted(root.iterdir()):
        if not agent_dir.is_dir():
            continue
        for path in agent_dir.glob("*.md"):
            name = path.name
            if len(name) < 18 or name[16] != "_":
                continue
            stamp = name[:16]
            message_id = name[17:-3]
            key = (agent_dir.name, message_id)
            current = newest.get(key)
            if current is None or stamp > current[0]:
                newest[key] = (stamp, path)
    tasks = []
    for (agent, message_id), (stamp, path) in sorted(newest.items()):
        try:
            text = path.read_text(encoding="utf-8")
            mtime = path.stat().st_mtime
        except OSError:
            continue
        fields = {}
        try:
            fields = parse_fields(text)
        except CoopError:
            fields = {}
        age_seconds = max(0, int(clock.timestamp() - mtime))
        status = str(fields.get("Status") or "working")
        if status == "done" and age_seconds > 86400:
            continue
        tasks.append(
            {
                "agent": agent,
                "id": message_id,
                "status": status,
                "step": str(fields.get("Step") or ""),
                "percent": str(fields.get("Percent") or ""),
                "now": str(fields.get("Now") or ""),
                "age_seconds": age_seconds,
                "path": path,
                "stamp": stamp,
            }
        )
    return tasks


def task_lines(board: Path, project: str, now: datetime | None = None) -> list[str]:
    """Return one short line for the driver task and each helper task."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    every = int(load_config(board).get("progress_every_minutes") or 10)
    stale_after = every * 2 * 60
    lines = []
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    driver = str(record.get("driver") or "")
    latest = latest_progress(board, project)
    if latest is not None and driver:
        line = _file_task_line(driver, latest, clock, stale_after)
        if line:
            lines.append(line)
    for task in helper_tasks(board, project, now=clock):
        who = str(task["agent"]) + " on " + str(task["id"])
        line = _format_task_line(
            who,
            str(task.get("status") or "working"),
            str(task.get("step") or ""),
            str(task.get("percent") or ""),
            str(task.get("now") or ""),
            int(task.get("age_seconds") or 0),
            stale_after,
        )
        if line:
            lines.append(line)
    return lines


def _file_task_line(who: str, path: Path, clock: datetime, stale_after: int) -> str:
    from agentcoop.progressmeta import parse_fields

    try:
        text = path.read_text(encoding="utf-8")
        age = max(0, int(clock.timestamp() - path.stat().st_mtime))
    except OSError:
        return ""
    try:
        fields = parse_fields(text)
    except CoopError:
        fields = {}
    status = str(fields.get("Status") or "working")
    if status == "done" and age > 86400:
        return ""
    now = str(fields.get("Now") or "") or section_text(text, "Working on now").replace("\n", " ").strip()
    return _format_task_line(
        who,
        status,
        str(fields.get("Step") or ""),
        str(fields.get("Percent") or ""),
        now,
        age,
        stale_after,
    )


def _format_task_line(
    who: str,
    status: str,
    step: str,
    percent: str,
    now: str,
    age_seconds: int,
    stale_after: int,
) -> str:
    bits = [status or "working"]
    if step:
        bits.append("step " + step)
    if percent:
        bits.append(str(percent) + "%")
    sentence = " ".join(now.split()).rstrip(".")
    if sentence:
        bits.append(sentence[:1].lower() + sentence[1:])
    bits.append(str(age_seconds // 60) + " min ago")
    line = who + ": " + ", ".join(bits)
    if status != "done" and age_seconds > stale_after:
        line += ", no update for " + str(age_seconds // 60) + " min"
    return line + "."


def check_task_line(task: dict) -> str:
    """Return one check header line for a helper task that is still running."""
    if task.get("status") == "done":
        return ""
    bits = []
    if task.get("percent"):
        bits.append(str(task["percent"]) + "%")
    now = str(task.get("now") or "").strip().rstrip(".")
    if now:
        bits.append(now[:1].lower() + now[1:])
    minutes = max(0, int(task.get("age_seconds") or 0) // 60)
    bits.append(str(minutes) + " min ago")
    return str(task.get("agent") or "") + " on " + str(task.get("id") or "") + ": " + ", ".join(bits) + "."


def progress_files_for(board: Path, project: str, agent: str, message_id: str) -> list[Path]:
    """Return one helper's progress files for one message, oldest first."""
    folder = helper_progress_dir(board, project, agent)
    if not folder.is_dir():
        return []
    suffix = "_" + message_id + ".md"
    found = [path for path in folder.glob("*.md") if path.name.endswith(suffix)]
    return sorted(found, key=lambda path: path.name)


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
    from agentcoop.activity import note_activity

    note_activity(board, agent, "progress", now=when)
    return name


def wait_for_reply(
    board: Path,
    project: str,
    agent: str,
    message_id: str,
    minutes: int,
    *,
    sleep=None,
    monotonic=None,
    wall=None,
) -> int:
    """Wait for a reply. Fresh helper progress moves the deadline, up to 60 minutes."""
    import time

    from agentcoop.core import file_id, new_messages
    from agentcoop.paths import inbox_msgs
    from agentcoop.progressmeta import parse_fields

    pause = sleep or time.sleep
    clock = monotonic or time.monotonic
    wall_clock = wall or time.time
    every = int(load_config(board).get("progress_every_minutes") or 10)
    fresh_seconds = every * 2 * 60
    started = clock()
    cap = started + 60 * 60
    deadline = started + minutes * 60
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    helper = ""
    for name in record.get("helpers") or {}:
        if (inbox_msgs(board, project, name) / (message_id + ".json")).is_file():
            helper = str(name)
            break
    from agentcoop.core import iter_project_messages
    from agentcoop.tasks import opinion_ids

    waiting_ids = opinion_ids(board, project, message_id)
    seen: set[str] = set()
    already = {
        file_id(item)
        for item in iter_project_messages(board, project, agent)
        if item.re in waiting_ids
    }
    first_mtime: float | None = None
    while True:
        messages, _issues = new_messages(board, project, agent)
        ready = []
        for wanted in waiting_ids:
            match = None
            for item in messages:
                if item.re == wanted and file_id(item) not in already:
                    match = item
                    break
            if match is None:
                ready = []
                break
            ready.append(match)
        if ready and len(ready) == len(waiting_ids):
            for item in ready:
                print(file_id(item) + "  " + item.subject)
            return 0
        if helper:
            for path in progress_files_for(board, project, helper, message_id):
                if path.name in seen:
                    continue
                seen.add(path.name)
                try:
                    text = path.read_text(encoding="utf-8")
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if first_mtime is None:
                    first_mtime = mtime
                try:
                    fields = parse_fields(text)
                except CoopError:
                    fields = {}
                step = str(fields.get("Step") or "")
                percent = str(fields.get("Percent") or "")
                now_text = str(fields.get("Now") or "").strip()
                bits = []
                if step and percent:
                    bits.append("step " + step + " (" + percent + "%).")
                elif step:
                    bits.append("step " + step + ".")
                elif percent:
                    bits.append(percent + "%.")
                if now_text:
                    bits.append(now_text if now_text.endswith(".") else now_text + ".")
                print(helper + ": " + " ".join(bits))
        if helper and seen:
            newest = progress_files_for(board, project, helper, message_id)
            if newest:
                try:
                    age = wall_clock() - newest[-1].stat().st_mtime
                except OSError:
                    age = fresh_seconds + 1
                if age <= fresh_seconds:
                    deadline = min(cap, clock() + minutes * 60)
        current = clock()
        if current >= deadline or current >= cap:
            if seen and helper and first_mtime is not None:
                worked = max(0, int((wall_clock() - first_mtime) // 60))
                print(
                    helper
                    + " has been working for "
                    + str(worked)
                    + " minutes. The wait stops at 60 minutes."
                )
            else:
                print(
                    "No reply to "
                    + message_id
                    + " after "
                    + str(minutes)
                    + " minutes. Keep working on something else, or post a progress update saying you are blocked."
                )
            return 3
        pause(5)


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
