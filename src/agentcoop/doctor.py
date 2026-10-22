"""Health checks with a plain next step for each problem."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop import __version__
from agentcoop.core import file_id, load_message, log_messages, new_messages, protocol_violation
from agentcoop.errors import CoopError
from agentcoop.detect import probe_json
from agentcoop.install import file_sha256
from agentcoop.paths import home_dir, inbox_done, inbox_msgs, is_same_or_inside
from agentcoop.projects import (
    INTEGRITY_WARNING,
    board_is_ready,
    load_agents,
    load_config,
    read_projects_checked,
    role_of,
)
from agentcoop.tools import get_tool, tool_can_drive

INBOX_LIMIT = 500


@dataclass
class DoctorItem:
    """One check. level is ok, note, or fix."""

    level: str
    text: str

    @property
    def ok(self) -> bool:
        """True when this line is not a fix."""
        return self.level != "fix"


def run_doctor(
    board: Path,
    project: str | None = None,
    *,
    now: datetime | None = None,
) -> list[DoctorItem]:
    """Run health checks. Pass a project name to focus on that project."""
    items: list[DoctorItem] = []
    if not board_is_ready(board):
        return [DoctorItem("fix", "Agent Co-op is not set up yet. Run: agentcoop setup")]
    if not os_access_write(board):
        items.append(DoctorItem("fix", "The board folder is not writable: " + str(board)))
    else:
        items.append(DoctorItem("ok", "Board folder is ready."))
    items.append(_bin_check(board))
    executable = _command_executable(board)
    if executable is not None:
        items.append(executable)
    from agentcoop.pathenv import path_notes_for_doctor

    for note in path_notes_for_doctor(board):
        if note.startswith("Fix: "):
            items.append(DoctorItem("fix", note[len("Fix: ") :]))
        elif note.startswith("Note: "):
            items.append(DoctorItem("note", note[len("Note: ") :]))
        else:
            items.append(DoctorItem("note", note))
    loose = _loose_permissions(board)
    if loose is not None:
        items.append(loose)
    projects, intact = read_projects_checked(board)
    if intact:
        items.append(DoctorItem("ok", "projects.json matches the saved check."))
    else:
        items.append(DoctorItem("fix", INTEGRITY_WARNING))
    agents = load_agents(board)
    config = load_config(board)
    member_names: set[str] | None = None
    project_names: set[str] | None = None
    if project:
        record = (projects.get("projects") or {}).get(project)
        if record is None:
            items.append(
                DoctorItem("fix", "There is no project named " + project + ". Run: agentcoop project list")
            )
            return items
        project_names = {project}
        member_names = {record.get("driver", "")} | set((record.get("helpers") or {}).keys())
        member_names.discard("")
    for name, record in (agents.get("agents") or {}).items():
        if member_names is not None and name not in member_names:
            continue
        items.extend(_agent_checks(name, record))
        if record.get("tool") == "runner":
            items.append(_runner_check(record))
    items.extend(_project_checks(projects, agents, config, only=project_names))
    items.extend(_duplicate_instruction_checks(agents, projects, only=project_names))
    items.extend(_message_checks(board, projects, only=project_names))
    items.extend(_context_checks(board, projects, only=project_names, now=now))
    items.extend(_schedule_checks(board, agents, projects, now=now))
    items.extend(_progress_checks(board, projects, now=now))
    items.extend(_outbox_checks(board, projects, agents, only=project_names, now=now))
    items.extend(_signin_checks(board, agents, only=member_names))
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


def _loose_permissions(board: Path) -> DoctorItem | None:
    """Report once when another account on this computer can read the board."""
    if os.name == "nt":
        return None
    seen = 0
    for dirpath, _dirnames, filenames in os.walk(board):
        if seen >= 5000:
            break
        seen += 1
        if os.stat(dirpath).st_mode & 0o077:
            return DoctorItem("fix", "other accounts on this computer can read the board. Run: agentcoop setup",
            )
        for name in filenames:
            seen += 1
            if seen > 5000:
                break
            if os.stat(os.path.join(dirpath, name)).st_mode & 0o077:
                return DoctorItem("fix", "other accounts on this computer can read the board. Run: agentcoop setup",
                )
    return None


def _bin_check(board: Path) -> DoctorItem:
    version_file = board / "bin" / "VERSION"
    if not version_file.is_file():
        return DoctorItem("fix", "The agentcoop command is not on the board yet. Run: agentcoop setup")
    current = version_file.read_text(encoding="utf-8").strip()
    if current != __version__:
        return DoctorItem("fix", "The agentcoop command on the board is version "
            + current
            + ". This copy is "
            + __version__
            + ". Run: agentcoop setup",
        )
    return DoctorItem("ok", "The agentcoop command on the board is current.")


def _command_executable(board: Path) -> DoctorItem | None:
    """Report when the installed command cannot be executed."""
    if os.name == "nt":
        return None
    path = board / "bin" / "agentcoop"
    if not path.is_file():
        return None
    if os.access(path, os.X_OK):
        return DoctorItem("ok", "The agentcoop command can run.")
    return DoctorItem("fix", "the agentcoop command cannot run. Run: agentcoop setup")


def _foreign_install_path(tool: object, installed: list) -> Path | None:
    """Return a user file that exists and is not the copy Agent Co-op installed."""
    known = {entry.get("path"): entry.get("sha256") for entry in installed}
    for item in tool.install_paths:  # type: ignore[attr-defined]
        if item["scope"] != "user":
            continue
        path = home_dir() / item["path"]
        if not path.is_file():
            continue
        if known.get(str(path)) != file_sha256(path):
            return path
    return None


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
            foreign = _foreign_install_path(tool, installed)
            if foreign is not None:
                items.append(
                    DoctorItem("fix", str(foreign)
                        + " belongs to you, not Agent Co-op. Move it, or run: agentcoop connect "
                        + tool_key
                        + " --yes to replace it.",
                    )
                )
            else:
                items.append(
                    DoctorItem("fix", display + " is registered but its skill is missing. Run: agentcoop connect " + tool_key,
                    )
                )
        else:
            items.append(DoctorItem("ok", display + ": connected."))
    for entry in installed:
        path = Path(entry.get("path", ""))
        if not path.is_file():
            items.append(
                DoctorItem("fix", display
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
                    "note",
                    str(path)
                    + " was changed after it was installed. Agent Co-op will leave it alone.",
                )
            )
        else:
            items.append(DoctorItem("ok", display + ": " + path.name + " is installed."))
    tool_key = record.get("tool", "")
    try:
        tool = get_tool(tool_key)
    except KeyError:
        tool = None
    if tool is not None and tool.hooks == "supported":
        hooked = any(entry.get("kind") == "hook" for entry in installed)
        if not hooked:
            items.append(
                DoctorItem("fix", display + " hooks are not installed. Run: agentcoop connect " + tool.key,
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
            return DoctorItem("fix", display + " is not reachable at " + host + ". Start LM Studio's server.")
        ids = [str(item.get("id")) for item in (data.get("data") or []) if isinstance(item, dict)]
        if model and ids and model not in ids:
            return DoctorItem("fix", "The model " + model + " is not loaded in LM Studio.")
        return DoctorItem("ok", display + ": server is reachable.")
    data = probe_json(host.rstrip("/") + "/api/tags")
    if data is None:
        return DoctorItem("fix", display + " is not running. Start Ollama, then run: agentcoop doctor")
    names = [str(item.get("name")) for item in (data.get("models") or []) if isinstance(item, dict)]
    if model and names and not any(model == name or name.startswith(model + ":") or model.startswith(name) for name in names):
        short = model.split(":")[0]
        return DoctorItem("fix", "The model " + model + " is not installed. Run: ollama pull " + short)
    return DoctorItem("ok", display + ": server is reachable.")


def _project_checks(
    projects: dict,
    agents: dict,
    config: dict,
    *,
    only: set[str] | None = None,
) -> list[DoctorItem]:
    items = []
    known = agents.get("agents") or {}
    roots = [Path(item) for item in (config.get("allowed_roots") or []) if isinstance(item, str)]
    seen: list[tuple[str, Path]] = []
    for name, project in (projects.get("projects") or {}).items():
        if only is not None and name not in only:
            continue
        path = Path(project.get("path", ""))
        if not path.exists():
            items.append(DoctorItem("fix", "The " + name + " folder is missing: " + str(path)))
        else:
            items.append(DoctorItem("ok", name + ": folder exists."))
        nested = False
        for other_name, other_path in seen:
            if is_same_or_inside(path, other_path) or is_same_or_inside(other_path, path):
                items.append(DoctorItem("fix", name + " sits inside " + other_name + ". Projects cannot be nested."))
                nested = True
        if not nested:
            seen.append((name, path))
        driver = project.get("driver", "")
        if driver not in known:
            items.append(DoctorItem("fix", driver + " is not connected. Run: agentcoop connect " + driver))
        else:
            tool_key = known[driver].get("tool", "")
            if not tool_can_drive(tool_key):
                items.append(DoctorItem("fix", driver + " cannot be the driver of " + name + "."))
            else:
                items.append(DoctorItem("ok", known[driver].get("display", driver) + ": connected as the driver of " + name + "."))
        for helper in (project.get("helpers") or {}):
            if helper not in known:
                items.append(DoctorItem("fix", helper + " is not connected. Run: agentcoop agents"))
            else:
                items.append(DoctorItem("ok", known[helper].get("display", helper) + ": connected."))
        if path.exists() and not any(is_same_or_inside(path, root) for root in roots):
            items.append(
                DoctorItem("fix", "The "
                    + name
                    + " folder is not on the allowed list, so attachments from it will be blocked.",
                )
            )
        items.extend(_driver_files_check(name, project, known))
        if path.is_dir():
            for backup in sorted(path.glob("*.agentcoop-backup-*")):
                items.append(
                    DoctorItem("fix", str(backup) + " is an old Agent Co-op backup. You can delete it.",
                    )
                )
    return items


def _duplicate_instruction_checks(
    agents: dict,
    projects: dict,
    *,
    only: set[str] | None = None,
) -> list[DoctorItem]:
    """Report when an agent has both a user and a project copy of the same kind."""
    items: list[DoctorItem] = []
    for agent_name, record in (agents.get("agents") or {}).items():
        user_by_kind: dict[str, str] = {}
        for entry in record.get("installed") or []:
            kind = entry.get("kind")
            if kind in ("skill", "hook") and entry.get("path"):
                user_by_kind[kind] = entry["path"]
        for project_name, project in (projects.get("projects") or {}).items():
            if only is not None and project_name not in only:
                continue
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
                        DoctorItem("fix", agent_name
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
                DoctorItem("fix", name
                    + ": "
                    + driver
                    + " is missing its project files. Run: agentcoop project repair "
                    + name,
                )
            ]
    return []


def _message_checks(
    board: Path,
    projects: dict,
    *,
    only: set[str] | None = None,
) -> list[DoctorItem]:
    items = []
    for name, project in (projects.get("projects") or {}).items():
        if only is not None and name not in only:
            continue
        members = [project.get("driver", "")] + list((project.get("helpers") or {}).keys())
        for agent in members:
            if not agent:
                continue
            _messages, issues = new_messages(board, name, agent)
            for issue in issues:
                items.append(
                    DoctorItem(
                        "fix",
                        "a message file could not be read and was skipped: "
                        + str(issue.path)
                        + ". Delete it if you do not need it.",
                    )
                )
            folder = inbox_msgs(board, name, agent)
            count = len([path for path in folder.glob("*.json") if not path.name.startswith(".")]) if folder.is_dir() else 0
            if count > INBOX_LIMIT:
                items.append(
                    DoctorItem("fix", agent
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
            done = inbox_done(board, name, agent)
            paths = folder.glob("*.json") if folder.is_dir() else []
            for path in paths:
                if path.name.startswith("."):
                    continue
                if (done / path.stem).exists():
                    continue
                message = load_message(path)
                if message is None:
                    continue
                if protocol_violation(project, message, board, name):
                    items.append(
                        DoctorItem("fix", "Out of protocol message "
                            + file_id(message)
                            + " in "
                            + agent
                            + "'s inbox. Ignore it.",
                        )
                    )
    return items


def _context_checks(
    board: Path,
    projects: dict,
    *,
    only: set[str] | None = None,
    now: datetime | None = None,
) -> list[DoctorItem]:
    """Note missing context and flag context requests older than 24 hours."""
    from agentcoop.context import latest_version, open_context_requests, version_time

    items: list[DoctorItem] = []
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = moment - timedelta(hours=24)
    for name, project in (projects.get("projects") or {}).items():
        if only is not None and name not in only:
            continue
        helpers = project.get("helpers") or {}
        if helpers and latest_version(board, name) is None:
            items.append(
                DoctorItem(
                    "note",
                    name + " has helpers and no project context yet. The driver can write one with: agentcoop context template",
                )
            )
        driver = str(project.get("driver") or "")
        for request in open_context_requests(board, name):
            when = None
            try:
                when = datetime.strptime(request.ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                when = version_time(request.id + ".md")
            if when is not None and when <= cutoff:
                items.append(
                    DoctorItem(
                        "fix",
                        "An open context request from "
                        + request.sender
                        + " to "
                        + driver
                        + " in "
                        + name
                        + " is older than 24 hours. "
                        + driver
                        + " should write the context and reply.",
                    )
                )
    return items


def _hello_check(board: Path, projects: dict, project_name: str) -> DoctorItem:
    project = (projects.get("projects") or {}).get(project_name)
    if project is None:
        return DoctorItem("fix", "There is no project named " + project_name + ". Run: agentcoop project list")
    driver = project.get("driver", "")
    messages = log_messages(board, project_name)
    hello_subject = "Agent Co-op hello"
    hellos = [
        item
        for item in messages
        if item.sender == driver and item.subject == hello_subject
    ]
    if not hellos:
        return DoctorItem(
            "fix",
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
    return DoctorItem("ok" if not waiting_on else "fix", text)


def _schedule_checks(board: Path, agents: dict, projects: dict, *, now: datetime | None = None) -> list[DoctorItem]:
    """Report whether the background schedule is installed and healthy."""
    from agentcoop.rounds import load_schedule
    from agentcoop.schedule import schedulable_reason

    items: list[DoctorItem] = []
    record = load_schedule(board)
    if record is None:
        items.append(DoctorItem("note", "Background checks are off. Run: agentcoop start"))
        return items
    items.append(DoctorItem("ok", "Background checks are on, every " + str(record.get("interval")) + " minutes."))
    for raw in record.get("files") or []:
        if not Path(raw).is_file():
            items.append(DoctorItem("fix", "A schedule file is missing: " + raw + ". Run: agentcoop start"))
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    interval = int(record.get("interval") or 5)
    last = board / ".state" / "last-tick"
    late = False
    if last.is_file():
        try:
            when = datetime.strptime(last.read_text(encoding="utf-8").strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            late = (moment - when).total_seconds() > interval * 120
            shown = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            late = True
            shown = "an unknown time"
    else:
        installed = str(record.get("installed") or "")
        try:
            when = datetime.strptime(installed, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            late = (moment - when).total_seconds() > interval * 120
            shown = installed
        except ValueError:
            late = False
            shown = ""
    if late:
        items.append(
            DoctorItem(
                "fix",
                "the background schedule has not run since "
                + shown
                + ". Run: agentcoop start",
            )
        )
    shim = board / "bin" / "shims" / ("git.cmd" if os.name == "nt" else "git")
    if not shim.is_file():
        items.append(DoctorItem("fix", "The git shim is missing while background checks are on. Run: agentcoop start"))
    items.extend(_worktree_checks(board, projects))
    for name, agent in (agents.get("agents") or {}).items():
        try:
            tool = get_tool(str(agent.get("tool") or ""))
        except KeyError:
            tool = None
        if tool is not None and tool.join_method == "outbox":
            continue
        reason = schedulable_reason(board, name, role="helper")
        if reason:
            items.append(DoctorItem("note", reason))
    return items


def _worktree_checks(board: Path, projects: dict) -> list[DoctorItem]:
    """Note worktrees that do not match a project, and missing instruction files."""
    items: list[DoctorItem] = []
    root = board / "worktrees"
    known = set((projects.get("projects") or {}).keys())
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and child.name not in known:
                items.append(DoctorItem("note", "A worktree remains for " + child.name + ", which is not a project."))
    for name, project in (projects.get("projects") or {}).items():
        dest = root / name
        if not dest.is_dir():
            continue
        for entry in project.get("installed") or []:
            if entry.get("scope") not in ("", "project"):
                continue
            source = Path(str(entry.get("path") or ""))
            if not source.is_file():
                continue
            copied = dest / source.name
            if not copied.is_file():
                items.append(
                    DoctorItem(
                        "note",
                        "The worktree for " + name + " is missing " + source.name + ".",
                    )
                )
    return items


def _progress_checks(board: Path, projects: dict, *, now: datetime | None = None) -> list[DoctorItem]:
    """Note stale blocked progress and urgent requests with no reply."""
    from agentcoop.core import log_messages
    from agentcoop.progress import latest_progress, progress_time, section_text, urgent_requests

    items: list[DoctorItem] = []
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    for name in (projects.get("projects") or {}):
        path = latest_progress(board, name)
        if path is None:
            continue
        when = progress_time(path.name)
        blocked = section_text(path.read_text(encoding="utf-8"), "Blocked or need help")
        if when and blocked and blocked.strip().lower() != "none":
            if (moment - when).total_seconds() > 3600:
                items.append(
                    DoctorItem(
                        "note",
                        name + " has been blocked since " + when.strftime("%Y-%m-%dT%H:%M:%SZ") + ".",
                    )
                )
    for row in urgent_requests(board):
        project = str(row.get("project") or "")
        mid = str(row.get("id") or "")
        try:
            sent = datetime.strptime(str(row.get("time") or ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if (moment - sent).total_seconds() < 1800:
            continue
        try:
            messages = log_messages(board, project)
        except CoopError:
            messages = []
        if any(item.re == mid for item in messages):
            continue
        items.append(DoctorItem("note", "Urgent request " + mid + " has had no reply for 30 minutes."))
    return items


def _outbox_checks(
    board: Path,
    projects: dict,
    agents: dict,
    *,
    only: set[str] | None,
    now: datetime | None = None,
) -> list[DoctorItem]:
    """Note slow outbox helpers, rejected answers, and a stale skill zip."""
    from agentcoop.desktop import export_version_text
    from agentcoop.outbox import first_rejected_reason, is_outbox_agent, waiting_minutes

    items: list[DoctorItem] = []
    for name, record in (projects.get("projects") or {}).items():
        if only is not None and name not in only:
            continue
        for agent in record.get("helpers") or {}:
            if agent not in (agents.get("agents") or {}):
                continue
            if not is_outbox_agent(board, agent):
                continue
            minutes = waiting_minutes(board, name, agent, now=now)
            if minutes is not None and minutes > 15:
                items.append(
                    DoctorItem(
                        "note",
                        agent
                        + " has not answered for "
                        + str(minutes)
                        + " minutes. Check that the Claude app is open and its scheduled task is running.",
                    )
                )
            reason = first_rejected_reason(board, name, agent)
            if reason:
                items.append(
                    DoctorItem(
                        "fix",
                        agent
                        + " has rejected answers on "
                        + name
                        + ". First reason: "
                        + reason,
                    )
                )
            zip_path = board / "exports" / (agent + "-" + name + ".zip")
            version = export_version_text(board, name, agent)
            if not zip_path.is_file() or version != __version__:
                items.append(
                    DoctorItem(
                        "fix",
                        "Run: agentcoop skill export --as "
                        + agent
                        + " --project "
                        + name
                        + ", then upload it again.",
                    )
                )
    return items


def _signin_checks(board: Path, agents: dict, *, only: set[str] | None) -> list[DoctorItem]:
    from agentcoop.rounds import _load_runs
    from agentcoop.tools import get_tool

    signin = _load_runs(board).get("signin") or {}
    items = []
    for name, failed in signin.items():
        if not failed:
            continue
        if only is not None and name not in only:
            continue
        record = (agents.get("agents") or {}).get(name) or {}
        display = str(record.get("display") or name)
        hint = ""
        try:
            hint = get_tool(str(record.get("tool") or "")).login_hint
        except KeyError:
            hint = ""
        items.append(
            DoctorItem(
                "fix",
                display
                + " is not signed in, so it cannot run in the background. Run: "
                + (hint or "the tool's sign-in command"),
            )
        )
    return items


def format_doctor(items: list[DoctorItem]) -> str:
    """Render doctor results as plain lines."""
    labels = {"ok": "OK: ", "note": "Note: ", "fix": "Fix: "}
    return "\n".join(labels.get(item.level, "OK: ") + item.text for item in items)
