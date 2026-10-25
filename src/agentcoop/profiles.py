"""What a helper is good at. Stored next to its role, not inside the note string."""

from __future__ import annotations

import re
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.projects import load_projects, mutate_projects, role_of

CAN = ("read", "run", "vision", "web")
COSTS = ("low", "medium", "high")
_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def parse_list(text: str, *, kind: str) -> list[str]:
    """Split a comma-separated list. Empty means none."""
    items = []
    for part in (text or "").split(","):
        item = part.strip().lower()
        if not item:
            continue
        if item not in items:
            items.append(item)
    if kind == "can":
        bad = [item for item in items if item not in CAN]
        if bad:
            raise CoopError(
                "can must be read, run, vision, or web. " + ", ".join(bad) + " is not one of those.",
                code=2,
            )
    else:
        bad = [item for item in items if _TAG.fullmatch(item) is None]
        if bad:
            raise CoopError(
                "A tag is short lowercase words, such as code-review. " + ", ".join(bad) + " is not one.",
                code=2,
            )
    return items


def parse_cost(text: str) -> str:
    """Return low, medium, or high."""
    cost = (text or "").strip().lower()
    if cost not in COSTS:
        raise CoopError("Cost must be low, medium, or high.", code=2)
    return cost


def profile_of(record: dict, agent: str) -> dict:
    """Return tags, can, cost, and the helper note. Missing fields stay empty."""
    stored = {}
    profiles = record.get("profiles")
    if isinstance(profiles, dict) and isinstance(profiles.get(agent), dict):
        stored = profiles[agent]
    tags = stored.get("tags") if isinstance(stored.get("tags"), list) else []
    can = stored.get("can") if isinstance(stored.get("can"), list) else []
    cost = stored.get("cost") if isinstance(stored.get("cost"), str) else ""
    note = ""
    helpers = record.get("helpers") or {}
    if isinstance(helpers, dict) and isinstance(helpers.get(agent), str):
        note = helpers[agent]
    return {
        "tags": [str(item) for item in tags],
        "can": [str(item) for item in can],
        "cost": cost,
        "notes": note,
    }


def describe(profile: dict) -> str:
    """Return one short sentence about a profile."""
    tags = ", ".join(profile.get("tags") or []) or "no tags yet"
    can = ", ".join(profile.get("can") or []) or "no abilities listed"
    cost = profile.get("cost") or "not set"
    return "tags " + tags + ". can " + can + ". cost " + cost + "."


def set_profile(
    board: Path,
    project: str,
    agent: str,
    *,
    tags: list[str] | None = None,
    can: list[str] | None = None,
    cost: str | None = None,
    note: str | None = None,
) -> str:
    """Save a helper profile. The caller already decided that this write is allowed."""

    def change(data: dict) -> None:
        record = (data.get("projects") or {}).get(project)
        if record is None:
            raise CoopError("There is no project named " + project + ".")
        if not role_of(record, agent):
            raise CoopError(agent + " is not in " + project + ".")
        profiles = record.get("profiles")
        if not isinstance(profiles, dict):
            profiles = {}
            record["profiles"] = profiles
        current = profiles.get(agent) if isinstance(profiles.get(agent), dict) else {}
        updated = dict(current)
        if tags is not None:
            updated["tags"] = list(tags)
        if can is not None:
            updated["can"] = list(can)
        if cost is not None:
            updated["cost"] = cost
        profiles[agent] = updated
        if note is not None:
            helpers = record.get("helpers")
            if isinstance(helpers, dict) and agent in helpers:
                helpers[agent] = note

    mutate_projects(board, change)
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    return agent + ": " + describe(profile_of(record, agent))
