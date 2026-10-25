"""projects.json, agents.json, and config.json."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from agentcoop.errors import CoopError
from agentcoop.paths import ensure_agent_dirs, home_dir, is_same_or_inside, paths_conflict, private_dir
from agentcoop.schema import check_agents, check_config, check_projects, format_problem
from agentcoop.tools import get_tool, tool_can_drive

PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

INTEGRITY_WARNING = (
    "projects.json was changed outside agentcoop. Roles may not be what you set. "
    "Run: agentcoop project review"
)

INTEGRITY_PAUSED = (
    "projects.json was changed outside agentcoop, so role changes are paused. "
    "Run: agentcoop project review"
)

_EMPTY_PROJECTS = {"version": 1, "projects": {}}
_EMPTY_AGENTS = {"version": 1, "agents": {}}
_EMPTY_CONFIG = {
    "version": 1,
    "allowed_roots": [],
    "body_limit_bytes": 16384,
    "context_limit_bytes": 65536,
    "max_runs_per_day": 48,
    "run_timeout_minutes": 15,
    "auto_branch": "agentcoop/auto",
    "progress_limit_bytes": 16384,
    "progress_every_minutes": 10,
    "progress_long_task_minutes": 5,
    "share_max_mb": 200,
    "share_keep_days": 30,
    "share_hard_max_mb": 4096,
    "share_warn_total_gb": 10,
    "board_format": 1,
    "min_version": "0.2.0",
    "update_check": True,
}


def now_stamp() -> str:
    """Return the current UTC time at second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_project_name(name: str) -> None:
    """Raise when a project name does not match the spec."""
    if not PROJECT_NAME_RE.fullmatch(name or ""):
        raise CoopError(
            "Project names use lowercase letters, numbers, and hyphens, "
            "and start with a letter or number. Example: frontend"
        )


