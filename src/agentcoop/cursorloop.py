"""Wake an open Cursor chat, and leave it alone when it is already watching."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop.core import format_ts, new_messages, utc_now
from agentcoop.errors import CoopError
from agentcoop.paths import project_dir
from agentcoop.projects import atomic_write_bytes, dump_json, load_config, load_projects, role_of
from agentcoop.watch import (
    HEARTBEAT_SECONDS,
    checking_enabled,
    end_heartbeat,
    heartbeat_fresh,
    write_heartbeat,
)

POLL_SECONDS = 2
HOOK_TIMEOUT_SECONDS = 3600
HOOK_TIMEOUT_MIN_SECONDS = 30
HOOK_TIMEOUT_MAX_SECONDS = 3600
HOOK_RESERVE_SECONDS = 5
OLD_DEFAULT_HOOK_TIMEOUT = 600
KEEP_AWAKE = (
    "Agent Co-op keep-awake. If you are working on anything, ignore this and keep going. "
    "Otherwise end your turn."
)
MAIL_TAIL_OLD = "Handle these, mark them done, then continue."
MAIL_TAIL = (
    "Handle these, mark them done, then continue what you were doing. "
    "If there is nothing else, start cursor-loop again if it is not running and end your turn."
)
TICK_PREFIX = "AGENT_LOOP_TICK_board "
TRANSCRIPT_WARNING = (
    "Cursor's transcript format is not recognized. A stale hook may interrupt work."
)
# Seen in Cursor 3.27.14 when a background shell notifies the chat. It is not a new task.
SHELL_TASK_NOTICE = "Briefly inform the user about the task result"
FORMAT_SAMPLE = 65536
MAIL_WAIT_LINE = "Cursor has not picked up mail for "


def hook_timeout_seconds(board: Path) -> int:
    """Return how long the stop hook may run. The default is 3600 seconds.

    Cursor accepts any positive number and warns above 3600. A missing key uses 3600.
    """
    try:
        raw = load_config(board).get("cursor_hook_timeout_seconds")
    except CoopError:
        return HOOK_TIMEOUT_SECONDS
    if raw is None:
        return HOOK_TIMEOUT_SECONDS
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return HOOK_TIMEOUT_SECONDS
    if isinstance(raw, bool) or seconds < HOOK_TIMEOUT_MIN_SECONDS:
        return HOOK_TIMEOUT_MIN_SECONDS
    if seconds > HOOK_TIMEOUT_MAX_SECONDS:
        return HOOK_TIMEOUT_MAX_SECONDS
    return seconds


def loop_interval_minutes(board: Path) -> int:
    """Return the same interval as agentcoop start --every. The default is 5."""
    from agentcoop.rounds import load_schedule

    record = load_schedule(board) or {}
    try:
        minutes = int(record.get("interval") or 5)
    except (TypeError, ValueError):
        minutes = 5
    if minutes < 1:
        return 5
    return minutes


def agent_watch_path(board: Path, project: str, agent: str) -> Path:
    """Return the per-agent watching flag, next to the project stop flag."""
    return project_dir(board, project) / "watching" / (agent + ".json")


def hook_stamp_path(board: Path, project: str, agent: str) -> Path:
    """Return the file that records the last Cursor stop hook."""
    return project_dir(board, project) / "watchers" / (agent + ".hook.json")


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def agent_wants_watching(board: Path, project: str, agent: str) -> bool:
    """Return False only when this agent was told to stop its own loop."""
    data = _load(agent_watch_path(board, project, agent))
    if not data:
        return True
    return data.get("watching") is not False


def set_agent_watching(board: Path, project: str, agent: str, watching: bool, now: datetime | None = None) -> None:
    """Remember whether this agent should keep its Cursor loop running."""
    payload = {"watching": bool(watching), "ts": format_ts(utc_now(now))}
    atomic_write_bytes(agent_watch_path(board, project, agent), dump_json(payload))


def note_hook(board: Path, project: str, agent: str, now: datetime | None = None) -> None:
    """Record that the Cursor stop hook ran."""
    payload = {"last_seen": format_ts(utc_now(now))}
    atomic_write_bytes(hook_stamp_path(board, project, agent), dump_json(payload))


def hook_fresh(board: Path, project: str, agent: str, now: datetime | None = None) -> bool:
    """Return True when the stop hook ran within the progress interval."""
    seen = _parse(_load(hook_stamp_path(board, project, agent)).get("last_seen"))
    if seen is None:
        return False
    every = int(load_config(board).get("progress_every_minutes") or 10)
    return (utc_now(now) - seen).total_seconds() < every * 60


def chat_is_watching(board: Path, project: str, agent: str, now: datetime | None = None) -> bool:
    """Return True when the open chat is watching or the hook ran recently."""
    return heartbeat_fresh(board, project, agent, now=now) or hook_fresh(board, project, agent, now=now)


def idle_mode(board: Path, agent: str) -> str:
    """Return wait_for_chat or background_run for this agent."""
    chosen = str(load_config(board).get("driver_when_chat_idle") or "")
    if chosen in ("wait_for_chat", "background_run"):
        return chosen
    from agentcoop.projects import load_agents

    record = (load_agents(board).get("agents") or {}).get(agent) or {}
    if record.get("tool") == "cursor":
        return "wait_for_chat"
    return "background_run"


def set_idle_mode(board: Path, mode: str) -> str:
    """Save what to do when the Cursor chat is not watching."""
    if mode not in ("wait_for_chat", "background_run"):
        raise CoopError("Use wait or background.", code=2)
    from agentcoop.projects import mutate_config

    def change(data: dict) -> None:
        data["driver_when_chat_idle"] = mode

    mutate_config(board, change)
    if mode == "background_run":
        return "When Cursor is not watching, the driver runs in the background."
    return "When Cursor is not watching, messages wait until you say Start working."


def leave_cursor_idle(board: Path, project: str, agent: str, role: str, now: datetime | None = None) -> bool:
    """Return True when a background round must not start this Cursor driver."""
    if role != "driver":
        return False
    from agentcoop.projects import load_agents

    record = (load_agents(board).get("agents") or {}).get(agent) or {}
    if record.get("tool") != "cursor":
        return False
    if chat_is_watching(board, project, agent, now=now):
        return True
    return idle_mode(board, agent) == "wait_for_chat"


def waiting_count(board: Path, project: str, agent: str) -> int:
    """Return how many new in-protocol messages are waiting."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    if not role_of(record, agent):
        return 0
    report_messages, _issues = new_messages(board, project, agent)
    from agentcoop.core import protocol_violation

    count = 0
    for message in report_messages:
        if protocol_violation(record, message, board, project) is None:
            count += 1
    return count


