"""Setup and project commands, interactive or from flags."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from agentcoop.core import send, waiting
from agentcoop.detect import detect_installed
from agentcoop.errors import CoopError
from agentcoop.install import (
    connect_agent,
    install_project_files,
    preview_project_files,
    remove_project_files,
    repair_project_files,
    rollback_project_add,
    stage_runtime,
    undo_entry,
    write_board_readme,
)
from agentcoop.paths import board_root
from agentcoop.projects import (
    add_helper,
    add_project,
    init_board_files,
    load_agents,
    load_projects,
    remove_helper,
    save_projects,
    set_driver,
    validate_project_name,
)
from agentcoop.tools import (
    find_tool,
    get_tool,
    sanitize_agent_name,
    supported_tools,
    tool_can_drive,
    tool_choices_text,
)


class Prompter:
    """Ask a person a question. Agents should use flags instead."""

    def __init__(self, input_fn=None, output_fn=print) -> None:
        self._input = input_fn or input
        self.output = output_fn

    def say(self, text: str) -> None:
        """Print one line."""
        self.output(text)

    def line(self, prompt: str, default: str | None = None) -> str:
        """Read one line. Enter keeps the default."""
        suffix = " [" + default + "]" if default else ""
        try:
            value = self._input(prompt + suffix + ": ")
        except EOFError as exc:
            raise CoopError(
                "This step needs an answer. Run the command with flags instead of the interactive form."
            ) from exc
        value = value.strip()
        if value:
            return value
        return default or ""

    def yes(self, prompt: str, default: bool = False) -> bool:
        """Ask a yes or no question."""
        hint = "Y/n" if default else "y/N"
        value = self.line(prompt + " (" + hint + ")", "yes" if default else "no")
        return value.lower() in ("y", "yes")


def setup_board(
    *,
    connect: list[str] | None = None,
    yes: bool = False,
    detect_only: bool = False,
    as_json: bool = False,
    model: str | None = None,
    prompter: Prompter | None = None,
) -> dict:
    """Create the board and connect agents. Safe to run again."""
    found = detect_installed()
    if detect_only:
        return {"found": found}
    board = board_root()
    init_board_files(board)
    write_board_readme(board)
    stage_runtime(board)
    names: list[str] = []
    tools = list(connect or [])
    if prompter is not None and not tools:
        prompter.say(_found_sentence(found))
        tools = _ask_tools(prompter)
    for tool_key in tools:
        names.append(_connect_named(board, tool_key, yes=yes, model=model, prompter=prompter))
    from agentcoop.install import prune_out_of_scope_project_files

    prune_out_of_scope_project_files(board)
    project = None
    if prompter is not None and prompter.yes("Do you want to add a project now?", default=False):
        project = add_project_flow(prompter=prompter, yes=yes)
    return {"board": str(board), "found": found, "connected": names, "project": project}


def connect_one(
    tool_key: str,
    *,
    agent_name: str | None = None,
    yes: bool = False,
    model: str | None = None,
    host: str | None = None,
    api: str | None = None,
) -> str:
    """Connect one tool and return the agent name."""
    board = board_root()
    init_board_files(board)
    write_board_readme(board)
    stage_runtime(board)
    return _connect_named(
        board,
        tool_key,
        yes=yes,
        agent_name=agent_name,
        model=model,
        host=host,
        api=api,
        prompter=None,
    )


def _connect_named(
    board: Path,
    tool_key: str,
    *,
    yes: bool,
    agent_name: str | None = None,
    model: str | None = None,
    host: str | None = None,
    api: str | None = None,
    prompter: Prompter | None = None,
) -> str:
    tool = find_tool(tool_key)
    if tool is None:
        raise CoopError(
            "Unknown agent " + tool_key + ". You can use: " + tool_choices_text()
        )
    tool_key = tool.key
    runner = None
    display = None
    if tool_key in ("ollama", "lmstudio", "runner"):
        if prompter is not None and not model:
            model = prompter.line("Which local model should be a helper?", "llava")
        if not model:
            example = "ollama_llava" if tool_key != "lmstudio" else "lmstudio_model"
            raise CoopError(
                tool.display
                + " needs a model name. Example: agentcoop connect "
                + tool_key
                + " --model llava --as "
                + example
            )
        prefix = "lmstudio" if tool_key == "lmstudio" or api == "openai" else "ollama"
        agent_name = agent_name or sanitize_agent_name(prefix, model.split(":")[0])
        chosen_api = api or ("openai" if tool_key == "lmstudio" else "ollama")
        chosen_host = host or ("http://localhost:1234" if chosen_api == "openai" else "http://localhost:11434")
        runner = {"api": chosen_api, "host": chosen_host, "model": model}
        display = ("LM Studio" if chosen_api == "openai" else "Ollama") + " (" + model + ")"
        if "llava" not in model.lower() and "vision" not in model.lower():
            pass
        vision_hint = (
            "To use a vision model with Ollama, run: ollama pull llava"
        )
        if prompter is not None and "llava" not in model.lower():
            prompter.say(vision_hint)
    return connect_agent(
        board,
        tool_key,
        agent_name,
        yes=yes,
        display=display,
        runner=runner,
    )


def add_project_flow(
    *,
    path: Path | None = None,
    name: str | None = None,
    driver: str | None = None,
    helpers: dict[str, str] | None = None,
    lock_helpers: bool = False,
    gitignore: bool = True,
    yes: bool = False,
    prompter: Prompter | None = None,
) -> str:
    """Add a project and install the driver's project files."""
    board = board_root()
    init_board_files(board)
    folder = (path or _default_folder()).expanduser().resolve()
    if prompter is not None and path is None:
        folder = Path(prompter.line("Project folder", str(folder))).expanduser().resolve()
    chosen = name or suggest_name(folder)
    if prompter is not None and name is None:
        chosen = prompter.line("Project name", chosen)
    validate_project_name(chosen)
    agents = load_agents(board)
    if prompter is not None and driver is None:
        driver = _ask_driver(prompter, agents, folder)
    if not driver:
        raise CoopError("Pick a driver. Example: agentcoop project add . --driver cursor --yes")
    helper_map = dict(helpers or {})
    if prompter is not None and helpers is None:
        helper_map = _ask_helpers(prompter, agents, driver)
    preview_project_files(board, driver, folder)
    add_project(board, chosen, folder, driver, helper_map)
    try:
        install_project_files(board, chosen, driver, yes=yes, gitignore=gitignore)
    except Exception as exc:
        rollback_project_add(board, chosen)
        raise CoopError(
            "Could not finish adding " + chosen + ". Nothing was changed. " + str(exc)
        ) from exc
    lines = [
        "Project " + chosen + " is ready.",
        "Folder: " + str(folder),
        "Driver: " + driver,
    ]
    for agent, note in helper_map.items():
        lines.append("Helper: " + agent + " (" + note + ")")
        if lock_helpers:
            tool_key = (agents["agents"].get(agent) or {}).get("tool", "")
            lines.append(lock_notice(tool_key))
    lines.append('Paste this into the driver: "Say hello to your helpers on the Agent Co-op board."')
    if prompter is not None:
        prompter.say("\n".join(lines))
    return "\n".join(lines)


