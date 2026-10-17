"""Attachment rules and secret warnings."""

from __future__ import annotations

import re
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import home_dir, is_same_or_inside

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
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith((".pem", ".key", ".p12")):
        return True
    if name.startswith(("id_rsa", "id_ed25519", "credentials")):
        return True
    return False


def deny_reason(path: Path, board: Path) -> str | None:
    """Return why a path is blocked, or None when the deny list does not hit."""
    home = home_dir()
    for relative in _DENY_HOME_DIRS:
        if is_same_or_inside(path, home / relative):
            return "That location is on the private-file list (" + str(relative) + ")."
    for relative in _DENY_HOME_FILES:
        candidate = home / relative
        if is_same_or_inside(path, candidate):
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
    denied = deny_reason(path, board)
    if denied:
        return denied
    roots = allowed_roots_for(project_path, board, project, allowed_roots)
    if any(is_same_or_inside(path, root) for root in roots):
        return None
    return "That file is outside the folders this project is allowed to share."


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


def secret_warning(body: str) -> str | None:
    """Return a warning when the body looks like it contains a key."""
    for pattern in _SECRET_PATTERNS:
        if pattern.search(body):
            return (
                "This message looks like it contains a password or API key. "
                "Remove it before you send. Nothing was blocked, but the board "
                "keeps messages forever."
            )
    return None
