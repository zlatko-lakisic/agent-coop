"""Version numbers, comparisons, and release asset names."""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path

from agentcoop.errors import CoopError

# Published releases before this one have no update command.
UPDATE_COMMAND_VERSION = (0, 2, 5)
# Oldest published release that can read a board this version writes.
DEFAULT_MIN_VERSION = (0, 2, 0)
# 0.2.5 keeps format 1. See migrate.py.
BOARD_FORMAT = 1

_VERSION_RE = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
RELEASES_PAGE = "https://github.com/zlatko-lakisic/agent-coop/releases"
API_ROOT = "https://api.github.com/repos/zlatko-lakisic/agent-coop"
REPO_BLOB = "https://github.com/zlatko-lakisic/agent-coop/blob"


def parse_version(text: str) -> tuple[int, int, int]:
    """Parse 0.2.4 or v0.2.4. Reject anything else."""
    raw = (text or "").strip()
    match = _VERSION_RE.fullmatch(raw)
    if match is None:
        raise CoopError(
            "A version looks like 0.2.4 or v0.2.4. "
            + (raw or "An empty version")
            + " is not a version.",
            code=2,
        )
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def version_text(version: tuple[int, int, int]) -> str:
    """Return the dotted form without a leading v."""
    return str(version[0]) + "." + str(version[1]) + "." + str(version[2])


def tag_name(version: tuple[int, int, int]) -> str:
    """Return the GitHub tag for a version."""
    return "v" + version_text(version)


def has_update_command(version: tuple[int, int, int]) -> bool:
    """Return True when that published release includes agentcoop update."""
    return version >= UPDATE_COMMAND_VERSION


def no_update_line(version: tuple[int, int, int]) -> str:
    """Return the warning shown before installing a release with no update command."""
    return (
        version_text(version)
        + " has no update command. To come back later, say: "
        + "Update Agent Co-op from github.com/zlatko-lakisic/agent-coop."
    )


def cpu_name(machine: str) -> str:
    """Return arm64 or x64. Raise when this CPU has no published build."""
    folded = (machine or "").lower()
    if folded in ("arm64", "aarch64"):
        return "arm64"
    if folded in ("x86_64", "amd64", "x64"):
        return "x64"
    raise CoopError("There is no published Agent Co-op build for this processor (" + (machine or "unknown") + ").")


def normalize_system(system: str) -> str:
    """Return windows, darwin, or linux."""
    folded = (system or "").lower()
    if folded in ("windows", "win32"):
        return "windows"
    if folded in ("darwin", "macos", "mac"):
        return "darwin"
    if folded == "linux":
        return "linux"
    raise CoopError("There is no published Agent Co-op build for this system (" + (system or "unknown") + ").")


def asset_name(kind: str, system: str, machine: str) -> str:
    """Pick the release file that matches an install.

    kind is zipapp or binary. Windows binaries use the zip so agentcoop.exe
    and agentcoopw.exe stay together.
    """
    if kind == "zipapp":
        return "agentcoop.pyz"
    if kind != "binary":
        raise CoopError("The install kind must be zipapp or binary.")
    host = normalize_system(system)
    cpu = cpu_name(machine)
    if host == "windows":
        if cpu != "x64":
            raise CoopError("There is no published Windows build for this processor.")
        return "agentcoop-windows-x64.zip"
    if host == "darwin":
        if cpu == "arm64":
            return "agentcoop-macos-arm64"
        return "agentcoop-macos-x86_64"
    if cpu == "arm64":
        return "agentcoop-linux-arm64"
    return "agentcoop-linux-x64"


def this_computer_asset(kind: str) -> str:
    """Return the asset name for this computer and install kind."""
    return asset_name(kind, platform.system(), platform.machine())


def _wrapper_script(path: Path) -> bool:
    """Return True when a file is the shell wrapper around the zipapp."""
    try:
        head = path.read_bytes()[:2]
    except OSError:
        return False
    return head == b"#!"


def installed_kind(board: Path) -> str:
    """Return zipapp or binary from the files in bin/."""
    bindir = board / "bin"
    if os.name == "nt":
        if (bindir / "agentcoop.exe").is_file():
            return "binary"
        if (bindir / "agentcoop.pyz").is_file():
            return "zipapp"
    else:
        binary = bindir / "agentcoop"
        if binary.is_file() and not _wrapper_script(binary):
            return "binary"
        if (bindir / "agentcoop.pyz").is_file():
            return "zipapp"
        if binary.is_file():
            return "binary"
    raise CoopError(
        "Agent Co-op has no program in " + str(bindir) + ". Run: agentcoop setup",
        code=3,
    )


def read_installed(board: Path) -> str:
    """Return the version string stored in bin/VERSION, or the running one."""
    from agentcoop import __version__

    path = board / "bin" / "VERSION"
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return __version__


def read_installed_version(board: Path) -> tuple[int, int, int]:
    """Return the installed version. A missing or junk VERSION uses the running one."""
    from agentcoop import __version__

    try:
        return parse_version(read_installed(board))
    except CoopError:
        return parse_version(__version__)
