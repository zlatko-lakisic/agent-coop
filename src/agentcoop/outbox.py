"""Outbox helpers: NEW.md and posting answers written as Markdown files."""

from __future__ import annotations

import os
import time
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
    archive_dir,
    ensure_outbox_dirs,
    inbox_done,
    inbox_msgs,
    inbox_new_md,
    native_path,
    outbox_dir,
    outbox_processing_dir,
    outbox_rejected_dir,
    outbox_sent_dir,
    require_board_path,
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
    for folder in (
        outbox_dir(board, project, agent),
        outbox_processing_dir(board, project, agent),
    ):
        for path in _markdown_files(folder):
            kind, parsed = parse_outbox_name(path.name)
            if kind == "answer" and parsed == mid:
                return True
    return False


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
    lines.extend(_delivery_lines(board, project, agent))
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
    candidates.sort(key=lambda item: (0 if item.name.endswith(".progress.md") else 1, item.name))
    for path in candidates:
        if path.parent.resolve() == box.resolve() and _is_young(path, now):
            continue
        claimed, lock_path = _claim(board, project, agent, path, now)
        if claimed is None or lock_path is None:
            continue
        try:
            kind, mid = parse_outbox_name(claimed.name)
            if kind == "progress":
                _post_progress(board, project, agent, driver, claimed, mid)
            elif kind == "nudge":
                _post_nudge(board, project, agent, driver, claimed, now)
            else:
                _post_claimed(board, project, agent, driver, claimed, mid, now)
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


