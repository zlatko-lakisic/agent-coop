"""Grades a driver gives a helper's answer. Totals are computed when read."""

from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import inbox_msgs, project_dir, sent_dir
from agentcoop.projects import load_projects, validate_agent_name

OUTCOMES = ("used", "partly_used", "not_used", "wrong")
WINDOW = 20


def grades_dir(board: Path, project: str, helper: str) -> Path:
    """Return the folder of one helper's grades."""
    return project_dir(board, project) / "grades" / helper


def removed_dir(board: Path, project: str, helper: str) -> Path:
    """Return the folder a removed grade is moved to."""
    return project_dir(board, project) / "grades" / "removed" / helper


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _clock(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _score(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CoopError(label + " must be a whole number from 1 to 5.")
    if value < 1 or value > 5:
        raise CoopError(label + " must be a whole number from 1 to 5.")
    return value


def _read_grade(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        return None
    return data


def list_grades(board: Path, project: str, helper: str) -> list[dict]:
    """Return one helper's grades, oldest first. Removed grades are left out."""
    folder = grades_dir(board, project, helper)
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.json")):
        if not path.is_file() or path.name.startswith("."):
            continue
        data = _read_grade(path)
        if data is not None:
            data["_path"] = str(path)
            found.append(data)
    found.sort(key=lambda item: (str(item.get("ts") or ""), str(item.get("_path") or "")))
    return found


def find_grade(board: Path, project: str, answer_id: str) -> tuple[str, dict] | None:
    """Return the helper name and grade for one answer, if it still stands."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    names = list((record.get("helpers") or {}).keys())
    root = project_dir(board, project) / "grades"
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and child.name not in names and child.name != "removed":
                names.append(child.name)
    for helper in names:
        for grade in list_grades(board, project, helper):
            if grade.get("answer") == answer_id:
                return helper, grade
    return None


def _find_message(board: Path, project: str, message_id: str):
    from agentcoop.core import list_message_paths, load_message
    from agentcoop.paths import archive_dir

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    names = [str(record.get("driver") or "")]
    names.extend(str(name) for name in (record.get("helpers") or {}))
    for name in names:
        if not name:
            continue
        folders = [
            inbox_msgs(board, project, name),
            sent_dir(board, project, name),
            archive_dir(board, project, name) / "msgs",
        ]
        for folder in folders:
            path = folder / (message_id + ".json")
            message = load_message(path)
            if message is not None:
                return message
            for item in list_message_paths(folder):
                message = load_message(item)
                if message is not None and message.id == message_id:
                    return message
    return None


def _progress_count(board: Path, project: str, helper: str, request_id: str) -> int:
    from agentcoop.progress import helper_progress_dir

    if not request_id:
        return 0
    folder = helper_progress_dir(board, project, helper)
    if not folder.is_dir():
        return 0
    count = 0
    for path in folder.glob("*.md"):
        name = path.name
        if len(name) < 18 or name[16] != "_":
            continue
        if name[17:-3] == request_id:
            count += 1
    return count


def _related(board: Path, project: str, request_id: str, answer_ts: str) -> tuple[bool, bool]:
    """Return whether the answer was declined, and whether it arrived late."""
    from agentcoop.core import iter_project_messages

    if not request_id:
        return False, False
    answer_at = _parse_ts(answer_ts)
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    names = [str(record.get("driver") or "")]
    names.extend(str(name) for name in (record.get("helpers") or {}))
    declined = False
    late = False
    for name in names:
        if not name:
            continue
        for message in iter_project_messages(board, project, name):
            raw = _raw_fields(message)
            moved = raw.get("moved_from") if isinstance(raw.get("moved_from"), str) else ""
            if message.re != request_id and moved != request_id:
                continue
            when = _parse_ts(message.ts)
            if message.type == "decline" and message.re == request_id:
                declined = True
            after = answer_at is not None and when is not None and when <= answer_at
            if after and message.type == "cancel" and message.re == request_id:
                late = True
            if after and moved == request_id:
                late = True
    return declined, late


def _raw_fields(message) -> dict:
    if message.path is None or not message.path.is_file():
        return {}
    try:
        data = json.loads(message.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_grade(
    board: Path,
    project: str,
    actor: str,
    answer_id: str,
    *,
    score: int,
    outcome: str,
    note: str,
    correct: int | None = None,
    useful: int | None = None,
    followed_ask: int | None = None,
    tags: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """Write one grade. A second grade for the same answer is refused."""
    from agentcoop.core import _roles_at, utc_now

    validate_agent_name(actor)
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ".")
    moment = utc_now(now)
    driver, _helpers = _roles_at(record, _clock(moment))
    if actor != driver:
        raise CoopError("Only the driver can grade an answer.")
    cleaned_score = _score(score, "score")
    cleaned_outcome = str(outcome or "").strip()
    if cleaned_outcome not in OUTCOMES:
        raise CoopError("outcome must be used, partly_used, not_used, or wrong.")
    sentence = " ".join(str(note or "").split())
    if not sentence:
        raise CoopError("Say why in one sentence. Add --note.")
    if len(sentence) > 200:
        raise CoopError("Keep the note to one sentence.")
    optional = {}
    if correct is not None:
        optional["correct"] = _score(correct, "correct")
    if useful is not None:
        optional["useful"] = _score(useful, "useful")
    if followed_ask is not None:
        optional["followed_ask"] = _score(followed_ask, "followed_ask")
    answer = _find_message(board, project, answer_id)
    then_driver, _then_helpers = _roles_at(record, answer.ts if answer is not None else _clock(moment))
    if answer is None or answer.sender == then_driver or answer.recipient != then_driver:
        raise CoopError("Grade a helper's answer. " + answer_id + " is not one.")
    helper = answer.sender
    if find_grade(board, project, answer_id) is not None:
        raise CoopError(answer_id + " already has a grade.")
    request_id = answer.re or ""
    reply_minutes = None
    if request_id:
        request = _find_message(board, project, request_id)
        if request is not None:
            start = _parse_ts(request.ts)
            end = _parse_ts(answer.ts)
            if start is not None and end is not None:
                reply_minutes = int(round((end - start).total_seconds() / 60))
                if reply_minutes < 0:
                    reply_minutes = 0
    declined, late = _related(board, project, request_id, answer.ts)
    if answer.type == "decline":
        declined = True
    raw = _raw_fields(answer)
    group = raw.get("group") if isinstance(raw.get("group"), str) else ""
    payload = {
        "v": 1,
        "answer": answer_id,
        "helper": helper,
        "project": project,
        "by": actor,
        "ts": _clock(moment),
        "score": cleaned_score,
        "outcome": cleaned_outcome,
        "tags": list(tags or []),
        "note": sentence,
        "reply_minutes": reply_minutes,
        "progress_updates": _progress_count(board, project, helper, request_id),
        "late": late,
        "declined": declined,
        "second_opinion_group": group,
    }
    payload.update(optional)
    folder = grades_dir(board, project, helper)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (_stamp(moment) + "_" + answer_id + ".json")
    body = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        fd = os.open(
            str(path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            0o600 if os.name != "nt" else 0o666,
        )
    except FileExistsError as exc:
        raise CoopError(answer_id + " already has a grade.") from exc
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    return payload


def remove_grade(board: Path, project: str, answer_id: str) -> Path:
    """Move a grade to grades/removed/. The caller already confirmed with the person."""
    found = find_grade(board, project, answer_id)
    if found is None:
        raise CoopError("There is no grade on " + answer_id + ".")
    helper, grade = found
    source = Path(str(grade.get("_path") or ""))
    if not source.is_file():
        raise CoopError("There is no grade on " + answer_id + ".")
    dest_folder = removed_dir(board, project, helper)
    dest_folder.mkdir(parents=True, exist_ok=True)
    dest = dest_folder / source.name
    if dest.exists():
        dest = dest_folder / (source.stem + "-removed" + source.suffix)
    os.replace(source, dest)
    return dest


def summarize(grades: list[dict]) -> dict:
    """Totals from the last 20 grades. Nothing here is stored on disk."""
    ordered = list(grades)
    window = ordered[-WINDOW:]
    scores = [int(item["score"]) for item in window if isinstance(item.get("score"), int)]
    replies = [
        int(item["reply_minutes"])
        for item in window
        if isinstance(item.get("reply_minutes"), int)
    ]
    per_tag: dict[str, list[int]] = {}
    for item in window:
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        for tag in tags:
            if isinstance(tag, str) and tag:
                per_tag.setdefault(tag, []).append(int(item["score"]))
    average = round(sum(scores) / len(scores), 1) if scores else None
    median = int(round(statistics.median(replies))) if replies else None
    return {
        "total": len(ordered),
        "count": len(window),
        "average": average,
        "per_tag": {tag: round(sum(values) / len(values), 1) for tag, values in sorted(per_tag.items())},
        "median_reply": median,
        "declined": sum(1 for item in window if item.get("declined")),
        "late": sum(1 for item in window if item.get("late")),
    }


def summarize_project(board: Path, project: str, helper: str) -> dict:
    """Totals for one helper on one project."""
    return summarize(list_grades(board, project, helper))


def summarize_agent(board: Path, helper: str) -> dict:
    """Totals for one helper across every project, using the last 20 grades."""
    found = []
    projects = load_projects(board).get("projects") or {}
    for name in projects:
        found.extend(list_grades(board, name, helper))
    found.sort(key=lambda item: (str(item.get("ts") or ""), str(item.get("_path") or "")))
    return summarize(found)


def display_summary(summary: dict) -> str:
    """Return new, or the average and the count."""
    if int(summary.get("total") or 0) < 3:
        return "new"
    average = summary.get("average")
    text = str(average) + " (" + str(summary.get("count") or 0) + ")"
    if summary.get("median_reply") is not None:
        text += ". replies in " + str(summary["median_reply"]) + " min"
    return text


def header_lines(board: Path, project: str, agent: str, role: str) -> list[str]:
    """Lines for a check. A helper sees only its own grades."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    helpers = list((record.get("helpers") or {}).keys())
    if role == "driver":
        lines = []
        for name in sorted(helpers):
            summary = summarize_project(board, project, name)
            lines.append(name + ": " + display_summary(summary))
        return lines
    if agent not in helpers:
        return []
    summary = summarize_project(board, project, agent)
    lines = ["Your grades: " + display_summary(summary)]
    notes = [item for item in list_grades(board, project, agent) if str(item.get("note") or "")]
    for item in notes[-WINDOW:]:
        lines.append(str(item.get("score")) + " " + str(item.get("outcome")) + ". " + str(item.get("note")))
    return lines


def new_md_lines(board: Path, project: str, agent: str) -> list[str]:
    """Grade lines for one helper's NEW.md. Other helpers are left out."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    if agent not in (record.get("helpers") or {}):
        return []
    return header_lines(board, project, agent, "helper")


def status_lines(board: Path, project: str) -> list[str]:
    """One grade line per helper, for STATUS.md and agentcoop status."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    lines = []
    for name in sorted((record.get("helpers") or {}).keys()):
        summary = summarize_project(board, project, name)
        lines.append(name + " grade: " + display_summary(summary))
    return lines


def viewer_rows(board: Path, project: str) -> list[dict]:
    """Last 20 grades with notes, for the viewer."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    rows = []
    for name in sorted((record.get("helpers") or {}).keys()):
        grades = list_grades(board, project, name)
        summary = summarize(grades)
        recent = []
        for item in grades[-WINDOW:]:
            recent.append(
                {
                    "score": item.get("score"),
                    "outcome": item.get("outcome") or "",
                    "note": item.get("note") or "",
                    "ts": item.get("ts") or "",
                }
            )
        rows.append({"helper": name, "line": display_summary(summary), "recent": recent})
    return rows
