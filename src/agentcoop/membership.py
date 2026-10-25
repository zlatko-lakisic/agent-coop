"""Create, join, and leave a project as the agent that is asking."""

from __future__ import annotations

import re
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import board_root, paths_conflict
from agentcoop.projects import (
    is_within_project,
    load_agents,
    load_projects,
    require_board,
    validate_project_name,
)

_SLUG = re.compile(r"[^a-z0-9-]+")


def folder_for_project(start: Path) -> Path:
    """Return the git root that contains start, or start itself."""
    folder = start.expanduser().resolve()
    for parent in [folder, *folder.parents]:
        if (parent / ".git").exists():
            return parent
    return folder


def _slug(text: str) -> str:
    raw = text.lower().replace("_", "-")
    raw = _SLUG.sub("-", raw)
    raw = re.sub(r"-{2,}", "-", raw).strip("-")
    return raw


def _containing(board: Path, folder: Path) -> tuple[str, dict] | None:
    projects = load_projects(board).get("projects") or {}
    here = folder.expanduser().resolve()
    matches = []
    for name, project in projects.items():
        raw = project.get("path")
        if not isinstance(raw, str):
            continue
        path = Path(raw)
        if paths_conflict(here, path) and is_within_project(here, path):
            matches.append((name, path, project))
    if not matches:
        return None
    matches.sort(key=lambda item: len(item[1].parts), reverse=True)
    name, _path, project = matches[0]
    return name, project


def _with_suffix(base: str, number: int) -> str:
    suffix = "-" + str(number)
    trimmed = base[: 48 - len(suffix)].rstrip("-")
    if not trimmed or not trimmed[0].isalnum():
        trimmed = "p"
    return trimmed + suffix


def free_project_name(board: Path, folder: Path, explicit: str | None) -> tuple[str, str | None]:
    """Return a free name, and the folder name when a suffix was added."""
    from agentcoop.wizard import suggest_name

    projects = load_projects(board).get("projects") or {}
    if explicit:
        validate_project_name(explicit)
        if explicit in projects:
            raise CoopError(
                "A project named " + explicit + " already exists. Run: agentcoop project show " + explicit
            )
        return explicit, None
    base = suggest_name(folder)
    if base not in projects:
        return base, None
    number = 2
    while number < 1000:
        candidate = _with_suffix(base, number)
        if candidate not in projects:
            return candidate, base
        number += 1
    raise CoopError("Could not find a free project name.")


def projects_matching(names: list[str], words: str) -> list[str]:
    """Return project names contained in the person's words."""
    folded = _slug(words)
    if not folded:
        return []
    padded = "-" + folded + "-"
    hits = []
    for name in names:
        if folded == name or ("-" + name + "-") in padded:
            hits.append(name)
    return hits


def choose_project(board: Path, *, explicit: str | None, cwd: Path, words: str) -> str:
    """Pick a project from a flag, the current folder, or the person's words."""
    projects = load_projects(board).get("projects") or {}
    names = list(projects)
    if explicit:
        if explicit not in projects:
            raise CoopError("There is no project named " + explicit + ". Run: agentcoop project list")
        return explicit
    found = _containing(board, cwd)
    if found is not None:
        return found[0]
    hits = projects_matching(names, words)
    if len(hits) == 1:
        return hits[0]
    lines = "\n".join("  " + name for name in sorted(names)) or "  (none)"
    raise CoopError(
        "Agent Co-op could not tell which project you mean.\n"
        "Projects:\n"
        + lines
        + "\nSay which project and try again.",
        code=4,
    )


def _default_note(board: Path, agent: str) -> str:
    from agentcoop.tools import get_tool

    record = (load_agents(board).get("agents") or {}).get(agent) or {}
    tool_key = str(record.get("tool") or "")
    try:
        note = get_tool(tool_key).helper_note
    except KeyError:
        note = ""
    return note or "helps the driver"


def _prepare_outbox(board: Path, project: str, agent: str) -> None:
    from agentcoop.outbox import is_outbox_agent, write_new_md
    from agentcoop.paths import ensure_outbox_dirs

    if not is_outbox_agent(board, agent):
        return
    ensure_outbox_dirs(board, project, agent)
    write_new_md(board, project, agent)