def unread_answer_lines(board: Path, project: str, record: dict, now: datetime | None = None) -> list[str]:
    """Return lines for answers the driver has not read past the progress interval."""
    driver = str(record.get("driver") or "")
    if not driver:
        return []
    every = int(load_config(board).get("progress_every_minutes") or 10)
    moment = utc_now(now)
    messages, _issues = new_messages(board, project, driver)
    lines = []
    helpers = record.get("helpers") or {}
    for message in messages:
        if message.type != "answer" or not message.re or message.sender not in helpers:
            continue
        try:
            when = datetime.strptime(message.ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        age = (moment - when).total_seconds()
        if age <= every * 60:
            continue
        lines.append(
            driver
            + " has not read "
            + message.sender
            + "'s answer to "
            + message.re
            + " (posted "
            + _posted_ago(age)
            + ")."
        )
    return lines


def _posted_ago(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return str(minutes) + " " + unit + " ago"
    return str(minutes // 60) + " h ago"


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


STALE_SECONDS = 10 * 60


def parse_outbox_name(name: str) -> tuple[str, str]:
    """Return (kind, id) for an outbox file name.

    kind is progress, nudge, or answer. The id is empty for a nudge.
    An answer id is the text before the first dot. A progress id is the
    text before .progress.md, so a progress file is never read as an answer.
    """
    folded = name.casefold()
    if folded.endswith(".progress.md"):
        return "progress", name[: -len(".progress.md")]
    if folded == "nudge.md" or (folded.startswith("nudge.") and folded.endswith(".md")):
        return "nudge", ""
    stem = name[: -len(".md")] if folded.endswith(".md") else name
    return "answer", stem.split(".", 1)[0]


def read_outbox_text(path: Path) -> str:
    """Read an outbox file as UTF-8, dropping a BOM and turning CRLF into LF."""
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


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
    now: datetime | None = None,
) -> tuple[Path | None, Path | None]:
    """Move an outbox file into processing and create an exclusive lock.

    Take board_lock only for the claim. send() also takes board_lock and is
    not re-entrant, so the lock MUST be released before send().
    A leftover processing file does not block a new outbox file: the new file
    gets the next free name. A lock older than 10 minutes is not live.
    """
    processing = outbox_processing_dir(board, project, agent)
    try:
        with board_lock(board):
            if not path.is_file():
                return None, None
            in_processing = _same_file(path.parent, processing)
            if in_processing:
                dest = path
                lock_path = dest.with_name(dest.name + ".lock")
                if lock_path.is_file() and _is_fresh(lock_path, now, STALE_SECONDS):
                    return None, None
                if lock_path.is_file():
                    try:
                        lock_path.unlink()
                    except OSError:
                        return None, None
            else:
                dest, reserve = _reserve(processing, path.name)
                lock_path = dest.with_name(dest.name + ".lock")
                try:
                    _replace_file(path, dest)
                finally:
                    if reserve.is_file():
                        try:
                            reserve.unlink()
                        except OSError:
                            pass
            if not _create_excl(lock_path):
                return None, None
            return dest, lock_path
    except CoopError as exc:
        if "longer than Windows allows" in str(exc):
            raise
        return None, None


def _post_progress(board: Path, project: str, agent: str, driver: str, path: Path, mid: str) -> None:
    """Move a helper progress note into the progress folder. It is not an answer."""
    from agentcoop.progress import write_helper_progress
    from agentcoop.progressmeta import parse_fields

    msg_path = inbox_msgs(board, project, agent) / (mid + ".json")
    if not ID_RE.fullmatch(mid) or not msg_path.is_file():
        _reject(board, project, agent, path, mid + " is not an open request from the driver to " + agent + ".")
        return
    original = load_message(msg_path)
    if original is None or original.sender != driver or (inbox_done(board, project, agent) / mid).is_file():
        _reject(board, project, agent, path, mid + " is not an open request from the driver to " + agent + ".")
        return
    try:
        text = read_outbox_text(path)
    except (OSError, UnicodeError):
        _reject(board, project, agent, path, "Could not read the progress file.")
        return
    try:
        fields = parse_fields(text)
    except CoopError as exc:
        _reject(board, project, agent, path, str(exc))
        return
    try:
        write_helper_progress(
            board,
            project,
            agent,
            mid,
            fields.get("rest") or "",
            status=str(fields.get("Status") or "working"),
            step=str(fields.get("Step") or ""),
            percent=str(fields.get("Percent") or ""),
            now=str(fields.get("Now") or ""),
            allow_secret=True,
        )
    except CoopError as exc:
        _reject(board, project, agent, path, str(exc))
        return
    _finish(path, outbox_sent_dir(board, project, agent))


def _post_claimed(
    board: Path,
    project: str,
    agent: str,
    driver: str,
    path: Path,
    mid: str,
    now: datetime | None,
) -> None:
    msg_path = inbox_msgs(board, project, agent) / (mid + ".json")
    if not ID_RE.fullmatch(mid) or not msg_path.is_file():
        _reject(board, project, agent, path, "There is no message with this id for " + agent + ".")
        return
    original = load_message(msg_path)
    if original is None or original.sender != driver:
        _reject(board, project, agent, path, "There is no message with this id for " + agent + ".")
        return
    try:
        text = read_outbox_text(path)
    except (OSError, UnicodeError):
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
            posted = send(
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
        saved = _finish(path, outbox_sent_dir(board, project, agent))
        if saved is not None:
            _write_receipt(saved, posted + ".json", driver, now)
        return
    from agentcoop.runner import answer_body_and_attachments

    body, attachments = answer_body_and_attachments(
        board,
        agent=agent,
        project=project,
        message_id=mid,
        answer=text,
    )
    match = _matching_reply(board, project, agent, driver, mid, body, attachments)
    if match is not None:
        mark_done(board, project, agent, [mid])
        saved = _finish(path, outbox_sent_dir(board, project, agent))
        if saved is not None:
            _write_receipt(saved, match.path.name if match.path is not None else match.id + ".json", driver, now)
        return
    count = len(_replies_to(board, project, agent, driver, mid)) + 1
    subject = "Re: " + original.subject
    if count >= 2:
        subject = subject + " (follow-up " + str(count) + ")"
    try:
        posted = send(
            board,
            project=project,
            sender=agent,
            recipient=original.sender,
            subject=subject,
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
    saved = _finish(path, outbox_sent_dir(board, project, agent))
    if saved is not None:
        _write_receipt(saved, posted + ".json", original.sender, now)


def _post_nudge(
    board: Path,
    project: str,
    agent: str,
    driver: str,
    path: Path,
    now: datetime | None,
) -> None:
    """Post a helper nudge. The file text is the body."""
    try:
        text = read_outbox_text(path).strip()
    except (OSError, UnicodeError):
        _reject(board, project, agent, path, "Could not read the nudge file.")
        return
    if not text:
        _reject(board, project, agent, path, "A nudge needs a short message.")
        return
    if len(text) > 500:
        _reject(board, project, agent, path, "A nudge can be at most 500 characters.")
        return
    if not driver:
        _reject(board, project, agent, path, "This project has no driver to nudge.")
        return
    try:
        posted = send(
            board,
            project=project,
            sender=agent,
            recipient=driver,
            subject="Attention",
            body=text,
            type="nudge",
            now=now,
        )
    except CoopError as exc:
        _reject(board, project, agent, path, str(exc))
        return
    saved = _finish(path, outbox_sent_dir(board, project, agent))
    if saved is not None:
        _write_receipt(saved, posted + ".json", driver, now)


def _replies_to(board: Path, project: str, agent: str, driver: str, mid: str) -> list:
    if not driver:
        return []
    found = []
    folders = [
        inbox_msgs(board, project, driver),
        archive_dir(board, project, driver) / "msgs",
    ]
    for folder in folders:
        for path in _json_files(folder):
            message = load_message(path)
            if message is None:
                continue
            if message.sender == agent and message.re == mid and message.type == "answer":
                found.append(message)
    return found


def _matching_reply(board: Path, project: str, agent: str, driver: str, mid: str, body: str, attachments: list[str]):
    wanted = [_norm_body(body), list(attachments)]
    for message in _replies_to(board, project, agent, driver, mid):
        if [_norm_body(message.body), list(message.attachments)] == wanted:
            return message
    return None


def _norm_body(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def _reject(board: Path, project: str, agent: str, path: Path, reason: str) -> None:
    dest_dir = outbox_rejected_dir(board, project, agent)
    saved = _finish(path, dest_dir)
    if saved is None:
        return
    _write_exclusive(saved.with_name(saved.name + ".reason.txt"), (reason + "\n").encode("utf-8"))
    refresh_new_md_for_agent(board, project, agent)


def _finish(path: Path, dest_dir: Path) -> Path | None:
    """Move path into dest_dir under a name that does not overwrite. Returns the new path."""
    if not path.is_file():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    if _same_file(path.parent, dest_dir):
        return path
    last: Exception | None = None
    failed: set[str] = set()
    for _attempt in range(8):
        dest, reserve = _reserve(dest_dir, path.name, skip=failed)
        try:
            _replace_file(path, dest)
        except CoopError as exc:
            last = exc
            failed.add(dest.name.casefold())
            if dest.is_file() and not path.is_file():
                return dest
            continue
        else:
            return dest
        finally:
            if reserve.is_file():
                try:
                    reserve.unlink()
                except OSError:
                    pass
    if last is not None:
        raise last
    return None


def _delivery_lines(board: Path, project: str, agent: str) -> list[str]:
    """Return rejected reasons, then the last 5 delivered or rejected answers."""
    rejected = outbox_rejected_dir(board, project, agent)
    sent = outbox_sent_dir(board, project, agent)
    lines = ["", "Rejected answers:"]
    reasons = []
    for path in _markdown_files(rejected):
        reason_path = path.with_name(path.name + ".reason.txt")
        reason = ""
        if reason_path.is_file():
            try:
                reason = reason_path.read_text(encoding="utf-8").strip()
            except OSError:
                reason = ""
        kind, mid = parse_outbox_name(path.name)
        label = mid or kind
        reasons.append("- " + path.name + " (re " + label + "): " + (reason or "No reason was recorded."))
    if reasons:
        lines.extend(reasons)
    else:
        lines.append("none")
    events = []
    for folder, state in ((sent, "delivered"), (rejected, "rejected")):
        for path in _markdown_files(folder):
            kind, mid = parse_outbox_name(path.name)
            if kind == "progress":
                continue
            when = _receipt_time(path) or _mtime_stamp(path)
            events.append((path.stat().st_mtime if path.exists() else 0, path.name, mid or kind, when, state))
    events.sort(key=lambda item: item[0], reverse=True)
    lines.append("Recent answers:")
    if not events:
        lines.append("none yet")
        return lines
    for _mtime, name, mid, when, state in events[:5]:
        lines.append("- " + name + " re " + mid + " at " + when + " " + state)
    return lines


def _receipt_time(path: Path) -> str:
    receipt = path.with_name(path.name + ".posted.txt")
    if not receipt.is_file():
        return ""
    try:
        parts = receipt.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if len(parts) >= 3:
        return parts[2].strip()
    return ""


def _mtime_stamp(path: Path) -> str:
    try:
        moment = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0)
    except OSError:
        return ""
    return format_ts(moment)


def _write_receipt(path: Path, posted_name: str, recipient: str, now: datetime | None) -> None:
    text = posted_name + "\n" + recipient + "\n" + format_ts(utc_now(now)) + "\n"
    _write_exclusive(path.with_name(path.name + ".posted.txt"), text.encode("utf-8"))


def _json_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        (item for item in folder.iterdir() if item.is_file() and item.suffix == ".json" and not item.name.startswith(".")),
        key=lambda item: item.name,
    )


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _is_fresh(path: Path, now: datetime | None, limit: float) -> bool:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    clock = now.timestamp() if now is not None else datetime.now().timestamp()
    return clock - mtime < limit


def _taken_names(folder: Path) -> set[str]:
    if not folder.is_dir():
        return set()
    found = set()
    try:
        names = list(folder.iterdir())
    except OSError:
        return set()
    for item in names:
        found.add(item.name.casefold())
    return found


def _numbered(name: str, number: int) -> str:
    if number <= 1:
        return name
    folded = name.casefold()
    if folded.endswith(".md"):
        return name[: -len(".md")] + "." + str(number) + ".md"
    return name + "." + str(number)


def _reserve(folder: Path, name: str, skip: set[str] | None = None) -> tuple[Path, Path]:
    """Reserve a free name with O_EXCL. Returns the destination and the reserve file."""
    folder.mkdir(parents=True, exist_ok=True)
    blocked = skip or set()
    for _round in range(3):
        taken = _taken_names(folder)
        for number in range(1, 1000):
            candidate = _numbered(name, number)
            if candidate.casefold() in taken or candidate.casefold() in blocked:
                continue
            if (candidate + ".reserve").casefold() in taken:
                continue
            reserve = folder / (candidate + ".reserve")
            if _create_excl(reserve):
                return folder / candidate, reserve
    raise CoopError("Could not pick a free file name in " + str(folder) + ".")


def _create_excl(path: Path) -> bool:
    require_board_path(path)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    mode = 0o600 if os.name != "nt" else 0o666
    try:
        fd = os.open(str(path), flags, mode)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def _write_exclusive(path: Path, payload: bytes) -> None:
    require_board_path(path)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    mode = 0o600 if os.name != "nt" else 0o666
    try:
        fd = os.open(str(path), flags, mode)
    except FileExistsError:
        alt = path.with_name(path.stem + ".2" + path.suffix)
        fd = os.open(str(alt), flags, mode)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def _replace_file(src: Path, dest: Path) -> None:
    """Replace dest with src, retrying when Windows briefly refuses the rename."""
    require_board_path(dest)
    delay = 0.05
    last: Exception | None = None
    for attempt in range(6):
        try:
            os.replace(src, dest)
            return
        except PermissionError as exc:
            last = exc
            if attempt == 5:
                break
            time.sleep(delay)
            delay = min(delay * 2, 0.4)
    raise CoopError(
        "Could not move "
        + src.name
        + " because another program has the file open. Close it and try again."
    ) from last
