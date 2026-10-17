"""Launcher, command wrapper, and PATH changes for agentcoop."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import home_dir, quote_path
from agentcoop.projects import load_config, mutate_config

MARKER_START = "# agentcoop:start"
MARKER_END = "# agentcoop:end"
WINDOWS_PATH_KEY = "windows_user_path"


def pick_launcher(
    *,
    which=None,
    name: str | None = None,
    executable: str | None = None,
) -> str:
    """Return an absolute Python launcher path for the zipapp."""
    which_fn = which or shutil.which
    platform = name if name is not None else os.name
    if platform == "nt":
        names = ("py", "python", "python3")
    else:
        names = ("python3", "python")
    for candidate in names:
        found = which_fn(candidate)
        if found:
            path = Path(found)
            try:
                return str(path.resolve())
            except OSError:
                return str(path)
    exe = executable if executable is not None else sys.executable
    try:
        return str(Path(exe).resolve())
    except OSError:
        return str(exe)


def write_command_wrapper(board: Path, launcher: str) -> Path | None:
    """Write bin/agentcoop or bin/agentcoop.cmd for a zipapp install."""
    if getattr(sys, "frozen", False):
        return None
    bindir = board / "bin"
    pyz = bindir / "agentcoop.pyz"
    if not pyz.is_file():
        return None
    if os.name == "nt":
        path = bindir / "agentcoop.cmd"
        line = '@"' + launcher + '" "' + str(pyz) + '" %*\r\n'
        path.write_bytes(line.encode("utf-8"))
        return path
    path = bindir / "agentcoop"
    path.write_text(
        "#!/bin/sh\n" + 'exec "' + launcher + '" "' + str(pyz) + '" "$@"\n',
        encoding="utf-8",
    )
    os.chmod(path, 0o700)
    return path


def command_argv(board: Path) -> list[str]:
    """Return the argv that runs the installed agentcoop command."""
    bindir = board / "bin"
    if os.name == "nt":
        exe = bindir / "agentcoop.exe"
        if exe.is_file():
            return [str(exe)]
        cmd = bindir / "agentcoop.cmd"
        if cmd.is_file():
            return [str(cmd)]
    else:
        binary = bindir / "agentcoop"
        if binary.is_file():
            return [str(binary)]
    pyz = bindir / "agentcoop.pyz"
    launcher = pick_launcher()
    try:
        from agentcoop.projects import board_is_ready

        if board_is_ready(board):
            stored = load_config(board).get("launcher")
            if isinstance(stored, str) and stored:
                launcher = stored
    except (OSError, CoopError):
        pass
    return [launcher, str(pyz)]


def agentcoop_command(board: Path) -> str:
    """Return the quoted absolute path of the wrapper or binary."""
    bindir = board / "bin"
    if os.name == "nt":
        exe = bindir / "agentcoop.exe"
        if exe.is_file():
            return quote_path(str(exe))
        cmd = bindir / "agentcoop.cmd"
        if cmd.is_file():
            return quote_path(str(cmd))
    else:
        binary = bindir / "agentcoop"
        if binary.is_file():
            return quote_path(str(binary))
    pyz = bindir / "agentcoop.pyz"
    launcher = pick_launcher()
    config = None
    try:
        from agentcoop.projects import board_is_ready

        if board_is_ready(board):
            config = load_config(board)
            stored = config.get("launcher")
            if isinstance(stored, str) and stored:
                launcher = stored
    except (OSError, CoopError):
        pass
    return quote_path(launcher) + " " + quote_path(str(pyz))


def store_launcher(board: Path, launcher: str) -> None:
    """Save the chosen launcher in config.json."""

    def change(data: dict) -> None:
        data["launcher"] = launcher

    mutate_config(board, change)


def apply_path_change(board: Path, enabled: bool) -> list[str]:
    """Add board/bin to PATH when enabled. Return the records stored in config."""
    if not enabled:
        return []
    if os.name == "nt":
        changed = _windows_add_path(board)
    else:
        changed = _posix_add_path(board)
    if changed:
        _record_path_changes(board, changed)
        print("Open a new terminal, then type agentcoop to use it.")
    return changed


def undo_path_changes(board: Path) -> None:
    """Remove PATH changes recorded in config.json."""
    try:
        config = load_config(board)
    except CoopError:
        return
    changes = config.get("path_changes") or []
    if not isinstance(changes, list):
        return
    for item in changes:
        if item == WINDOWS_PATH_KEY:
            _windows_remove_path(board)
            continue
        if isinstance(item, str):
            _posix_remove_block(Path(item))

    def change(data: dict) -> None:
        data.pop("path_changes", None)

    try:
        mutate_config(board, change)
    except CoopError:
        return


def path_notes_for_doctor(board: Path) -> list[str]:
    """Return doctor Note lines for missing PATH blocks or a missing launcher."""
    notes: list[str] = []
    try:
        config = load_config(board)
    except CoopError:
        return notes
    launcher = config.get("launcher")
    if isinstance(launcher, str) and launcher and not Path(launcher).is_file():
        notes.append(
            "Fix: Agent Co-op cannot find Python ("
            + launcher
            + "). Run: agentcoop setup"
        )
    changes = config.get("path_changes") or []
    if not isinstance(changes, list):
        return notes
    for item in changes:
        if item == WINDOWS_PATH_KEY:
            continue
        if isinstance(item, str):
            path = Path(item)
            if not path.is_file() or MARKER_START not in path.read_text(encoding="utf-8"):
                notes.append("Note: the PATH block is missing from " + str(path))
    return notes


def _record_path_changes(board: Path, changed: list[str]) -> None:
    def change(data: dict) -> None:
        existing = list(data.get("path_changes") or [])
        for item in changed:
            if item not in existing:
                existing.append(item)
        data["path_changes"] = existing

    mutate_config(board, change)


def _posix_block(board: Path) -> str:
    return (
        MARKER_START
        + "\n"
        + 'export PATH="'
        + str(board / "bin")
        + ':$PATH"\n'
        + MARKER_END
        + "\n"
    )


def _posix_add_path(board: Path) -> list[str]:
    shell = os.environ.get("SHELL", "")
    if shell.endswith("fish"):
        print(
            'Add this line to your fish config: set -gx PATH "'
            + str(board / "bin")
            + '" $PATH'
        )
        return []
    home = home_dir()
    names = (".zshrc", ".bashrc", ".bash_profile", ".profile")
    targets = [home / name for name in names if (home / name).is_file()]
    if not targets:
        if shell.endswith("zsh"):
            targets = [home / ".zshrc"]
        else:
            targets = [home / ".profile"]
    block = _posix_block(board)
    changed: list[str] = []
    for path in targets:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if MARKER_START in text:
            continue
        if text and not text.endswith("\n"):
            text += "\n"
        path.write_text(text + block, encoding="utf-8")
        changed.append(str(path))
    return changed


def _posix_remove_block(path: Path) -> None:
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    start = text.find(MARKER_START)
    if start < 0:
        return
    end = text.find(MARKER_END, start)
    if end < 0:
        return
    end = end + len(MARKER_END)
    if end < len(text) and text[end] == "\n":
        end += 1
    path.write_text(text[:start] + text[end:], encoding="utf-8")


def _windows_add_path(board: Path, *, winreg_module=None) -> list[str]:
    bindir = str(board / "bin")
    winreg = winreg_module
    if winreg is None:
        import winreg as winreg  # type: ignore[import-not-found]
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Environment",
        0,
        winreg.KEY_READ | winreg.KEY_SET_VALUE,
    )
    try:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, value_type = "", winreg.REG_EXPAND_SZ
        parts = [part for part in str(current).split(";") if part]
        if any(Path(part) == Path(bindir) for part in parts):
            return []
        parts.append(bindir)
        winreg.SetValueEx(key, "Path", 0, value_type, ";".join(parts))
    finally:
        winreg.CloseKey(key)
    _broadcast_environment()
    return [WINDOWS_PATH_KEY]


def _windows_remove_path(board: Path, *, winreg_module=None) -> None:
    bindir = str(board / "bin")
    winreg = winreg_module
    if winreg is None:
        try:
            import winreg as winreg  # type: ignore[import-not-found]
        except ImportError:
            return
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Environment",
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        )
    except OSError:
        return
    try:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return
        parts = [
            part
            for part in str(current).split(";")
            if part and Path(part) != Path(bindir)
        ]
        winreg.SetValueEx(key, "Path", 0, value_type, ";".join(parts))
    finally:
        winreg.CloseKey(key)
    _broadcast_environment()


def _broadcast_environment() -> None:
    try:
        import ctypes
    except ImportError:
        return
    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002
    result = ctypes.c_ulong()
    try:
        ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )
    except AttributeError:
        return
