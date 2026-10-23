"""Install a published Agent Co-op version, or go back to the previous one."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from agentcoop import __version__
from agentcoop.errors import CoopError
from agentcoop.migrate import apply_migrations, assert_downgrade_allowed
from agentcoop.projects import board_is_ready, load_config, require_board
from agentcoop.releases import (
    RateLimitError,
    Release,
    download_named,
    fetch_changelog,
    fetch_releases,
    fetch_tag,
    headings_between,
    http_get,
    require_hash,
    stable_releases,
)
from agentcoop.version import (
    has_update_command,
    installed_kind,
    no_update_line,
    parse_version,
    read_installed,
    read_installed_version,
    tag_name,
    this_computer_asset,
    version_text,
)

# setup flags checked on tags v0.2.0, v0.2.1, v0.2.2, v0.2.3, and v0.2.4.
# Each accepts --detect, --json, --connect, --yes, --model,
# --add-to-path, --schedule, and --every. None of them accept --version.
LEGACY_SETUP_FLAGS = (
    "--detect",
    "--json",
    "--connect",
    "--yes",
    "--model",
    "--add-to-path",
    "--schedule",
    "--every",
)
LOCK_WAIT_SECONDS = 120
BUSY_LINE = "A background run is still going. Try again later."


def cleanup_old_binaries(board: Path) -> None:
    """Delete bin files renamed aside on Windows during the last update."""
    bindir = board / "bin"
    if not bindir.is_dir():
        return
    for path in bindir.glob("*.old"):
        try:
            path.unlink()
        except OSError:
            pass


def place_file(src: Path, dest: Path, *, windows: bool | None = None) -> None:
    """Copy src onto dest. On Windows, rename a running file to name.old first."""
    win = os.name == "nt" if windows is None else windows
    dest.parent.mkdir(parents=True, exist_ok=True)
    if win and dest.exists():
        old = dest.with_name(dest.name + ".old")
        if old.exists():
            try:
                old.unlink()
            except OSError:
                pass
        os.replace(dest, old)
    elif dest.exists():
        dest.unlink()
    shutil.copy2(src, dest)
    if not win:
        dest.chmod(0o700)


def _snapshot(bindir: Path) -> Path:
    temp = Path(tempfile.mkdtemp(prefix="agentcoop-bin-"))
    if not bindir.is_dir():
        return temp
    for path in bindir.iterdir():
        target = temp / path.name
        if path.is_dir():
            shutil.copytree(path, target)
        elif path.is_file():
            shutil.copy2(path, target)
    return temp


def _restore(bindir: Path, snap: Path) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    for path in list(bindir.iterdir()):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for path in snap.iterdir():
        target = bindir / path.name
        if path.is_dir():
            shutil.copytree(path, target)
        elif path.is_file():
            shutil.copy2(path, target)


def remember_previous(board: Path, old_version: str) -> None:
    """Keep a copy of the current bin files. Only the last two versions stay."""
    bindir = board / "bin"
    root = bindir / "previous"
    root.mkdir(parents=True, exist_ok=True)
    dest = root / old_version
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir()
    for path in bindir.iterdir():
        if path.name == "previous" or path.name.endswith(".old"):
            continue
        target = dest / path.name
        if path.is_dir():
            shutil.copytree(path, target)
        elif path.is_file():
            shutil.copy2(path, target)
    order_path = root / "order.txt"
    current: list[str] = []
    if order_path.is_file():
        current = [line.strip() for line in order_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    current = [old_version] + [item for item in current if item != old_version]
    keep = current[:2]
    for item in current[2:]:
        folder = root / item
        if folder.is_dir():
            shutil.rmtree(folder)
    order_path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    (root / "last").write_text(old_version + "\n", encoding="utf-8")


def previous_version_name(board: Path) -> str:
    """Return the version saved by the last update."""
    last = board / "bin" / "previous" / "last"
    if not last.is_file():
        raise CoopError("There is no previous version to go back to.", code=1)
    name = last.read_text(encoding="utf-8").strip()
    folder = board / "bin" / "previous" / name
    if not name or not folder.is_dir():
        raise CoopError("There is no previous version to go back to.", code=1)
    return name


def _wait_for_runs(board: Path, timeout: float) -> object:
    """Hold the tick lock so a background round cannot start. Wait up to 2 minutes."""
    from agentcoop.rounds import _lock_held, _try_lock

    state = board / ".state"
    state.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        fd = _try_lock(state / "tick.lock")
        busy = False
        if fd is not None:
            for path in state.glob("run-*.lock"):
                if _lock_held(path):
                    busy = True
                    break
        if fd is not None and not busy:
            return fd
        if fd is not None:
            from agentcoop.rounds import _unlock

            _unlock(fd)
        if time.monotonic() >= deadline:
            raise CoopError(BUSY_LINE, code=1)
        time.sleep(0.2)


def _unlock(fd: object) -> None:
    from agentcoop.rounds import _unlock as release

    release(fd)


def program_argv(path: Path) -> list[str]:
    """Return the argv that runs a binary, a Windows zip member, or a zipapp."""
    if path.suffix.lower() == ".pyz":
        from agentcoop.pathenv import pick_launcher

        return [pick_launcher(), str(path)]
    return [str(path)]


def probe_version(path: Path) -> tuple[int, int, int]:
    """Run a program's version command."""
    completed = subprocess.run(
        program_argv(path) + ["version"],
        capture_output=True,
        text=True,
    )
    for line in (completed.stdout or "").splitlines():
        if line.startswith("agentcoop "):
            return parse_version(line.split(None, 1)[1])
    raise CoopError("Could not read a version from " + path.name + ".", code=1)


