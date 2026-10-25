"""Messages: send, check, done, waiting, archive, and role rules."""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import (
    agentcoop_command,
    archive_dir,
    ensure_agent_dirs,
    files_dir,
    inbox_done,
    inbox_msgs,
    sent_dir,
)
from agentcoop.projects import (
    INTEGRITY_PAUSED,
    INTEGRITY_WARNING,
    load_agents,
    load_config,
    load_projects,
    read_projects_checked,
    require_board,
    resolve_project,
    role_of,
    validate_agent_name,
)
from agentcoop.safety import check_attachment, require_attachment, secret_warning

ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
KNOWN_TYPES = {"question", "answer", "task", "status", "ack", "patch", "context_request", "nudge"}
NUDGE_CHARS = 500
NUDGE_MINUTES = 30
YOUNG_SECONDS = 5


@dataclass
class Message:
    """One message file."""

    v: int
    id: str
    ts: str
    project: str
    sender: str
    recipient: str
    type: str
    re: str | None
    subject: str
    body: str
    attachments: list[str]
    needs_reply: bool
    path: Path | None = None
    attachment_info: list[dict] = field(default_factory=list)
    seq: int | None = None

    def to_dict(self) -> dict:
        """Return the on-disk JSON object."""
        payload = {
            "v": self.v,
            "id": self.id,
            "ts": self.ts,
            "project": self.project,
            "sender": self.sender,
            "recipient": self.recipient,
            "type": self.type,
            "re": self.re,
            "subject": self.subject,
            "body": self.body,
            "attachments": list(self.attachments),
            "needs_reply": self.needs_reply,
        }
        if self.attachment_info:
            payload["attachment_info"] = list(self.attachment_info)
        if self.seq is not None:
            payload["seq"] = self.seq
        return payload


@dataclass
class ReadIssue:
    """A message file that could not be parsed."""

    path: Path
    kind: str
    detail: str


@dataclass
class AttachmentStatus:
    """How check should show one attachment."""

    path: str
    blocked: bool
    reason: str = ""


@dataclass
class CheckedMessage:
    """A new message plus the role and safety labels check adds."""

    message: Message
    violation: str | None
    attachments: list[AttachmentStatus] = field(default_factory=list)


@dataclass
class InboxReport:
    """Everything check needs to print for one project."""

    project: str
    agent: str
    role: str
    driver: str
    helpers: dict[str, str]
    project_path: str
    files_path: str
    messages: list[CheckedMessage]
    issues: list[ReadIssue]
    integrity_ok: bool
    board_path: str = ""
    notices: list[str] = field(default_factory=list)


def utc_now(now: datetime | None = None) -> datetime:
    """Return an aware UTC time."""
    if now is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).replace(microsecond=0)


