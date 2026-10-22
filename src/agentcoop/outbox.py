"""Outbox helpers: NEW.md and posting answers written as Markdown files."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from agentcoop.core import (
    ID_RE,
    YOUNG_SECONDS,
    format_ts,
    iter_project_messages,
    load_message,
    mark_done,
    new_messages,
    protocol_violation,
    send,
    utc_now,
)
from agentcoop.errors import CoopError
from agentcoop.paths import (
    ensure_outbox_dirs,
    inbox_done,
    inbox_msgs,
    inbox_new_md,
    native_path,
    outbox_dir,
    outbox_processing_dir,
    outbox_rejected_dir,
    outbox_sent_dir,
)
from agentcoop.projects import (
    atomic_write_bytes,
    board_lock,
    load_agents,
    load_config,
    load_projects,
    read_projects_checked,
)
from agentcoop.safety import check_attachment
from agentcoop.tools import get_tool


def is_outbox_agent(board: Path, agent: str) -> bool:
    """Return True when this connected agent posts through an outbox."""
    record = (load_agents(board).get("agents") or {}).get(agent) or {}
    tool_key = str(record.get("tool") or "")
    try:
        return get_tool(tool_key).join_method == "outbox"
    except KeyError:
        return False


def outbox_helpers(board: Path, project: str) -> list[str]:
    """Return outbox helper names on this project, in helpers-map order."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    names = []
    for name in record.get("helpers") or {}:
        if is_outbox_agent(board, name):
            names.append(name)
    return names


def answer_waiting(board: Path, project: str, agent: str, mid: str) -> bool:
    """Return True when an unposted answer file for this id is waiting."""
    name = mid + ".md"
    return (outbox_dir(board, project, agent) / name).is_file() or (
        outbox_processing_dir(board, project, agent) / name
    ).is_file()


def refresh_new_md_for_agent(
    board: Path,
    project: str,
    agent: str,
    now: datetime | None = None,
) -> None:
    """Rewrite NEW.md when this agent is an outbox helper."""
    if not is_outbox_agent(board, agent):
        return
    write_new_md(board, project, agent, now=now)


def write_new_md(
    board: Path,
    project: str,
    agent: str,
    now: datetime | None = None,
) -> None:
    """Write inbox/<agent>/NEW.md with in-protocol new messages only."""
    ensure_outbox_dirs(board, project, agent)
    projects, _intact = read_projects_checked(board)
    record = (projects.get("projects") or {}).get(project)
    if record is None:
        return
    messages, _issues = new_messages(board, project, agent)
    visible = [
        message
        for message in messages
        if protocol_violation(record, message, board, project) is None
    ]
    from agentcoop.context import latest_version
    from agentcoop.progress import latest_progress

    latest_ctx = latest_version(board, project)
    latest_prog = latest_progress(board, project)
    ctx_text = native_path(latest_ctx.resolve()) if latest_ctx is not None else "None yet"
    prog_text = native_path(latest_prog.resolve()) if latest_prog is not None else "None yet"
    if visible:
        first = "1 new message." if len(visible) == 1 else str(len(visible)) + " new messages."
    else:
        first = "No new messages."
    lines = [
        first,
        "",
        "Written: " + format_ts(utc_now(now)),
        "Latest context: " + ctx_text,
        "Latest progress: " + prog_text,
    ]
    config = load_config(board)
    roots = list(config.get("allowed_roots") or [])
    project_path = Path(record["path"])
    for message in visible:
        lines.append("---")
        lines.append("id: " + message.id)
        lines.append("from: " + message.sender)
        lines.append("type: " + message.type)
        lines.append("time: " + message.ts)
        lines.append("subject: " + message.subject)
        lines.append("attachments:")
        if message.attachments:
            for raw in message.attachments:
                reason = check_attachment(
                    raw,
                    project_path=project_path,
                    board=board,
                    project=project,
                    allowed_roots=roots,
                )
                if reason is not None:
                    lines.append("  BLOCKED: do not open " + native_path(raw))
                else:
                    try:
                        shown = native_path(Path(raw).resolve())
                    except OSError:
                        shown = native_path(raw)
                    lines.append("  " + shown)
        else:
            lines.append("  none")
        if answer_waiting(board, project, agent, message.id):
            lines.append("Answer written, waiting to be posted.")
        lines.append("body:")
        if message.body:
            lines.extend(message.body.splitlines())
    text = "\n".join(lines) + "\n"
    path = inbox_new_md(board, project, agent)
    payload = text.encode("utf-8")
    tmp = path.with_name("." + path.name + "." + str(os.getpid()) + ".tmp")
    if tmp.is_file():
        tmp.unlink()
    atomic_write_bytes(path, payload)


