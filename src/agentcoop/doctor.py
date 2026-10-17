"""Health checks with a plain next step for each problem."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentcoop import __version__
from agentcoop.core import load_message, log_messages, new_messages, protocol_violation
from agentcoop.detect import probe_json
from agentcoop.install import file_sha256
from agentcoop.paths import inbox_msgs, is_same_or_inside
from agentcoop.projects import board_is_ready, integrity_ok, load_agents, load_config, load_projects, role_of
from agentcoop.tools import get_tool, tool_can_drive

INBOX_LIMIT = 500


@dataclass
class DoctorItem:
    """One check."""

    ok: bool
    text: str


def run_doctor(board: Path, project: str | None = None) -> list[DoctorItem]:
    """Run health checks. Pass a project name to include the hello test."""
    items: list[DoctorItem] = []
    if not board_is_ready(board):
        return [DoctorItem(False, "Agent Co-op is not set up yet. Run: agentcoop setup")]
    if not os_access_write(board):
        items.append(DoctorItem(False, "The board folder is not writable: " + str(board)))
    else:
        items.append(DoctorItem(True, "Board folder is ready."))
    items.append(_bin_check(board))
    if integrity_ok(board):
        items.append(DoctorItem(True, "projects.json matches the saved check."))
    else:
        items.append(
            DoctorItem(
                False,
                "projects.json was changed outside agentcoop. Roles may not be what you set. "
                "Run: agentcoop doctor",
            )
        )
    agents = load_agents(board)
    projects = load_projects(board)
    config = load_config(board)
    for name, record in (agents.get("agents") or {}).items():
        items.extend(_agent_checks(name, record))
        if record.get("tool") == "runner":
            items.append(_runner_check(record))
    items.extend(_project_checks(projects, agents, config))
    items.extend(_duplicate_instruction_checks(agents, projects))
    items.extend(_message_checks(board, projects))
    if project:
        items.append(_hello_check(board, projects, project))
    return items


def os_access_write(path: Path) -> bool:
    """Return True when a folder can be created inside path."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor-write"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _bin_check(board: Path) -> DoctorItem:
    version_file = board / "bin" / "VERSION"
    if not version_file.is_file():
        return DoctorItem(False, "The agentcoop command is not on the board yet. Run: agentcoop setup")
    current = version_file.read_text(encoding="utf-8").strip()
    if current != __version__:
        return DoctorItem(
            False,
            "The agentcoop command on the board is version "
            + current
            + ". This copy is "
            + __version__
            + ". Run: agentcoop setup",
        )
    return DoctorItem(True, "The agentcoop command on the board is current.")


def _agent_checks(name: str, record: dict) -> list[DoctorItem]:
    items = []
    display = record.get("display", name)
    installed = record.get("installed") or []
    if not installed and record.get("tool") not in ("runner",):
        tool_key = record.get("tool", "")
        try:
            tool = get_tool(tool_key)
        except KeyError:
            tool = None
        if tool is not None and any(item["scope"] == "user" for item in tool.install_paths):
            items.append(
                DoctorItem(
                    False,
                    display + " is registered but its skill is missing. Run: agentcoop connect " + tool_key,
                )
            )
        else:
            items.append(DoctorItem(True, display + ": connected."))
    for entry in installed:
        path = Path(entry.get("path", ""))
        if not path.is_file():
            items.append(
                DoctorItem(
                    False,
                    display
                    + " is missing "
                    + str(path)
                    + ". Run: agentcoop connect "
                    + record.get("tool", name),
                )
            )
            continue
        if file_sha256(path) != entry.get("sha256"):
            items.append(
                DoctorItem(
                    False,
                    str(path) + " was edited by hand. Run: agentcoop connect " + record.get("tool", name) + " --yes",
                )
            )
        else:
            items.append(DoctorItem(True, display + ": " + path.name + " is installed."))
    tool_key = record.get("tool", "")
    try:
        tool = get_tool(tool_key)
    except KeyError:
        tool = None
    if tool is not None and tool.hooks == "supported":
        hooked = any(entry.get("kind") == "hook" for entry in installed)
        if not hooked:
            items.append(
                DoctorItem(
                    False,
                    display + " hooks are not installed. Run: agentcoop connect " + tool.key,
                )
            )
    return items