def status_idle_line(board: Path, project: str, agent: str, now: datetime | None = None) -> str:
    """Return the phone line when a Cursor driver is not watching, or empty."""
    if idle_mode(board, agent) != "wait_for_chat":
        return ""
    if chat_is_watching(board, project, agent, now=now):
        return ""
    count = waiting_count(board, project, agent)
    if count < 1:
        return ""
    noun = "message waits" if count == 1 else "messages wait"
    return agent + " is not watching. " + str(count) + " " + noun + ". In Cursor, say: Start working."


def notify_command(message: str) -> list[str] | None:
    """Return a no-focus notification command, or None when this OS has none."""
    text = " ".join(message.split())
    if not text:
        return None
    if sys.platform == "darwin":
        return ["osascript", "-e", "display notification " + json.dumps(text) + " with title " + json.dumps("Agent Co-op")]
    if sys.platform.startswith("linux"):
        return ["notify-send", "Agent Co-op", text]
    if sys.platform == "win32":
        script = (
            "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            "$n.Visible = $true;"
            "$n.ShowBalloonTip(4000, 'Agent Co-op', "
            + json.dumps(text)
            + ", 'Info')"
        )
        return ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script]
    return None


def maybe_notify(board: Path, message: str, runner=None) -> None:
    """Show a notification only when the person turned it on. Failures are ignored."""
    if load_config(board).get("notify_when_idle") is not True:
        return
    command = notify_command(message)
    if not command:
        return
    import subprocess

    run = runner or subprocess.run
    try:
        run(command, capture_output=True, text=True)
    except OSError:
        return


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _emit(line: str, stream) -> None:
    stream.write(line)
    if not line.endswith("\n"):
        stream.write("\n")
    stream.flush()