def process_outbox(
    board: Path,
    project: str,
    agent: str,
    now: datetime | None = None,
) -> None:
    """Claim waiting outbox files and post them as replies."""
    if not is_outbox_agent(board, agent):
        return
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is None or agent not in (record.get("helpers") or {}):
        return
    ensure_outbox_dirs(board, project, agent)
    driver = str(record.get("driver") or "")
    box = outbox_dir(board, project, agent)
    processing = outbox_processing_dir(board, project, agent)
    candidates = _markdown_files(processing) + _markdown_files(box)
    for path in candidates:
        if path.parent.resolve() == box.resolve() and _is_young(path, now):
            continue
        claimed, lock_path = _claim(board, project, agent, path)
        if claimed is None or lock_path is None:
            continue
        try:
            _post_claimed(board, project, agent, driver, claimed, now)
        finally:
            if lock_path.is_file():
                try:
                    lock_path.unlink()
                except OSError:
                    pass
    refresh_new_md_for_agent(board, project, agent, now=now)


def sync_project_outbox(board: Path, project: str, now: datetime | None = None) -> None:
    """Post waiting outbox answers for every outbox helper on this project."""
    for agent in outbox_helpers(board, project):
        process_outbox(board, project, agent, now=now)


def sync_board_outboxes(board: Path, now: datetime | None = None) -> None:
    """Post waiting outbox answers on every project."""
    for name in load_projects(board).get("projects") or {}:
        sync_project_outbox(board, name, now=now)


def format_outbox_status(board: Path, agent: str) -> list[str]:
    """Return status lines for one outbox helper, indented like other details."""
    lines: list[str] = []
    projects = load_projects(board).get("projects") or {}
    for name, record in projects.items():
        if agent not in (record.get("helpers") or {}):
            continue
        messages, _issues = new_messages(board, name, agent)
        visible = [
            message
            for message in messages
            if protocol_violation(record, message, board, name) is None
        ]
        minutes = waiting_minutes(board, name, agent)
        if visible:
            wait = "waiting " + str(minutes) + " minutes" if minutes is not None else "waiting"
            lines.append("  " + name + ": " + str(len(visible)) + " new, " + wait)
        else:
            lines.append("  " + name + ": no new messages")
        reason = first_rejected_reason(board, name, agent)
        if reason:
            lines.append("  " + name + ": rejected answers. " + reason)
    return lines


