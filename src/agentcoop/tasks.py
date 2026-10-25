"""Request states worked out from messages. Nothing here is stored on its own."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.projects import load_projects, role_of


def _parse_ts(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _all(board: Path, project: str) -> list:
    from agentcoop.core import iter_project_messages

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    names = [str(record.get("driver") or "")]
    names.extend(str(name) for name in (record.get("helpers") or {}))
    found = []
    for name in names:
        if name:
            found.extend(iter_project_messages(board, project, name))
    return found


def _by_re(messages: list, message_id: str, msg_type: str) -> list:
    return [item for item in messages if item.type == msg_type and item.re == message_id]


def request_state(board: Path, project: str, request, messages: list | None = None) -> str:
    """Return open, working, answered, declined, cancelled, moved, or late."""
    from agentcoop.progress import helper_tasks

    stored = messages if messages is not None else _all(board, project)
    cancels = _by_re(stored, request.id, "cancel")
    declines = _by_re(stored, request.id, "decline")
    answers = _by_re(stored, request.id, "answer")
    moved = [item for item in stored if item.moved_from == request.id]
    if moved:
        return "moved"
    if cancels and answers:
        cancel_at = _parse_ts(cancels[0].ts)
        answer_at = _parse_ts(answers[-1].ts)
        if cancel_at is not None and answer_at is not None and answer_at >= cancel_at:
            return "late"
    if cancels:
        return "cancelled"
    if declines and not answers:
        return "declined"
    if answers:
        return "answered"
    for task in helper_tasks(board, project):
        if task.get("id") == request.id and str(task.get("status") or "") not in ("done",):
            return "working"
    for item in stored:
        if item.type == "heartbeat" and item.on == request.id:
            return "working"
    return "open"


def answer_is_late(board: Path, project: str, message) -> bool:
    """An answer that arrived after a cancel or a move is late."""
    if message.type != "answer" or not message.re:
        return False
    stored = _all(board, project)
    cancels = _by_re(stored, message.re, "cancel")
    if not cancels:
        return False
    answer_at = _parse_ts(message.ts)
    cancel_at = min((_parse_ts(item.ts) for item in cancels), default=None)
    if answer_at is None or cancel_at is None:
        return False
    return answer_at >= cancel_at


def opinion_ids(board: Path, project: str, message_id: str) -> list[str]:
    """Return every question id in this second-opinion group, or just this id."""
    stored = _all(board, project)
    chosen = None
    for item in stored:
        if item.id == message_id:
            chosen = item
            break
    if chosen is None or not chosen.group:
        return [message_id]
    ids = []
    for item in stored:
        if item.group == chosen.group and item.sender == chosen.sender and item.type == chosen.type:
            if item.id not in ids:
                ids.append(item.id)
    return ids or [message_id]


def group_expected(board: Path, project: str, group: str) -> int:
    """How many helpers were asked in this group."""
    count = 0
    for item in _all(board, project):
        if item.group == group and item.type in ("question", "task"):
            count += 1
    return count


def request_lines(board: Path, project: str, now: datetime | None = None) -> list[str]:
    """Open, working, and declined requests for the driver's check."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    driver = str(record.get("driver") or "")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stored = _all(board, project)
    lines = []
    for message in stored:
        if message.sender != driver or not message.needs_reply:
            continue
        if message.type not in ("question", "task"):
            continue
        state = request_state(board, project, message, stored)
        if state not in ("open", "working", "declined"):
            continue
        sent_at = _parse_ts(message.ts)
        age = "just now"
        if sent_at is not None:
            minutes = int(max(0, (moment - sent_at).total_seconds()) // 60)
            age = str(minutes) + " min" if minutes else "just now"
        lines.append(state + ": " + message.recipient + " " + message.subject + ", " + age + ".")
    return lines


def recent_event_lines(board: Path, project: str, now: datetime | None = None) -> list[str]:
    """Declined, cancelled, moved, and late requests from the last day."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = moment - timedelta(days=1)
    stored = _all(board, project)
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    driver = str(record.get("driver") or "")
    lines = []
    for message in stored:
        if message.sender != driver or message.type not in ("question", "task"):
            continue
        state = request_state(board, project, message, stored)
        if state not in ("declined", "cancelled", "moved", "late"):
            continue
        sent_at = _parse_ts(message.ts)
        if sent_at is not None and sent_at < cutoff:
            continue
        lines.append(state + ": " + message.recipient + " " + message.subject + ".")
    return lines


def group_lines(board: Path, project: str, messages: list) -> list[str]:
    """Say when a second opinion's answers are in, labelled by helper."""
    stored = _all(board, project)
    groups: dict[str, list] = {}
    for item in messages:
        message = item.message if hasattr(item, "message") else item
        group = message.group
        if not group and message.re:
            for earlier in stored:
                if earlier.id == message.re and earlier.group:
                    group = earlier.group
                    break
        if group and message.type == "answer":
            groups.setdefault(group, []).append(message)
    lines = []
    for group, answers in groups.items():
        expected = group_expected(board, project, group)
        have = len(answers)
        if expected and have >= expected:
            lines.append("Group " + group + ": all " + str(expected) + " answers are in.")
        else:
            lines.append("Group " + group + ": " + str(have) + " of " + str(expected or have) + " answers are in.")
        for answer in answers:
            lines.append("  " + answer.sender + ": " + answer.subject)
    return lines


def decline_request(board: Path, project: str, helper: str, message_id: str, reason: str, now: datetime | None = None) -> str:
    """A helper declines one request from the driver."""
    from agentcoop.core import send

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    if role_of(record, helper) != "helper":
        raise CoopError("Only a helper can decline a request.")
    driver = str(record.get("driver") or "")
    text = " ".join(reason.split())
    if not text:
        raise CoopError("Say why you are declining.")
    return send(
        board,
        project=project,
        sender=helper,
        recipient=driver,
        subject="Declined",
        body=text,
        type="decline",
        re=message_id,
        now=now,
    )


def cancel_request(board: Path, project: str, driver: str, message_id: str, reason: str, now: datetime | None = None) -> str:
    """The driver tells a helper to stop."""
    from agentcoop.core import load_message, send
    from agentcoop.paths import inbox_msgs

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    if role_of(record, driver) != "driver":
        raise CoopError("Only the driver can cancel a request.")
    helper = ""
    for name in record.get("helpers") or {}:
        if load_message(inbox_msgs(board, project, str(name)) / (message_id + ".json")) is not None:
            helper = str(name)
            break
    if not helper:
        raise CoopError("There is no request " + message_id + " to cancel.")
    text = " ".join(reason.split()) or "Stop this request."
    return send(
        board,
        project=project,
        sender=driver,
        recipient=helper,
        subject="Cancelled",
        body=text,
        type="cancel",
        re=message_id,
        now=now,
    )


def move_request(
    board: Path,
    project: str,
    driver: str,
    message_id: str,
    helper: str,
    reason: str = "",
    now: datetime | None = None,
) -> str:
    """Cancel a request and send the same request to another helper."""
    from agentcoop.core import load_message, send
    from agentcoop.paths import inbox_msgs

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    if role_of(record, driver) != "driver":
        raise CoopError("Only the driver can move a request.")
    if helper not in (record.get("helpers") or {}):
        raise CoopError(helper + " is not a helper on " + project + ".")
    original = None
    for name in record.get("helpers") or {}:
        message = load_message(inbox_msgs(board, project, str(name)) / (message_id + ".json"))
        if message is not None and message.sender == driver:
            original = message
            break
    if original is None:
        raise CoopError("There is no request " + message_id + " to move.")
    if original.recipient == helper:
        raise CoopError(message_id + " is already with " + helper + ".")
    cancel_request(board, project, driver, message_id, reason or "Moved to " + helper + ".", now=now)
    return send(
        board,
        project=project,
        sender=driver,
        recipient=helper,
        subject=original.subject,
        body=original.body,
        type=original.type or "question",
        attachments=list(original.attachments),
        needs_reply=True,
        moved_from=message_id,
        now=now,
    )
