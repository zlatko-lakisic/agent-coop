"""Attachment rules and secret warnings."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import casefold_key, home_dir, is_same_or_inside

BODY_LIMIT_BYTES = 16 * 1024
SUBJECT_LIMIT = 120

# Directories that are always off limits, relative to the home folder.
_DENY_HOME_DIRS = (
    ".ssh",
    ".aws",
    ".gnupg",
    Path(".config") / "gh",
    ".kube",
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Firefox",
    "Library/Application Support/BraveSoftware",
    "Library/Application Support/Microsoft Edge",
    "Library/Safari",
    ".mozilla",
    ".config/google-chrome",
    ".config/chromium",
    ".config/BraveSoftware",
    "AppData/Local/Google/Chrome",
    "AppData/Local/Microsoft/Edge",
    "AppData/Roaming/Mozilla/Firefox",
)

_DENY_HOME_FILES = (Path(".docker") / "config.json",)

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _name_is_secret(name: str) -> bool:
    folded = name.casefold()
    if folded == ".env" or folded.startswith(".env."):
        return True
    if folded.endswith((".pem", ".key", ".p12", ".pfx", ".kdbx")):
        return True
    if folded.startswith(("id_rsa", "id_ed25519", "id_ecdsa", "credentials")):
        return True
    if folded in (".netrc", ".npmrc", ".pypirc"):
        return True
    return False


def _folded_inside(child: Path, parent: Path) -> bool:
    """Return True when child is parent or inside it, ignoring case on every OS."""
    try:
        child_key = casefold_key(child, fold=True)
        parent_key = casefold_key(parent, fold=True)
    except OSError:
        return False
    if len(child_key) < len(parent_key):
        return False
    return child_key[: len(parent_key)] == parent_key


def deny_reason(path: Path, board: Path) -> str | None:
    """Return why a path is blocked, or None when the deny list does not hit."""
    home = home_dir()
    for relative in _DENY_HOME_DIRS:
        if _folded_inside(path, home / relative):
            return "That location is on the private-file list (" + str(relative) + ")."
    for relative in _DENY_HOME_FILES:
        candidate = home / relative
        if _folded_inside(path, candidate):
            return "That file is on the private-file list (" + str(relative) + ")."
    if _name_is_secret(path.name):
        return "That file name is on the private-file list (" + path.name + ")."
    for blocked in (
        board / "config.json",
        board / "agents.json",
        board / "projects.json",
    ):
        if is_same_or_inside(path, blocked) and path.resolve() == blocked.resolve():
            return "Board settings files cannot be attachments."
    if is_same_or_inside(path, board / ".state"):
        return "Board settings files cannot be attachments."
    return None


def allowed_roots_for(project_path: Path, board: Path, project: str, extra: list[str]) -> list[Path]:
    """Return folders an attachment for this project may live in."""
    roots = [project_path, board / "projects" / project / "files"]
    for item in extra:
        if item:
            roots.append(Path(item))
    return roots


def check_attachment(
    raw: str,
    *,
    project_path: Path,
    board: Path,
    project: str,
    allowed_roots: list[str],
) -> str | None:
    """Return a block reason, or None when the attachment may be opened.

    Callers that are sending a message should raise. Callers that are
    showing a message should mark the path blocked.
    """
    if not isinstance(raw, str) or not raw.strip():
        return "An attachment path is missing."
    path = Path(raw)
    if not path.is_absolute():
        return "Attachments must be absolute paths."
    if not path.exists():
        return "That file does not exist."
    try:
        real = Path(raw).resolve(strict=True)
    except OSError:
        return "That file does not exist."
    denied = deny_reason(path, board) or deny_reason(real, board)
    if denied:
        return denied
    try:
        mode = os.stat(real).st_mode
    except OSError:
        return "That file does not exist."
    if not stat.S_ISREG(mode):
        return "Only regular files can be attachments. Folders, pipes, and devices are not allowed."
    roots = allowed_roots_for(project_path, board, project, allowed_roots)
    if any(is_same_or_inside(real, root) for root in roots):
        return None
    return "That file is outside the folders this project is allowed to share."


def open_attachment(
    raw: str,
    *,
    max_bytes: int,
    clip: bool = False,
    project_path: Path,
    board: Path,
    project: str,
    allowed_roots: list[str],
) -> bytes:
    """Read an attachment after the same checks as check_attachment."""
    reason = check_attachment(
        raw,
        project_path=project_path,
        board=board,
        project=project,
        allowed_roots=allowed_roots,
    )
    if reason:
        raise CoopError("This attachment is not allowed: " + raw + ". " + reason)
    real = Path(raw).resolve(strict=True)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_BINARY", 0)
    fd = os.open(str(real), flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CoopError(
                "Only regular files can be attachments. Folders, pipes, and devices are not allowed."
            )
        if info.st_size > max_bytes and not clip:
            raise CoopError(real.name + " is larger than " + str(max_bytes) + ".")
        chunks: list[bytes] = []
        total = 0
        limit = max_bytes + 1
        while total < limit:
            try:
                piece = os.read(fd, min(65536, limit - total))
            except (BlockingIOError, InterruptedError):
                break
            if not piece:
                break
            chunks.append(piece)
            total += len(piece)
        data = b"".join(chunks)
        if clip:
            return data[:max_bytes]
        if len(data) > max_bytes:
            raise CoopError(real.name + " is larger than " + str(max_bytes) + ".")
        return data
    finally:
        os.close(fd)


def require_attachment(
    raw: str,
    *,
    project_path: Path,
    board: Path,
    project: str,
    allowed_roots: list[str],
) -> None:
    """Raise when an attachment must not be sent."""
    reason = check_attachment(
        raw,
        project_path=project_path,
        board=board,
        project=project,
        allowed_roots=allowed_roots,
    )
    if reason:
        raise CoopError("This attachment is not allowed: " + raw + ". " + reason)


def secret_warning(text: str) -> str | None:
    """Return a short reason when text looks like it contains a key."""
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return "looks like an API key"
    return None