def waiting_minutes(
    board: Path,
    project: str,
    agent: str,
    now: datetime | None = None,
) -> int | None:
    """Return how many minutes the oldest new in-protocol message has waited."""
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is None:
        return None
    messages, _issues = new_messages(board, project, agent)
    times: list[datetime] = []
    for message in messages:
        if protocol_violation(record, message, board, project) is not None:
            continue
        try:
            when = datetime.strptime(message.ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        times.append(when)
    if not times:
        return None
    moment = utc_now(now)
    delta = moment - min(times)
    return int(delta.total_seconds() // 60)


def first_rejected_reason(board: Path, project: str, agent: str) -> str | None:
    """Return the first rejected-answer reason, if any."""
    folder = outbox_rejected_dir(board, project, agent)
    if not folder.is_dir():
        return None
    paths = sorted(
        item
        for item in folder.iterdir()
        if item.is_file()
        and item.name.endswith(".reason.txt")
        and not item.name.startswith(".")
    )
    if not paths:
        return None
    try:
        text = paths[0].read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _markdown_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    found = []
    for item in folder.iterdir():
        if not item.is_file() or item.name.startswith("."):
            continue
        if item.suffix.lower() != ".md":
            continue
        found.append(item)
    return sorted(found, key=lambda item: item.name)


def _is_young(path: Path, now: datetime | None) -> bool:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return True
    clock = now.timestamp() if now is not None else datetime.now().timestamp()
    return clock - mtime < YOUNG_SECONDS


def _claim(
    board: Path,
    project: str,
    agent: str,
    path: Path,
) -> tuple[Path | None, Path | None]:
    """Move an outbox file into processing and create an exclusive lock.

    Take board_lock only for the claim. send() also takes board_lock and is
    not re-entrant, so the lock MUST be released before send().
    """
    processing = outbox_processing_dir(board, project, agent)
    dest = processing / path.name
    lock_path = dest.with_name(dest.name + ".lock")
    try:
        with board_lock(board):
            if not path.is_file():
                return None, None
            if path.resolve() != dest.resolve():
                if dest.exists():
                    return None, None
                os.replace(path, dest)
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
            mode = 0o600 if os.name != "nt" else 0o666
            try:
                fd = os.open(str(lock_path), flags, mode)
            except FileExistsError:
                return None, None
            os.close(fd)
            return dest, lock_path
    except CoopError:
        return None, None


def _post_claimed(
    board: Path,
    project: str,
    agent: str,
    driver: str,
    path: Path,
    now: datetime | None,
) -> None:
    mid = path.stem
    msg_path = inbox_msgs(board, project, agent) / (mid + ".json")
    if not ID_RE.fullmatch(mid) or not msg_path.is_file():
        _reject(board, project, agent, path, "There is no message with this id for " + agent + ".")
        return
    original = load_message(msg_path)
    if original is None:
        return
    if _existing_reply(board, project, agent, driver, mid):
        mark_done(board, project, agent, [mid])
        _finish(path, outbox_sent_dir(board, project, agent))
        return
    if (inbox_done(board, project, agent) / mid).is_file():
        _reject(board, project, agent, path, "This message was already answered.")
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        _reject(board, project, agent, path, "Could not read the answer file.")
        return
    lines = text.splitlines()
    first = lines[0] if lines else ""
    if first.startswith("NEED CONTEXT:"):
        from agentcoop.context import helper_has_open_context_request

        if helper_has_open_context_request(board, project, agent):
            mark_done(board, project, agent, [mid])
            _finish(path, outbox_sent_dir(board, project, agent))
            return
        body = text.split(":", 1)[1].strip() if ":" in text else ""
        body = body or "Need project context"
        try:
            send(
                board,
                project=project,
                sender=agent,
                recipient=driver,
                subject="Context for " + project,
                body=body,
                type="context_request",
                now=now,
            )
        except CoopError as exc:
            _reject(board, project, agent, path, str(exc))
            return
        mark_done(board, project, agent, [mid])
        _finish(path, outbox_sent_dir(board, project, agent))
        return
    from agentcoop.runner import answer_body_and_attachments

    body, attachments = answer_body_and_attachments(
        board,
        agent=agent,
        project=project,
        message_id=mid,
        answer=text,
    )
    try:
        send(
            board,
            project=project,
            sender=agent,
            recipient=original.sender,
            subject="Re: " + original.subject,
            body=body,
            type="answer",
            re=mid,
            attachments=attachments,
            now=now,
        )
    except CoopError as exc:
        reason = str(exc)
        from agentcoop.safety import secret_warning

        if secret_warning(text):
            reason = (
                "This answer looks like it contains a password or API key, so it was not posted. "
                "Remove it and write the answer again."
            )
        _reject(board, project, agent, path, reason)
        return
    mark_done(board, project, agent, [mid])
    _finish(path, outbox_sent_dir(board, project, agent))


def _existing_reply(board: Path, project: str, agent: str, driver: str, mid: str) -> bool:
    if not driver:
        return False
    for message in iter_project_messages(board, project, driver):
        if message.sender == agent and message.re == mid:
            return True
    return False


def _reject(board: Path, project: str, agent: str, path: Path, reason: str) -> None:
    dest_dir = outbox_rejected_dir(board, project, agent)
    dest_dir.mkdir(parents=True, exist_ok=True)
    reason_path = dest_dir / (path.name + ".reason.txt")
    reason_path.write_text(reason + "\n", encoding="utf-8")
    _finish(path, dest_dir)
    refresh_new_md_for_agent(board, project, agent)


def _finish(path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if path.is_file():
        os.replace(path, dest)
