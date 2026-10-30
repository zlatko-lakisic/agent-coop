"""Turn a board into one list of events for the viewer."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop.core import log_messages
from agentcoop.errors import CoopError
from agentcoop.projects import load_projects, require_board
from agentcoop.safety import secret_warning

_CACHE: dict[str, tuple] = {}
_RESULT: dict[tuple, tuple] = {}

MESSAGE_KINDS = {
    "question": "message.question",
    "answer": "message.answer",
    "task": "message.task",
    "status": "message.status",
    "ack": "message.ack",
    "patch": "message.patch",
    "context_request": "message.context_request",
    "nudge": "message.nudge",
    "ping": "message.ping",
    "heartbeat": "message.heartbeat",
    "decline": "message.decline",
    "cancel": "message.cancel",
}

# One severity table for every timeline kind. Keep design/viewer/SEVERITY.md in step.
SEVERITY: dict[str, str] = {
    "message.question": "info",
    "message.answer": "info",
    "message.task": "info",
    "message.status": "info",
    "message.ack": "info",
    "message.patch": "info",
    "message.context_request": "info",
    "message.nudge": "attention",
    "message.ping": "info",
    "message.heartbeat": "info",
    "message.decline": "attention",
    "message.cancel": "attention",
    "message.review_request": "info",
    "message.review_verdict": "info",
    "message.move": "info",
    "message.unknown": "info",
    "progress.update": "info",
    "progress.review": "info",
    "progress.waiting": "attention",
    "progress.done": "info",
    "context.version": "info",
    "membership.join": "info",
    "membership.driver": "info",
    "membership.helper_add": "info",
    "membership.helper_remove": "info",
    "settings.watch": "attention",
    "settings.review": "info",
    "settings.notice": "info",
    "settings.approval": "info",
    "settings.override": "attention",
    "file.shared": "info",
    "file.held": "attention",
    "outbox.sent": "info",
    "outbox.rejected": "attention",
    "grade.late": "attention",
    "grade.answer": "info",
    "round.log": "info",
    "trace.line": "info",
    "stall.mail": "attention",
    "stall.progress": "problem",
    "update.check": "info",
    "update.installed": "info",
    "review.metric": "info",
}

SYSTEM_ACTOR = "Agent Co-op"


def severity_for(kind: str, *, verdict: str = "") -> str:
    """Return the severity for one event kind. Verdict can raise a review to attention."""
    level = SEVERITY.get(kind)
    if level is None:
        if kind.startswith("message."):
            level = "info"
        elif kind.startswith("stall."):
            level = "problem"
        elif kind.startswith("update."):
            level = "info"
        else:
            level = "info"
    if kind == "message.review_verdict" and verdict in ("changes", "cannot_verify"):
        return "attention"
    if kind == "review.metric" and verdict in ("changes", "cannot_verify"):
        return "attention"
    return level


def clear_cache() -> None:
    """Drop the file cache. Tests use this between boards."""
    _CACHE.clear()
    _RESULT.clear()


def events_for(board: Path, project: str | None = None) -> list[dict]:
    """Return every event on the board, oldest first."""
    require_board(board)
    key = (str(board), project or "*")
    signature = _signature(board, project)
    cached = _RESULT.get(key)
    if cached and cached[0] == signature:
        return _with_stalls(board, project, cached[1])
    projects = load_projects(board).get("projects") or {}
    names = [project] if project else sorted(projects)
    found: list[dict] = []
    for name in names:
        if name not in projects:
            continue
        found.extend(_project_events(board, name, projects[name]))
    found.extend(_board_events(board))
    found.sort(key=_oldest_key)
    for index, event in enumerate(found, start=1):
        event["order"] = index
    _RESULT[key] = (signature, found)
    return _with_stalls(board, project, found)


def query_events(
    board: Path,
    *,
    project: str | None = None,
    start: str | None = None,
    end: str | None = None,
    actors: list[str] | None = None,
    kinds: list[str] | None = None,
    query: str | None = None,
    severity: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    include_low: bool = False,
) -> dict:
    """Return one page of events, newest first."""
    rows = [
        event
        for event in events_for(board, project)
        if _matches(event, start, end, actors, kinds, query, severity, include_low)
    ]
    rows.reverse()
    begin = 0
    if cursor:
        for index, event in enumerate(rows):
            if event["id"] == cursor:
                begin = index + 1
                break
    page = rows[begin : begin + max(1, min(limit, 200))]
    nxt = page[-1]["id"] if begin + len(page) < len(rows) and page else ""
    day_counts: dict[str, int] = {}
    for event in rows:
        day = str(event.get("ts") or "")[:10]
        if len(day) == 10:
            day_counts[day] = day_counts.get(day, 0) + 1
    return {
        "events": [_public(event) for event in page],
        "next": nxt,
        "total": len(rows),
        "day_counts": day_counts,
    }


def histogram(
    board: Path,
    *,
    project: str | None = None,
    start: str | None = None,
    end: str | None = None,
    buckets: int = 24,
) -> dict:
    """Return counts per time bucket, plus markers."""
    rows = [
        event
        for event in events_for(board, project)
        if _in_range(event["ts"], start, end) and not event.get("low_level")
    ]
    count = max(1, min(int(buckets or 24), 200))
    if not rows:
        return {"buckets": []}
    times = [_parse(event["ts"]) for event in rows]
    times = [item for item in times if item is not None]
    if not times:
        return {"buckets": []}
    first = min(times)
    last = max(times)
    span = max((last - first).total_seconds(), 1.0)
    width = span / count
    slots = [
        {"start": "", "actors": {}, "severity": {}, "markers": []}
        for _ in range(count)
    ]
    for event in rows:
        when = _parse(event["ts"])
        if when is None:
            continue
        index = min(count - 1, int((when - first).total_seconds() / width))
        slot = slots[index]
        if not slot["start"]:
            slot["start"] = event["ts"]
        actor = str(event.get("actor") or "")
        slot["actors"][actor] = int(slot["actors"].get(actor) or 0) + 1
        level = str(event.get("severity") or "info")
        slot["severity"][level] = int(slot["severity"].get(level) or 0) + 1
        marker = _marker(str(event.get("kind") or ""))
        if marker and marker not in slot["markers"]:
            slot["markers"].append(marker)
    return {"buckets": [slot for slot in slots if slot["start"]]}


def event_detail(board: Path, event_id: str) -> dict | None:
    """Return one event, its text, and the thread around it."""
    rows = events_for(board, None)
    found = None
    for event in rows:
        if event["id"] == event_id:
            found = event
            break
    if found is None:
        return None
    thread_id = str((found.get("refs") or {}).get("thread") or found["id"])
    thread = [
        _public(event, full=True)
        for event in rows
        if event["id"] == thread_id or str((event.get("refs") or {}).get("thread") or "") == thread_id
    ]
    detail = _public(found, full=True)
    detail["thread"] = thread
    return detail


def projects_view(board: Path) -> list[dict]:
    """Return project names, roles, and the last activity time."""
    require_board(board)
    projects = load_projects(board).get("projects") or {}
    rows = []
    for name, record in projects.items():
        activity = ""
        for event in events_for(board, name):
            if event["ts"] > activity:
                activity = event["ts"]
        rows.append(
            {
                "name": name,
                "driver": record.get("driver") or "",
                "helpers": list((record.get("helpers") or {}).keys()),
                "last": activity,
            }
        )
    return rows


def _project_events(board: Path, project: str, record: dict) -> list[dict]:
    found: list[dict] = []
    for message in log_messages(board, project):
        found.append(_message_event(board, project, message))
    root = board / "projects" / project
    found.extend(_progress_events(board, project, root / "progress"))
    found.extend(_context_events(project, root / "context"))
    found.extend(_history_events(project, record))
    if record.get("review"):
        found.append(
            _event(
                project + ":review-mode",
                _stamp(str(record.get("created") or "")),
                0,
                project,
                SYSTEM_ACTOR,
                "settings.review",
                "Review mode " + str(record.get("review")),
                "Review is " + str(record.get("review")) + ".",
                {},
                [],
                severity_for("settings.review"),
            )
        )
    for index, notice in enumerate(record.get("notices") or []):
        if not isinstance(notice, str):
            continue
        found.append(
            _event(
                project + ":notice:" + str(index),
                _stamp(str(record.get("created") or "")),
                index,
                project,
                SYSTEM_ACTOR,
                "settings.notice",
                "Notice",
                _plain(notice),
                {},
                [],
                severity_for("settings.notice"),
            )
        )
    found.extend(_request_events(project, root / "requests"))
    found.extend(_review_events(project, root / "reviews"))
    found.extend(_approval_events(project, root / "approvals.json"))
    found.extend(_share_events(board, project))
    found.extend(_outbox_events(project, root / "outbox"))
    found.extend(_grade_events(project, root / "grades"))
    watch = root / "watch.json"
    if watch.is_file():
        found.append(
            _file_event(
                watch,
                project,
                SYSTEM_ACTOR,
                "settings.watch",
                "Checking stopped",
                "Checking is stopped for this project.",
                severity_for("settings.watch"),
            )
        )
    return found


def _board_events(board: Path) -> list[dict]:
    found = []
    log = board / "logs" / "schedule" / "round.log"
    if log.is_file():
        found.extend(_round_events(log))
    trace = board / "logs" / "trace.jsonl"
    if trace.is_file():
        found.extend(_trace_events(trace))
    mail = board / ".state" / "cursor-mail.json"
    if mail.is_file():
        found.append(
            _file_event(
                mail,
                "",
                "cursor",
                "stall.mail",
                "Mail not picked up",
                _read_text(mail)[:200],
                severity_for("stall.mail"),
            )
        )
    check = board / "update-check.json"
    if check.is_file():
        found.append(
            _file_event(
                check,
                "",
                SYSTEM_ACTOR,
                "update.check",
                "Update check",
                "Update check ran.",
                severity_for("update.check"),
            )
        )
    previous = board / "bin" / "previous"
    if previous.is_dir():
        for path in sorted(previous.iterdir()):
            if path.is_dir():
                found.append(
                    _file_event(
                        path,
                        "",
                        SYSTEM_ACTOR,
                        "update.installed",
                        "Update " + path.name,
                        "An older installed copy was kept.",
                        severity_for("update.installed"),
                    )
                )
    return found


def _message_event(board: Path, project: str, message) -> dict:
    kind = MESSAGE_KINDS.get(message.type, "message." + (message.type or "unknown"))
    if message.type == "review":
        kind = "message.review_verdict" if message.verdict else "message.review_request"
    if message.moved_from:
        kind = "message.move"
    severity = severity_for(kind, verdict=str(message.verdict or ""))
    title = message.subject or message.type
    summary = _plain(message.body)
    source = ""
    if message.path is not None:
        source = _rel(board, message.path)
    attachments = []
    for info in message.attachment_info or []:
        name = str(info.get("name") or "")
        attachments.append(
            {
                "name": name,
                "kind": str(info.get("kind") or ""),
                "bytes": int(info.get("bytes") or 0),
                "url": "/files/" + project + "/" + name if name else "",
            }
        )
    return _event(
        source or message.id,
        message.ts if message.ts.endswith("Z") else message.ts + "Z",
        int(message.seq or 0),
        project,
        message.sender,
        kind,
        title,
        summary,
        {"re": message.re or "", "thread": message.re or message.id, "group": message.group or ""},
        attachments,
        severity,
        body=message.body,
        seq=message.seq,
    )


def _progress_events(board: Path, project: str, folder: Path) -> list[dict]:
    from agentcoop.progressmeta import parse_fields

    if not folder.is_dir():
        return []
    found = []
    for path in folder.rglob("*.md"):
        if not path.is_file():
            continue
        text = _cached_text(path)
        try:
            fields = parse_fields(text)
        except CoopError:
            fields = {}
        status = str(fields.get("Status") or "")
        kind = "progress.update"
        if status == "review":
            kind = "progress.review"
        elif status == "waiting":
            kind = "progress.waiting"
        elif status == "done":
            kind = "progress.done"
        now = str(fields.get("Now") or "")
        title = "Progress"
        if status:
            title = "Progress " + status
        if now:
            title = title + ": " + now[:80]
        actor = path.stem.split("_", 1)[-1] if "_" in path.stem else ""
        found.append(
            _file_event(
                path,
                project,
                actor,
                kind,
                title,
                _plain(text),
                severity_for(kind),
                body=text,
            )
        )
    return found


def _context_events(project: str, folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.md"):
        actor = path.stem.split("_", 1)[-1] if "_" in path.stem else ""
        found.append(
            _file_event(
                path,
                project,
                actor,
                "context.version",
                "Context " + path.name,
                _plain(_cached_text(path)),
                severity_for("context.version"),
                body=_cached_text(path),
            )
        )
    return found


def _with_stalls(board: Path, project: str | None, rows: list[dict]) -> list[dict]:
    """Add a stall event from the frozen snapshot. This is not cached."""
    from agentcoop.progress import stall_snapshot

    names = [project] if project else list(load_projects(board).get("projects") or {})
    extra = []
    for name in names:
        if not name:
            continue
        snap = stall_snapshot(board, name)
        if not snap:
            continue
        stamp = str(snap.get("detected_at") or "")
        if not stamp.endswith("Z"):
            stamp = _mtime_stamp(board / "projects" / name / "progress")
        extra.append(
            _event(
                name + ":stall:" + str(snap.get("progress") or ""),
                stamp,
                0,
                name,
                SYSTEM_ACTOR,
                "stall.progress",
                "Driver stalled",
                str(snap.get("text") or ""),
                {},
                [],
                severity_for("stall.progress"),
            )
        )
    if not extra:
        return rows
    combined = list(rows) + extra
    combined.sort(key=_oldest_key)
    for index, event in enumerate(combined, start=1):
        event["order"] = index
    return combined


def _history_events(project: str, record: dict) -> list[dict]:
    found = []
    previous: set[str] = set()
    previous_driver = ""
    for index, row in enumerate(record.get("roles_history") or []):
        if not isinstance(row, dict):
            continue
        driver = str(row.get("driver") or "")
        helpers = {str(item) for item in (row.get("helpers") or [])}
        since = _stamp(str(row.get("since") or record.get("created") or ""))
        if index == 0:
            found.append(
                _event(
                    project + ":roles:" + str(index),
                    since,
                    index,
                    project,
                    driver or SYSTEM_ACTOR,
                    "membership.join",
                    "Driver " + driver,
                    "Helpers: " + ", ".join(sorted(helpers)),
                    {},
                    [],
                    severity_for("membership.join"),
                )
            )
        elif driver != previous_driver:
            found.append(
                _event(
                    project + ":roles:" + str(index),
                    since,
                    index,
                    project,
                    driver or SYSTEM_ACTOR,
                    "membership.driver",
                    "Driver " + driver,
                    "Helpers: " + ", ".join(sorted(helpers)),
                    {},
                    [],
                    severity_for("membership.driver"),
                )
            )
        for name in sorted(helpers - previous):
            if index == 0:
                continue
            found.append(
                _event(
                    project + ":helper-add:" + name + ":" + str(index),
                    since,
                    index,
                    project,
                    name,
                    "membership.helper_add",
                    "Helper " + name,
                    "Added as a helper.",
                    {},
                    [],
                    severity_for("membership.helper_add"),
                )
            )
        for name in sorted(previous - helpers):
            found.append(
                _event(
                    project + ":helper-remove:" + name + ":" + str(index),
                    since,
                    index,
                    project,
                    name,
                    "membership.helper_remove",
                    "Helper " + name,
                    "Removed as a helper.",
                    {},
                    [],
                    severity_for("membership.helper_remove"),
                )
            )
        previous = helpers
        previous_driver = driver
    return found


def _request_events(project: str, folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    found = []
    for path in folder.rglob("*.json"):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            data = json.loads(_cached_text(path))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        action = str(data.get("action") or path.stem)
        kind = "settings.watch" if action in ("stop_watching", "start_watching") else "membership." + action
        actor = path.parent.name if path.parent.name not in ("requests", "done", "rejected") else SYSTEM_ACTOR
        found.append(
            _file_event(
                path,
                project,
                actor,
                kind,
                action.replace("_", " "),
                action,
                severity_for(kind),
            )
        )
    return found


def _review_events(project: str, folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.json"):
        if path.name in ("open.json", "fingerprint.json"):
            continue
        try:
            data = json.loads(_cached_text(path))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if path.name == "override.json":
            found.append(
                _file_event(
                    path,
                    project,
                    str(data.get("by") or ""),
                    "settings.override",
                    "Review override",
                    "The person marked the task done anyway.",
                    severity_for("settings.override"),
                )
            )
            continue
        verdict = str(data.get("verdict") or "")
        found.append(
            _file_event(
                path,
                project,
                str(data.get("reviewer") or ""),
                "review.metric",
                "Review " + verdict,
                verdict,
                severity_for("review.metric", verdict=verdict),
            )
        )
    return found


def _approval_events(project: str, path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(_cached_text(path))
    except json.JSONDecodeError:
        return []
    rows = data if isinstance(data, list) else data.get("approvals") if isinstance(data, dict) else []
    if not isinstance(rows, list):
        rows = []
    found = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        found.append(
            _event(
                _source(path) + ":" + str(index),
                _mtime_stamp(path),
                index,
                project,
                str(row.get("by") or "person"),
                "settings.approval",
                "Standing approval",
                _plain(str(row.get("text") or row.get("phrase") or "Standing approval")),
                {},
                [],
                severity_for("settings.approval"),
            )
        )
    if not found and path.is_file():
        found.append(
            _file_event(
                path,
                project,
                "person",
                "settings.approval",
                "Standing approvals",
                "Standing approvals are saved.",
                severity_for("settings.approval"),
            )
        )
    return found


def _share_events(board: Path, project: str) -> list[dict]:
    from agentcoop.share import load_index, load_pending

    index = load_index(board, project)
    held = {str(item.get("name") or "") for item in load_pending(board, project)}
    found = []
    for info in index.get("files") or []:
        if not isinstance(info, dict):
            continue
        name = str(info.get("name") or "")
        kind = "file.held" if info.get("held") or name in held else "file.shared"
        url = "" if kind == "file.held" else "/files/" + project + "/" + name
        found.append(
            _event(
                project + ":file:" + name,
                _stamp(str(info.get("shared_on") or "")),
                0,
                project,
                str(info.get("sender") or ""),
                kind,
                name,
                str(info.get("description") or name),
                {"thread": str(info.get("message") or "")},
                [{"name": name, "kind": str(info.get("kind") or ""), "bytes": int(info.get("bytes") or 0), "url": url}],
                severity_for(kind),
            )
        )
    return found


def _outbox_events(project: str, folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    found = []
    for agent_dir in folder.iterdir():
        if not agent_dir.is_dir():
            continue
        sent = agent_dir / "sent"
        rejected = agent_dir / "rejected"
        if sent.is_dir():
            for path in sent.glob("*.md"):
                found.append(
                    _file_event(
                        path,
                        project,
                        agent_dir.name,
                        "outbox.sent",
                        "Sent " + path.name,
                        _plain(_cached_text(path)),
                        severity_for("outbox.sent"),
                        body=_cached_text(path),
                    )
                )
        if rejected.is_dir():
            for path in rejected.glob("*.md"):
                reason = ""
                reason_path = path.with_name(path.name + ".reason.txt")
                if reason_path.is_file():
                    reason = _cached_text(reason_path).strip()
                found.append(
                    _file_event(
                        path,
                        project,
                        agent_dir.name,
                        "outbox.rejected",
                        "Rejected " + path.name,
                        reason or "Rejected.",
                        severity_for("outbox.rejected"),
                        body=_cached_text(path),
                    )
                )
    return found


def _grade_events(project: str, folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    found = []
    for path in folder.rglob("*.json"):
        if "removed" in path.parts:
            continue
        try:
            data = json.loads(_cached_text(path))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        score = str(data.get("score") or "")
        kind = "grade.late" if data.get("late") else "grade.answer"
        found.append(
            _file_event(
                path,
                project,
                path.parent.name,
                kind,
                "Grade " + score,
                str(data.get("note") or score),
                severity_for(kind),
            )
        )
    return found


def _round_events(path: Path) -> list[dict]:
    found = []
    for index, line in enumerate(_cached_text(path).splitlines()):
        if not line.strip():
            continue
        stamp, _, rest = line.partition(" ")
        found.append(
            _event(
                _source(path) + ":" + str(index),
                _stamp(stamp),
                index,
                "",
                SYSTEM_ACTOR,
                "round.log",
                "Background round",
                rest or line,
                {},
                [],
                severity_for("round.log"),
                low_level=False,
            )
        )
    return found


def _trace_events(path: Path) -> list[dict]:
    found = []
    for index, line in enumerate(_cached_text(path).splitlines()):
        if not line.strip():
            continue
        title = "Trace"
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                title = str(data.get("command") or "Trace")
        except json.JSONDecodeError:
            data = {}
        found.append(
            _event(
                _source(path) + ":" + str(index),
                _mtime_stamp(path),
                index,
                "",
                SYSTEM_ACTOR,
                "trace.line",
                title,
                _plain(line),
                {},
                [],
                severity_for("trace.line"),
                low_level=True,
            )
        )
    return found


def _file_event(
    path: Path,
    project: str,
    actor: str,
    kind: str,
    title: str,
    summary: str,
    severity: str,
    body: str = "",
) -> dict:
    return _event(
        _source(path),
        _mtime_stamp(path),
        0,
        project,
        actor,
        kind,
        title,
        summary,
        {},
        [],
        severity,
        body=body,
    )


def _event(
    source: str,
    ts: str,
    order: int,
    project: str,
    actor: str,
    kind: str,
    title: str,
    summary: str,
    refs: dict,
    attachments: list,
    severity: str,
    body: str = "",
    seq: int | None = None,
    low_level: bool = False,
) -> dict:
    clean_title = _mask(title)
    clean_summary = _mask(summary)[:200]
    clean_body = _mask(body) if body else ""
    return {
        "id": _eid(source, kind),
        "ts": ts if ts.endswith("Z") else (ts + "Z" if ts else _mtime_stamp(Path("."))),
        "order": order,
        "project": project,
        "actor": actor,
        "kind": kind,
        "title": clean_title,
        "summary": clean_summary,
        "refs": refs,
        "attachments": attachments,
        "severity": severity,
        "source": source,
        "body": clean_body,
        "seq": seq,
        "low_level": low_level,
    }


def _public(event: dict, full: bool = False) -> dict:
    shown = {
        "id": event["id"],
        "ts": event["ts"],
        "order": event["order"],
        "project": event["project"],
        "actor": event["actor"],
        "kind": event["kind"],
        "title": event["title"],
        "summary": event["summary"],
        "refs": event.get("refs") or {},
        "attachments": event.get("attachments") or [],
        "severity": event.get("severity") or "info",
        "source": event.get("source") or "",
    }
    if full:
        shown["body"] = event.get("body") or ""
    return shown


def _matches(event, start, end, actors, kinds, query, severity, include_low) -> bool:
    if event.get("low_level") and not include_low:
        return False
    if not _in_range(event["ts"], start, end):
        return False
    if actors and event.get("actor") not in actors:
        return False
    if kinds and not any(str(event.get("kind") or "").startswith(item) for item in kinds):
        return False
    if severity and event.get("severity") != severity:
        return False
    if query:
        hay = (str(event.get("title") or "") + "\n" + str(event.get("body") or "") + "\n" + str(event.get("summary") or "")).lower()
        if query.lower() not in hay:
            return False
    return True


def _in_range(ts: str, start: str | None, end: str | None) -> bool:
    if start and ts < start:
        return False
    if end and ts > end:
        return False
    return True


def _oldest_key(event: dict) -> tuple:
    """Sort by time, then send order (seq), then source. Same day never needs two keys."""
    seq = event.get("seq")
    send_order = int(seq) if seq is not None else 0
    return (
        event.get("ts") or "",
        send_order,
        int(event.get("order") or 0),
        event.get("source") or "",
        event.get("kind") or "",
    )


def _marker(kind: str) -> str:
    if kind.startswith("stall."):
        return "stall"
    if "review" in kind:
        return "review"
    if kind == "settings.override":
        return "override"
    if kind == "outbox.rejected":
        return "rejected"
    if kind.startswith("update."):
        return "update"
    return ""


def _mask(text: str) -> str:
    if text and secret_warning(text):
        return "[hidden: looks like a secret]"
    return text


def _plain(text: str) -> str:
    return " ".join(str(text or "").split())


def _eid(source: str, kind: str) -> str:
    import hashlib

    return hashlib.sha256((kind + "\0" + source).encode("utf-8")).hexdigest()[:16]


def _parse(value: str):
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _stamp(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "1970-01-01T00:00:00Z"
    if text.endswith("Z"):
        return text
    if "T" in text:
        return text + "Z"
    return "1970-01-01T00:00:00Z"


def _mtime_stamp(path: Path) -> str:
    try:
        when = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return "1970-01-01T00:00:00Z"
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _source(path: Path) -> str:
    text = path.as_posix()
    for marker in ("/projects/", "/logs/", "/.state/", "/bin/"):
        index = text.find(marker)
        if index >= 0:
            return text[index + 1 :]
    return path.name


def _rel(board: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(board.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _signature(board: Path, project: str | None) -> tuple:
    roots = [board / "logs", board / ".state"]
    if project:
        roots.append(board / "projects" / project)
    else:
        roots.append(board / "projects")
    count = 0
    newest = 0
    for root in roots:
        count, newest = _scan_signature(root, count, newest)
    return (count, newest)


def _scan_signature(root: Path, count: int, newest: int) -> tuple:
    if not root.is_dir():
        return count, newest
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        with entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                count += 1
                if stat.st_mtime_ns > newest:
                    newest = stat.st_mtime_ns
    return count, newest


def agents_view(board: Path, project: str) -> list[dict]:
    """Return each agent's role, profile, grades, and recent activity."""
    require_board(board)
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    from agentcoop.grades import summarize_project
    from agentcoop.profiles import profile_of
    from agentcoop.projects import load_agents
    from agentcoop.taskreview import review_summary
    from agentcoop.watch import watcher_path, watch_path

    known = load_agents(board).get("agents") or {}
    names = []
    driver = str(record.get("driver") or "")
    if driver:
        names.append(driver)
    names.extend((record.get("helpers") or {}).keys())
    by_actor: dict[str, list[dict]] = {}
    for event in events_for(board, project):
        by_actor.setdefault(str(event.get("actor") or ""), []).append(event)
    stopped = watch_path(board, project).is_file()
    rows = []
    for name in names:
        agent = known.get(name) or {}
        role = "driver" if name == driver else "helper"
        if role == "helper" and "review" in str((record.get("helpers") or {}).get(name) or "").casefold():
            role = "reviewer"
        events = by_actor.get(name) or []
        last = max((str(event.get("ts") or "") for event in events), default="")
        heartbeat = {}
        raw = watcher_path(board, project, name)
        if raw.is_file():
            try:
                loaded = json.loads(raw.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    heartbeat = loaded
            except (OSError, json.JSONDecodeError):
                heartbeat = {}
        state = "offline"
        if stopped:
            state = "stopped"
        elif heartbeat.get("last_seen"):
            state = "watching"
        if role == "driver":
            from agentcoop.progress import stall_snapshot, waiting_line

            if waiting_line(board, project):
                state = "waiting"
            elif stall_snapshot(board, project):
                state = "stalled"
        profile = profile_of(record, name)
        if isinstance(agent.get("accepts"), list):
            profile["accepts"] = [str(item) for item in agent["accepts"]]
        else:
            profile["accepts"] = []
        grades = summarize_project(board, project, name) if role != "driver" else {}
        rows.append(
            {
                "name": name,
                "role": role,
                "tool": agent.get("tool") or "",
                "profile": profile,
                "state": state,
                "last_seen": last or str(heartbeat.get("last_seen") or ""),
                "grades": grades,
                "review": review_summary(board, project, name) if role == "driver" else "",
                "open_requests": _open_requests(board, project, name),
                "activity": _activity_days(events),
                "events": len(events),
            }
        )
    return rows


def _open_requests(board: Path, project: str, agent: str) -> list[str]:
    folder = board / "projects" / project / "requests"
    if not folder.is_dir():
        return []
    found = []
    for path in folder.rglob("*.json"):
        if not path.is_file():
            continue
        if "done" in path.parts or "rejected" in path.parts:
            continue
        if path.parent.name not in (agent, "requests"):
            continue
        found.append(path.name)
    return found


def _activity_days(events: list[dict]) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    counts = {}
    for event in events:
        day = str(event.get("ts") or "")[:10]
        if len(day) == 10:
            counts[day] = counts.get(day, 0) + 1
    rows = []
    for offset in range(6, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        rows.append({"day": day, "count": counts.get(day, 0)})
    return rows


def rules_view(board: Path, project: str) -> dict:
    """Return the rules in force for one project."""
    from agentcoop.projects import load_config
    from agentcoop.taskreview import review_mode
    from agentcoop.version import read_installed
    from agentcoop.watch import watch_path

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    config = load_config(board)
    return {
        "driver": record.get("driver") or "",
        "helpers": record.get("helpers") or {},
        "history": record.get("roles_history") or [],
        "review": review_mode(board, project),
        "approvals": record.get("approvals") or [],
        "checking": "stopped" if watch_path(board, project).is_file() else "on",
        "progress_every_minutes": config.get("progress_every_minutes"),
        "hook_wait_seconds": config.get("cursor_hook_timeout_seconds") or 3600,
        "share_max_mb": config.get("share_max_mb") or 200,
        "share_keep_days": config.get("share_keep_days") or 30,
        "body_limit_bytes": config.get("body_limit_bytes"),
        "may": [
            "Only the driver edits project files.",
            "Helpers reply only to the driver.",
            "A helper may send one context request and pings.",
        ],
        "version": read_installed(board),
        "update_check": bool(config.get("update_check", True)),
    }


def status_view(board: Path) -> dict:
    """Return the attention items a person would see on STATUS.md."""
    from agentcoop.cursorloop import cursor_mail_line
    from agentcoop.progress import stall_snapshot, waiting_line

    items = []
    mail = cursor_mail_line(board)
    if mail:
        items.append({"kind": "stall.mail", "text": mail})
    for name in load_projects(board).get("projects") or {}:
        waiting = waiting_line(board, name)
        if waiting:
            items.append({"kind": "progress.waiting", "project": name, "text": waiting})
            continue
        snap = stall_snapshot(board, name)
        if snap:
            minutes = int(snap.get("minutes") or 0)
            items.append(
                {
                    "kind": "stall.progress",
                    "project": name,
                    "text": "Driver stalled for " + str(minutes) + " min",
                    "minutes": minutes,
                }
            )
    return {"items": items}


def changes_since(board: Path, since: str) -> dict:
    """Return events newer than an order cursor."""
    try:
        mark = int(since or "0")
    except ValueError:
        mark = 0
    rows = [event for event in events_for(board, None) if int(event.get("order") or 0) > mark]
    nxt = str(rows[-1]["order"]) if rows else str(mark)
    return {"events": [_public(event) for event in rows], "cursor": nxt}


def _installed(board: Path) -> str:
    path = board / "bin" / "VERSION"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _cached_text(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    key = str(path)
    hit = _CACHE.get(key)
    if hit and hit[0] == stat.st_mtime_ns and hit[1] == stat.st_size:
        return hit[2]
    text = _read_text(path)
    _CACHE[key] = (stat.st_mtime_ns, stat.st_size, text)
    return text