def change_driver(project_name: str, agent: str, *, yes: bool) -> str:
    """Move the driver role and close waiting requests."""
    board = board_root()
    projects = load_projects(board)
    project = projects["projects"].get(project_name)
    if project is None:
        raise CoopError("There is no project named " + project_name + ".")
    previous = project["driver"]
    if previous == agent:
        return agent + " is already the driver for " + project_name + "."
    pending = waiting(board, project_name, previous)
    if pending and not yes:
        raise CoopError(
            previous
            + " still has "
            + str(len(pending))
            + " request(s) waiting. Run again with --yes to close them and change the driver."
        )
    for item in pending:
        send(
            board,
            project=project_name,
            sender=previous,
            recipient=item.recipient,
            subject="Driver changed",
            body="Driver changed to " + agent + ", this request is closed.",
            type="status",
            re=item.id,
        )
    kept = []
    for entry in list(project.get("installed") or []):
        if entry.get("agent") == previous:
            undo_entry(entry)
        else:
            kept.append(entry)
    project["installed"] = kept
    save_projects(board, projects)
    set_driver(board, project_name, agent)
    install_project_files(board, project_name, agent, yes=yes, gitignore=False)
    return "Driver for " + project_name + " is now " + agent + ". It was " + previous + "."


def helper_add(project_name: str, agent: str, note: str) -> str:
    """Add a helper and explain the read-only rule."""
    board = board_root()
    add_helper(board, project_name, agent, note)
    agents = load_agents(board)
    tool_key = (agents["agents"].get(agent) or {}).get("tool", "")
    return agent + " is a helper on " + project_name + ". " + lock_notice(tool_key)


