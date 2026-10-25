"""Standing approvals the person gives in the driver's own chat."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import project_dir
from agentcoop.projects import atomic_write_bytes, dump_json, load_projects, require_board, role_of

_LIMIT = 500


def approvals_path(board: Path, project: str) -> Path:
    """Return the standing-approvals file for one project."""
    return project_dir(board, project) / "approvals.json"


def load_approvals(board: Path, project: str) -> list[dict]:
    """Return saved approvals. A missing file means there are none."""
    path = approvals_path(board, project)
    if not path.is_file():
        return []
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    kept = []
    for item in items:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            kept.append(item)
    return kept


def add_approval(board: Path, project: str, agent: str, text: str, now: datetime | None = None) -> str:
    """Record one standing approval from the driver. Return the sentence to print."""
    require_board(board)
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    if role_of(record, agent) != "driver":
        raise CoopError("Only the driver can record a standing approval.")
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        raise CoopError("Say what you may do while the person is away.", code=2)
    if len(cleaned) > _LIMIT:
        raise CoopError("A standing approval must be " + str(_LIMIT) + " characters or fewer.", code=2)
    from agentcoop.safety import secret_warning

    if secret_warning(cleaned):
        raise CoopError("That text looks like a password or a key, so it was not saved.")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stamp = moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = load_approvals(board, project)
    items.append({"text": cleaned, "by": agent, "ts": stamp})
    payload = {"items": items}
    atomic_write_bytes(approvals_path(board, project), dump_json(payload))
    return "Standing approval saved: " + cleaned


def clear_approvals(board: Path, project: str, agent: str) -> str:
    """Remove standing approvals. Only the driver may do this."""
    require_board(board)
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    if role_of(record, agent) != "driver":
        raise CoopError("Only the driver can clear standing approvals.")
    path = approvals_path(board, project)
    if path.is_file():
        path.unlink()
    return "Standing approvals cleared."


def header_lines(board: Path, project: str) -> list[str]:
    """Return check lines for standing approvals and a wait on the person."""
    lines = []
    texts = [str(item.get("text") or "") for item in load_approvals(board, project)]
    texts = [text for text in texts if text]
    if texts:
        lines.append("Standing approvals: " + "; ".join(texts) + ".")
    waiting = _waiting_now(board, project)
    if waiting:
        lines.append("Waiting on the person: " + waiting)
    return lines


def status_waiting_lines(board: Path) -> list[str]:
    """Return the phone lines for every project that is waiting on the person."""
    lines = []
    projects = load_projects(board).get("projects") or {}
    for name in sorted(projects):
        waiting = _waiting_now(board, name)
        if waiting:
            lines.append("Waiting on you in " + name + ": " + waiting)
    return lines


def _waiting_now(board: Path, project: str) -> str:
    from agentcoop.progress import latest_progress
    from agentcoop.progressmeta import parse_fields

    path = latest_progress(board, project)
    if path is None:
        return ""
    try:
        fields = parse_fields(path.read_text(encoding="utf-8"))
    except (OSError, CoopError):
        return ""
    if str(fields.get("Status") or "") != "waiting":
        return ""
    now = str(fields.get("Now") or "").strip()
    return now or "the driver needs a decision."