def _tick(stream, prompt: str) -> None:
    _emit(TICK_PREFIX + json.dumps({"prompt": prompt}, ensure_ascii=False), stream)


def _mail_prompt(board: Path, project: str, agent: str) -> tuple[str, set[str]]:
    from agentcoop.chatlines import read_line
    from agentcoop.core import file_id, protocol_violation

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    messages, _issues = new_messages(board, project, agent)
    lines = []
    ids: set[str] = set()
    for message in messages:
        if protocol_violation(record, message, board, project) is not None:
            continue
        ids.add(file_id(message))
        lines.append(read_line(message.sender, message.subject, message.body))
    if not lines:
        return "", set()
    return "\n".join(lines) + "\nHandle these, mark them done, then continue.", ids


def _task_now(board: Path, project: str) -> tuple[str, str]:
    from agentcoop.progress import latest_progress
    from agentcoop.progressmeta import parse_fields

    path = latest_progress(board, project)
    if path is None:
        return "", ""
    try:
        fields = parse_fields(path.read_text(encoding="utf-8"))
    except (OSError, CoopError):
        return "", ""
    return str(fields.get("Status") or "").strip(), str(fields.get("Now") or "").strip()


def _keep_task(status: str, now_line: str) -> bool:
    """Return True when an open task should keep waking the chat.

    A missing Status still counts when Now names the work. done and waiting do not.
    """
    if not now_line:
        return False
    return status in ("working", "blocked", "")


def assert_single_loop(board: Path, project: str, agent: str, now: datetime | None = None) -> None:
    """Refuse a second loop while this agent's heartbeat is still fresh."""
    if heartbeat_fresh(board, project, agent, now=now):
        raise CoopError(agent + " is already running cursor-loop. A second loop was not started.")


def run_cursor_loop(
    board: Path,
    project: str,
    agent: str,
    *,
    now=None,
    sleep=None,
    out=None,
    max_passes: int | None = None,
) -> int:
    """Print ticks for new mail and for an open task. Exit when watching stops."""
    import time

    from agentcoop.version import read_installed
    from agentcoop.watch import assert_member

    assert_member(board, project, agent)
    clock = now or (lambda: datetime.now(timezone.utc))
    pause = sleep or time.sleep
    stream = out if out is not None else sys.stdout
    assert_single_loop(board, project, agent, now=_aware(clock()))
    set_agent_watching(board, project, agent, True, now=_aware(clock()))
    started_version = read_installed(board)
    announced: set[str] = set()
    last_keep: datetime | None = None
    last_beat: datetime | None = None
    since = _aware(clock())
    passes = 0
    while True:
        if max_passes is not None and passes >= max_passes:
            end_heartbeat(board, project, agent)
            return 0
        passes += 1
        current = _aware(clock())
        if read_installed(board) != started_version:
            _tick(stream, "Agent Co-op was updated. Run agentcoop cursor-loop again.")
            end_heartbeat(board, project, agent)
            return 5
        if not checking_enabled(board, project) or not agent_wants_watching(board, project, agent):
            _emit("Checking is stopped. cursor-loop has exited.", stream)
            end_heartbeat(board, project, agent)
            return 4
        if last_beat is None or (current - last_beat).total_seconds() >= HEARTBEAT_SECONDS:
            write_heartbeat(board, project, agent, since=since, seen=current, how="cursor-loop")
            last_beat = current
        prompt, ids = _mail_prompt(board, project, agent)
        fresh = ids - announced
        if fresh and prompt:
            announced.update(ids)
            _note_loop(board, agent, current)
            _tick(stream, prompt)
        else:
            status, now_line = _task_now(board, project)
            every = loop_interval_minutes(board)
            due = last_keep is None or (current - last_keep).total_seconds() >= every * 60
            if _keep_task(status, now_line) and due:
                last_keep = current
                _note_loop(board, agent, current)
                _tick(stream, "Check the Agent Co-op board and continue your current task: " + now_line)
        pause(POLL_SECONDS)


