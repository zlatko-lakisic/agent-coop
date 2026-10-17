"""Install and remove skills, rules, hooks, and the local command."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import zipapp
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from agentcoop import __version__
from agentcoop.errors import CoopError
from agentcoop.paths import agentcoop_command, home_dir
from agentcoop.projects import (
    connect_agent_record,
    load_agents,
    load_projects,
    mutate_agents,
    mutate_projects,
    now_stamp,
    record_installed,
)
from agentcoop.tools import get_tool

MARKER_START = "<!-- agentcoop:start -->"
MARKER_END = "<!-- agentcoop:end -->"


def marker_start(agent: str) -> str:
    """Return the section start marker for one agent."""
    return "<!-- agentcoop:start " + agent + " -->"


def marker_end(agent: str) -> str:
    """Return the section end marker for one agent."""
    return "<!-- agentcoop:end " + agent + " -->"

_KIND_TEMPLATE = {
    "skill": "SKILL.md.tmpl",
    "rule": "cursor-rule.mdc.tmpl",
    "agents_md": "AGENTS.md.tmpl",
    "gemini_md": "GEMINI.md.tmpl",
}


def read_template(name: str) -> str:
    """Load a template shipped inside the package.

    Works from source, from a zipapp, and from a PyInstaller binary.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS")) / "templates"
        return (base / name).read_text(encoding="utf-8")
    node = resources.files("agentcoop").joinpath("templates")
    for part in name.split("/"):
        node = node.joinpath(part)
    return node.read_text(encoding="utf-8")



def render_template(name: str, *, agent_name: str, board: Path) -> str:
    """Fill agent name, command, and board path into a template."""
    text = read_template(name)
    return (
        text.replace("{{AGENT_NAME}}", agent_name)
        .replace("{{AGENTCOOP}}", agentcoop_command(board))
        .replace("{{BOARD}}", str(board))
    )


def file_sha256(path: Path) -> str:
    """Hash a file the manifest can compare later."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_sha256(text: str) -> str:
    """Hash text using the same encoding we write to disk."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def backup_file(path: Path, board: Path, scope: str) -> Path:
    """Copy a file under the board before Agent Co-op changes it."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    folder = board / ".state" / "backups" / (scope or "user") / digest
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / (path.name + "." + stamp)
    shutil.copy2(path, dest)
    return dest


def _replace_marked(original: str, start: str, end: str, block: str) -> str | None:
    if start not in original or end not in original:
        return None
    pre, rest = original.split(start, 1)
    _old, post = rest.split(end, 1)
    if post.startswith("\n"):
        post = post[1:]
    return pre + block + post


def upsert_marked_section(original: str, section: str, agent: str = "") -> str:
    """Insert or replace one agent's Agent Co-op section."""
    start = marker_start(agent) if agent else MARKER_START
    end = marker_end(agent) if agent else MARKER_END
    block = start + "\n" + section.strip() + "\n" + end + "\n"
    updated = _replace_marked(original, start, end, block)
    if updated is not None:
        return updated
    if agent and MARKER_START in original and marker_start(agent) not in original and "<!-- agentcoop:start " not in original:
        updated = _replace_marked(original, MARKER_START, MARKER_END, block)
        if updated is not None:
            return updated
    base = original
    if base and not base.endswith("\n"):
        base += "\n"
    if base and not base.endswith("\n\n"):
        base += "\n"
    return base + block


def remove_marked_section(original: str, agent: str = "") -> str:
    """Remove one agent's section. The old marker form is removed when it is the only one."""
    if agent:
        start = marker_start(agent)
        end = marker_end(agent)
        updated = _replace_marked(original, start, end, "")
        text = original if updated is None else updated
        if "<!-- agentcoop:start " not in text and MARKER_START in text:
            legacy = _replace_marked(text, MARKER_START, MARKER_END, "")
            if legacy is not None:
                text = legacy
        return text
    updated = _replace_marked(original, MARKER_START, MARKER_END, "")
    return original if updated is None else updated


def merge_hook_settings(existing: dict, addition: dict) -> dict:
    """Add hook entries that are not already present. Keep every other hook."""
    result = json.loads(json.dumps(existing))
    hooks = result.setdefault("hooks", {})
    for event, entries in (addition.get("hooks") or {}).items():
        current = hooks.setdefault(event, [])
        if not isinstance(current, list):
            continue
        known = {_hook_command(item) for item in current}
        for entry in entries:
            command = _hook_command(entry)
            if command and command in known:
                continue
            current.append(entry)
            if command:
                known.add(command)
    return result