def _runner_check(record: dict) -> DoctorItem:
    runner = record.get("runner") or {}
    host = str(runner.get("host") or "http://localhost:11434")
    model = str(runner.get("model") or "")
    api = runner.get("api") or "ollama"
    display = record.get("display", model or "local model")
    if api == "openai":
        data = probe_json(host.rstrip("/") + "/v1/models")
        if data is None:
            return DoctorItem(False, display + " is not reachable at " + host + ". Start LM Studio's server.")
        ids = [str(item.get("id")) for item in (data.get("data") or []) if isinstance(item, dict)]
        if model and ids and model not in ids:
            return DoctorItem(False, "The model " + model + " is not loaded in LM Studio.")
        return DoctorItem(True, display + ": server is reachable.")
    data = probe_json(host.rstrip("/") + "/api/tags")
    if data is None:
        return DoctorItem(False, display + " is not running. Start Ollama, then run: agentcoop doctor")
    names = [str(item.get("name")) for item in (data.get("models") or []) if isinstance(item, dict)]
    if model and names and not any(model == name or name.startswith(model + ":") or model.startswith(name) for name in names):
        short = model.split(":")[0]
        return DoctorItem(False, "The model " + model + " is not installed. Run: ollama pull " + short)
    return DoctorItem(True, display + ": server is reachable.")


def _project_checks(projects: dict, agents: dict, config: dict) -> list[DoctorItem]:
    items = []
    known = agents.get("agents") or {}
    roots = [Path(item) for item in (config.get("allowed_roots") or []) if isinstance(item, str)]
    seen: list[tuple[str, Path]] = []
    for name, project in (projects.get("projects") or {}).items():
        path = Path(project.get("path", ""))
        if not path.exists():
            items.append(DoctorItem(False, "The " + name + " folder is missing: " + str(path)))
        else:
            items.append(DoctorItem(True, name + ": folder exists."))
        nested = False
        for other_name, other_path in seen:
            if is_same_or_inside(path, other_path) or is_same_or_inside(other_path, path):
                items.append(DoctorItem(False, name + " sits inside " + other_name + ". Projects cannot be nested."))
                nested = True
        if not nested:
            seen.append((name, path))
        driver = project.get("driver", "")
        if driver not in known:
            items.append(DoctorItem(False, driver + " is not connected. Run: agentcoop connect " + driver))
        else:
            tool_key = known[driver].get("tool", "")
            if not tool_can_drive(tool_key):
                items.append(DoctorItem(False, driver + " cannot be the driver of " + name + "."))
            else:
                items.append(DoctorItem(True, known[driver].get("display", driver) + ": connected as the driver of " + name + "."))
        for helper in (project.get("helpers") or {}):
            if helper not in known:
                items.append(DoctorItem(False, helper + " is not connected. Run: agentcoop agents"))
            else:
                items.append(DoctorItem(True, known[helper].get("display", helper) + ": connected."))
        if path.exists() and not any(is_same_or_inside(path, root) for root in roots):
            items.append(
                DoctorItem(
                    False,
                    "The "
                    + name
                    + " folder is not on the allowed list, so attachments from it will be blocked.",
                )
            )
        items.extend(_driver_files_check(name, project, known))
    return items


def _duplicate_instruction_checks(agents: dict, projects: dict) -> list[DoctorItem]:
    """Report when an agent has both a user and a project copy of the same kind."""
    items: list[DoctorItem] = []
    for agent_name, record in (agents.get("agents") or {}).items():
        user_by_kind: dict[str, str] = {}
        for entry in record.get("installed") or []:
            kind = entry.get("kind")
            if kind in ("skill", "hook") and entry.get("path"):
                user_by_kind[kind] = entry["path"]
        for project_name, project in (projects.get("projects") or {}).items():
            project_by_kind: dict[str, str] = {}
            for entry in project.get("installed") or []:
                if entry.get("agent") != agent_name:
                    continue
                kind = entry.get("kind")
                if kind in ("skill", "hook") and entry.get("path"):
                    project_by_kind[kind] = entry["path"]
            for kind in ("skill", "hook"):
                if kind in user_by_kind and kind in project_by_kind:
                    items.append(
                        DoctorItem(
                            False,
                            agent_name
                            + " has two copies of the Agent Co-op instructions: "
                            + user_by_kind[kind]
                            + " and "
                            + project_by_kind[kind]
                            + ". Run: agentcoop project repair "
                            + project_name,
                        )
                    )
    return items