def _sync_waiting(board: Path, names: list[str], moment: datetime) -> None:
    """Post outbox answers so a helper file becomes mail while the hook waits."""
    from agentcoop.outbox import sync_project_outbox
    from agentcoop.requests import process_project_requests

    from agentcoop.ping import post_due_heartbeats

    for name in names:
        try:
            process_project_requests(board, name, now=moment)
            sync_project_outbox(board, name, now=moment)
            post_due_heartbeats(board, name, now=moment)
        except CoopError:
            continue


def cursor_stop_followup(
    board: Path,
    agent: str,
    raw: str,
    *,
    now=None,
    sleep=None,
    timeout_seconds: float | None = None,
    reserve_seconds: float = HOOK_RESERVE_SECONDS,
) -> dict:
    """Return a Cursor stop-hook response. Empty means do not send a follow-up."""
    import time

    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    status = str(payload.get("status") or "completed")
    if status != "completed":
        return {}
    clock = now or (lambda: datetime.now(timezone.utc))
    pause = sleep or time.sleep
    if timeout_seconds is None:
        timeout_seconds = hook_timeout_seconds(board)
    started = _aware(clock())
    generation = payload.get("generation_id")
    if not isinstance(generation, str):
        generation = ""
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str):
        transcript = ""
    transcript_size = _file_size(transcript)
    cursor_version = payload.get("cursor_version")
    if not isinstance(cursor_version, str):
        cursor_version = ""
    _remember_transcript(board, transcript, cursor_version)
    projects = []
    for name, record in (load_projects(board).get("projects") or {}).items():
        if role_of(record, agent):
            projects.append(name)
    if not projects:
        return {}
    active = [
        name
        for name in projects
        if checking_enabled(board, name) and agent_wants_watching(board, name, agent)
    ]
    if not active:
        return {}
    for name in active:
        note_hook(board, name, agent, now=started)
    deadline = started + timedelta(seconds=max(0, timeout_seconds - reserve_seconds))
    while True:
        current = _aware(clock())
        if _hold_back(board, agent, started, generation, transcript, transcript_size, current):
            return {}
        _sync_waiting(board, active, current)
        if _hold_back(board, agent, started, generation, transcript, transcript_size, current):
            return {}
        chunks = []
        for name in active:
            prompt, _ids = _mail_prompt(board, name, agent)
            if prompt:
                chunks.append(_hook_mail(prompt))
        if chunks:
            if _hold_back(board, agent, started, generation, transcript, transcript_size, current):
                return {}
            return _follow(board, agent, "\n".join(chunks), current)
        current = _aware(clock())
        if current >= deadline:
            if _hold_back(board, agent, started, generation, transcript, transcript_size, current):
                return {}
            return _follow(board, agent, KEEP_AWAKE, current)
        remaining = (deadline - current).total_seconds()
        pause(min(POLL_SECONDS, remaining) if remaining > 0 else 0)


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _note_loop(board: Path, agent: str, moment: datetime) -> None:
    from agentcoop.activity import note_activity, note_attention

    note_activity(board, agent, "cursor-loop", now=moment)
    note_attention(board, agent, "tick", now=moment)