def format_ts(now: datetime) -> str:
    """Format a UTC timestamp the way message files store it."""
    return utc_now(now).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seq_value(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1:
        return None
    return value


def message_sort_key(message: Message) -> tuple:
    """Order by time, then send sequence, then id.

    A message with no seq is an older file. It stays in id order within that second.
    """
    if message.seq is None:
        return (message.ts, 0, 0, message.id)
    return (message.ts, 1, message.seq, message.id)


def next_message_seq(board: Path) -> int:
    """Return the next send sequence number for this board."""
    from agentcoop.projects import atomic_write_bytes, board_lock

    path = board / ".state" / "message.seq"
    with board_lock(board):
        current = 0
        if path.is_file():
            try:
                current = int(path.read_text(encoding="ascii").strip() or "0")
            except ValueError:
                current = 0
        nxt = current + 1
        atomic_write_bytes(path, (str(nxt) + "\n").encode("ascii"))
        return nxt


def make_id(sender: str, now: datetime | None = None) -> str:
    """Build a time-first message id."""
    stamp = utc_now(now).strftime("%Y%m%dT%H%M%SZ")
    return stamp + "_" + sender + "_" + secrets.token_hex(2)


def write_message_file(directory: Path, message: dict) -> Path:
    """Write a message atomically. Readers ignore the dot-file temp name."""
    directory.mkdir(parents=True, exist_ok=True)
    mid = message["id"]
    if not ID_RE.fullmatch(mid):
        raise CoopError("Message id " + mid + " cannot be used as a file name.")
    final = directory / (mid + ".json")
    if final.exists():
        raise FileExistsError(str(final))
    tmp = directory / ("." + mid + ".json.tmp")
    payload = (json.dumps(message, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        fd = os.open(
            str(tmp),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            0o600 if os.name != "nt" else 0o666,
        )
    except FileExistsError as exc:
        raise CoopError(
            "Could not write the message because a temporary file is still there. Try again."
        ) from exc
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.replace(tmp, final)
    return final


def _message_from_dict(data: dict, path: Path | None) -> Message:
    attachments = data.get("attachments") or []
    if not isinstance(attachments, list):
        raise ValueError("attachments must be a list")
    re_value = data.get("re")
    if re_value is not None and not isinstance(re_value, str):
        raise ValueError("re must be a string or null")
    mid = data.get("id")
    if not isinstance(mid, str) or not ID_RE.fullmatch(mid):
        raise ValueError("id is missing or not safe as a file name")
    for key in ("ts", "project", "sender", "recipient", "type", "subject", "body"):
        if not isinstance(data.get(key), str):
            raise ValueError(key + " is missing")
    return Message(
        v=int(data.get("v", 1) or 1),
        id=mid,
        ts=data["ts"],
        project=data["project"],
        sender=data["sender"],
        recipient=data["recipient"],
        type=data["type"],
        re=re_value,
        subject=data["subject"],
        body=data["body"],
        attachments=[str(item) for item in attachments],
        needs_reply=bool(data.get("needs_reply", False)),
        path=path,
        attachment_info=[item for item in (data.get("attachment_info") or []) if isinstance(item, dict)]
        if isinstance(data.get("attachment_info"), list)
        else [],
        seq=_seq_value(data.get("seq")),
    )


def read_message_file(path: Path, now: float | None = None) -> tuple[Message | None, ReadIssue | None]:
    """Load one message file.

    A file that does not parse and was modified less than 5 seconds ago is
    skipped with no issue. An older broken file is reported.
    """
    if path.name.startswith(".") or not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("message is not an object")
        return _message_from_dict(data, path), None
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        modified = path.stat().st_mtime
        clock = utc_now().timestamp() if now is None else now
        if clock - modified < YOUNG_SECONDS:
            return None, None
        return None, ReadIssue(path=path, kind="unparseable", detail=str(exc))


def list_message_paths(folder: Path) -> list[Path]:
    """Return message files in name order, skipping dot files."""
    if not folder.is_dir():
        return []
    paths = [item for item in folder.iterdir() if item.is_file() and not item.name.startswith(".")]
    return sorted(paths, key=lambda item: item.name)


def load_message(path: Path) -> Message | None:
    """Load a message, or return None when it should be skipped."""
    message, _issue = read_message_file(path)
    return message


def iter_project_messages(board: Path, project: str, agent: str) -> list[Message]:
    """Return every readable message in an agent's inbox, new or done."""
    found = []
    for path in list_message_paths(inbox_msgs(board, project, agent)):
        message = load_message(path)
        if message is not None:
            found.append(message)
    return found


def new_messages(board: Path, project: str, agent: str, now: float | None = None) -> tuple[list[Message], list[ReadIssue]]:
    """Return messages with no done marker, plus old unreadable files."""
    done = inbox_done(board, project, agent)
    messages: list[Message] = []
    issues: list[ReadIssue] = []
    for path in list_message_paths(inbox_msgs(board, project, agent)):
        stem = path.stem
        if (done / stem).exists():
            continue
        message, issue = read_message_file(path, now=now)
        if issue is not None:
            issues.append(issue)
        elif message is not None:
            messages.append(message)
    messages.sort(key=message_sort_key)
    return messages, issues


def _roles_at(project: dict, ts: str) -> tuple[str, dict]:
    """Return the driver and helpers that were in effect at ts."""
    history = project.get("roles_history")
    current_helpers = dict(project.get("helpers") or {})
    if not isinstance(history, list) or not history:
        return str(project.get("driver") or ""), current_helpers
    usable = [entry for entry in history if isinstance(entry, dict) and isinstance(entry.get("since"), str)]
    if not usable:
        return str(project.get("driver") or ""), current_helpers
    chosen = usable[0]
    if isinstance(ts, str) and TS_RE.fullmatch(ts):
        for entry in usable:
            if entry["since"] <= ts:
                chosen = entry
    names = chosen.get("helpers") or []
    if isinstance(names, dict):
        helpers = {str(name): str(note) for name, note in names.items()}
    else:
        helpers = {str(name): "" for name in names}
    return str(chosen.get("driver") or ""), helpers


def file_id(message: Message) -> str:
    """Return the file name stem, which is the id readers use."""
    if message.path is not None:
        return message.path.stem
    return message.id


def _clip_field(value: str) -> str:
    """Keep an out of protocol field short enough to print."""
    if len(value) <= 200:
        return value
    return value[:200]


def _message_time(message: Message) -> datetime | None:
    try:
        return datetime.strptime(message.ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _stored_messages(board: Path, project: str, agent: str) -> list[Message]:
    from agentcoop.paths import archive_dir

    found: list[Message] = []
    folders = [inbox_msgs(board, project, agent), archive_dir(board, project, agent) / "msgs"]
    for folder in folders:
        for path in list_message_paths(folder):
            message = load_message(path)
            if message is not None:
                found.append(message)
    return found


def _nudge_is_open(board: Path, project: str, nudge: Message, driver: str) -> bool:
    sent_at = _message_time(nudge)
    if sent_at is None:
        return True
    for message in _stored_messages(board, project, nudge.sender):
        if message.sender != driver:
            continue
        replied = _message_time(message)
        if replied is not None and replied > sent_at:
            return False
    return True


def _nudge_violation(board: Path, project_name: str, message: Message, driver: str) -> str | None:
    """Return a reason when this nudge is not allowed. None means it is allowed."""
    if message.recipient != driver or message.re:
        return "A nudge goes to the driver, with no reply id."
    if message.sender == driver:
        return "Only a helper can ask the driver for attention."
    if len(message.body) > NUDGE_CHARS:
        return "A nudge can be at most 500 characters."
    this_id = None if message.path is None else file_id(message)
    stored = [
        item
        for item in _stored_messages(board, project_name, driver)
        if item.type == "nudge" and item.sender == message.sender and file_id(item) != this_id
    ]
    open_ones = [item for item in stored if _nudge_is_open(board, project_name, item, driver)]
    moment = _message_time(message) or utc_now()
    earlier = []
    for item in stored:
        when = _message_time(item)
        if when is not None and when <= moment:
            earlier.append((when, item))
    latest = max((when for when, _item in earlier), default=None)
    wait_until = latest + timedelta(minutes=NUDGE_MINUTES) if latest is not None else moment
    if wait_until < moment:
        wait_until = moment
    if open_ones:
        return (
            "You already asked "
            + driver
            + " for attention. The next nudge is allowed at "
            + format_ts(wait_until)
            + ", and only after "
            + driver
            + " sends you a message."
        )
    if latest is not None and moment < latest + timedelta(minutes=NUDGE_MINUTES):
        return "The next nudge is allowed at " + format_ts(latest + timedelta(minutes=NUDGE_MINUTES)) + "."
    return None


def protocol_violation(project: dict, message: Message, board: Path, project_name: str) -> str | None:
    """Return a plain reason when a message breaks the role rules."""
    if message.path is not None and message.path.parent.name == "msgs":
        if message.id != message.path.stem:
            return "The id inside the file does not match the file name."
        if message.recipient != message.path.parent.parent.name:
            return "This message is in the wrong inbox."
        if message.project != message.path.parent.parent.parent.parent.name:
            return "This message names a different project."
        limit = int(load_config(board).get("body_limit_bytes") or 16384)
        if len(message.subject) > 120 or len(message.body.encode("utf-8")) > limit:
            return "This message is larger than the board allows."
    driver, helpers = _roles_at(project, message.ts)
    members = {driver, *helpers.keys()}
    if message.sender not in members or message.recipient not in members:
        return "The sender or recipient is not a member of this project."
    if message.sender == driver:
        if message.type == "nudge":
            return "Only a helper can ask the driver for attention."
        if message.recipient not in helpers:
            return "The driver can only message helpers."
        return None
    if message.type == "nudge":
        return _nudge_violation(board, project_name, message, driver)
    # Helper rules. One exception: a context_request to the driver with no re,
    # when the helper has no open context request in this project.
    if (
        message.type == "context_request"
        and message.recipient == driver
        and not message.re
    ):
        if message.sender not in helpers:
            return "The sender or recipient is not a member of this project."
        from agentcoop.context import open_context_requests

        open_ones = open_context_requests(board, project_name, helper=message.sender)
        if message.path is None:
            # Probe from send: any open request blocks a new one.
            if open_ones:
                return (
                    "You already asked the driver for context. Wait for the answer, or "
                    "read the latest context with: agentcoop context show --as "
                    + message.sender
                )
            return None
        # Bare mode: the earliest open request is allowed; later ones are not.
        mid = file_id(message)
        if open_ones and file_id(open_ones[0]) != mid:
            return (
                "You already asked the driver for context. Wait for the answer, or "
                "read the latest context with: agentcoop context show --as "
                + message.sender
            )
        return None
    if message.recipient != driver or not message.re:
        return (
            "Helpers can only reply to the driver. "
            + str(driver)
            + " is the driver for "
            + project_name
            + ". Use --re with the ID of the message you are answering."
        )
    original_path = inbox_msgs(board, project_name, message.sender) / (message.re + ".json")
    original = load_message(original_path)
    if original is None or original.sender != driver:
        return "A helper reply must answer a message the driver sent to that helper."
    return None


def assert_can_send(
    board: Path,
    project_name: str,
    sender: str,
    recipient: str,
    re_id: str | None,
    msg_type: str = "status",
    now: datetime | None = None,
) -> dict:
    """Raise when agentcoop send must refuse this message. Returns the project."""
    projects, intact = read_projects_checked(board)
    if not intact:
        raise CoopError(INTEGRITY_PAUSED, code=5)
    project = (projects.get("projects") or {}).get(project_name)
    if project is None:
        raise CoopError("There is no project named " + project_name + ". Run: agentcoop project list")
    if (
        msg_type == "context_request"
        and not re_id
        and role_of(project, sender) == "helper"
        and recipient == project.get("driver")
    ):
        from agentcoop.context import helper_has_open_context_request

        if helper_has_open_context_request(board, project_name, sender):
            raise CoopError(
                "You already asked the driver for context. Wait for the answer, or "
                "read the latest context with: agentcoop context show --as "
                + sender
            )
    probe = Message(
        v=1,
        id="probe",
        ts=format_ts(utc_now(now)),
        project=project_name,
        sender=sender,
        recipient=recipient,
        type=msg_type,
        re=re_id,
        subject="probe",
        body="",
        attachments=[],
        needs_reply=False,
    )
    # probe id is not a file. protocol_violation only opens a file when re is set.
    reason = protocol_violation(project, probe, board, project_name)
    if reason:
        raise CoopError(reason)
    if role_of(project, sender) == "":
        raise CoopError(sender + " is not a member of " + project_name + ".")
    agents = load_agents(board)
    if recipient not in (agents.get("agents") or {}):
        raise CoopError(recipient + " is not connected. Run: agentcoop agents")
    return project


def send(
    board: Path,
    *,
    project: str,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    type: str = "question",
    re: str | None = None,
    attachments: list[str] | None = None,
    needs_reply: bool = False,
    allow_secret: bool = False,
    now: datetime | None = None,
    describes: list[str] | None = None,
    kinds: list[str] | None = None,
    media_types: list[str] | None = None,
    alt_ofs: list[str] | None = None,
    over_limit: bool = False,
) -> str:
    """Write a message and return its id."""
    require_board(board)
    validate_agent_name(sender)
    validate_agent_name(recipient)
    if type not in KNOWN_TYPES:
        raise CoopError(
            "Message type must be one of: " + ", ".join(sorted(KNOWN_TYPES)) + "."
        )
    if type == "context_request":
        needs_reply = True
    if type == "nudge":
        needs_reply = True
        if len(body) > NUDGE_CHARS:
            raise CoopError("A nudge can be at most 500 characters.")
    if len(subject) > 120:
        raise CoopError("The subject is too long. Keep it to 120 characters.")
    if not allow_secret and (secret_warning(subject) or secret_warning(body)):
        raise CoopError(
            "This message looks like it contains a password or API key, so it was "
            "not sent. Remove the secret and send again. If this is a false alarm, "
            "add --allow-secret."
        )
    config = load_config(board)
    limit = int(config.get("body_limit_bytes") or 16384)
    if len(body.encode("utf-8")) > limit:
        raise CoopError(
            "The message is too long. Put the long text in a file under your files "
            "folder and attach that path."
        )
    project_record = assert_can_send(board, project, sender, recipient, re, msg_type=type, now=now)
    roots = list(config.get("allowed_roots") or [])
    project_path = Path(project_record["path"])
    sources = list(attachments or [])
    for attachment in sources:
        require_attachment(
            attachment,
            project_path=project_path,
            board=board,
            project=project,
            allowed_roots=roots,
        )
    moment = utc_now(now)
    ensure_agent_dirs(board, project, sender)
    ensure_agent_dirs(board, project, recipient)
    from agentcoop.share import mismatch_note, place_file

    describe_list = list(describes or [])
    kind_list = list(kinds or [])
    media_list = list(media_types or [])
    alt_list = list(alt_ofs or [])
    last_error: Exception | None = None
    for _attempt in range(8):
        mid = make_id(sender, moment)
        shared_paths: list[str] = []
        infos: list[dict] = []
        for index, source in enumerate(sources):
            info = place_file(
                board,
                project,
                source,
                sender=sender,
                recipient=recipient,
                message_id=mid,
                description=describe_list[index] if index < len(describe_list) else "",
                kind=kind_list[index] if index < len(kind_list) else "",
                media_type=media_list[index] if index < len(media_list) else "",
                alt_of=alt_list[index] if index < len(alt_list) else "",
                over_limit=over_limit,
                ask=over_limit,
            )
            note = mismatch_note(board, project, recipient, str(info.get("media_type") or ""))
            if note:
                info = dict(info)
                info["mismatch"] = note
            shared_paths.append(str(info["path"]))
            infos.append(info)
        sent_body = body
        held = [str(info.get("name") or "file") for info in infos if info.get("held") == "program"]
        if held:
            sent_body = body + "\n\n" + " ".join(name + " was held until it was allowed." for name in held)
        payload = {
            "v": 1,
            "id": mid,
            "ts": format_ts(moment),
            "seq": next_message_seq(board),
            "project": project,
            "sender": sender,
            "recipient": recipient,
            "type": type,
            "re": re,
            "subject": subject,
            "body": sent_body,
            "attachments": shared_paths,
            "needs_reply": bool(needs_reply),
        }
        if infos:
            payload["attachment_info"] = infos
        try:
            write_message_file(inbox_msgs(board, project, recipient), payload)
        except FileExistsError as exc:
            last_error = exc
            continue
        try:
            write_message_file(sent_dir(board, project, sender), payload)
        except FileExistsError:
            pass
        from agentcoop.outbox import refresh_new_md_for_agent
        from agentcoop.statusfile import refresh_status

        refresh_new_md_for_agent(board, project, recipient, now=moment)
        refresh_status(board, now=moment)
        return mid
    raise CoopError("Could not pick a unique message id. Try again.") from last_error


def mark_done(board: Path, project: str, agent: str, ids: list[str]) -> None:
    """Create empty done markers for message ids in this agent's inbox."""
    require_board(board)
    msgs = inbox_msgs(board, project, agent)
    done = inbox_done(board, project, agent)
    done.mkdir(parents=True, exist_ok=True)
    for mid in ids:
        if not ID_RE.fullmatch(mid):
            raise CoopError("There is no message " + mid + " in the inbox for " + agent + ".")
        if not (msgs / (mid + ".json")).is_file():
            raise CoopError("There is no message " + mid + " in the inbox for " + agent + ".")
        marker = done / mid
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    from agentcoop.outbox import refresh_new_md_for_agent

    refresh_new_md_for_agent(board, project, agent)


def check_inbox(
    board: Path,
    project: str,
    agent: str,
    now: float | None = None,
) -> InboxReport:
    """Collect new messages for an agent without marking any of them done."""
    from agentcoop.requests import process_project_requests

    outbox_now = None
    if now is not None:
        outbox_now = datetime.fromtimestamp(now, timezone.utc)
    process_project_requests(board, project, now=outbox_now)
    projects, intact = read_projects_checked(board)
    record = (projects.get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ". Run: agentcoop project list")
    role = role_of(record, agent)
    if not role:
        raise CoopError(agent + " is not a member of " + project + ".")
    from agentcoop.outbox import sync_project_outbox

    sync_project_outbox(board, project, now=outbox_now)
    messages, issues = new_messages(board, project, agent, now=now)
    config = load_config(board)
    roots = list(config.get("allowed_roots") or [])
    project_path = Path(record["path"])
    checked: list[CheckedMessage] = []
    for message in messages:
        violation = protocol_violation(record, message, board, project)
        statuses = []
        if violation is None:
            for attachment in message.attachments:
                reason = check_attachment(
                    attachment,
                    project_path=project_path,
                    board=board,
                    project=project,
                    allowed_roots=roots,
                )
                statuses.append(
                    AttachmentStatus(path=attachment, blocked=reason is not None, reason=reason or "")
                )
        checked.append(CheckedMessage(message=message, violation=violation, attachments=statuses))
    if role == "driver":
        # Nudges first, then open context requests, then everything else.
        checked.sort(
            key=lambda item: (
                0
                if item.message.type == "nudge" and item.violation is None
                else 1
                if item.message.type == "context_request" and item.violation is None
                else 2,
            )
            + message_sort_key(item.message)
        )
    return InboxReport(
        project=project,
        agent=agent,
        role=role,
        driver=record.get("driver", ""),
        helpers=dict(record.get("helpers") or {}),
        project_path=str(project_path),
        files_path=str(files_dir(board, project, agent)),
        messages=checked,
        issues=issues,
        integrity_ok=intact,
        board_path=str(board),
        notices=_notice_lines(record),
    )


def _notice_lines(record: dict) -> list[str]:
    """Return pending join and leave lines for the driver."""
    raw = record.get("notices") or []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


def projects_for_check(board: Path, agent: str, explicit: str | None, cwd: Path | None, all_projects: bool) -> list[str]:
    """Resolve one project, or every project the agent belongs to."""
    if all_projects:
        projects = load_projects(board)
        names = []
        for name, record in (projects.get("projects") or {}).items():
            if role_of(record, agent):
                names.append(name)
        return names
    return [resolve_project(board, explicit=explicit, cwd=cwd, agent=agent)]


def waiting(board: Path, project: str, agent: str) -> list[Message]:
    """List this driver's requests that have no reply yet."""
    require_board(board)
    projects = load_projects(board)
    record = (projects.get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ". Run: agentcoop project list")
    if record.get("driver") != agent:
        raise CoopError("Only the driver has a waiting list. The driver for " + project + " is " + str(record.get("driver")) + ".")
    replies = set()
    for message in iter_project_messages(board, project, agent):
        if message.re:
            replies.add(message.re)
    pending = []
    seen = set()
    folders = [sent_dir(board, project, agent)]
    for helper in (record.get("helpers") or {}):
        folders.append(inbox_msgs(board, project, helper))
    for folder in folders:
        for path in list_message_paths(folder):
            message = load_message(path)
            if message is None or file_id(message) in seen:
                continue
            if message.sender != agent or not message.needs_reply:
                continue
            if message.project != project:
                continue
            seen.add(file_id(message))
            if file_id(message) not in replies:
                pending.append(message)
    pending.sort(key=message_sort_key)
    return pending


def archive(board: Path, project: str, agent: str, older_than_days: int = 30, now: datetime | None = None) -> int:
    """Move handled messages older than the given number of days.

    Also removes old context versions for the project, always keeping the latest 5.
    """
    require_board(board)
    moment = utc_now(now)
    cutoff = moment - timedelta(days=older_than_days)
    msgs = inbox_msgs(board, project, agent)
    done = inbox_done(board, project, agent)
    dest_msgs = archive_dir(board, project, agent) / "msgs"
    dest_done = archive_dir(board, project, agent) / "done"
    dest_msgs.mkdir(parents=True, exist_ok=True)
    dest_done.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in list_message_paths(msgs):
        marker = done / path.stem
        if not marker.exists():
            continue
        message = load_message(path)
        when = None
        if message is not None:
            try:
                when = datetime.strptime(message.ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                when = None
        if when is None:
            when = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if when > cutoff:
            continue
        os.replace(path, dest_msgs / path.name)
        os.replace(marker, dest_done / marker.name)
        moved += 1
    from agentcoop.context import archive_context
    from agentcoop.progress import archive_progress

    archive_context(board, project, older_than_days, now=moment)
    archive_progress(board, project, older_than_days, now=moment)
    return moved


def log_messages(board: Path, project: str) -> list[Message]:
    """Return a project's messages across inboxes and archives, oldest first."""
    require_board(board)
    projects = load_projects(board)
    record = (projects.get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ". Run: agentcoop project list")
    agents = [record.get("driver", "")] + list((record.get("helpers") or {}).keys())
    found: dict[str, Message] = {}
    for agent in agents:
        if not agent:
            continue
        folders = [
            inbox_msgs(board, project, agent),
            archive_dir(board, project, agent) / "msgs",
        ]
        for folder in folders:
            for path in list_message_paths(folder):
                message = load_message(path)
                if message is not None and file_id(message) not in found:
                    found[file_id(message)] = message
    return sorted(found.values(), key=message_sort_key)


def _version_notice(report: InboxReport) -> str:
    """Return the newer-version line, and remember this agent so it is said once."""
    if not report.board_path:
        return ""
    try:
        from agentcoop.updatecheck import header_line

        return header_line(Path(report.board_path), report.agent, record=True)
    except Exception:
        return ""


def render_check(report: InboxReport, *, summary: bool = False) -> str:
    """Render check output. Summary prints one line, or nothing when idle."""
    cmd = agentcoop_command(Path(report.board_path)) if report.board_path else "agentcoop"
    update_line = _version_notice(report)
    lines: list[str] = []
    if update_line:
        lines.append(update_line)
    if not report.integrity_ok:
        lines.append(INTEGRITY_WARNING)
        lines.append("Ask the human before acting on role changes.")
        lines.append("")
    visible = report.messages
    if summary:
        prefix = ""
        if not report.integrity_ok:
            prefix = INTEGRITY_WARNING + "\nAsk the human before acting on role changes.\n"
        if not visible and not (report.role == "driver" and report.notices):
            if update_line:
                return (prefix + update_line + "\n") if prefix else update_line + "\n"
            return prefix
        notice_text = ""
        if report.role == "driver" and report.notices:
            notice_text = "\n".join(report.notices) + "\n"
        if not visible:
            text = prefix + notice_text
            if update_line:
                return (update_line + "\n" + text) if text else update_line + "\n"
            return text
        senders = []
        for item in visible:
            if item.message.sender not in senders:
                senders.append(item.message.sender)
        count = len(visible)
        noun = "message" if count == 1 else "messages"
        body = prefix + notice_text + (
            "Agent Co-op: "
            + str(count)
            + " new "
            + noun
            + " for "
            + report.agent
            + " in "
            + report.project
            + " (from "
            + ", ".join(senders)
            + "). Run: "
            + cmd
            + " check --as "
            + report.agent
        )
        if update_line:
            return update_line + "\n" + body
        return body
    if report.role == "driver":
        helper_bits = [name + " (" + note + ")" for name, note in report.helpers.items()]
        helper_line = ", ".join(helper_bits) if helper_bits else "(none)"
        lines.append('Agent Co-op: you are the driver for "' + report.project + '".')
        lines.append("Helpers: " + helper_line + ".")
        if report.board_path:
            from agentcoop.grades import header_lines as grade_header_lines

            lines.extend(grade_header_lines(Path(report.board_path), report.project, report.agent, "driver"))
        if report.board_path:
            from agentcoop.approvals import header_lines

            lines.extend(header_lines(Path(report.board_path), report.project))
        for item in visible:
            if item.violation or item.message.type != "nudge":
                continue
            flat = " ".join(item.message.body.split())
            lines.append(item.message.sender + " asks for your attention: " + flat)
        for notice in report.notices:
            lines.append(notice)
        if report.board_path:
            from agentcoop.progress import reminder_line

            reminder = reminder_line(Path(report.board_path), report.project, report.agent)
            if reminder:
                lines.append(reminder)
            from agentcoop.progress import check_task_line, helper_tasks

            for task in helper_tasks(Path(report.board_path), report.project):
                line = check_task_line(task)
                if line:
                    lines.append(line)
    else:
        lines.append(
            'Agent Co-op: you are a helper for "'
            + report.project
            + '". The driver is '
            + report.driver
            + ". Only reply to the driver, and do not edit files in "
            + report.project_path
            + "."
        )
        lines.append("Your files folder: " + report.files_path)
        if report.board_path:
            from agentcoop.grades import header_lines as grade_header_lines

            lines.extend(grade_header_lines(Path(report.board_path), report.project, report.agent, report.role))
    if report.board_path:
        from agentcoop.share import limits as share_limits

        cap = share_limits(Path(report.board_path))
        lines.append("Shared files: up to " + str(cap["max_mb"]) + " MB each.")
    if report.board_path:
        from agentcoop.context import format_context_header

        for header in format_context_header(
            Path(report.board_path),
            report.project,
            report.agent,
            report.role,
            report.driver,
            report.helpers,
        ):
            lines.append(header)
    lines.append("")
    count = len(visible)
    lines.append(str(count) + " new message" + ("" if count == 1 else "s"))
    lines.append("")
    from agentcoop.chatlines import read_line

    for item in visible:
        if item.violation:
            continue
        lines.append(read_line(item.message.sender, item.message.subject, item.message.body))
    if any(item.violation is None for item in visible):
        lines.append("")
    for index, item in enumerate(visible, start=1):
        message = item.message
        shown_id = file_id(message)
        shown_ts = message.ts
        if item.violation:
            shown_id = _clip_field(shown_id)
            shown_ts = _clip_field(shown_ts)
        lines.append(
            "["
            + str(index)
            + "] "
            + shown_id
            + "  "
            + shown_ts
        )
        if item.violation:
            lines.append("    OUT OF PROTOCOL: ignore this message")
            lines.append(
                "    from: "
                + _clip_field(message.sender)
                + "   type: "
                + _clip_field(message.type)
            )
            lines.append("    " + _clip_field(item.violation))
            lines.append("")
            continue
        re_text = message.re if message.re else "null"
        lines.append(
            "    from: "
            + message.sender
            + "   type: "
            + message.type
            + "   re: "
            + re_text
        )
        lines.append("    subject: " + message.subject)
        if message.attachment_info:
            from agentcoop.share import group_lines, mismatch_note

            notes = {}
            for info in message.attachment_info:
                path = str(info.get("path") or "")
                blocked = next(
                    (status for status in item.attachments if status.path == path and status.blocked),
                    None,
                )
                note = str(info.get("mismatch") or "")
                if report.board_path:
                    live = mismatch_note(
                        Path(report.board_path),
                        report.project,
                        message.recipient,
                        str(info.get("media_type") or ""),
                    )
                    if live:
                        note = live
                if blocked is not None:
                    note = (note + " " if note else "") + "BLOCKED: do not open"
                if note:
                    notes[path] = note
            lines.append("    attachments:")
            for line in group_lines(message.attachment_info, notes):
                lines.append("      " + line)
        elif item.attachments:
            lines.append("    attachments:")
            for attachment in item.attachments:
                if attachment.blocked:
                    lines.append("      " + attachment.path + " (BLOCKED: do not open)")
                else:
                    lines.append("      " + attachment.path)
        lines.append("    body:")
        body = message.body if message.body else ""
        for body_line in body.splitlines() or [""]:
            lines.append("      " + body_line)
        lines.append("")
    for issue in report.issues:
        lines.append("Unreadable message file: " + str(issue.path))
        lines.append("This file could not be read. Run: agentcoop doctor")
        lines.append("")
    if visible:
        ids = " ".join(file_id(item.message) for item in visible)
        lines.append(
            "When finished, run: "
            + cmd
            + " done --as "
            + report.agent
            + " "
            + ids
        )
    return "\n".join(lines).rstrip() + ("\n" if lines else "")