def _driver_files_check(name: str, project: dict, known: dict) -> list[DoctorItem]:
    """Report when the driver's project files are missing or not in the manifest."""
    driver = project.get("driver", "")
    if driver not in known:
        return []
    tool_key = known[driver].get("tool", "")
    if tool_key == "runner":
        return []
    try:
        tool = get_tool(tool_key)
    except KeyError:
        return []
    root = Path(project.get("path", ""))
    installed = {entry.get("path") for entry in (project.get("installed") or [])}
    for item in tool.install_paths:
        if item["scope"] != "project":
            continue
        if item["kind"] == "hook" and tool.hooks != "supported":
            continue
        path = root / item["path"]
        if not path.is_file() or str(path) not in installed:
            return [
                DoctorItem(
                    False,
                    name
                    + ": "
                    + driver
                    + " is missing its project files. Run: agentcoop project repair "
                    + name,
                )
            ]
    return []


def _message_checks(board: Path, projects: dict) -> list[DoctorItem]:
    items = []
    for name, project in (projects.get("projects") or {}).items():
        members = [project.get("driver", "")] + list((project.get("helpers") or {}).keys())
        for agent in members:
            if not agent:
                continue
            _messages, issues = new_messages(board, name, agent)
            for issue in issues:
                items.append(
                    DoctorItem(
                        False,
                        "A message file could not be read: " + str(issue.path) + ". Run: agentcoop doctor",
                    )
                )
            folder = inbox_msgs(board, name, agent)
            count = len([path for path in folder.glob("*.json") if not path.name.startswith(".")]) if folder.is_dir() else 0
            if count > INBOX_LIMIT:
                items.append(
                    DoctorItem(
                        False,
                        agent
                        + " has "
                        + str(count)
                        + " messages in "
                        + name
                        + ". Run: agentcoop archive --as "
                        + agent
                        + " --project "
                        + name,
                    )
                )
            paths = folder.glob("*.json") if folder.is_dir() else []
            for path in paths:
                if path.name.startswith("."):
                    continue
                message = load_message(path)
                if message is None:
                    continue
                if protocol_violation(project, message, board, name):
                    items.append(
                        DoctorItem(
                            False,
                            "Out of protocol message "
                            + message.id
                            + " in "
                            + agent
                            + "'s inbox. Ignore it.",
                        )
                    )
    return items


def _hello_check(board: Path, projects: dict, project_name: str) -> DoctorItem:
    project = (projects.get("projects") or {}).get(project_name)
    if project is None:
        return DoctorItem(False, "There is no project named " + project_name + ". Run: agentcoop project list")
    driver = project.get("driver", "")
    messages = log_messages(board, project_name)
    hellos = [
        item
        for item in messages
        if item.sender == driver and "hello" in (item.subject + " " + item.body).lower()
    ]
    if not hellos:
        return DoctorItem(
            False,
            'No hello test yet for '
            + project_name
            + '. Paste this into the driver: "Say hello to your helpers on the Agent Co-op board."',
        )
    replies = {item.re for item in messages if item.re}
    received = []
    replied = []
    for item in hellos:
        if item.recipient not in received:
            received.append(item.recipient)
        if item.id in replies and item.recipient not in replied:
            replied.append(item.recipient)
    waiting_on = [name for name in received if name not in replied]
    text = project_name + " hello test: sent to " + ", ".join(received) + "."
    if replied:
        text += " Replied: " + ", ".join(replied) + "."
    if waiting_on:
        text += " Still waiting: " + ", ".join(waiting_on) + "."
    return DoctorItem(not waiting_on, text)


def format_doctor(items: list[DoctorItem]) -> str:
    """Render doctor results as plain lines."""
    return "\n".join(("OK: " if item.ok else "Fix: ") + item.text for item in items)