def _follow(board: Path, agent: str, text: str, when: datetime) -> dict:
    from agentcoop.activity import note_attention

    note_attention(board, agent, "follow-up", now=when)
    return {"followup_message": text}


def _hold_back(
    board: Path,
    agent: str,
    started: datetime,
    generation: str,
    transcript: str,
    transcript_size: int,
    now: datetime,
) -> bool:
    """Return True when the hook should send nothing.

    Overdue mail is still sent. A transcript that looks busy does not drop it.
    """
    if _overdue_mail(board, now) is not None:
        return False
    return _hook_is_stale(board, agent, started, generation, transcript, transcript_size)


def _overdue_mail(board: Path, now: datetime) -> str | None:
    """Return the status line when the driver has left mail sitting too long.

    A tick, a hook follow-up, or an agentcoop command after the mail arrived
    counts as a pickup. A heartbeat does not. The transcript does not.
    """
    from agentcoop.activity import attention_at
    from agentcoop.core import new_messages

    try:
        every = int(load_config(board).get("progress_every_minutes") or 10)
    except (TypeError, ValueError, CoopError):
        every = 10
    if isinstance(every, bool) or every < 1:
        every = 10
    limit = every * 2 * 60
    moment = _aware(now)
    oldest: datetime | None = None
    try:
        projects = load_projects(board).get("projects") or {}
    except CoopError:
        return None
    for name, record in projects.items():
        if not isinstance(record, dict):
            continue
        driver = str(record.get("driver") or "")
        if not driver:
            continue
        try:
            messages, _issues = new_messages(board, str(name), driver)
        except CoopError:
            continue
        seen = attention_at(board, driver)
        for message in messages:
            arrived = _message_time(message)
            if arrived is None:
                continue
            if seen is not None and seen >= arrived:
                continue
            if (moment - arrived).total_seconds() <= limit:
                continue
            if oldest is None or arrived < oldest:
                oldest = arrived
    state_path = board / ".state" / "cursor-mail.json"
    if oldest is None:
        if state_path.is_file():
            try:
                state_path.unlink()
            except OSError:
                pass
        return None
    minutes = int((moment - oldest).total_seconds() // 60)
    line = MAIL_WAIT_LINE + str(minutes) + " min."
    since = oldest.strftime("%Y-%m-%dT%H:%M:%SZ")
    previous: dict = {}
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, json.JSONDecodeError):
            previous = {}
    if previous.get("since") != since:
        from agentcoop.trace import note_warning

        note_warning(board, line)
        try:
            atomic_write_bytes(state_path, dump_json({"since": since, "line": line}))
        except OSError:
            pass
    return line


def cursor_mail_line(board: Path, now: datetime | None = None) -> str:
    """Return the STATUS line for mail Cursor has not picked up, or empty."""
    moment = now or datetime.now(timezone.utc)
    found = _overdue_mail(board, moment)
    return found or ""


def _message_time(message: object) -> datetime | None:
    raw = getattr(message, "ts", None)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hook_is_stale(
    board: Path,
    agent: str,
    started: datetime,
    generation: str,
    transcript: str = "",
    transcript_size: int = 0,
) -> bool:
    from agentcoop.activity import turn_moved

    if turn_moved(board, agent, started, generation):
        return True
    return _later_user_message(transcript, transcript_size)


def _file_size(path: str) -> int:
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _later_user_message(path: str, size_at_start: int) -> bool:
    """Return True when a real new turn was written after the hook started.

    Only the bytes added since the hook started are read. A user line that is
    only a timestamp is not a new task.
    """
    extra = _read_added(path, size_at_start)
    if not extra:
        return False
    for line in extra.decode("utf-8", errors="replace").splitlines():
        if _line_kind(line) == "turn":
            return True
    return False