def helper_remove(project_name: str, agent: str) -> str:
    """Remove a helper."""
    board = board_root()
    projects = load_projects(board)
    project = projects["projects"].get(project_name)
    if project is None:
        raise CoopError("There is no project named " + project_name + ".")
    kept = []
    for entry in list(project.get("installed") or []):
        if entry.get("agent") == agent:
            undo_entry(entry)
        else:
            kept.append(entry)
    project["installed"] = kept
    save_projects(board, projects)
    remove_helper(board, project_name, agent)
    return agent + " is no longer a helper on " + project_name + "."


def project_repair(project_name: str) -> str:
    """Reinstall missing driver project files."""
    board = board_root()
    written = repair_project_files(board, project_name)
    if not written:
        return "Nothing to repair."
    return "Repaired:\n" + "\n".join("  " + path for path in written)


def project_remove(project_name: str, *, yes: bool) -> str:
    """Remove a project after confirmation."""
    if not yes:
        raise CoopError(
            "This will remove "
            + project_name
            + " from the board and delete the files Agent Co-op installed in the project. "
            "Run again with --yes."
        )
    board = board_root()
    remove_project_files(board, project_name)
    return "Removed " + project_name + ". A copy of its board folder is under the removed folder."


def format_agents(board: Path | None = None) -> str:
    """List connected agents and their roles."""
    root = board or board_root()
    agents = load_agents(root)
    projects = load_projects(root)
    lines = []
    for name, record in (agents.get("agents") or {}).items():
        roles = []
        for project_name, project in (projects.get("projects") or {}).items():
            if project.get("driver") == name:
                roles.append(project_name + " driver")
            elif name in (project.get("helpers") or {}):
                roles.append(project_name + " helper")
        role_text = ", ".join(roles) if roles else "no project yet"
        lines.append(record.get("display", name) + " (" + name + ", " + record.get("tool", "") + "): " + role_text)
    if not lines:
        return "No agents are connected yet. Run: agentcoop setup"
    return "\n".join(lines)


def format_projects(board: Path | None = None, name: str | None = None) -> str:
    """Describe one project or all of them."""
    root = board or board_root()
    projects = load_projects(root).get("projects") or {}
    chosen = {name: projects[name]} if name else projects
    if name and name not in projects:
        raise CoopError("There is no project named " + name + ". Run: agentcoop project list")
    if not chosen:
        return "No projects yet. Run: agentcoop project add"
    lines = []
    for project_name, project in chosen.items():
        lines.append(project_name)
        lines.append("  Folder: " + project.get("path", ""))
        lines.append("  Driver: " + project.get("driver", ""))
        helpers = project.get("helpers") or {}
        if not helpers:
            lines.append("  Helpers: (none)")
        for agent, note in helpers.items():
            lines.append("  Helper: " + agent + " (" + note + ")")
        installed = project.get("installed") or []
        if installed:
            lines.append("  Installed files:")
            for entry in installed:
                lines.append("    " + entry.get("path", ""))
    return "\n".join(lines)


