"""Remove Agent Co-op from this computer."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from agentcoop.core import send, waiting
from agentcoop.errors import CoopError
from agentcoop.install import disconnect_agent, file_sha256, remove_project_files
from agentcoop.paths import home_dir, quote_path
from agentcoop.projects import load_agents, load_projects

LAUNCHD_NAME = "com.agentcoop.runner.plist"
TASK_SCHEDULER_NOTE = (
    "If you made a Task Scheduler task for the runner, delete it in Task Scheduler."
)
WINDOWS_REMOVE = 'Remove-Item -Recurse -Force "$env:USERPROFILE\\.agent_board"'


def run_uninstall(
    board: Path,
    *,
    agent: bool = False,
    dry_run: bool = False,
    keep_board: bool = False,
) -> int:
    """Print the plan, then undo every install. Return a process exit code."""
    if not sys.stdin.isatty() and not agent:
        raise CoopError(
            "To uninstall from an agent, add --agent. The board will be moved, not deleted.",
            code=2,
        )
    agent_mode = agent
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = _unique_dest(stamp)
    print("\n".join(_plan(board, agent_mode=agent_mode, keep_board=keep_board, dest=dest)))
    if dry_run:
        return 0
    if not agent_mode:
        answer = input("Type uninstall to confirm: ")
        if answer.strip() != "uninstall":
            raise CoopError("Cancelled.", code=1)
    from agentcoop.pathenv import undo_path_changes
    from agentcoop.rounds import stop_schedule

    try:
        stop_schedule(board)
    except CoopError:
        pass
    undo_path_changes(board)
    notes = _remove_installs(board)
    _remove_runner_autostart()
    if notes:
        print("Files you changed, left in place:")
        for note in notes:
            print(note)
    if agent_mode:
        _move_board(board, dest)
        print(
            "Agent Co-op is uninstalled. The old board, with every message, was moved to "
            + str(dest)
            + ". Delete that folder when you no longer need it. "
            "You can also delete the copy you downloaded or cloned."
        )
    else:
        if keep_board:
            _delete_bin(board)
        else:
            _delete_board(board)
        print("Agent Co-op is uninstalled. You can delete the copy you downloaded or cloned.")
    bindir = board / "bin"
    if _on_path(bindir):
        print("Remove " + str(bindir) + " from your PATH.")
    return 0


def _plan(board: Path, *, agent_mode: bool, keep_board: bool, dest: Path) -> list[str]:
    lines = ["Uninstall plan", ""]
    projects = (load_projects(board).get("projects") or {})
    if not projects:
        lines.append("No projects.")
    for name, project in projects.items():
        lines.append("Project " + name + " at " + str(project.get("path", "")))
        installed = project.get("installed") or []
        if not installed:
            lines.append("  No Agent Co-op files in this folder.")
        for entry in installed:
            lines.append("  " + _describe(entry))
        lines.append("")
    agents = (load_agents(board).get("agents") or {})
    if not agents:
        lines.append("No connected agents.")
    for name, record in agents.items():
        lines.append("Agent " + name)
        installed = record.get("installed") or []
        if not installed:
            lines.append("  No Agent Co-op files in the home folder.")
        for entry in installed:
            lines.append("  " + _describe(entry))
        lines.append("")
    runner = _runner_lines()
    if runner:
        lines.append("Runner autostart")
        lines.extend("  " + line for line in runner)
        lines.append("")
    lines.append(_board_sentence(board, agent_mode=agent_mode, keep_board=keep_board, dest=dest))
    return lines


def _describe(entry: dict) -> str:
    path = Path(entry.get("path", ""))
    kind = entry.get("kind")
    if not str(path) or not path.exists():
        return "already gone " + str(path)
    if kind == "hook":
        command = entry.get("command") or "the Agent Co-op check command"
        return "remove hook " + command + " from " + str(path)
    if kind in ("gitignore", "agents_md", "gemini_md"):
        return "change " + str(path)
    if path.is_file() and entry.get("sha256") and file_sha256(path) != entry.get("sha256"):
        return "leave " + str(path) + " in place because it was changed"
    return "remove " + str(path)


def _board_sentence(board: Path, *, agent_mode: bool, keep_board: bool, dest: Path) -> str:
    if agent_mode:
        return "The board at " + str(board) + " will be moved to " + str(dest) + "."
    if keep_board:
        return "The board at " + str(board) + " will be kept. The bin folder will be deleted."
    return "The board at " + str(board) + " will be deleted."


def _remove_installs(board: Path) -> list[str]:
    notes: list[str] = []
    projects = load_projects(board).get("projects") or {}
    queued = [(name, str(project.get("driver") or "")) for name, project in projects.items()]
    for name, driver in queued:
        if driver:
            for item in waiting(board, name, driver):
                send(
                    board,
                    project=name,
                    sender=driver,
                    recipient=item.recipient,
                    subject="Project removed",
                    body="This project was removed, so this request is closed.",
                    type="status",
                    re=item.id,
                )
        notes.extend(remove_project_files(board, name))
    names = list((load_agents(board).get("agents") or {}).keys())
    for name in names:
        notes.extend(disconnect_agent(board, name))
    return notes


def _runner_lines() -> list[str]:
    lines = []
    if sys.platform == "darwin":
        plist = _launchd_plist()
        if plist.is_file():
            lines.append("unload and delete " + str(plist))
    elif sys.platform == "linux":
        for unit in _systemd_units():
            lines.append("disable and delete " + unit.name)
    elif os.name == "nt":
        lines.append(TASK_SCHEDULER_NOTE)
    return lines


def _remove_runner_autostart() -> None:
    if sys.platform == "darwin":
        plist = _launchd_plist()
        if plist.is_file():
            _run_command(["launchctl", "unload", str(plist)])
            _delete_file(plist)
    elif sys.platform == "linux":
        for unit in _systemd_units():
            _run_command(["systemctl", "--user", "disable", "--now", unit.name])
            _delete_file(unit)
    elif os.name == "nt":
        print(TASK_SCHEDULER_NOTE)


def _launchd_plist() -> Path:
    return home_dir() / "Library" / "LaunchAgents" / LAUNCHD_NAME


def _systemd_units() -> list[Path]:
    folder = home_dir() / ".config" / "systemd" / "user"
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.service")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _exec_start_runs_runner(text):
            found.append(path)
    return found


def _exec_start_runs_runner(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("ExecStart"):
            continue
        if "agentcoop" in stripped and " runner" in stripped:
            return True
    return False


def _run_command(argv: list[str]) -> None:
    shown = " ".join(quote_path(part) if " " in part else part for part in argv)
    try:
        completed = subprocess.run(argv, capture_output=True, text=True)
    except OSError:
        print(shown)
        return
    if completed.returncode != 0:
        print(shown)


def _delete_file(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        print("Delete this file yourself: " + str(path))


def _unique_dest(stamp: str) -> Path:
    dest = home_dir() / ("agent-coop-uninstalled-" + stamp)
    if not dest.exists():
        return dest
    extra = datetime.now().strftime("%H%M%S%f")
    return home_dir() / ("agent-coop-uninstalled-" + stamp + "-" + extra)


def _move_board(board: Path, dest: Path) -> None:
    exe = _running_exe(board)
    if exe is None:
        shutil.move(str(board), str(dest))
        return
    dest.mkdir(parents=True, exist_ok=True)
    for child in list(board.iterdir()):
        if child.name == "bin":
            continue
        shutil.move(str(child), str(dest / child.name))
    _delete_except(board, exe)
    print(WINDOWS_REMOVE)


def _delete_board(board: Path) -> None:
    exe = _running_exe(board)
    if exe is None:
        shutil.rmtree(board)
        return
    _delete_except(board, exe)
    print(WINDOWS_REMOVE)


def _delete_bin(board: Path) -> None:
    bindir = board / "bin"
    if not bindir.exists():
        return
    exe = _running_exe(board)
    if exe is not None and _is_inside(exe, bindir):
        _delete_except(bindir, exe)
        print(WINDOWS_REMOVE)
        return
    shutil.rmtree(bindir)


def _running_exe(board: Path) -> Path | None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable)
    if _is_inside(exe, board):
        return exe
    return None


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _delete_except(root: Path, keep: Path) -> None:
    kept = keep.resolve()
    for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
        folder = Path(dirpath)
        for name in filenames:
            path = folder / name
            try:
                if path.resolve() == kept:
                    continue
            except OSError:
                continue
            _delete_file(path)
        if folder.resolve() == root.resolve():
            continue
        if _is_inside(kept, folder):
            continue
        try:
            folder.rmdir()
        except OSError:
            pass


def _on_path(bindir: Path) -> bool:
    try:
        target = bindir.resolve()
    except OSError:
        target = bindir
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).expanduser().resolve() == target:
                return True
        except OSError:
            continue
    return False