def _remember_transcript(board: Path, path: str, cursor_version: str) -> None:
    """Remember whether this Cursor transcript is a shape we can read.

    The format is sampled once, from the start of the file, not on every poll.
    """
    status = _transcript_status(path)
    recognized = status == "ok"
    state_path = board / ".state" / "cursor-transcript.json"
    previous: dict = {}
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, json.JSONDecodeError):
            previous = {}
    warned = previous.get("warned") is True and previous.get("recognized") is False
    payload = {
        "recognized": recognized,
        "cursor_version": cursor_version,
        "warned": (not recognized) or warned,
    }
    if not recognized and not warned:
        from agentcoop.trace import note_warning

        note_warning(board, TRANSCRIPT_WARNING)
        payload["warned"] = True
    if recognized:
        payload["warned"] = False
    try:
        atomic_write_bytes(state_path, dump_json(payload))
    except OSError:
        return


def _transcript_status(path: str) -> str:
    """Return ok, missing, unreadable, or unrecognized. Reads at most one sample."""
    kind, sample = _read_prefix(path)
    if kind != "data":
        return kind
    for line in sample.decode("utf-8", errors="replace").splitlines():
        if _line_kind(line) in ("known", "turn"):
            return "ok"
    return "unrecognized"


def _read_prefix(path: str) -> tuple[str, bytes]:
    """Return (kind, sample). kind is data, missing, or unreadable."""
    if not path:
        return "missing", b""
    file = Path(path)
    try:
        if not file.exists():
            return "missing", b""
        if not file.is_file():
            return "unreadable", b""
        with file.open("rb") as handle:
            return "data", handle.read(FORMAT_SAMPLE)
    except OSError:
        return "unreadable", b""


def _read_added(path: str, size_at_start: int) -> bytes:
    """Return only the bytes written after the hook started."""
    if not path:
        return b""
    file = Path(path)
    try:
        with file.open("rb") as handle:
            handle.seek(max(0, size_at_start))
            return handle.read()
    except OSError:
        return b""


def _line_kind(line: str) -> str:
    """Return blank, unknown, known, or turn."""
    text = line.strip()
    if not text:
        return "blank"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return "unknown"
    if not isinstance(data, dict):
        return "unknown"
    if data.get("type") == "turn_ended":
        return "known"
    role = data.get("role")
    if role == "assistant":
        return "known"
    if role != "user":
        return "unknown"
    query = _user_query_text(data)
    if query is None:
        return "known"
    if _real_query(query):
        return "turn"
    return "known"


def _user_query_text(data: dict) -> str | None:
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    if not parts:
        return None
    return "\n".join(parts)


def _real_query(text: str) -> bool:
    import re

    cleaned = re.sub(r"<timestamp>.*?</timestamp>", "", text, flags=re.S)
    cleaned = cleaned.replace("<user_query>", "").replace("</user_query>", "")
    cleaned = cleaned.strip()
    if not cleaned:
        return False
    if cleaned.startswith(SHELL_TASK_NOTICE):
        return False
    return True


def _hook_mail(prompt: str) -> str:
    if prompt.endswith(MAIL_TAIL_OLD):
        return prompt[: -len(MAIL_TAIL_OLD)] + MAIL_TAIL
    return prompt


def migrate_saved_hook_timeout(board: Path) -> None:
    """Move an untouched 600 second wait to 3600. A value the person set stays."""
    from agentcoop.projects import mutate_config

    try:
        data = load_config(board)
    except CoopError:
        return
    if data.get("cursor_hook_timeout_user_set") is True:
        return
    if data.get("cursor_hook_timeout_seconds") != OLD_DEFAULT_HOOK_TIMEOUT:
        return

    def change(config: dict) -> None:
        if config.get("cursor_hook_timeout_user_set") is True:
            return
        if config.get("cursor_hook_timeout_seconds") == OLD_DEFAULT_HOOK_TIMEOUT:
            config["cursor_hook_timeout_seconds"] = HOOK_TIMEOUT_SECONDS

    mutate_config(board, change)