def _ensure_known(board: Path, agent: str) -> None:
    known = load_agents(board).get("agents") or {}
    if agent in known:
        return
    if agent == "claude_desktop":
        from agentcoop.wizard import connect_one

        connect_one("claude-desktop", agent_name=agent, yes=True)
        return
    raise CoopError(agent + " is not connected yet. Run: agentcoop connect self --tool <tool>")


def add_self_driver(agent: str, start: Path, name: str | None = None) -> str:
    """Create a project with this agent as the driver, or explain an existing one."""
    from agentcoop.confirm import confirm_person
    from agentcoop.wizard import add_project_flow, change_driver

    board = board_root()
    require_board(board)
    folder = folder_for_project(start)
    if not folder.is_dir():
        raise CoopError("That folder does not exist.")
    existing = _containing(board, folder)
    if existing is not None:
        project_name, record = existing
        driver = str(record.get("driver") or "")
        if driver == agent:
            return (
                "This folder is already the "
                + project_name
                + " project, and you are the driver. Nothing was changed."
            )
        print("This folder is already the " + project_name + " project. The driver is " + driver + ".")
        print("Changing the driver needs you to allow it.")
        confirm_person(
            "This will make " + agent + " the driver of " + project_name + ".",
            expected=project_name,
            kind="project",
            asked_by=agent,
        )
        return change_driver(project_name, agent, yes=True)
    chosen, renamed_from = free_project_name(board, folder, name)
    add_project_flow(
        path=folder,
        name=chosen,
        driver=agent,
        helpers={},
        lock_helpers=False,
        gitignore=True,
        yes=True,
        prompter=None,
    )
    line = "Created the project " + chosen + " in Agent Co-op. You are the driver."
    if renamed_from:
        line += " " + renamed_from + " was already used by another folder."
    return line + "\nFolder: " + str(folder)


def join_named(
    agent: str,
    project: str,
    note: str | None = None,
    *,
    tags: list[str] | None = None,
    can: list[str] | None = None,
    cost: str | None = None,
) -> str:
    """Add this agent as a helper. The caller already chose the project."""
    from agentcoop.wizard import helper_add

    board = board_root()
    require_board(board)
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ". Run: agentcoop project list")
    if record.get("driver") == agent:
        raise CoopError(
            "You are the driver for " + project + ", so you cannot join it as a helper."
        )
    if agent in (record.get("helpers") or {}):
        if tags is not None or can is not None or cost is not None or note is not None:
            from agentcoop.profiles import set_profile

            set_profile(board, project, agent, tags=tags, can=can, cost=cost, note=note)
            return "You are already a helper on " + project + ". Your profile was updated."
        return "You are already a helper on " + project + ". Nothing was changed."
    _ensure_known(board, agent)
    used = note if note else _default_note(board, agent)
    helper_add(project, agent, used)
    if tags is not None or can is not None or cost is not None:
        from agentcoop.profiles import set_profile

        set_profile(board, project, agent, tags=tags, can=can, cost=cost)
    _prepare_outbox(board, project, agent)
    return "You joined " + project + " as a helper."


def join_self(
    agent: str,
    *,
    project: str | None,
    cwd: Path,
    words: str,
    note: str | None,
    tags: list[str] | None = None,
    can: list[str] | None = None,
    cost: str | None = None,
) -> str:
    """Join the project this folder, flag, or wording points at."""
    board = board_root()
    require_board(board)
    chosen = choose_project(board, explicit=project, cwd=cwd, words=words)
    return join_named(agent, chosen, note=note, tags=tags, can=can, cost=cost)


def leave_named(agent: str, project: str) -> str:
    """Remove this agent as a helper. A driver is told to change roles instead."""
    from agentcoop.wizard import helper_remove

    board = board_root()
    require_board(board)
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ". Run: agentcoop project list")
    if record.get("driver") == agent:
        raise CoopError(
            "You are the driver for "
            + project
            + ". Changing the driver needs the person to allow it."
        )
    if agent not in (record.get("helpers") or {}):
        return "You are not a helper on " + project + ". Nothing was changed."
    text = helper_remove(project, agent)
    line = "You left " + project + "."
    extra = [item for item in text.splitlines()[1:] if item.strip()]
    if extra:
        return line + "\n" + "\n".join(extra)
    return line


def leave_self(agent: str, *, project: str | None, cwd: Path, words: str) -> str:
    """Leave the project this folder, flag, or wording points at."""
    board = board_root()
    require_board(board)
    chosen = choose_project(board, explicit=project, cwd=cwd, words=words)
    return leave_named(agent, chosen)