def lock_notice(tool_key: str) -> str:
    """Explain that a real read-only lock is not confirmed for this tool."""
    try:
        tool = get_tool(tool_key)
    except KeyError:
        return "A read-only lock is not available for this agent. The skill tells it not to edit project files."
    if tool.read_only_lock == "advisory":
        return (
            tool.display
            + ": the read-only rule is advisory for this tool. The skill tells it not to edit project files."
        )
    return (
        tool.display
        + ": a read-only lock is not available yet (the exact setting is still a TODO). "
        + "The skill tells it not to edit project files."
    )


def suggest_name(path: Path) -> str:
    """Suggest a project name from a folder name."""
    raw = path.name.lower().replace("_", "-")
    raw = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")
    if not raw or not raw[0].isalnum():
        raw = "project"
    return raw[:48]


def show_board() -> str:
    """Open the board folder in the system file manager."""
    board = board_root()
    board.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(board)], check=False)
        elif os.name == "nt":
            os.startfile(str(board))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(board)], check=False)
    except OSError as exc:
        raise CoopError("Could not open the board folder: " + str(board)) from exc
    return "Opened " + str(board)


def _default_folder() -> Path:
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
    return cwd


def _found_sentence(found: list[dict]) -> str:
    if not found:
        return (
            "Found no agents automatically. Connect one by name. "
            "Example: agentcoop connect claude-code"
        )
    parts = []
    for item in found:
        reason = item.get("reason") or ""
        if reason:
            parts.append(item["display"] + " (" + reason + ")")
        else:
            parts.append(item["display"])
    return "Found: " + ", ".join(parts)


def _ask_tools(prompter: Prompter) -> list[str]:
    tools = [tool for tool in supported_tools() if tool.key not in ("runner",)]
    for index, tool in enumerate(tools, start=1):
        prompter.say(str(index) + ". " + tool.display + " (" + tool.key + ")")
    raw = prompter.line("Which agents should be connected? Enter numbers, or leave blank", "")
    if not raw:
        return []
    chosen = []
    for part in raw.replace(",", " ").split():
        if not part.isdigit() or int(part) < 1 or int(part) > len(tools):
            raise CoopError("Choose numbers from the list, or run agentcoop connect TOOL.")
        chosen.append(tools[int(part) - 1].key)
    return chosen


def _ask_driver(prompter: Prompter, agents: dict, folder: Path) -> str:
    options = []
    for name, record in (agents.get("agents") or {}).items():
        if tool_can_drive(record.get("tool", "")):
            options.append(name)
    if not options:
        raise CoopError("Connect an agent that can edit files first. Example: agentcoop connect cursor")
    for index, name in enumerate(options, start=1):
        prompter.say(str(index) + ". " + name)
    default = options[0]
    env_agent = os.environ.get("AGENTCOOP_AGENT")
    if env_agent in options:
        default = env_agent
    if (folder / ".cursor").exists() and "cursor" in options:
        default = "cursor"
    raw = prompter.line("Which agent writes the files in this project?", default)
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    if raw not in options:
        raise CoopError(raw + " cannot be the driver. Pick one of: " + ", ".join(options))
    return raw


def _ask_helpers(prompter: Prompter, agents: dict, driver: str) -> dict[str, str]:
    options = [name for name in (agents.get("agents") or {}) if name != driver]
    if not options:
        return {}
    for index, name in enumerate(options, start=1):
        record = agents["agents"][name]
        try:
            note = get_tool(record.get("tool", "")).helper_note
        except KeyError:
            note = "helps the driver"
        prompter.say(str(index) + ". " + name + " (suggested note: " + note + ")")
    raw = prompter.line("Helpers to add? Enter numbers, or leave blank", "")
    chosen: dict[str, str] = {}
    if not raw:
        return chosen
    for part in raw.replace(",", " ").split():
        if not part.isdigit() or int(part) < 1 or int(part) > len(options):
            raise CoopError("Choose helper numbers from the list.")
        name = options[int(part) - 1]
        try:
            suggestion = get_tool(agents["agents"][name].get("tool", "")).helper_note
        except KeyError:
            suggestion = "helps the driver"
        note = prompter.line("Note for " + name, suggestion)
        chosen[name] = note
    return chosen