def describe_hook_timeout(board: Path) -> str:
    """Return the sentence for How often does Cursor end its turn?"""
    seconds = hook_timeout_seconds(board)
    if seconds % 60 == 0:
        amount = str(seconds // 60) + " minutes"
    else:
        amount = str(seconds) + " seconds"
    return "Cursor ends its turn after " + amount + " when the chat is idle. A busy chat is left alone."


def set_hook_timeout_minutes(board: Path, minutes: int) -> str:
    """Save the wake-up wait and mark it as chosen by the person."""
    from agentcoop.projects import mutate_config

    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1 or minutes > 60:
        raise CoopError("Wake-up wait must be a whole number of minutes from 1 to 60.", code=2)
    seconds = minutes * 60

    def change(config: dict) -> None:
        config["cursor_hook_timeout_seconds"] = seconds
        config["cursor_hook_timeout_user_set"] = True

    mutate_config(board, change)
    refresh_cursor_hook_files(board)
    return (
        "Cursor will wait "
        + str(minutes)
        + " minutes before the keep-awake. A busy chat is left alone."
    )


def refresh_cursor_hook_files(board: Path) -> None:
    """Point installed Cursor hooks at the current wait and the prompt hook."""
    import hashlib

    from agentcoop.projects import load_projects, mutate_projects

    timeout = hook_timeout_seconds(board)
    try:
        projects = load_projects(board).get("projects") or {}
    except CoopError:
        return
    rewritten: list[tuple[str, Path, str]] = []
    for name, record in projects.items():
        if not isinstance(record, dict):
            continue
        raw = record.get("path")
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw) / ".cursor" / "hooks.json"
        if not path.is_file():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(loaded, dict) or not _patch_cursor_hooks(loaded, timeout):
            continue
        try:
            path.write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        rewritten.append((name, path, digest))
    if not rewritten:
        return

    def change(data: dict) -> None:
        for name, path, digest in rewritten:
            project = (data.get("projects") or {}).get(name)
            if not isinstance(project, dict):
                continue
            for entry in project.get("installed") or []:
                if isinstance(entry, dict) and Path(str(entry.get("path") or "")) == path:
                    entry["sha256"] = digest

    try:
        mutate_projects(board, change)
    except CoopError:
        return


def _patch_cursor_hooks(data: dict, timeout: int) -> bool:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    stop = hooks.get("stop")
    if not isinstance(stop, list):
        return False
    prompts = hooks.get("beforeSubmitPrompt")
    prompts_added = False
    if not isinstance(prompts, list):
        prompts = []
        prompts_added = True
    changed = False
    additions: list[str] = []
    for entry in stop:
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command") or "")
        needle = " hook cursor-stop --as "
        if needle not in command:
            continue
        if entry.get("timeout") != timeout:
            entry["timeout"] = timeout
            changed = True
        if "loop_limit" not in entry or entry.get("loop_limit") is not None:
            entry["loop_limit"] = None
            changed = True
        additions.append(command.replace(needle, " hook cursor-prompt --as ", 1))
    if not additions:
        return False
    if prompts_added:
        hooks["beforeSubmitPrompt"] = prompts
        changed = True
    for prompt_command in additions:
        if any(isinstance(item, dict) and item.get("command") == prompt_command for item in prompts):
            continue
        prompts.append({"command": prompt_command})
        changed = True
    return changed


def cursor_hooks(board: Path, agent_name: str) -> dict:
    """Return the project hooks.json fragment for one Cursor agent."""
    from agentcoop.paths import agentcoop_command

    base = agentcoop_command(board)
    stop = base + " hook cursor-stop --as " + agent_name
    prompt = base + " hook cursor-prompt --as " + agent_name
    return {
        "version": 1,
        "hooks": {
            "beforeSubmitPrompt": [{"command": prompt}],
            "stop": [
                {
                    "command": stop,
                    "timeout": hook_timeout_seconds(board),
                    "loop_limit": None,
                }
            ],
        },
    }
