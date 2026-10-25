"""Rank helpers so the driver can pick one."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agentcoop.grades import display_summary, list_grades, summarize
from agentcoop.profiles import profile_of

COST_RANK = {"low": 0, "medium": 1, "high": 2}
AVAIL_RANK = {"watching": 0, "busy": 1, "offline": 2}


def _moment(now: datetime | None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _watching(board: Path, project: str, agent: str, now: datetime) -> bool:
    from agentcoop.outbox import is_outbox_agent
    from agentcoop.watch import STALE_SECONDS, heartbeat_seen, latest_desktop_seen

    if is_outbox_agent(board, agent):
        seen = latest_desktop_seen(board, project, agent)
    else:
        seen = heartbeat_seen(board, project, agent)
    if seen is None:
        return False
    return (now - seen).total_seconds() < STALE_SECONDS


def _busy(board: Path, project: str, agent: str, now: datetime) -> str:
    from agentcoop.progress import helper_tasks

    for task in helper_tasks(board, project, now=now):
        if task.get("agent") != agent:
            continue
        if str(task.get("status") or "") in ("done", "waiting"):
            continue
        what = str(task.get("now") or task.get("step") or "a task")
        return " ".join(what.split()) or "a task"
    return ""


def _open_requests(board: Path, project: str, helper: str, driver: str) -> int:
    from agentcoop.core import iter_project_messages

    replies = {
        message.re
        for message in iter_project_messages(board, project, driver)
        if message.sender == helper and message.re and message.type in ("answer", "decline")
    }
    count = 0
    for message in iter_project_messages(board, project, helper):
        if message.sender != driver or not message.needs_reply:
            continue
        if message.type in ("cancel", "ack", "nudge", "ping"):
            continue
        if message.id not in replies:
            count += 1
    return count


def _grade_phrase(board: Path, project: str, helper: str, tag: str) -> tuple[str, float | None]:
    grades = list_grades(board, project, helper)
    if tag:
        tagged = [item for item in grades if tag in (item.get("tags") or [])]
        window = tagged[-20:]
        summary = summarize(window)
        summary["total"] = len(tagged)
        if len(window) < 3:
            return "new", None
        average = summary.get("average")
        return tag + " " + str(average) + " (" + str(summary.get("count") or 0) + ")", average
    summary = summarize(grades)
    if int(summary.get("total") or 0) < 3:
        return "new", None
    shown = display_summary(summary)
    grade = shown.split(". replies")[0]
    return grade, summary.get("average")


def choice_rows(board: Path, project: str, tag: str = "", now: datetime | None = None) -> list[dict]:
    """Return helpers ranked for a tag. Higher grade, then availability, then cost."""
    from agentcoop.projects import load_projects

    moment = _moment(now)
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    driver = str(record.get("driver") or "")
    rows = []
    for name in record.get("helpers") or {}:
        profile = profile_of(record, name)
        busy = _busy(board, project, name, moment)
        if busy:
            state = "busy"
            state_text = "busy (" + busy + ")"
        elif _watching(board, project, name, moment):
            state = "watching"
            state_text = "watching, idle"
        else:
            state = "offline"
            state_text = "offline"
        grade_text, average = _grade_phrase(board, project, name, tag.strip().lower())
        summary = summarize(list_grades(board, project, name))
        cost = profile.get("cost") or "medium"
        rows.append(
            {
                "name": name,
                "state": state,
                "state_text": state_text,
                "open": _open_requests(board, project, name, driver),
                "grade_text": grade_text,
                "average": average,
                "median_reply": summary.get("median_reply"),
                "cost": cost,
                "tags": profile.get("tags") or [],
                "can": profile.get("can") or [],
            }
        )
    rows.sort(
        key=lambda row: (
            0 if row["average"] is not None else 1,
            -(row["average"] or 0),
            AVAIL_RANK.get(row["state"], 9),
            COST_RANK.get(row["cost"], 9),
            row["name"],
        )
    )
    return rows


def format_choice(row: dict, *, short: bool = False) -> str:
    """Return one helper line."""
    parts = [row["name"] + ": " + row["state_text"]]
    if row["open"]:
        noun = "open request" if row["open"] == 1 else "open requests"
        parts.append(str(row["open"]) + " " + noun)
    parts.append(row["grade_text"])
    if row["median_reply"] is not None and row["grade_text"] != "new":
        parts.append("replies in " + str(row["median_reply"]) + " min")
    parts.append("cost " + row["cost"])
    line = ". ".join(parts) + "."
    if short:
        return line
    tags = ", ".join(row["tags"]) or "none"
    can = ", ".join(row["can"]) or "none"
    return line + "\n  tags: " + tags + ". can: " + can + "."


def choice_lines(board: Path, project: str, tag: str = "", *, short: bool = False, now: datetime | None = None) -> list[str]:
    """Ranked lines for helpers, or for a check header when short is set."""
    lines = []
    for row in choice_rows(board, project, tag, now=now):
        text = format_choice(row, short=short)
        lines.extend(text.splitlines())
    return lines