def confirm_downgrade(version: tuple[int, int, int], *, asked_by: str = "") -> None:
    """Ask the person before every agent on this computer moves to an older version."""
    from agentcoop.confirm import confirm_person

    text = version_text(version)
    summary = "This will install Agent Co-op " + text + " for every agent on this computer."
    confirm_person(summary, expected=text, kind="downgrade", asked_by=asked_by)


def _guard_downgrade(board: Path, target: tuple[int, int, int], installed: tuple[int, int, int]) -> None:
    if target >= installed:
        return
    assert_downgrade_allowed(board, target)
    from agentcoop.compat import loss_lines

    for line in loss_lines(installed, target):
        print(line)
    if not has_update_command(target):
        print(no_update_line(target))
    confirm_downgrade(target)


def _write_version(board: Path, version: str) -> None:
    path = board / "bin" / "VERSION"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(version + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def _place_named(board: Path, files: dict[str, Path], *, windows: bool | None = None) -> None:
    bindir = board / "bin"
    names = set(files)
    if "agentcoop.pyz" in names:
        for extra in ("agentcoop.exe", "agentcoopw.exe"):
            path = bindir / extra
            if path.is_file():
                path.unlink()
    if "agentcoop.exe" in names or ("agentcoop" in names and "agentcoop.pyz" not in names):
        pyz = bindir / "agentcoop.pyz"
        if pyz.is_file():
            pyz.unlink()
    for name, src in files.items():
        place_file(src, bindir / name, windows=windows)
    if "agentcoop.pyz" in names:
        from agentcoop.pathenv import pick_launcher, store_launcher, write_command_wrapper

        launcher = pick_launcher()
        if board_is_ready(board):
            store_launcher(board, launcher)
        write_command_wrapper(board, launcher)
        if os.name != "nt":
            (bindir / "agentcoop.pyz").chmod(0o700)


def _finish_board(board: Path, new_version: str, old_version: str) -> None:
    """Migrations, installed files, the schedule, and the desktop skill."""
    apply_migrations(board)
    from agentcoop.install import install_project_files, project_instruction_agents, refresh_installed_files
    from agentcoop.projects import load_projects

    left = refresh_installed_files(board)
    for project_name, project in (load_projects(board).get("projects") or {}).items():
        for agent in project_instruction_agents(board, project):
            install_project_files(
                board,
                project_name,
                agent,
                yes=True,
                gitignore=True,
                only_missing=True,
            )
    from agentcoop.rounds import refresh_schedule_command

    refresh_schedule_command(board)
    desktop = _refresh_desktop_skill(board, new_version)
    headings: list[str] = []
    if os.environ.get("AGENTCOOP_TEST_OFFLINE") != "1":
        try:
            text = fetch_changelog(parse_version(new_version), timeout=5)
            headings = headings_between(text, parse_version(old_version), parse_version(new_version))
        except Exception:
            headings = []
    print("Updated Agent Co-op from " + old_version + " to " + new_version + ".")
    for line in headings:
        print(line)
    print("Refreshed skills, rules, and hooks.")
    if left:
        print("Files you changed, left in place:")
        for path in left:
            print("  " + path)
    if desktop:
        print(
            "The Claude desktop skill changed in "
            + new_version
            + ". In the Claude desktop app, say: Update Agent Co-op."
        )
    else:
        print("The Claude desktop skill did not change.")


def _refresh_desktop_skill(board: Path, version: str) -> bool:
    """Write a new skill zip when its text changed. Return True when it did."""
    from agentcoop.desktop import general_skill_text, prepare_skill
    from agentcoop.updatecheck import clear_desktop_skill, mark_desktop_skill

    new_text = general_skill_text()
    if not new_text.endswith("\n"):
        new_text += "\n"
    zip_path = board / "exports" / "agent-coop.zip"
    old_text = ""
    if zip_path.is_file():
        try:
            with zipfile.ZipFile(zip_path) as archive:
                old_text = archive.read("agent-coop/SKILL.md").decode("utf-8")
        except (OSError, KeyError, zipfile.BadZipFile, UnicodeError):
            old_text = ""
    if old_text == new_text:
        clear_desktop_skill(board)
        return False
    prepare_skill(board)
    mark_desktop_skill(board, version)
    from agentcoop.statusfile import refresh_status

    refresh_status(board)
    return True


def install_runtime(
    board: Path,
    files: dict[str, Path],
    version: str,
    *,
    fail_after: str = "",
    windows: bool | None = None,
    finish: bool = True,
) -> None:
    """Replace bin files. On a failure after they are in place, put the old files back."""
    require_board(board)
    bindir = board / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    old_version = read_installed(board)
    snap = _snapshot(bindir)
    fd = None
    try:
        fd = _wait_for_runs(board, LOCK_WAIT_SECONDS)
        if any(path.is_file() for path in bindir.iterdir() if path.name != "previous"):
            remember_previous(board, old_version)
        _place_named(board, files, windows=windows)
        if fail_after == "placed":
            raise CoopError("The new files could not be saved.")
        _write_version(board, version)
        if finish:
            _finish_board(board, version, old_version)
    except Exception as exc:
        try:
            _restore(bindir, snap)
        except OSError:
            pass
        if isinstance(exc, CoopError):
            message = str(exc)
            if "put back" not in message and "Nothing was changed" not in message:
                message = message + " The old files were put back."
            raise CoopError(message, code=exc.code or 1) from exc
        raise CoopError(
            "The update failed (" + type(exc).__name__ + "). The old files were put back.",
            code=1,
        ) from exc
    finally:
        if fd is not None:
            _unlock(fd)
        shutil.rmtree(snap, ignore_errors=True)


def install_this(board: Path | None = None, *, fail_after: str = "") -> int:
    """Install the program that is running this command."""
    from agentcoop.paths import board_root

    target = board or board_root()
    require_board(target)
    bindir = target / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    old_version = read_installed(target)
    snap = _snapshot(bindir)
    fd = None
    try:
        fd = _wait_for_runs(target, LOCK_WAIT_SECONDS)
        remember_previous(target, old_version)
        from agentcoop.install import stage_runtime

        stage_runtime(target)
        if fail_after == "placed":
            raise CoopError("The new files could not be saved.")
        _write_version(target, __version__)
        _finish_board(target, __version__, old_version)
    except Exception as exc:
        try:
            _restore(bindir, snap)
        except OSError:
            pass
        if isinstance(exc, CoopError):
            message = str(exc)
            if "put back" not in message:
                message = message + " The old files were put back."
            raise CoopError(message, code=exc.code or 1) from exc
        raise CoopError(
            "The update failed (" + type(exc).__name__ + "). The old files were put back.",
            code=1,
        ) from exc
    finally:
        if fd is not None:
            _unlock(fd)
        shutil.rmtree(snap, ignore_errors=True)
    return 0


def _find_sums(path: Path) -> Path | None:
    for folder in (path.parent, path.parent.parent):
        candidate = folder / "SHA256SUMS"
        if candidate.is_file():
            return candidate
    return None


def _extract_zip(zip_path: Path, dest: Path) -> dict[str, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as archive:
        names = [item for item in archive.namelist() if not item.endswith("/")]
        for needed in ("agentcoop.exe", "agentcoopw.exe"):
            matches = [item for item in names if Path(item).name == needed]
            if not matches:
                raise CoopError("The Windows zip must contain " + needed + ". Nothing was changed.", code=1)
            target = dest / needed
            target.write_bytes(archive.read(matches[0]))
            found[needed] = target
    return found


def _runtime_map(path: Path, folder: Path) -> dict[str, Path]:
    """Return the bin names for a downloaded file."""
    if path.suffix.lower() == ".zip":
        return _extract_zip(path, folder)
    if path.suffix.lower() == ".pyz":
        return {"agentcoop.pyz": path}
    if path.name.lower() == "agentcoop.exe":
        files = {"agentcoop.exe": path}
        sibling = path.with_name("agentcoopw.exe")
        if sibling.is_file():
            files["agentcoopw.exe"] = sibling
        return files
    return {"agentcoop": path}


def _run_checked(argv: list[str]) -> int:
    completed = subprocess.run(argv)
    return int(completed.returncode)


def legacy_setup_argv(program: list[str], board: Path) -> list[str]:
    """Build setup argv using only flags those old releases accept."""
    from agentcoop.rounds import load_schedule

    record = load_schedule(board) if board_is_ready(board) else None
    schedule = "yes" if record else "no"
    every = "5"
    if record and record.get("interval"):
        every = str(int(record["interval"]))
    on_path = "no"
    if board_is_ready(board):
        changes = load_config(board).get("path_changes")
        if isinstance(changes, list) and changes:
            on_path = "yes"
    return program + [
        "setup",
        "--yes",
        "--add-to-path",
        on_path,
        "--schedule",
        schedule,
        "--every",
        every,
    ]


def _run_legacy_setup(program: Path, board: Path) -> None:
    code = _run_checked(legacy_setup_argv(program_argv(program), board))
    if code != 0:
        raise CoopError("The older setup command failed. The old files were put back.", code=code or 1)


def install_from_file(board: Path, raw_path: str) -> int:
    """Install a file that was already downloaded. SHA256SUMS must sit beside it."""
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise CoopError("There is no file at " + str(path) + ".", code=1)
    sums = _find_sums(path)
    if sums is None:
        raise CoopError("SHA256SUMS is missing. Nothing was changed.", code=1)
    require_hash(path, sums.read_text(encoding="utf-8"))
    folder = Path(tempfile.mkdtemp(prefix="agentcoop-file-"))
    try:
        files = _runtime_map(path, folder)
        probe = next(iter(files.values()))
        if path.suffix.lower() == ".zip":
            probe = files["agentcoop.exe"]
        try:
            version = probe_version(probe)
        except CoopError:
            version = None
        if version is not None and has_update_command(version):
            return _run_checked(program_argv(probe) + ["update", "--install-this"])
        if version is None:
            raise CoopError("Could not read a version from " + path.name + ". Nothing was changed.", code=1)
        if board_is_ready(board):
            _guard_downgrade(board, version, read_installed_version(board))
            install_runtime(board, files, version_text(version), finish=False)
            _run_legacy_setup(probe if probe.suffix.lower() != ".pyz" else files.get("agentcoop.pyz", probe), board)
            return 0
        raise CoopError("Agent Co-op is not set up yet. Run: agentcoop setup", code=3)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _program_in(folder: Path) -> Path:
    for name in ("agentcoop.exe", "agentcoop", "agentcoop.pyz"):
        path = folder / name
        if path.is_file():
            return path
    raise CoopError("The previous version has no program to run.", code=1)


def rollback(board: Path) -> int:
    """Go back to the version kept from the last update."""
    require_board(board)
    name = previous_version_name(board)
    target = parse_version(name)
    installed = read_installed_version(board)
    _guard_downgrade(board, target, installed if target < installed else target)
    # _guard compares target >= installed and returns. Force the warning and confirm.
    if target >= installed:
        if not has_update_command(target):
            print(no_update_line(target))
        confirm_downgrade(target)
    folder = board / "bin" / "previous" / name
    program = _program_in(folder)
    if has_update_command(target):
        return _run_checked(program_argv(program) + ["update", "--install-this"])
    files = {}
    for path in folder.iterdir():
        if path.is_file() and path.name != "VERSION":
            files[path.name] = path
    install_runtime(board, files, name, finish=False)
    _run_legacy_setup(program, board)
    print("Updated Agent Co-op from " + version_text(installed) + " to " + name + ".")
    return 0


def _download_release(release: Release, kind: str, fetch, folder: Path) -> dict[str, Path]:
    name = this_computer_asset(kind)
    if "SHA256SUMS" not in release.assets:
        raise CoopError("SHA256SUMS is missing. Nothing was changed.", code=1)
    asset = download_named(release, name, folder / name, fetch=fetch)
    sums = download_named(release, "SHA256SUMS", folder / "SHA256SUMS", fetch=fetch)
    require_hash(asset, sums.read_text(encoding="utf-8"))
    if name.endswith(".zip"):
        return _extract_zip(asset, folder / "unpacked")
    if name.endswith(".pyz"):
        return {"agentcoop.pyz": asset}
    return {"agentcoop": asset}


def install_release(board: Path, release: Release, *, fetch=None, kind: str | None = None) -> int:
    """Download one release and install it."""
    require_board(board)
    installed = read_installed_version(board)
    _guard_downgrade(board, release.version, installed)
    chosen = kind or installed_kind(board)
    get = fetch or http_get
    folder = Path(tempfile.mkdtemp(prefix="agentcoop-update-"))
    try:
        files = _download_release(release, chosen, get, folder)
        probe = files.get("agentcoop.exe") or files.get("agentcoop") or files.get("agentcoop.pyz")
        if probe is None:
            raise CoopError("The release has no program to install. Nothing was changed.", code=1)
        if has_update_command(release.version):
            return _run_checked(program_argv(probe) + ["update", "--install-this"])
        install_runtime(board, files, release.text, finish=False)
        _run_legacy_setup(probe, board)
        print("Updated Agent Co-op from " + version_text(installed) + " to " + release.text + ".")
        return 0
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _resolve_release(text: str, fetch) -> Release:
    version = parse_version(text)
    try:
        return fetch_tag(version, fetch=fetch)
    except CoopError as exc:
        if "not a published" in str(exc) or "no file" in str(exc) or "GitHub returned 404" in str(exc):
            _print_missing(fetch)
        raise


def _print_missing(fetch) -> None:
    try:
        releases = stable_releases(fetch_releases(fetch=fetch))
    except CoopError:
        return
    if not releases:
        return
    print("Published versions:")
    for item in releases:
        print("  " + item.text)


def print_check(board: Path, fetch=None) -> int:
    """Print the installed version, the newest version, and headings in between."""
    require_board(board)
    installed = read_installed_version(board)
    print("Installed: " + version_text(installed))
    get = fetch or http_get
    try:
        releases = stable_releases(fetch_releases(fetch=get))
    except RateLimitError as exc:
        print(str(exc))
        return 0
    if not releases:
        print("Newest: unknown")
        return 0
    newest = releases[0]
    print("Newest: " + newest.text)
    if newest.version > installed:
        for line in headings_between(fetch_changelog(newest.version, fetch=get), installed, newest.version):
            print(line)
    return 0


def print_list(board: Path, fetch=None) -> int:
    """List published versions, newest first, and mark the installed one."""
    require_board(board)
    installed = read_installed_version(board)
    get = fetch or http_get
    try:
        releases = stable_releases(fetch_releases(fetch=get))
    except RateLimitError as exc:
        print(str(exc))
        return 0
    if not releases:
        print("GitHub has no published version.")
        return 0
    for item in releases:
        mark = "  installed" if item.version == installed else ""
        print(item.text + mark)
    return 0


def update_to_newest(board: Path, fetch=None) -> int:
    """Install the newest stable release."""
    require_board(board)
    get = fetch or http_get
    releases = stable_releases(fetch_releases(fetch=get))
    if not releases:
        raise CoopError("GitHub has no published version.", code=1)
    return install_release(board, releases[0], fetch=get)


def update_to_version(board: Path, text: str, fetch=None) -> int:
    """Install one published version, newer or older."""
    require_board(board)
    get = fetch or http_get
    try:
        release = fetch_tag(parse_version(text), fetch=get)
    except CoopError as exc:
        if isinstance(exc, RateLimitError):
            raise
        _print_missing(get)
        raise CoopError(text.lstrip("v") + " is not a published version.", code=1) from exc
    if release.prerelease:
        raise CoopError(release.text + " is a pre-release. Nothing was changed.", code=1)
    return install_release(board, release, fetch=get)


def forward_setup_argv(program: list[str], args: object) -> list[str]:
    """Pass setup flags that every published release accepts. Never pass --version."""
    argv = list(program) + ["setup"]
    if getattr(args, "detect", False):
        argv.append("--detect")
    if getattr(args, "json", False):
        argv.append("--json")
    connect = getattr(args, "connect", "") or ""
    if connect:
        argv += ["--connect", connect]
    if getattr(args, "yes", False):
        argv.append("--yes")
    model = getattr(args, "model", "") or ""
    if model:
        argv += ["--model", model]
    argv += ["--add-to-path", getattr(args, "add_to_path", "no") or "no"]
    argv += ["--schedule", getattr(args, "schedule", "yes") or "yes"]
    argv += ["--every", str(getattr(args, "every", 5))]
    return argv


def setup_at_version(args: object, fetch=None) -> int:
    """Install the named release. An existing board is updated. An empty one is set up."""
    from agentcoop.paths import board_root

    text = str(getattr(args, "setup_version", "") or "")
    get = fetch or http_get
    try:
        version = parse_version(text)
    except CoopError:
        raise
    try:
        release = fetch_tag(version, fetch=get)
    except RateLimitError:
        raise
    except CoopError as exc:
        _print_missing(get)
        raise CoopError(version_text(version) + " is not a published version.", code=1) from exc
    board = board_root()
    if board_is_ready(board):
        return install_release(board, release, fetch=get)
    if not has_update_command(release.version):
        print(no_update_line(release.version))
    folder = Path(tempfile.mkdtemp(prefix="agentcoop-setup-"))
    try:
        files = _download_release(release, "zipapp" if _prefer_zipapp() else "binary", get, folder)
        probe = files.get("agentcoop.exe") or files.get("agentcoop") or files.get("agentcoop.pyz")
        if probe is None:
            raise CoopError("The release has no program to install. Nothing was changed.", code=1)
        return _run_checked(forward_setup_argv(program_argv(probe), args))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _prefer_zipapp() -> bool:
    """A source run installs the zipapp. A frozen build installs a binary."""
    return not getattr(sys, "frozen", False)


def run_update(args: object) -> int:
    """Dispatch one update command."""
    from agentcoop.paths import board_root
    from agentcoop.updatecheck import set_update_checks

    chosen = [
        bool(getattr(args, "check", False)),
        bool(getattr(args, "list_versions", False)),
        bool(getattr(args, "rollback", False)),
        bool(getattr(args, "from_file", "")),
        bool(getattr(args, "install_this", False)),
        bool(getattr(args, "notify", "")),
        bool(getattr(args, "to_version", "")),
    ]
    if sum(1 for item in chosen if item) > 1:
        raise CoopError(
            "Use one of: update, update --to, update --check, update --list, "
            "update --rollback, update --from-file, update --install-this, "
            "or update --notify.",
            code=2,
        )
    board = board_root()
    if args.install_this:
        return install_this(board)
    require_board(board)
    if args.notify:
        print(set_update_checks(board, args.notify == "on"))
        return 0
    if args.check:
        return print_check(board)
    if args.list_versions:
        return print_list(board)
    if args.rollback:
        return rollback(board)
    if args.from_file:
        return install_from_file(board, args.from_file)
    if args.to_version:
        return update_to_version(board, args.to_version)
    return update_to_newest(board)
