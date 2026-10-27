"""Write projects/STATUS.md for agents that read the board as files."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from agentcoop.projects import atomic_write_bytes, board_is_ready, load_projects


def status_path(board: Path) -> Path:
    """Return the status file at the top of the projects folder."""
    return board / "projects" / "STATUS.md"


def progress_headline(board: Path, project: str) -> str:
    """Return the first line of Working on now, or a plain empty line."""
    from agentcoop.progress import latest_progress, section_text

    path = latest_progress(board, project)
    if path is None:
        return "No progress yet."
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        return "No progress yet."
    working = section_text(body, "Working on now")
    for line in working.splitlines():
        text = line.strip()
        if text:
            return text
    from agentcoop.errors import CoopError
    from agentcoop.progressmeta import parse_fields

    try:
        now_line = str(parse_fields(body).get("Now") or "").strip()
    except CoopError:
        now_line = ""
    if now_line:
        return now_line
    return "No progress yet."


def render_status(board: Path, now: datetime | None = None) -> str:
    """Return the status page text."""
    from agentcoop.rounds import load_schedule

    moment = now or datetime.now(timezone.utc).replace(microsecond=0)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stamp = moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = load_schedule(board)
    if record:
        schedule = "Schedule: on, every " + str(record.get("interval")) + " minutes."
    else:
        schedule = "Schedule: off."
    last_path = board / ".state" / "last-tick"
    if last_path.is_file():
        try:
            last = "Last round: " + last_path.read_text(encoding="utf-8").strip()
        except OSError:
            last = "Last round: none yet."
    else:
        last = "Last round: none yet."
    lines = ["# Status", ""]
    from agentcoop.cursorloop import cursor_mail_line

    late = cursor_mail_line(board, moment)
    if late:
        lines.append(late)
        lines.append("")
    from agentcoop.approvals import status_waiting_lines

    waiting = status_waiting_lines(board)
    if waiting:
        lines.extend(waiting)
        lines.append("")
    try:
        from agentcoop.updatecheck import status_lines

        lines.extend(status_lines(board))
        lines.append("")
    except Exception:
        pass
    lines.extend(["Updated: " + stamp, "", schedule, last, ""])
    projects = load_projects(board).get("projects") or {}
    for name in sorted(projects):
        project = projects[name]
        helpers = project.get("helpers") or {}
        helper_text = ", ".join(str(item) for item in helpers) if helpers else "(none)"
        lines.append("## " + str(name))
        lines.append("Folder: " + str(project.get("path") or ""))
        lines.append("Driver: " + str(project.get("driver") or ""))
        lines.append("Helpers: " + helper_text)
        from agentcoop.grades import status_lines as grade_status_lines

        lines.extend(grade_status_lines(board, name))
        from agentcoop.taskreview import open_review_lines, review_summary

        summary = review_summary(board, name, str(project.get("driver") or ""))
        if summary:
            lines.append(summary)
        lines.extend(open_review_lines(board, name))
        from agentcoop.ping import not_answering_lines
        from agentcoop.tasks import recent_event_lines

        lines.extend(not_answering_lines(board, name))
        lines.extend(recent_event_lines(board, name, now=moment))
        lines.append("Progress: " + progress_headline(board, name))
        from agentcoop.progress import task_lines

        for task_line in task_lines(board, name, now=moment):
            lines.append(task_line)
        lines.extend(_watching_lines(board, name, project, moment))
        from agentcoop.cursorloop import status_idle_line

        driver = str(project.get("driver") or "")
        idle = status_idle_line(board, name, driver, now=moment)
        if idle:
            lines.append(idle)
        from agentcoop.outbox import unread_answer_lines

        lines.extend(unread_answer_lines(board, name, project, moment))
        from agentcoop.share import status_lines as share_status_lines

        lines.extend(share_status_lines(board, name))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _watching_lines(board: Path, name: str, project: dict, now: datetime) -> list[str]:
    """Return the checking line and one line per agent."""
    from agentcoop.outbox import is_outbox_agent
    from agentcoop.watch import agent_watch_line, checking_enabled

    lines = ["Checking: " + ("on." if checking_enabled(board, name) else "stopped.")]
    names: list[str] = []
    driver = str(project.get("driver") or "")
    if driver:
        names.append(driver)
    for helper in project.get("helpers") or {}:
        helper_name = str(helper)
        if helper_name not in names:
            names.append(helper_name)
    for agent in names:
        lines.append(
            agent_watch_line(
                board,
                name,
                agent,
                now,
                outbox=is_outbox_agent(board, agent),
            )
        )
    return lines


def refresh_status(board: Path, now: datetime | None = None) -> None:
    """Rewrite STATUS.md when the board is set up."""
    if not board_is_ready(board):
        return
    path = status_path(board)
    payload = render_status(board, now=now).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    _replace_status(path, payload)


def _replace_status(path: Path, payload: bytes) -> None:
    """Replace STATUS.md, retrying the Windows sharing race."""
    tmp = path.with_name("." + path.name + "." + str(os.getpid()) + ".tmp")
    for attempt in range(8):
        try:
            atomic_write_bytes(path, payload)
            return
        except (PermissionError, FileExistsError):
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))
