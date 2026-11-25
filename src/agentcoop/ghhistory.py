"""Copy a project's earlier messages to GitHub, and close conversations that are finished.

A project that already has messages can start to talk through GitHub. Its
earlier messages are packed many to a comment under one issue, so the record
is complete in one place and another computer that joins gets them too.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop import ghfiles, github
from agentcoop.errors import CoopError, TransportDown
from agentcoop.ghsync import (
    _ensure_labels,
    _new_issue,
    _remember_issue,
    _require_private,
    _save_index,
    _with_state,
    load_index,
    load_state,
    sync_dir,
)
from agentcoop.ghwire import (
    HISTORY_LIMIT,
    HISTORY_TITLE,
    encoded_length,
    history_event,
    render_history,
    render_history_issue,
)
from agentcoop.paths import archive_dir, inbox_done
from agentcoop.projects import board_lock, driver_is_here, github_repo, load_projects

MISSING = "missing:"
PACE_SECONDS = 1.0
MOVING_SECONDS = 15 * 60


class Copied:
    """What a copy of earlier messages did."""

    def __init__(self) -> None:
        self.messages = 0
        self.files = 0
        self.missing_files = 0
        self.too_long = 0


def moving_path(board: Path, project: str) -> Path:
    """Return the file that says a project is being moved to GitHub right now."""
    return sync_dir(board, project) / "moving"


def counts(board: Path, project: str) -> tuple[int, int]:
    """Return how many earlier messages and attached files a project has."""
    files = set()
    messages = _raw_messages(board, project)
    for raw in messages:
        for item in raw.get("attachments") or []:
            files.add(str(item))
    return len(messages), len(files)


def _raw_messages(board: Path, project: str) -> list[dict]:
    from agentcoop.core import log_messages

    found = []
    for message in log_messages(board, project):
        if message.path is None:
            continue
        try:
            data = json.loads(message.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id") == message.id:
            found.append(data)
    return found


def _is_done(board: Path, project: str, agent: str, message_id: str) -> bool:
    if (inbox_done(board, project, agent) / message_id).exists():
        return True
    return (archive_dir(board, project, agent) / "done" / message_id).exists()


def _wire(board: Path, project: str, repo: str, raw: dict, result: Copied, pause) -> dict:
    """Return a message as it goes to GitHub. Its files are committed first when they can be."""
    paths = raw.get("attachments")
    if not isinstance(paths, list) or not paths:
        return raw
    infos = raw.get("attachment_info") if isinstance(raw.get("attachment_info"), list) else []
    limit = ghfiles.max_bytes(board)
    wire = dict(raw)
    refs = []
    wire_infos = []
    for position, shown in enumerate(paths):
        info = dict(infos[position]) if position < len(infos) and isinstance(infos[position], dict) else {}
        local = Path(str(info.get("path") or shown))
        name = str(info.get("name") or local.name or "file")
        ref = MISSING + name
        try:
            usable = local.is_absolute() and local.is_file() and local.stat().st_size <= limit
        except OSError:
            usable = False
        if usable:
            digest = hashlib.sha256(local.read_bytes()).hexdigest()
            try:
                ref = ghfiles.upload(repo, project, local, digest, name)
            except TransportDown:
                raise
            except CoopError:
                ref = MISSING + name
            else:
                info.setdefault("name", name)
                info["sha256"] = digest
                info["bytes"] = local.stat().st_size
                result.files += 1
                pause()
        if ref.startswith(MISSING):
            result.missing_files += 1
        refs.append(ref)
        info.pop("source_path", None)
        info["path"] = ref
        info.setdefault("name", name)
        wire_infos.append(info)
    wire["attachments"] = refs
    wire["attachment_info"] = wire_infos
    return wire


def post_history(board: Path, project: str, repo: str, *, sleep=None) -> Copied:
    """Copy every earlier message that is not on GitHub yet. Safe to run again after a failure."""
    result = Copied()
    wait = sleep or time.sleep

    def pause() -> None:
        if PACE_SECONDS > 0:
            wait(PACE_SECONDS)

    client = github.client()
    _require_private(client, repo, project, {})
    marker = moving_path(board, project)

    def action(state: dict) -> bool:
        state["repo"] = repo
        index = load_index(board, project)
        pending = [raw for raw in _raw_messages(board, project) if str(raw.get("id")) not in index["messages"]]
        if not pending:
            return True
        _ensure_labels(client, repo, project, state)
        issue = state.get("history_issue")
        if not isinstance(issue, int):
            issue = _new_issue(client, repo, project, HISTORY_TITLE, render_history_issue(project))
            if issue is None:
                raise TransportDown("GitHub did not open the issue for earlier messages.")
            state["history_issue"] = issue
            _remember_issue(board, project, issue)
        part = int(state.get("history_parts") or 0)
        batch: list[dict] = []
        size = 0

        def flush() -> None:
            nonlocal batch, size, part
            if not batch:
                return
            done: dict[str, list[str]] = {}
            for item in batch:
                agent = str(item.get("recipient"))
                if _is_done(board, project, agent, str(item.get("id"))):
                    done.setdefault(agent, []).append(str(item.get("id")))
            part += 1
            reply = client.call(
                "POST",
                "/repos/" + repo + "/issues/" + str(issue) + "/comments",
                body={"body": render_history(history_event(project, part, batch, done))},
            )
            comment = reply.data.get("id") if isinstance(reply.data, dict) else 0
            with board_lock(board):
                fresh = load_index(board, project)
                for item in batch:
                    fresh["messages"][str(item.get("id"))] = {"issue": issue, "comment": comment or 0, "history": True}
                for agent, ids in done.items():
                    for mid in ids:
                        fresh["done"][agent + "/" + mid] = comment or 0
                _save_index(board, project, fresh)
            state["history_parts"] = part
            result.messages += len(batch)
            batch = []
            size = 0
            pause()

        for raw in pending:
            wire = _wire(board, project, repo, raw, result, pause)
            length = encoded_length(wire)
            if length > HISTORY_LIMIT:
                # It cannot fit in a comment. It stays on this computer and is still valid here.
                with board_lock(board):
                    fresh = load_index(board, project)
                    fresh["messages"][str(raw.get("id"))] = {"issue": issue, "comment": 0, "history": True, "local": True}
                    _save_index(board, project, fresh)
                result.too_long += 1
                continue
            if batch and size + length > HISTORY_LIMIT:
                flush()
            batch.append(wire)
            size += length
        flush()
        return True

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(time.time()), encoding="ascii")
    try:
        if _with_state(board, project, action, wait=30.0) is None:
            raise TransportDown("Another command is reading GitHub. Try again in a moment.")
    finally:
        try:
            marker.unlink()
        except OSError:
            pass
    return result


def close_finished(board: Path, project: str, repo: str, older_than_days: int, now: datetime | None = None) -> int:
    """Close the conversation issues that are finished. Only the driver's computer does.

    A conversation is finished when every message in it was marked done by the
    agent it was sent to, and the newest one is older than the given number of days.
    """
    from agentcoop.core import file_id, log_messages

    record = (load_projects(board).get("projects") or {}).get(project)
    if github_repo(record) != repo or not driver_is_here(board, record):
        return 0
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = (moment - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    index = load_index(board, project)
    state = load_state(board, project)
    skip = {state.get("project_issue"), state.get("history_issue")}
    known = {file_id(message): message for message in log_messages(board, project)}
    by_issue: dict[int, list[str]] = {}
    for mid, entry in index["messages"].items():
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int) and not entry.get("history"):
            by_issue.setdefault(entry["issue"], []).append(mid)
    finished = []
    for issue, ids in sorted(by_issue.items()):
        if issue in skip or str(issue) in index["closed"]:
            continue
        if all(
            mid in known and known[mid].ts <= cutoff and (known[mid].recipient + "/" + mid) in index["done"]
            for mid in ids
        ):
            finished.append(issue)
    if not finished:
        return 0
    client = github.client()
    closed = 0
    for issue in finished:
        try:
            client.call(
                "PATCH",
                "/repos/" + repo + "/issues/" + str(issue),
                body={"state": "closed", "state_reason": "completed"},
            )
        except github.NotFound:
            pass
        with board_lock(board):
            fresh = load_index(board, project)
            fresh["closed"][str(issue)] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
            _save_index(board, project, fresh)
        closed += 1
    return closed
