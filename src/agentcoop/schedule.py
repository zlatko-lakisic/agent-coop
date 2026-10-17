"""Which agents can run in the background, and the commands that start them.

The schedule installer, tick loop, and worktree live in later parts.
This module owns the command line and the status text those parts call.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import agentcoop_command
from agentcoop.projects import load_agents, load_projects, require_board, role_of
from agentcoop.tools import get_tool

NOT_FOUND = (
    "{display} cannot be started in the background because its command was not found. "
    "It answers when you open it."
)


def resolve_cli_path(tool: object) -> str | None:
    """Return the absolute path of the tool's CLI, or None."""
    names = getattr(tool, "cli_names", ()) or ()
    if tool.key == "chatgpt-codex":  # type: ignore[attr-defined]
        found = shutil.which("codex")
        return str(Path(found).resolve()) if found else None
    for name in names:
        found = shutil.which(name)
        if found:
            return str(Path(found).resolve())
    return None


def schedulable_reason(board: Path, agent_name: str) -> str | None:
    """Return None when the agent can be scheduled, else a plain reason."""
    agents = load_agents(board)
    record = (agents.get("agents") or {}).get(agent_name)
    if record is None:
        return agent_name + " is not connected."
    tool_key = str(record.get("tool") or "")
    try:
        tool = get_tool(tool_key)
    except KeyError:
        return agent_name + " uses an unknown tool."
    display = str(record.get("display") or tool.display)
    if tool.headless == "none":
        return display + " answers when you open the app."
    if tool.headless == "todo":
        return (
            display
            + " cannot be started in the background yet. "
            + "A one-shot mode with permission controls is not confirmed."
        )
    if tool_key == "runner" or record.get("runner"):
        return None
    if tool.key == "chatgpt-codex" and not shutil.which("codex"):
        return "Answers when you open the ChatGPT desktop app."
    cli = record.get("cli_path") or ""
    if not cli or not Path(cli).is_file():
        return NOT_FOUND.format(display=display)
    return None


def build_argv(
    board: Path,
    agent_name: str,
    *,
    role: str,
    prompt: str,
    cwd: Path,
    project_path: Path,
) -> list[str]:
    """Return the argv for one scheduled run."""
    agents = load_agents(board)
    record = (agents.get("agents") or {}).get(agent_name) or {}
    tool = get_tool(str(record.get("tool") or ""))
    spec = tool.headless
    if not isinstance(spec, dict):
        raise CoopError(agent_name + " cannot be started in the background.")
    template = spec.get("driver" if role == "driver" else "helper") or []
    model = str(record.get("model") or "").strip()
    cli = str(record.get("cli_path") or "")
    if tool.key == "runner" or record.get("runner"):
        cli = agentcoop_command(board)
    values = {
        "cli": cli,
        "prompt": prompt,
        "cwd": str(cwd),
        "project_path": str(project_path),
        "board_cmd": agentcoop_command(board),
        "agent": agent_name,
        "model_flag": "",
    }
    argv: list[str] = []
    for part in template:
        text = str(part)
        if text == "{model_flag}":
            if model:
                argv.extend(_model_args(tool.key, model))
            continue
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        if text:
            argv.append(text)
    return argv


def _model_args(tool_key: str, model: str) -> list[str]:
    if tool_key == "gemini-cli":
        return ["--model", model]
    if tool_key in ("codex", "chatgpt-codex"):
        return ["--model", model]
    return ["--model", model]


def format_status(board: Path) -> str:
    """Return a short status. The full status lands with the schedule."""
    require_board(board)
    agents = load_agents(board)
    projects = load_projects(board)
    lines = ["Schedule: not installed yet."]
    for name, record in (agents.get("agents") or {}).items():
        reason = schedulable_reason(board, name)
        display = str(record.get("display") or name)
        if reason:
            lines.append(display + ": " + reason)
        else:
            lines.append(display + ": can run in the background.")
        memberships = []
        for project, item in (projects.get("projects") or {}).items():
            role = role_of(item, name)
            if role:
                memberships.append(project + " (" + role + ")")
        if memberships:
            lines.append("  projects: " + ", ".join(memberships))
    return "\n".join(lines) + "\n"


def draft_file(board: Path, agent: str, name: str, body: str) -> Path:
    """Write a draft into the agent's files folder and return its path."""
    from agentcoop.paths import files_dir, private_dir
    from agentcoop.projects import resolve_project, validate_agent_name

    validate_agent_name(agent)
    if not name or name != Path(name).name or ".." in Path(name).parts:
        raise CoopError(
            "The draft name must be a file name, not a path. Example: notes.md"
        )
    project = resolve_project(board, explicit=None, cwd=Path.cwd(), agent=agent)
    folder = files_dir(board, project, agent)
    private_dir(folder)
    path = folder / name
    payload = body if body.endswith("\n") else body + "\n"
    data = payload.encode("utf-8")
    mode = 0o600 if os.name != "nt" else 0o666
    fd = os.open(
        str(path),
        os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, "O_BINARY", 0),
        mode,
    )
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path
