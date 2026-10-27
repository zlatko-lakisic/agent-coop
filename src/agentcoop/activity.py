"""Remember that an agent is in the middle of a turn.

The Cursor stop hook waits after a turn ends. If this agent does anything
during that wait, the old hook must send nothing. A newer turn starts its
own hook when it ends.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.projects import atomic_write_bytes, dump_json, validate_agent_name


def activity_path(board: Path, agent: str) -> Path:
    """Return the activity file for one agent."""
    validate_agent_name(agent)
    return board / ".state" / "activity" / (agent + ".json")


def note_activity(
    board: Path,
    agent: str,
    source: str,
    *,
    now: datetime | None = None,
    generation_id: str | None = None,
    conversation_id: str | None = None,
) -> None:
    """Record one sign that this agent is busy. Failures stay quiet."""
    try:
        _write_activity(
            board,
            agent,
            source,
            now=now,
            generation_id=generation_id,
            conversation_id=conversation_id,
        )
    except Exception:
        return


def note_prompt(board: Path, agent: str, raw: str) -> None:
    """Record a Cursor beforeSubmitPrompt payload."""
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    generation = payload.get("generation_id")
    conversation = payload.get("conversation_id")
    note_activity(
        board,
        agent,
        "prompt",
        generation_id=generation if isinstance(generation, str) else None,
        conversation_id=conversation if isinstance(conversation, str) else None,
    )


def note_attention(
    board: Path,
    agent: str,
    source: str,
    *,
    now: datetime | None = None,
) -> None:
    """Record a tick, a command, or a hook follow-up. A heartbeat does not count."""
    try:
        validate_agent_name(agent)
        moment = _aware(now or datetime.now(timezone.utc))
        path = board / ".state" / "attention" / (agent + ".json")
        atomic_write_bytes(path, dump_json({"last_at": _stamp(moment), "source": source}))
    except Exception:
        return


def attention_at(board: Path, agent: str) -> datetime | None:
    """Return the last tick, command, or follow-up time for this agent."""
    try:
        path = board / ".state" / "attention" / (agent + ".json")
    except CoopError:
        return None
    data = _read_json(path)
    return _parse_stamp(data.get("last_at"))


def load_activity(board: Path, agent: str) -> dict:
    """Return the activity record, or an empty dict when there is none."""
    try:
        path = activity_path(board, agent)
    except CoopError:
        return {}
    return _read_json(path)


def turn_moved(board: Path, agent: str, started: datetime, generation_id: str = "") -> bool:
    """Return True when this agent did something after the hook started.

    Cursor's stop hook sees stdin only once. loop_count cannot change while
    the hook waits. A later command, heartbeat, progress write, or prompt
    updates this file. A prompt can also store a new generation_id. That id
    changes on every user message, so a different one means a newer turn.
    """
    data = load_activity(board, agent)
    last = _parse_stamp(data.get("last_at"))
    if last is None:
        return False
    start = _aware(started)
    if last > start:
        return True
    recorded = data.get("generation_id")
    hook_generation = generation_id if isinstance(generation_id, str) else ""
    if (
        hook_generation
        and isinstance(recorded, str)
        and recorded
        and recorded != hook_generation
        and data.get("source") == "prompt"
        and last >= start
    ):
        return True
    return False


def _write_activity(
    board: Path,
    agent: str,
    source: str,
    *,
    now: datetime | None,
    generation_id: str | None,
    conversation_id: str | None,
) -> None:
    previous = load_activity(board, agent)
    moment = _aware(now or datetime.now(timezone.utc))
    payload = {"last_at": _stamp(moment), "source": source}
    generation = generation_id if isinstance(generation_id, str) and generation_id else ""
    if not generation and isinstance(previous.get("generation_id"), str):
        generation = previous["generation_id"]
    if generation:
        payload["generation_id"] = generation
    conversation = conversation_id if isinstance(conversation_id, str) and conversation_id else ""
    if not conversation and isinstance(previous.get("conversation_id"), str):
        conversation = previous["conversation_id"]
    if conversation:
        payload["conversation_id"] = conversation
    atomic_write_bytes(activity_path(board, agent), dump_json(payload))


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    delay = 0.02
    for attempt in range(8):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            if attempt == 7:
                return {}
            time.sleep(delay)
            delay = min(delay * 2, 0.2)
            continue
        if isinstance(data, dict):
            return data
        return {}
    return {}


def _stamp(moment: datetime) -> str:
    return _aware(moment).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    text = value[:-1]
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