def validate_agent_name(name: str) -> None:
    """Raise when an agent name does not match the spec."""
    if not AGENT_NAME_RE.fullmatch(name or ""):
        raise CoopError(
            "Agent names use lowercase letters, numbers, and underscores, "
            "start with a letter, and are 2 to 32 characters. Example: cursor"
        )


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes by renaming a temp file into place."""
    private_dir(path.parent)
    tmp = path.with_name("." + path.name + "." + str(os.getpid()) + ".tmp")
    mode = 0o600 if os.name != "nt" else 0o666
    fd = os.open(
        str(tmp),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
        mode,
    )
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    delay = 0.05
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 5:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.4)


def dump_json(data: dict) -> bytes:
    """Serialize JSON the same way every save does, so hashes stay stable."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def read_json(path: Path) -> dict:
    """Load a JSON object or raise a plain error.

    A save replaces the file. On Windows a reader can hit that for a moment,
    so a busy or half-written file is tried again.
    """
    delay = 0.05
    last: Exception | None = None
    for _attempt in range(8):
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except FileNotFoundError as exc:
            raise CoopError("Agent Co-op is not set up yet. Run: agentcoop setup", code=3) from exc
        except (OSError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(delay)
            delay = min(delay * 2, 0.4)
            continue
        if not isinstance(data, dict):
            raise CoopError(str(path) + " is not a JSON object. Run: agentcoop doctor")
        return data
    raise CoopError(str(path) + " could not be read. Run: agentcoop doctor") from last


def board_is_ready(board: Path) -> bool:
    """Return True when the three board JSON files exist."""
    return all((board / name).is_file() for name in ("projects.json", "agents.json", "config.json"))


def existing_install(board: Path) -> bool:
    """Return True when this folder is already an Agent Co-op install.

    bin/VERSION is written by 0.2.5 and newer. A 0.1 board has no VERSION
    file. projects.json or agents.json is enough to count as installed.
    Check again right before installing: a True result means another agent
    finished, so connect yourself and do not install a second copy.
    """
    if (board / "bin" / "VERSION").is_file():
        return True
    return (board / "projects.json").is_file() or (board / "agents.json").is_file()


def require_board(board: Path) -> None:
    """Raise exit code 3 when setup has not been run."""
    if not board_is_ready(board):
        raise CoopError("Agent Co-op is not set up yet. Run: agentcoop setup", code=3)
    ensure_good_copy(board)


def require_integrity(board: Path) -> None:
    """Raise exit code 5 when projects.json was changed outside agentcoop."""
    if not integrity_ok(board):
        raise CoopError(INTEGRITY_PAUSED, code=5)


def tighten_board_modes(board: Path) -> None:
    """On POSIX, keep board files private and commands under bin executable."""
    if os.name == "nt" or not board.exists():
        return
    bindir = board / "bin"
    for dirpath, _dirnames, filenames in os.walk(board):
        os.chmod(dirpath, 0o700)
        folder = Path(dirpath)
        for name in filenames:
            path = folder / name
            if _is_board_executable(bindir, path):
                os.chmod(path, 0o700)
            else:
                os.chmod(path, 0o600)


def _is_board_executable(bindir: Path, path: Path) -> bool:
    """Return True for files that must stay executable under bin/."""
    try:
        path.relative_to(bindir)
    except ValueError:
        return False
    if path.name == "VERSION":
        return False
    return True


def init_board_files(board: Path) -> None:
    """Create missing board files. Never rewrite files that already exist."""
    private_dir(board)
    private_dir(board / ".state")
    private_dir(board / "projects")
    private_dir(board / "bin")
    if not (board / "config.json").is_file():
        atomic_write_bytes(board / "config.json", dump_json(dict(_EMPTY_CONFIG)))
    if not (board / "agents.json").is_file():
        atomic_write_bytes(board / "agents.json", dump_json(dict(_EMPTY_AGENTS)))
    if not (board / "projects.json").is_file():
        save_projects(board, {"version": 1, "projects": {}})


def _checked(path: Path, data: dict, problems: list[str]) -> dict:
    if problems:
        raise CoopError(format_problem(path, problems[0]), code=6)
    return data


def load_projects(board: Path) -> dict:
    """Load projects.json."""
    require_board(board)
    path = board / "projects.json"
    data = read_json(path)
    return _checked(path, data, check_projects(data))


def load_agents(board: Path) -> dict:
    """Load agents.json."""
    require_board(board)
    path = board / "agents.json"
    data = read_json(path)
    return _checked(path, data, check_agents(data))


def load_config(board: Path) -> dict:
    """Load config.json."""
    require_board(board)
    path = board / "config.json"
    data = read_json(path)
    return _checked(path, data, check_config(data))


def save_config(board: Path, data: dict) -> None:
    """Write config.json."""
    atomic_write_bytes(board / "config.json", dump_json(data))


def save_agents(board: Path, data: dict) -> None:
    """Write agents.json."""
    atomic_write_bytes(board / "agents.json", dump_json(data))


def integrity_path(board: Path) -> Path:
    """Return the hash file for projects.json."""
    return board / ".state" / "projects.sha256"


def good_copy_path(board: Path) -> Path:
    """Return the last good copy of projects.json."""
    return board / ".state" / "projects.good.json"


def save_projects(board: Path, data: dict) -> None:
    """Write projects.json, refresh its integrity hash, and keep a good copy."""
    raw = dump_json(data)
    (board / ".state").mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(board / "projects.json", raw)
    digest = hashlib.sha256(raw).hexdigest() + "\n"
    atomic_write_bytes(integrity_path(board), digest.encode("ascii"))
    atomic_write_bytes(good_copy_path(board), raw)


def ensure_good_copy(board: Path) -> None:
    """Create the good copy when the hash matches and the copy is missing."""
    good = good_copy_path(board)
    if good.is_file():
        return
    if not integrity_ok(board):
        return
    path = board / "projects.json"
    if path.is_file():
        (board / ".state").mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(good, path.read_bytes())


def integrity_ok(board: Path) -> bool:
    """Return True when projects.json still matches the stored hash."""
    path = board / "projects.json"
    digest_path = integrity_path(board)
    if not path.is_file() or not digest_path.is_file():
        return False
    current = hashlib.sha256(path.read_bytes()).hexdigest()
    stored = digest_path.read_text(encoding="ascii").strip()
    return current == stored


class board_lock:
    """One writer at a time for the board settings files.

    The lock is not re-entrant. Take it only in the mutate and read helpers.
    """

    def __init__(self, board: Path, *, shared: bool = False, timeout: float = 30.0) -> None:
        self.board = board
        self.shared = shared
        self.timeout = timeout
        self._fd: int | None = None

    def __enter__(self) -> "board_lock":
        folder = self.board / ".state"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "board.lock"
        self._fd = os.open(str(path), os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
        if os.path.getsize(path) == 0:
            os.write(self._fd, b"\0")
        os.lseek(self._fd, 0, os.SEEK_SET)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._acquire()
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise CoopError(
                        "Another agentcoop command is changing the board. Try again in a moment."
                    )
                time.sleep(0.05)

    def _acquire(self) -> None:
        assert self._fd is not None
        if os.name == "nt":
            import msvcrt

            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        flags = fcntl.LOCK_NB | (fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX)
        fcntl.flock(self._fd, flags)

    def __exit__(self, *args: object) -> None:
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


def mutate_projects(
    board: Path,
    change: Callable[[dict], None],
    *,
    enforce_integrity: bool = True,
    schema_code: int = 6,
    skip_schema: bool = False,
) -> None:
    """Lock, check, edit, validate, and save projects.json."""
    with board_lock(board):
        path = board / "projects.json"
        data = read_json(path)
        if not skip_schema:
            problems = check_projects(data)
            if problems:
                raise CoopError(format_problem(path, problems[0]), code=schema_code)
        if enforce_integrity:
            require_integrity(board)
        change(data)
        validate_all(board, data)
        save_projects(board, data)


def mutate_agents(board: Path, change: Callable[[dict], None]) -> None:
    """Lock, check, edit, and save agents.json."""
    with board_lock(board):
        path = board / "agents.json"
        data = read_json(path)
        problems = check_agents(data)
        if problems:
            raise CoopError(format_problem(path, problems[0]), code=6)
        change(data)
        problems = check_agents(data)
        if problems:
            raise CoopError(format_problem(path, problems[0]), code=6)
        save_agents(board, data)


def mutate_config(board: Path, change: Callable[[dict], None]) -> None:
    """Lock, check, edit, and save config.json."""
    with board_lock(board):
        path = board / "config.json"
        data = read_json(path)
        problems = check_config(data)
        if problems:
            raise CoopError(format_problem(path, problems[0]), code=6)
        change(data)
        problems = check_config(data)
        if problems:
            raise CoopError(format_problem(path, problems[0]), code=6)
        save_config(board, data)


def read_projects_checked(board: Path) -> tuple[dict, bool]:
    """Load projects.json and its integrity result under one shared lock."""
    with board_lock(board, shared=True):
        data = load_projects(board)
        return data, integrity_ok(board)


def _members(project: dict) -> list[str]:
    helpers = project.get("helpers") or {}
    if not isinstance(helpers, dict):
        return []
    return [project.get("driver", "")] + list(helpers.keys())


def validate_project_record(board: Path, name: str, project: dict, agents: dict) -> None:
    """Raise when one project record breaks a role or path rule."""
    validate_project_name(name)
    if "drivers" in project or isinstance(project.get("driver"), list):
        raise CoopError("A project has exactly one driver.")
    driver = project.get("driver")
    if not isinstance(driver, str):
        raise CoopError("A project has exactly one driver.")
    validate_agent_name(driver)
    helpers = project.get("helpers")
    if not isinstance(helpers, dict):
        raise CoopError("Helpers must be a map of agent name to a short note.")
    if driver in helpers:
        raise CoopError(driver + " is the driver, so it cannot also be a helper.")
    known = agents.get("agents") or {}
    if driver not in known:
        raise CoopError(driver + " is not connected yet. Run: agentcoop connect " + driver)
    tool_key = (known[driver] or {}).get("tool", "")
    if tool_key == "runner":
        can_drive = False
    else:
        try:
            can_drive = tool_can_drive(tool_key)
        except KeyError:
            can_drive = False
    if not can_drive:
        raise CoopError(
            known[driver].get("display", driver)
            + " cannot be the driver. Pick an agent that edits project files."
        )
    path_raw = project.get("path")
    if not isinstance(path_raw, str) or not path_raw:
        raise CoopError("Project " + name + " needs a folder path.")
    for helper, note in helpers.items():
        validate_agent_name(helper)
        if helper not in known:
            raise CoopError(helper + " is not connected yet. Run: agentcoop agents")
        if not isinstance(note, str):
            raise CoopError("The note for " + helper + " must be text.")
    project_path = Path(path_raw)
    for other_name, other in (load_projects(board).get("projects") or {}).items():
        if other_name == name:
            continue
        other_path = other.get("path")
        if isinstance(other_path, str):
            _raise_folder_conflict(project_path, Path(other_path), other_name)


def _raise_folder_conflict(project_path: Path, other_path: Path, other_name: str) -> None:
    """Refuse nested project folders with the right message for each direction."""
    if not paths_conflict(project_path, other_path):
        return
    if project_path != other_path and is_same_or_inside(other_path, project_path):
        raise CoopError(
            "This folder contains the "
            + other_name
            + " project. Projects cannot be inside each other."
        )
    raise CoopError("This folder is already part of the " + other_name + " project.")


def validate_all(board: Path, projects: dict | None = None) -> None:
    """Validate every project against the current agent list."""
    agents = load_agents(board)
    if projects is None:
        projects = load_projects(board)
    seen: list[tuple[str, Path]] = []
    for name, project in (projects.get("projects") or {}).items():
        validate_project_record(board, name, project, agents)
        path = Path(project["path"])
        for other_name, other_path in seen:
            _raise_folder_conflict(path, other_path, other_name)
        seen.append((name, path))


def refuse_broad_folder(path: Path, board: Path) -> None:
    """Refuse a project folder that is home, a root, or the board."""
    real = path.expanduser().resolve()
    home = home_dir().resolve()
    board_real = board.expanduser().resolve()
    example = "Pick the folder of one project, for example ~/projects/frontend."
    if real == home:
        raise CoopError("Your home folder cannot be a project. " + example)
    if real == Path(real.anchor):
        raise CoopError("A filesystem root cannot be a project. " + example)
    if real != home and is_same_or_inside(home, real):
        raise CoopError("This folder contains your home folder, so it cannot be a project. " + example)
    if paths_conflict(real, board_real):
        raise CoopError("This folder contains the Agent Co-op board, so it cannot be a project.")


def add_allowed_root(board: Path, folder: str) -> None:
    """Add a folder to allowed_roots if it is not already there."""
    refuse_broad_folder(Path(folder), board)

    def change(config: dict) -> None:
        roots = list(config.get("allowed_roots") or [])
        if folder not in roots:
            roots.append(folder)
            config["allowed_roots"] = roots

    mutate_config(board, change)


def connect_agent_record(
    board: Path,
    name: str,
    tool_key: str,
    *,
    display: str | None = None,
    runner: dict | None = None,
    model: str | None = None,
    cli_path: str | None = None,
) -> None:
    """Register an agent. Does nothing destructive if it already exists."""
    validate_agent_name(name)
    tool = get_tool(tool_key)
    if not tool.supported:
        raise CoopError(tool.display + " cannot join. " + tool.notes)
    def change(agents: dict) -> None:
        current = agents["agents"].get(name)
        if current is None:
            record: dict = {
                "tool": tool_key if tool_key not in ("ollama", "lmstudio") else "runner",
                "display": display or tool.display,
                "connected": now_stamp(),
                "installed": [],
            }
            if tool_key in ("ollama", "lmstudio", "runner") or runner:
                record["tool"] = "runner"
                record["runner"] = runner or {
                    "api": "openai" if tool_key == "lmstudio" else "ollama",
                    "host": "http://localhost:1234" if tool_key == "lmstudio" else "http://localhost:11434",
                    "model": name,
                }
            if model:
                record["model"] = model
            if cli_path:
                record["cli_path"] = cli_path
            agents["agents"][name] = record
        else:
            if runner and current.get("tool") == "runner":
                current["runner"] = runner
                if display:
                    current["display"] = display
            if model:
                current["model"] = model
            if cli_path:
                current["cli_path"] = cli_path

    mutate_agents(board, change)


def _role_entry(driver: str, helpers: dict, since: str) -> dict:
    return {"since": since, "driver": driver, "helpers": sorted(helpers.keys())}


def _record_membership(project: dict, before_driver: str, before_helpers: dict) -> None:
    """Append roles_history when the driver or the helper names change."""
    after_helpers = dict(project.get("helpers") or {})
    same = before_driver == project.get("driver") and set(before_helpers) == set(after_helpers)
    if same:
        return
    history = list(project.get("roles_history") or [])
    if not history:
        history.append(_role_entry(before_driver, before_helpers, str(project.get("created") or now_stamp())))
    history.append(_role_entry(str(project.get("driver")), after_helpers, now_stamp()))
    project["roles_history"] = history


def add_project(
    board: Path,
    name: str,
    path: Path,
    driver: str,
    helpers: dict[str, str],
) -> None:
    """Add a project and create its board folders."""
    validate_project_name(name)
    record = {
        "path": str(path.expanduser().resolve()),
        "driver": driver,
        "helpers": dict(helpers),
        "created": now_stamp(),
        "installed": [],
        "roles_history": [
            {"since": "", "driver": driver, "helpers": sorted(helpers.keys())}
        ],
    }
    record["roles_history"][0]["since"] = record["created"]

    def change(projects: dict) -> None:
        if name in projects["projects"]:
            raise CoopError("A project named " + name + " already exists. Run: agentcoop project show " + name)
        projects["projects"][name] = record
        agents = load_agents(board)
        _validate_new_project(board, name, record, projects, agents)

    mutate_projects(board, change)
    add_allowed_root(board, str(Path(record["path"]).expanduser()))
    for agent in [driver, *helpers.keys()]:
        ensure_agent_dirs(board, name, agent)
    from agentcoop.statusfile import refresh_status

    refresh_status(board)


def _validate_new_project(board: Path, name: str, record: dict, projects: dict, agents: dict) -> None:
    validate_project_name(name)
    if isinstance(record.get("driver"), list) or "drivers" in record:
        raise CoopError("A project has exactly one driver.")
    driver = record.get("driver")
    if not isinstance(driver, str):
        raise CoopError("A project has exactly one driver.")
    validate_agent_name(driver)
    helpers = record.get("helpers") or {}
    if not isinstance(helpers, dict):
        raise CoopError("Helpers must be a map of agent name to a short note.")
    if driver in helpers:
        raise CoopError(driver + " is the driver, so it cannot also be a helper.")
    known = agents.get("agents") or {}
    if driver not in known:
        raise CoopError(driver + " is not connected yet. Run: agentcoop connect " + driver)
    tool_key = (known[driver] or {}).get("tool", "")
    if not tool_can_drive(tool_key):
        display = (known[driver] or {}).get("display", driver)
        raise CoopError(display + " cannot be the driver. Pick an agent that edits project files.")
    path_raw = record.get("path")
    if not isinstance(path_raw, str) or not path_raw:
        raise CoopError("Project " + name + " needs a folder path.")
    for helper, note in helpers.items():
        validate_agent_name(helper)
        if helper not in known:
            raise CoopError(helper + " is not connected yet. Run: agentcoop agents")
        if not isinstance(note, str):
            raise CoopError("The note for " + helper + " must be text.")
    project_path = Path(path_raw)
    refuse_broad_folder(project_path, board)
    for other_name, other in projects["projects"].items():
        if other_name == name:
            continue
        other_path = other.get("path")
        if isinstance(other_path, str):
            _raise_folder_conflict(project_path, Path(other_path), other_name)


def set_driver(board: Path, name: str, agent: str) -> str:
    """Change the driver. Returns the previous driver name.

    The previous driver is no longer a member unless you add them as a helper.
    A helper who becomes the driver is removed from the helper list.
    """
    found = {"previous": ""}

    def change(projects: dict) -> None:
        project = _require_project(projects, name)
        previous = project["driver"]
        before_helpers = dict(project.get("helpers") or {})
        helpers = dict(before_helpers)
        helpers.pop(agent, None)
        project["helpers"] = helpers
        project["driver"] = agent
        _record_membership(project, previous, before_helpers)
        agents = load_agents(board)
        _validate_new_project(board, name, project, projects, agents)
        found["previous"] = previous

    mutate_projects(board, change)
    ensure_agent_dirs(board, name, agent)
    from agentcoop.statusfile import refresh_status

    refresh_status(board)
    return found["previous"]


def add_helper(board: Path, name: str, agent: str, note: str) -> None:
    """Add or update a helper note."""

    def change(projects: dict) -> None:
        project = _require_project(projects, name)
        if agent == project["driver"]:
            raise CoopError(agent + " is the driver, so it cannot also be a helper.")
        before_driver = project["driver"]
        before_helpers = dict(project.get("helpers") or {})
        helpers = dict(before_helpers)
        helpers[agent] = note
        project["helpers"] = helpers
        if agent not in before_helpers:
            _record_membership(project, before_driver, before_helpers)
            notices = [item for item in list(project.get("notices") or []) if isinstance(item, str)]
            notices.append(agent + " joined as a helper.")
            project["notices"] = notices
        agents = load_agents(board)
        _validate_new_project(board, name, project, projects, agents)

    mutate_projects(board, change)
    ensure_agent_dirs(board, name, agent)
    from agentcoop.statusfile import refresh_status

    refresh_status(board)


def remove_helper(board: Path, name: str, agent: str) -> None:
    """Remove a helper from a project."""

    def change(projects: dict) -> None:
        project = _require_project(projects, name)
        before_driver = project["driver"]
        before_helpers = dict(project.get("helpers") or {})
        if agent not in before_helpers:
            raise CoopError(agent + " is not a helper on " + name + ".")
        helpers = dict(before_helpers)
        helpers.pop(agent)
        project["helpers"] = helpers
        _record_membership(project, before_driver, before_helpers)
        notices = [item for item in list(project.get("notices") or []) if isinstance(item, str)]
        notices.append(agent + " left as a helper.")
        project["notices"] = notices

    mutate_projects(board, change)
    from agentcoop.statusfile import refresh_status

    refresh_status(board)


def clear_notices(board: Path, name: str) -> None:
    """Drop join and leave lines after the driver has seen them."""

    def change(projects: dict) -> None:
        project = (projects.get("projects") or {}).get(name)
        if project is not None and project.get("notices"):
            project["notices"] = []

    if not ((load_projects(board).get("projects") or {}).get(name) or {}).get("notices"):
        return
    mutate_projects(board, change)


def projects_for_agent(projects: dict, agent: str | None) -> list[str]:
    """List project names an agent belongs to. None lists every project."""
    names = []
    for name, project in (projects.get("projects") or {}).items():
        if agent is None or agent == project.get("driver") or agent in (project.get("helpers") or {}):
            names.append(name)
    return names


def resolve_project(board: Path, *, explicit: str | None, cwd: Path | None, agent: str | None) -> str:
    """Pick the project for this command."""
    projects = load_projects(board)
    table = projects.get("projects") or {}
    if explicit:
        if explicit not in table:
            raise CoopError(
                "There is no project named " + explicit + ". Run: agentcoop project list"
            )
        return explicit
    here = (cwd or Path.cwd()).expanduser().resolve()
    matches = []
    for name, project in table.items():
        raw = project.get("path")
        if not isinstance(raw, str):
            continue
        if paths_conflict(here, Path(raw)) and is_within_project(here, Path(raw)):
            matches.append((name, Path(raw)))
    if len(matches) == 1:
        return matches[0][0]
    if len(matches) > 1:
        matches.sort(key=lambda item: len(item[1].parts), reverse=True)
        return matches[0][0]
    belonging = projects_for_agent(projects, agent)
    lines = "\n".join("  " + name for name in belonging) or "  (none)"
    raise CoopError(
        "Agent Co-op could not tell which project this folder belongs to.\n"
        "Projects you can use:\n"
        + lines
        + "\nRun this from inside the project folder, or add --project NAME",
        code=4,
    )


def is_within_project(child: Path, project_path: Path) -> bool:
    """Return True when child is the project folder or inside it.

    paths_conflict is symmetric, so a parent of the project must not count
    as being inside the project.
    """
    from agentcoop.paths import is_same_or_inside

    return is_same_or_inside(child, project_path)


def _require_project(projects: dict, name: str) -> dict:
    table = projects.get("projects") or {}
    if name not in table:
        raise CoopError("There is no project named " + name + ". Run: agentcoop project list")
    return table[name]


def role_of(project: dict, agent: str) -> str:
    """Return driver, helper, or an empty string."""
    if project.get("driver") == agent:
        return "driver"
    if agent in (project.get("helpers") or {}):
        return "helper"
    return ""


def record_installed(entries: list, entry: dict) -> None:
    """Replace a manifest row for the same path, or append it."""
    path = entry.get("path")
    entries[:] = [item for item in entries if item.get("path") != path]
    entries.append(entry)
