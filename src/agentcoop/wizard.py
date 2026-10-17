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
    mutate_projects,
    remove_helper,
    set_driver,
    tighten_board_modes,
    validate_project_name,
)
from agentcoop.tools import (
    find_tool,
    get_tool,
    sanitize_agent_name,
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
        suffix = " [" + default + "]" if default is not None else ""
        try:
            value = self._input(prompt + suffix + ": ")
        except EOFError as exc:
            raise CoopError(
                "This step needs an answer. Run the command with flags instead of the interactive form."
            ) from exc
        value = value.strip()
        if value:
            return value
        return default if default is not None else ""

    def yes(self, prompt: str, default: bool = False) -> bool:
        """Ask a yes or no question. The prompt should already include (Y/n) or (y/N)."""
        try:
            value = self._input(prompt)
        except EOFError as exc:
            raise CoopError(
                "This step needs an answer. Run the command with flags instead of the interactive form."
            ) from exc
        value = value.strip()
        if not value:
            return default
        return value.lower() in ("y", "yes")

    def ask(self, prompt: str, parse, *, default: str | None = None) -> object:
        """Ask once, then once more after a one-line explanation. Cancel on a second failure."""
        last_error = "That answer is not valid."
        for attempt in range(2):
            raw = self.line(prompt, default)
            try:
                return parse(raw)
            except ValueError as exc:
                last_error = str(exc) or last_error
                self.say(last_error)
        raise CoopError("Cancelled.", code=1)


def setup_board(
    *,
    connect: list[str] | None = None,
    yes: bool = False,
    detect_only: bool = False,
    as_json: bool = False,
    model: str | None = None,
    prompter: Prompter | None = None,
    add_to_path: bool | None = None,
    schedule: bool | None = None,
    every: int = 5,
) -> dict:
    """Create the board and connect agents. Safe to run again."""
    found = detect_installed()
    if detect_only:
        return {"found": found}
    board = board_root()
    init_board_files(board)
    write_board_readme(board)
    stage_runtime(board)
    tighten_board_modes(board)
    from agentcoop.install import prune_out_of_scope_project_files, refresh_installed_files

    left_alone = refresh_installed_files(board)
    names: list[str] = []
    tools = list(connect or [])
    path_choice = bool(add_to_path) if add_to_path is not None else False
    if prompter is not None and not tools:
        agents_before = load_agents(board)
        tools = _ask_join_tools(prompter, found, agents_before)
        for tool_key in tools:
            chosen_model = model
            tool = find_tool(tool_key)
            if tool is not None and _tool_can_schedule(tool) and not chosen_model:
                answer = prompter.line(
                    "Which model should "
                    + tool.display
                    + " use when it runs in the background? Press Enter for its default: ",
                    "",
                )
                chosen_model = answer or None
            names.append(_connect_named(board, tool_key, yes=yes, model=chosen_model, prompter=None))
        for name in _ask_local_models(prompter, found):
            if name:
                names.append(name)
        path_choice = prompter.yes(
            "Add agentcoop to your PATH so you can type agentcoop in any terminal? (Y/n) ",
            default=True,
        )
        apply_path_change(board, path_choice)
        if prompter.yes("Check the board in the background every 5 minutes? (Y/n) ", default=True):
            _turn_on_schedule(board, every)
        if prompter.yes("Do you want to add a project now? (y/N) ", default=False):
            project = add_project_flow(prompter=prompter, yes=yes)
        else:
            project = None
    else:
        for tool_key in tools:
            names.append(_connect_named(board, tool_key, yes=yes, model=model, prompter=prompter))
        if add_to_path:
            apply_path_change(board, True)
        if schedule:
            _turn_on_schedule(board, every)
        project = None
    prune_out_of_scope_project_files(board)
    return {
        "board": str(board),
        "found": found,
        "connected": names,
        "project": project,
        "add_to_path": path_choice,
        "left_alone": left_alone,
    }


def _tool_can_schedule(tool: object) -> bool:
    """Return True when a tool has a background helper or driver command."""
    spec = getattr(tool, "headless", "none")
    if not isinstance(spec, dict):
        return False
    return isinstance(spec.get("helper"), list) or isinstance(spec.get("driver"), list)


def _turn_on_schedule(board: Path, minutes: int) -> None:
    from agentcoop.rounds import start_schedule

    start_schedule(board, minutes)


def apply_path_change(board: Path, enabled: bool) -> None:
    """Change PATH when the person said yes."""
    from agentcoop.pathenv import apply_path_change as _apply

    _apply(board, enabled)


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
        model=None if tool_key in ("ollama", "lmstudio", "runner") else model,
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
        folder = Path(
            prompter.line("Project folder", str(folder))
        ).expanduser().resolve()
    chosen = name or suggest_name(folder)
    if prompter is not None and name is None:
        chosen = prompter.line("Project name", chosen)
    validate_project_name(chosen)
    agents = load_agents(board)
    if prompter is not None and driver is None:
        driver = _ask_driver(prompter, agents, folder)
    if not driver:
        raise CoopError(
            "Run this in a terminal to answer questions, or pass the flags. "
            "Example: agentcoop project add . --driver cursor --yes",
            code=2,
        )
    helper_map = dict(helpers or {})
    if prompter is not None and helpers is None:
        helper_map = _ask_helpers(prompter, agents, driver)
    if prompter is not None:
        gitignore = prompter.yes(
            "Add the installed files to .gitignore? (Y/n) ",
            default=True,
        )
        lock_helpers = prompter.yes(
            "Lock helpers to read only where the tool supports it? (y/N) ",
            default=False,
        )
    preview_project_files(board, driver, folder)
    for helper in helper_map:
        record = (agents["agents"].get(helper) or {})
        tool_key = record.get("tool") or ""
        if tool_key and tool_key != "runner" and get_tool(tool_key).project_files_for == "members":
            preview_project_files(board, helper, folder)
    add_project(board, chosen, folder, driver, helper_map)
    try:
        install_project_files(board, chosen, driver, yes=yes, gitignore=gitignore)
        for helper in helper_map:
            record = (agents["agents"].get(helper) or {})
            tool_key = record.get("tool") or ""
            if tool_key and tool_key != "runner" and get_tool(tool_key).project_files_for == "members":
                install_project_files(board, chosen, helper, yes=yes, gitignore=False)
    except Exception as exc:
        notes = rollback_project_add(board, chosen)
        extra = (" " + " ".join(notes)) if notes else ""
        raise CoopError(
            "Could not finish adding " + chosen + ". Nothing was changed. " + str(exc) + extra
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
    text = "\n".join(lines)
    if prompter is not None:
        prompter.say(text)
        return ""
    return text


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
    notes = []

    def change(data: dict) -> None:
        current = data["projects"].get(project_name)
        if current is None:
            raise CoopError("There is no project named " + project_name + ".")
        kept = []
        for entry in list(current.get("installed") or []):
            if entry.get("agent") == previous:
                note = undo_entry(entry)
                if note:
                    notes.append(note)
            else:
                kept.append(entry)
        current["installed"] = kept

    mutate_projects(board, change)
    set_driver(board, project_name, agent)
    install_project_files(board, project_name, agent, yes=yes, gitignore=False)
    text = "Driver for " + project_name + " is now " + agent + ". It was " + previous + "."
    if notes:
        text += "\n" + "\n".join(notes)
    return text


def helper_add(project_name: str, agent: str, note: str) -> str:
    """Add a helper and explain the read-only rule."""
    board = board_root()
    add_helper(board, project_name, agent, note)
    agents = load_agents(board)
    tool_key = (agents["agents"].get(agent) or {}).get("tool", "")
    if tool_key and tool_key != "runner" and get_tool(tool_key).project_files_for == "members":
        install_project_files(board, project_name, agent, yes=False, gitignore=False)
    if tool_key == "runner":
        return (
            agent
            + " is a helper on "
            + project_name
            + ". "
            + agent
            + " is a local model runner. It only answers questions and cannot edit files."
        )
    return agent + " is a helper on " + project_name + ". " + lock_notice(tool_key)


def helper_remove(project_name: str, agent: str) -> str:
    """Remove a helper."""
    board = board_root()
    notes = []

    def change(data: dict) -> None:
        current = data["projects"].get(project_name)
        if current is None:
            raise CoopError("There is no project named " + project_name + ".")
        kept = []
        for entry in list(current.get("installed") or []):
            if entry.get("agent") == agent:
                note = undo_entry(entry)
                if note:
                    notes.append(note)
            else:
                kept.append(entry)
        current["installed"] = kept

    mutate_projects(board, change)
    remove_helper(board, project_name, agent)
    text = agent + " is no longer a helper on " + project_name + "."
    if notes:
        text += "\n" + "\n".join(notes)
    return text


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
    notes = remove_project_files(board, project_name)
    text = "Removed " + project_name + ". A copy of its board folder is under the removed folder."
    if notes:
        text += "\n" + "\n".join(notes)
    return text


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


def _connected_tool_keys(agents: dict) -> set[str]:
    keys = set()
    for record in (agents.get("agents") or {}).values():
        tool = record.get("tool") or ""
        if tool:
            keys.add(tool)
        if tool == "runner":
            api = ((record.get("runner") or {}).get("api") or "")
            if api == "openai":
                keys.add("lmstudio")
            else:
                keys.add("ollama")
    return keys


def _detected_join_items(found: list[dict]) -> list[dict]:
    """Detected tools a person can join by number, excluding local model servers."""
    items = []
    for item in found:
        if "models" in item:
            continue
        if item.get("tool") in ("ollama", "lmstudio", "runner"):
            continue
        items.append(item)
    return items


def _ask_join_tools(prompter: Prompter, found: list[dict], agents: dict) -> list[str]:
    """Ask which detected agents should join. Accepts numbers or tool names."""
    items = _detected_join_items(found)
    connected = _connected_tool_keys(agents)
    if not items:
        prompter.say(_found_sentence(found))
    for index, item in enumerate(items, start=1):
        mark = " (already connected)" if item.get("tool") in connected else ""
        prompter.say(str(index) + ". " + item["display"] + " (" + item["tool"] + ")" + mark)

    def parse(raw: str) -> list[str]:
        if not raw.strip():
            return []
        chosen: list[str] = []
        for part in raw.replace(",", " ").split():
            if part.isdigit():
                number = int(part)
                if number < 1 or number > len(items):
                    raise ValueError(
                        "Choose a number from the list, or type a tool name."
                    )
                key = items[number - 1]["tool"]
                if key not in chosen:
                    chosen.append(key)
                continue
            tool = find_tool(part)
            if tool is None or tool.key in ("runner",):
                raise ValueError(
                    part + " is not a known agent. Example: cursor or claude-code"
                )
            if tool.key not in chosen:
                chosen.append(tool.key)
        return chosen

    return list(
        prompter.ask(
            "Which agents should join? Type numbers separated by commas, or press Enter to skip",
            parse,
            default="",
        )
    )


def _ask_local_models(prompter: Prompter, found: list[dict]) -> list[str]:
    """Ask which Ollama or LM Studio models should join as helpers."""
    board = board_root()
    names: list[str] = []
    for item in found:
        models = item.get("models")
        if models is None:
            continue
        tool_key = item.get("tool") or ""
        display = item.get("display") or tool_key
        if not models:
            if tool_key == "ollama":
                prompter.say(
                    display
                    + " has no models yet. Pull a vision model with: ollama pull llava"
                )
            else:
                prompter.say(
                    display
                    + " has no models yet. Load a model in LM Studio, then run setup again."
                )
            continue
        for index, model_name in enumerate(models, start=1):
            prompter.say(str(index) + ". " + model_name)

        def parse(raw: str, models=models) -> list[str]:
            if not raw.strip():
                return []
            chosen: list[str] = []
            for part in raw.replace(",", " ").split():
                if not part.isdigit() or int(part) < 1 or int(part) > len(models):
                    raise ValueError("Choose a number from the model list, or press Enter to skip.")
                name = models[int(part) - 1]
                if name not in chosen:
                    chosen.append(name)
            return chosen

        selected = list(
            prompter.ask(
                "Which "
                + display
                + " model should join as a helper? Type numbers, or press Enter to skip",
                parse,
                default="",
            )
        )
        for model_name in selected:
            names.append(
                _connect_named(
                    board,
                    tool_key,
                    yes=True,
                    model=model_name,
                    prompter=None,
                )
            )
    return names


def suggest_driver(agents: dict, folder: Path) -> str | None:
    """Pick the default driver from folder evidence, then Cursor, then the first driver."""
    options = []
    for name, record in (agents.get("agents") or {}).items():
        if tool_can_drive(record.get("tool", "")):
            options.append(name)
    if not options:
        return None
    evidence: list[str] = []
    if (folder / ".cursor").exists():
        evidence.append("cursor")
    if (folder / ".claude").exists():
        evidence.append("claude_code")
    if (folder / ".codex").exists() or (folder / "AGENTS.md").is_file():
        evidence.append("codex")
    if (folder / ".gemini").exists() or (folder / "GEMINI.md").is_file():
        evidence.append("gemini")
    for name in evidence:
        if name in options:
            return name
    if "cursor" in options:
        return "cursor"
    return options[0]


def _ask_driver(prompter: Prompter, agents: dict, folder: Path) -> str:
    options = []
    for name, record in (agents.get("agents") or {}).items():
        if tool_can_drive(record.get("tool", "")):
            options.append(name)
    if not options:
        raise CoopError(
            "Connect an agent that can edit files first. Example: agentcoop connect cursor"
        )
    for index, name in enumerate(options, start=1):
        prompter.say(str(index) + ". " + name)
    default = suggest_driver(agents, folder) or options[0]

    def parse(raw: str) -> str:
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        if raw in options:
            return raw
        raise ValueError(raw + " cannot be the driver. Pick one of: " + ", ".join(options))

    return str(
        prompter.ask(
            "Which agent writes the files in this project?",
            parse,
            default=default,
        )
    )


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

    def parse(raw: str) -> list[str]:
        if not raw.strip():
            return []
        chosen: list[str] = []
        for part in raw.replace(",", " ").split():
            if not part.isdigit() or int(part) < 1 or int(part) > len(options):
                raise ValueError("Choose helper numbers from the list.")
            name = options[int(part) - 1]
            if name not in chosen:
                chosen.append(name)
        return chosen

    selected = list(
        prompter.ask(
            "Helpers to add? Enter numbers, or leave blank",
            parse,
            default="",
        )
    )
    chosen: dict[str, str] = {}
    for name in selected:
        try:
            suggestion = get_tool(agents["agents"][name].get("tool", "")).helper_note
        except KeyError:
            suggestion = "helps the driver"
        note = prompter.line("Note for " + name, suggestion)
        chosen[name] = note
    return chosen


def format_setup_summary(board: Path, newly_connected: list[str]) -> str:
    """List every connected agent, marking ones connected in this run."""
    agents = load_agents(board).get("agents") or {}
    if not agents:
        return "No agents connected yet. Example: agentcoop connect cursor"
    lines = []
    new_names = set(newly_connected)
    for name, record in agents.items():
        label = record.get("display", name) + " (" + name + ")"
        if name in new_names:
            label += " (new)"
        lines.append(label)
    return "Connected:\n" + "\n".join("  " + line for line in lines)