def remove_hook_command(settings: dict, command: str) -> dict:
    """Remove hook entries that run one command. Leave every other hook.

    An event key is deleted when its list becomes empty. The hooks key is
    deleted when nothing is left under it. Every other key stays as it was.
    """
    result = json.loads(json.dumps(settings))
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        kept = [entry for entry in entries if _hook_command(entry) != command]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if hooks:
        result["hooks"] = hooks
    else:
        result.pop("hooks", None)
    return result


def _hook_command(entry: dict) -> str:
    for hook in entry.get("hooks") or []:
        if isinstance(hook, dict) and hook.get("command"):
            return str(hook["command"])
    return ""


def build_zipapp(dest: Path) -> None:
    """Build a zipapp that runs with python3 agentcoop.pyz."""
    import tempfile

    package = Path(__file__).resolve().parent
    staging = Path(tempfile.mkdtemp(prefix="agentcoop-pyz-"))
    try:
        shutil.copytree(
            package,
            staging / "agentcoop",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (staging / "__main__.py").write_text(
            "import sys\nfrom agentcoop.__main__ import main\nraise SystemExit(main())\n",
            encoding="utf-8",
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        zipapp.create_archive(
            staging,
            dest,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
    finally:
        shutil.rmtree(staging)


def stage_runtime(board: Path) -> None:
    """Copy the running command into the board bin folder."""
    bindir = board / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        src = Path(sys.executable)
        name = "agentcoop.exe" if os.name == "nt" else "agentcoop"
        dest = bindir / name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        if os.name != "nt":
            dest.chmod(0o700)
    else:
        argv0 = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
        dest = bindir / "agentcoop.pyz"
        if argv0 is not None and argv0.suffix == ".pyz" and argv0.is_file() and argv0.resolve() != dest.resolve():
            shutil.copy2(argv0, dest)
        elif not (argv0 is not None and argv0.suffix == ".pyz"):
            build_zipapp(dest)
        if os.name != "nt" and dest.is_file():
            dest.chmod(0o700)
    version = bindir / "VERSION"
    version.write_text(__version__ + "\n", encoding="utf-8")
    if os.name != "nt":
        version.chmod(0o600)


def _plan_user_files(board: Path, tool: object, agent_name: str) -> list[dict]:
    """Render every user-scope file into memory. Nothing is written."""
    planned = []
    for item in tool.install_paths:  # type: ignore[attr-defined]
        if item["scope"] != "user":
            continue
        kind = item["kind"]
        path = home_dir() / item["path"]
        if kind == "hook":
            if tool.hooks != "supported":  # type: ignore[attr-defined]
                continue
            addition = claude_code_hooks(board, agent_name)
            existing: dict = {}
            if path.is_file():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise CoopError(str(path) + " is not valid JSON, so hooks were not changed.") from exc
                if isinstance(loaded, dict):
                    existing = loaded
            merged = merge_hook_settings(existing, addition)
            command = ""
            for entries in (addition.get("hooks") or {}).values():
                for entry in entries:
                    command = _hook_command(entry) or command
            planned.append(
                {
                    "path": path,
                    "content": json.dumps(merged, indent=2) + "\n",
                    "kind": kind,
                    "command": command,
                }
            )
            continue
        template_name = _KIND_TEMPLATE.get(kind)
        if template_name is None:
            continue
        rendered = render_template(template_name, agent_name=agent_name, board=board)
        if kind in ("agents_md", "gemini_md"):
            original = path.read_text(encoding="utf-8") if path.is_file() else ""
            rendered = upsert_marked_section(original, rendered, agent_name)
        planned.append({"path": path, "content": rendered, "kind": kind, "command": ""})
    return planned


def _refuse_foreign(path: Path, content: str, manifest: list, *, yes: bool) -> None:
    """Stop before any write when a target exists and is not ours."""
    if not path.exists() or yes:
        return
    current = path.read_text(encoding="utf-8")
    if current == content or _owned(manifest, path, current):
        return
    raise CoopError(
        str(path)
        + " already exists. Agent Co-op will not replace it unless you confirm. "
        "Run the command again with --yes."
    )


def connect_agent(
    board: Path,
    tool_key: str,
    agent_name: str | None = None,
    *,
    yes: bool = False,
    display: str | None = None,
    runner: dict | None = None,
) -> str:
    """Connect one agent at user scope and return its name."""
    tool = get_tool(tool_key)
    if not tool.supported or tool.join_method == "none":
        raise CoopError(tool.display + " cannot join. " + tool.notes)
    name = agent_name or tool.default_agent_name
    if not name:
        raise CoopError(tool.display + " needs a name. Add --as NAME.")
    if tool_key in ("ollama", "lmstudio", "runner"):
        connect_agent_record(board, name, tool_key, display=display, runner=runner)
        return name

    planned = _plan_user_files(board, tool, name)
    current_agents = load_agents(board)
    current = (current_agents.get("agents") or {}).get(name) or {}
    manifest = list(current.get("installed") or [])
    for item in planned:
        _refuse_foreign(item["path"], item["content"], manifest, yes=yes)
    written: list[dict] = []
    try:
        for item in planned:
            before = len(manifest)
            _write_content(
                item["path"],
                item["content"],
                manifest=manifest,
                agent=name,
                kind=item["kind"],
                yes=yes,
                board=board,
                scope="user",
                command=item.get("command") or "",
            )
            written.extend(manifest[before:])

        def change(agents: dict) -> None:
            record = agents["agents"].get(name)
            if record is None:
                record = {
                    "tool": tool_key,
                    "display": display or tool.display,
                    "connected": now_stamp(),
                    "installed": [],
                }
                agents["agents"][name] = record
            elif display:
                record["display"] = display
            record["installed"] = manifest

        mutate_agents(board, change)
    except Exception as exc:
        for entry in reversed(written):
            undo_entry(entry)
        reason = str(exc)
        raise CoopError(
            "Could not connect " + tool.display + ". Nothing was changed. " + reason
        ) from exc
    return name


def preview_project_files(board: Path, agent_name: str, project_path: Path) -> list[dict]:
    """Render project-scoped files into memory. No files are written.

    Raises if a template cannot be read, so project add can fail before saving.
    """
    agents = load_agents(board)
    agent = agents["agents"].get(agent_name)
    if agent is None:
        raise CoopError(agent_name + " is not connected yet. Run: agentcoop connect")
    if agent["tool"] == "runner":
        return []
    tool = get_tool(agent["tool"])
    planned: list[dict] = []
    for item in tool.install_paths:
        if item["scope"] != "project":
            continue
        if item["kind"] == "hook" and tool.hooks != "supported":
            continue
        kind = item["kind"]
        path = project_path / item["path"]
        if kind == "hook":
            claude_code_hooks(board, agent_name)
            planned.append({"path": str(path), "kind": kind, "relative": item["path"]})
            continue
        template_name = _KIND_TEMPLATE.get(kind)
        if template_name is None:
            continue
        content = render_template(template_name, agent_name=agent_name, board=board)
        planned.append(
            {
                "path": str(path),
                "kind": kind,
                "relative": item["path"],
                "content": content,
            }
        )
    return planned


def project_instruction_agents(board: Path, project: dict) -> list[str]:
    """Return members who should have project-scope instruction files."""
    agents = load_agents(board)
    driver = project.get("driver") or ""
    names = [driver] + list((project.get("helpers") or {}).keys())
    chosen = []
    for name in names:
        if not name:
            continue
        record = (agents.get("agents") or {}).get(name) or {}
        tool_key = record.get("tool") or ""
        if not tool_key or tool_key == "runner":
            continue
        tool = get_tool(tool_key)
        if name == driver or tool.project_files_for == "members":
            chosen.append(name)
    return chosen


def install_project_files(
    board: Path,
    project_name: str,
    agent_name: str,
    *,
    yes: bool = False,
    gitignore: bool = False,
) -> list[str]:
    """Install project-scoped files for one agent. Returns relative paths written."""
    agents = load_agents(board)
    agent = agents["agents"].get(agent_name)
    if agent is None:
        raise CoopError(agent_name + " is not connected yet. Run: agentcoop connect")
    if agent["tool"] == "runner":
        return []
    tool = get_tool(agent["tool"])
    written: list[str] = []

    def change(projects: dict) -> None:
        project = projects["projects"].get(project_name)
        if project is None:
            raise CoopError("There is no project named " + project_name + ".")
        root = Path(project["path"])
        manifest = project.setdefault("installed", [])
        for item in tool.install_paths:
            if item["scope"] != "project":
                continue
            if item["kind"] == "hook" and tool.hooks != "supported":
                continue
            _install_one(
                board,
                agent_name=agent_name,
                item=item,
                root=root,
                manifest=manifest,
                yes=yes,
                tool_key=tool.key,
                scope=project_name,
            )
            written.append(item["path"])
        if gitignore and written:
            _install_gitignore(root, written, manifest, agent_name, board, project_name)

    mutate_projects(board, change)
    return written


def rollback_project_add(board: Path, name: str) -> list[str]:
    """Undo a half-finished project add: installed files, record, and board folder."""
    notes: list[str] = []

    def change(projects: dict) -> None:
        project = projects["projects"].get(name)
        if project is None:
            return
        for entry in list(project.get("installed") or []):
            note = undo_entry(entry)
            if note:
                notes.append(note)
        projects["projects"].pop(name, None)

    mutate_projects(board, change)
    board_dir = board / "projects" / name
    if board_dir.exists():
        shutil.rmtree(board_dir)
    return notes


def repair_project_files(board: Path, project_name: str) -> list[str]:
    """Reinstall missing driver project files. Returns absolute paths written."""
    pruned = prune_out_of_scope_project_files(board, project_name)
    projects = load_projects(board)
    project = projects["projects"].get(project_name)
    if project is None:
        raise CoopError("There is no project named " + project_name + ".")
    before = {entry.get("path") for entry in (project.get("installed") or [])}
    root = Path(project["path"])
    agents = load_agents(board)
    to_fix: list[str] = []
    targets = project_instruction_agents(board, project)
    for name in targets:
        record = (agents.get("agents") or {}).get(name)
        if record is None:
            raise CoopError(name + " is not connected yet. Run: agentcoop connect")
        tool = get_tool(record["tool"])
        for item in tool.install_paths:
            if item["scope"] != "project":
                continue
            if item["kind"] == "hook" and tool.hooks != "supported":
                continue
            path = root / item["path"]
            in_manifest = str(path) in before
            if not path.is_file() or not in_manifest:
                to_fix.append(str(path))
    if not to_fix:
        return pruned
    for name in targets:
        install_project_files(board, project_name, name, yes=False, gitignore=False)
    repaired = [path for path in to_fix if Path(path).is_file()]
    return pruned + repaired


def prune_out_of_scope_project_files(board: Path, project_name: str | None = None) -> list[str]:
    """Remove project installs that are no longer in the capability table.

    Only removes files that are in the manifest and still match the saved hash.
    """
    agents = load_agents(board)
    removed: list[str] = []

    def change(projects: dict) -> None:
        names = [project_name] if project_name else list((projects.get("projects") or {}).keys())
        for name in names:
            project = (projects.get("projects") or {}).get(name)
            if project is None:
                continue
            root = Path(project.get("path", ""))
            kept = []
            for entry in list(project.get("installed") or []):
                agent_name = entry.get("agent", "")
                agent = (agents.get("agents") or {}).get(agent_name) or {}
                tool_key = agent.get("tool", "")
                if tool_key in ("", "runner"):
                    kept.append(entry)
                    continue
                try:
                    tool = get_tool(tool_key)
                except KeyError:
                    kept.append(entry)
                    continue
                allowed = {
                    str(root / item["path"])
                    for item in tool.install_paths
                    if item["scope"] == "project"
                    and not (item["kind"] == "hook" and tool.hooks != "supported")
                }
                path = entry.get("path", "")
                if path in allowed or entry.get("kind") == "gitignore":
                    kept.append(entry)
                    continue
                path_obj = Path(path)
                if path_obj.is_file() and entry.get("sha256") and file_sha256(path_obj) != entry.get("sha256"):
                    kept.append(entry)
                    continue
                note = undo_entry(entry)
                if note:
                    kept.append(entry)
                    continue
                removed.append(path)
            if kept != list(project.get("installed") or []):
                project["installed"] = kept

    mutate_projects(board, change)
    return removed


def disconnect_agent(board: Path, name: str) -> list[str]:
    """Remove what connect installed and unregister the agent."""
    projects = load_projects(board)
    for project_name, project in (projects.get("projects") or {}).items():
        if project.get("driver") == name:
            raise CoopError(
                name
                + " is the driver for "
                + project_name
                + ". Change the driver first: agentcoop project driver "
                + project_name
                + " AGENT"
            )
        if name in (project.get("helpers") or {}):
            raise CoopError(
                name
                + " is a helper on "
                + project_name
                + ". Remove the helper first: agentcoop project helper remove "
                + project_name
                + " "
                + name
            )
    notes: list[str] = []

    def change(agents: dict) -> None:
        if name not in agents["agents"]:
            raise CoopError(name + " is not connected. Run: agentcoop agents")
        record = agents["agents"].pop(name)
        for entry in list(record.get("installed") or []):
            if not _path_used_elsewhere(board, entry.get("path", ""), skip_agent=name):
                note = undo_entry(entry)
                if note:
                    notes.append(note)

    mutate_agents(board, change)
    return notes


def remove_project_files(board: Path, project_name: str) -> list[str]:
    """Undo files installed for a project, then drop the project entry."""
    notes: list[str] = []

    def change(projects: dict) -> None:
        project = projects["projects"].get(project_name)
        if project is None:
            raise CoopError("There is no project named " + project_name + ".")
        for entry in list(project.get("installed") or []):
            note = undo_entry(entry)
            if note:
                notes.append(note)
        projects["projects"].pop(project_name)

    mutate_projects(board, change)
    removed = board / "removed"
    removed.mkdir(parents=True, exist_ok=True)
    src = board / "projects" / project_name
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dest = removed / (project_name + "-" + stamp)
    if dest.exists():
        dest = removed / (project_name + "-" + now_stamp().replace(":", ""))
    if src.exists():
        shutil.move(str(src), str(dest))
    return notes


def undo_entry(entry: dict) -> str | None:
    """Undo one manifest row. Return a note when a changed file is left in place."""
    path = Path(entry.get("path", ""))
    if not path.exists():
        return None
    kind = entry.get("kind")
    if kind == "hook":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        command = entry.get("command") or ""
        updated = remove_hook_command(data, command) if command else data
        hooks = updated.get("hooks") or {}
        empty_hooks = all(not entries for entries in hooks.values()) if isinstance(hooks, dict) else False
        other_keys = [key for key in updated.keys() if key != "hooks"]
        if entry.get("created") and empty_hooks and not other_keys:
            path.unlink()
            return None
        path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        return None
    if kind in ("agents_md", "gemini_md"):
        text = remove_marked_section(path.read_text(encoding="utf-8"), str(entry.get("agent") or ""))
        if entry.get("created") and not text.strip():
            path.unlink()
        else:
            path.write_text(text, encoding="utf-8")
        return None
    if kind == "gitignore":
        lines = path.read_text(encoding="utf-8").splitlines()
        remove = set(entry.get("lines") or [])
        kept = [line for line in lines if line not in remove]
        if entry.get("created") and not any(line.strip() for line in kept):
            path.unlink()
        else:
            body = "\n".join(kept)
            if body:
                body += "\n"
            path.write_text(body, encoding="utf-8")
        return None
    if path.is_file() and entry.get("sha256") and file_sha256(path) != entry.get("sha256"):
        return "Left " + str(path) + " in place because it was changed after Agent Co-op installed it."
    if entry.get("created"):
        path.unlink()
        return None
    backup = entry.get("backup")
    if backup and Path(backup).is_file():
        shutil.copy2(backup, path)
    return None


def _path_used_elsewhere(board: Path, path: str, *, skip_agent: str) -> bool:
    if not path:
        return False
    agents = load_agents(board)
    for name, record in (agents.get("agents") or {}).items():
        if name == skip_agent:
            continue
        for entry in record.get("installed") or []:
            if entry.get("path") == path:
                return True
    projects = load_projects(board)
    for project in (projects.get("projects") or {}).values():
        for entry in project.get("installed") or []:
            if entry.get("path") == path:
                return True
    return False


def _install_one(
    board: Path,
    *,
    agent_name: str,
    item: dict,
    root: Path,
    manifest: list,
    yes: bool,
    tool_key: str,
    scope: str,
) -> None:
    kind = item["kind"]
    path = root / item["path"]
    if kind == "hook":
        if get_tool(tool_key).hooks != "supported":
            return
        _install_hook(board, agent_name, path, manifest, yes, tool_key, scope)
        return
    template_name = _KIND_TEMPLATE.get(kind)
    if template_name is None:
        return
    rendered = render_template(template_name, agent_name=agent_name, board=board)
    if kind in ("agents_md", "gemini_md"):
        original = path.read_text(encoding="utf-8") if path.is_file() else ""
        content = upsert_marked_section(original, rendered, agent_name)
    else:
        content = rendered
    _write_content(
        path,
        content,
        manifest=manifest,
        agent=agent_name,
        kind=kind,
        yes=yes,
        board=board,
        scope=scope,
    )


def claude_code_hooks(board: Path, agent_name: str) -> dict:
    """Build Claude Code hook settings as a dict.

    Paths are inserted with json.dumps, so Windows backslashes stay valid JSON.
    """
    command = agentcoop_command(board) + " check --as " + agent_name + " --summary"
    entry = {"hooks": [{"type": "command", "command": command}]}
    return {
        "hooks": {
            "SessionStart": [{"matcher": "*", **entry}],
            "UserPromptSubmit": [dict(entry)],
        }
    }


def _install_hook(
    board: Path,
    agent_name: str,
    path: Path,
    manifest: list,
    yes: bool,
    tool_key: str,
    scope: str,
) -> None:
    if tool_key != "claude-code":
        return
    addition = claude_code_hooks(board, agent_name)
    command = ""
    for entries in (addition.get("hooks") or {}).values():
        for entry in entries:
            command = _hook_command(entry) or command
    existing: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CoopError(str(path) + " is not valid JSON, so hooks were not changed.") from exc
        if isinstance(loaded, dict):
            existing = loaded
    merged = merge_hook_settings(existing, addition)
    content = json.dumps(merged, indent=2) + "\n"
    _write_content(
        path,
        content,
        manifest=manifest,
        agent=agent_name,
        kind="hook",
        yes=yes,
        command=command,
        board=board,
        scope=scope,
    )


def _write_content(
    path: Path,
    content: str,
    *,
    manifest: list,
    agent: str,
    kind: str,
    yes: bool,
    board: Path,
    scope: str,
    command: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    backup = ""
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == content:
            record_installed(
                manifest,
                _entry(path, agent, kind, text_sha256(content), created=False, backup="", command=command),
            )
            return
        owned = _owned(manifest, path, current)
        if not owned and not yes:
            raise CoopError(
                str(path)
                + " already exists. Agent Co-op will not replace it unless you confirm. "
                "Run the command again with --yes."
            )
        backup = str(backup_file(path, board, scope))
    path.write_text(content, encoding="utf-8")
    record_installed(
        manifest,
        _entry(path, agent, kind, file_sha256(path), created=created, backup=backup, command=command),
    )


def _owned(manifest: list, path: Path, current: str) -> bool:
    digest = text_sha256(current)
    for item in manifest:
        if item.get("path") == str(path) and item.get("sha256") == digest:
            return True
    return False


def _entry(path: Path, agent: str, kind: str, digest: str, *, created: bool, backup: str, command: str) -> dict:
    row = {
        "path": str(path),
        "agent": agent,
        "kind": kind,
        "sha256": digest,
        "created": created,
    }
    if backup:
        row["backup"] = backup
    if command:
        row["command"] = command
    return row


def _install_gitignore(
    root: Path,
    relative_paths: list[str],
    manifest: list,
    agent: str,
    board: Path,
    scope: str,
) -> None:
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = existing.splitlines()
    added = []
    for relative in relative_paths:
        if relative not in lines:
            lines.append(relative)
            added.append(relative)
    if not added and path.is_file():
        return
    body = "\n".join(lines)
    if body:
        body += "\n"
    created = not path.exists()
    if path.exists() and path.read_text(encoding="utf-8") != body:
        backup_file(path, board, scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    record_installed(
        manifest,
        {
            "path": str(path),
            "agent": agent,
            "kind": "gitignore",
            "sha256": file_sha256(path),
            "created": created,
            "lines": added,
        },
    )


def write_board_readme(board: Path) -> None:
    """Create the board README if it is not already there."""
    path = board / "README.md"
    if path.exists():
        return
    path.write_text(
        render_template("board-README.md.tmpl", agent_name="agent", board=board),
        encoding="utf-8",
    )
