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
HOOK_TIMEOUT_SECONDS = 30
HOOK_RESERVE_SECONDS = 5
TICK_PREFIX = "AGENT_LOOP_TICK_board "


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
            _tick(stream, prompt)
        else:
            status, now_line = _task_now(board, project)
            every = loop_interval_minutes(board)
            due = last_keep is None or (current - last_keep).total_seconds() >= every * 60
            if status in ("working", "blocked") and due and now_line:
                last_keep = current
                _tick(stream, "Check the Agent Co-op board and continue your current task: " + now_line)
        pause(POLL_SECONDS)


def cursor_stop_followup(
    board: Path,
    agent: str,
    raw: str,
    *,
    now=None,
    sleep=None,
    timeout_seconds: float = HOOK_TIMEOUT_SECONDS,
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
    projects = []
    for name, record in (load_projects(board).get("projects") or {}).items():
        if role_of(record, agent):
            projects.append(name)
    if not projects:
        return {}
    clock = now or (lambda: datetime.now(timezone.utc))
    pause = sleep or time.sleep
    started = _aware(clock())
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
        chunks = []
        for name in active:
            prompt, _ids = _mail_prompt(board, name, agent)
            if prompt:
                chunks.append(prompt.replace("then continue.", "then end your turn."))
        if chunks:
            return {"followup_message": "\n".join(chunks)}
        current = _aware(clock())
        if current >= deadline:
            return {}
        remaining = (deadline - current).total_seconds()
        pause(min(POLL_SECONDS, remaining) if remaining > 0 else 0)


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def cursor_hooks(board: Path, agent_name: str) -> dict:
    """Return the project hooks.json fragment for one Cursor agent."""
    from agentcoop.paths import agentcoop_command

    command = agentcoop_command(board) + " hook cursor-stop --as " + agent_name
    return {
        "version": 1,
        "hooks": {
            "stop": [
                {
                    "command": command,
                    "timeout": HOOK_TIMEOUT_SECONDS,
                    "loop_limit": None,
                }
            ]
        },
    }
