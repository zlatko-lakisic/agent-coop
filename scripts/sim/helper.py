"""Play an outbox helper by writing files. This script never runs agentcoop."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def outbox(board: Path, project: str, agent: str) -> Path:
    """Return the helper outbox folder."""
    return board / "projects" / project / "outbox" / agent


def stamp(moment: datetime) -> str:
    """Return a UTC stamp at second precision."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_text(path: Path, text: str, *, crlf: bool = False, bom: bool = False) -> None:
    """Write one outbox file, with optional CRLF and a UTF-8 BOM."""
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if crlf:
        body = body.replace("\n", "\r\n")
    if not body.endswith("\n") and not body.endswith("\r\n"):
        body += "\r\n" if crlf else "\n"
    raw = body.encode("utf-8")
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def apply_event(
    board: Path,
    project: str,
    agent: str,
    event: dict,
    re_id: str,
    moment: datetime,
) -> list[Path]:
    """Write the files for one timeline event. Return the paths."""
    kind = str(event.get("kind") or "answer")
    if kind == "silent":
        return []
    folder = outbox(board, project, agent)
    crlf = bool(event.get("crlf"))
    bom = bool(event.get("bom"))
    written: list[Path] = []
    if kind == "nudge":
        path = folder / ("nudge." + stamp(moment) + ".md")
        write_text(path, str(event.get("text") or "Please look."), crlf=crlf, bom=bom)
        return [path]
    texts = event.get("texts")
    if kind == "burst":
        texts = list(texts or [])
    else:
        texts = [str(event.get("text") or "")]
    for index, text in enumerate(texts):
        suffix = "" if index == 0 else "." + str(index + 1)
        path = folder / (re_id + "." + stamp(moment) + suffix + ".md")
        write_text(path, str(text), crlf=crlf, bom=bom)
        written.append(path)
    return written


def run_timeline(
    board: Path,
    project: str,
    agent: str,
    events: list[dict],
    re_id: str,
    start: datetime | None = None,
) -> list[Path]:
    """Apply a scenario timeline. `at` is seconds from the start."""
    origin = start or datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    written: list[Path] = []
    for event in events:
        moment = origin + timedelta(seconds=float(event.get("at") or 0))
        written.extend(apply_event(board, project, agent, event, re_id, moment))
    return written


def load_scenario(path: Path) -> dict:
    """Load a scenario JSON object."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("A scenario file must be a JSON object.")
    return data
