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


def role_template(tool: object, role: str) -> object:
    """Return the helper or driver template, which may be a list, todo, or none."""
    spec = getattr(tool, "headless", "none")
    if not isinstance(spec, dict):
        return spec
    return spec.get(role, "todo")


def schedulable_reason(board: Path, agent_name: str, role: str | None = None) -> str | None:
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
    if role:
        template = role_template(tool, role)
        if template == "todo" or not isinstance(template, list):
            detail = ""
            if isinstance(tool.headless, dict):
                detail = str(tool.headless.get(role + "_todo") or "")
            reason = display + " cannot be scheduled as the " + role + "."
            if detail:
                reason += " " + detail
            return reason
    if tool_key == "runner" or record.get("runner"):
        return None
    if tool.key == "chatgpt-codex" and not shutil.which("codex") and not record.get("cli_path"):
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
    template = spec.get(role) if isinstance(spec, dict) else None
    if not isinstance(template, list):
        raise CoopError(agent_name + " cannot be started in the background as the " + role + ".")
    model = str(record.get("model") or "").strip()
    cli = str(record.get("cli_path") or "")
    if tool.key == "runner" or record.get("runner"):
        from agentcoop.pathenv import command_argv

        cli = command_argv(board)[0]
    values = {
        "cli": cli,
        "prompt": prompt,
        "cwd": str(cwd),
        "project_path": str(project_path),
        "board": str(board),
        "board_cmd": agentcoop_command(board),
        "agent": agent_name,
        "model_flag": "",
    }
    from agentcoop.pathenv import command_argv

    argv: list[str] = []
    for part in template:
        text = str(part)
        if text == "{model_flag}":
            if model:
                argv.extend(_model_args(tool.key, model))
            continue
        if text == "{board_cmd}":
            argv.extend(command_argv(board))
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
    """Show the schedule and each agent."""
    from agentcoop.rounds import format_status as _status

    return _status(board)


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
