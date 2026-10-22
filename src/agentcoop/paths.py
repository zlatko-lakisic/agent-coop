"""Board location and the folders inside it."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def home_dir() -> Path:
    """Return the home directory, honoring HOME and USERPROFILE."""
    return Path.home()


def board_root() -> Path:
    """Return the board folder.

    AGENTCOOP_BOARD is an advanced override. Tests set it to a temp folder.
    """
    override = os.environ.get("AGENTCOOP_BOARD")
    if override:
        return Path(override).expanduser()
    return home_dir() / ".agent_board"


def casefold_key(path: Path, *, fold: bool | None = None) -> tuple[str, ...]:
    """Return path parts for comparison.

    macOS and Windows treat paths as case-insensitive. Linux does not.
    Pass fold=True to compare case-insensitively on every OS.
    """
    resolved = path.expanduser().resolve()
    parts = resolved.parts
    if fold is None:
        fold = sys.platform == "darwin" or os.name == "nt"
    if fold:
        return tuple(part.casefold() for part in parts)
    return tuple(parts)


def is_same_or_inside(child: Path, parent: Path) -> bool:
    """Return True when child is parent or a path inside parent."""
    child_key = casefold_key(child)
    parent_key = casefold_key(parent)
    if len(child_key) < len(parent_key):
        return False
    return child_key[: len(parent_key)] == parent_key


def paths_conflict(left: Path, right: Path) -> bool:
    """Return True when two project paths are the same or nested."""
    return is_same_or_inside(left, right) or is_same_or_inside(right, left)


def project_dir(board: Path, project: str) -> Path:
    """Return the board folder for one project."""
    return board / "projects" / project


def inbox_msgs(board: Path, project: str, agent: str) -> Path:
    """Return the folder of messages sent to an agent."""
    return project_dir(board, project) / "inbox" / agent / "msgs"


def inbox_done(board: Path, project: str, agent: str) -> Path:
    """Return the folder of done markers for an agent."""
    return project_dir(board, project) / "inbox" / agent / "done"


def sent_dir(board: Path, project: str, agent: str) -> Path:
    """Return the folder of copies of messages an agent sent."""
    return project_dir(board, project) / "sent" / agent


def files_dir(board: Path, project: str, agent: str) -> Path:
    """Return the folder where an agent stores patches and drafts."""
    return project_dir(board, project) / "files" / agent


def archive_dir(board: Path, project: str, agent: str) -> Path:
    """Return the archive folder for an agent's handled messages."""
    return project_dir(board, project) / "archive" / agent


def inbox_new_md(board: Path, project: str, agent: str) -> Path:
    """Return the plain-text new-message list for an outbox helper."""
    return project_dir(board, project) / "inbox" / agent / "NEW.md"


def outbox_dir(board: Path, project: str, agent: str) -> Path:
    """Return the folder where an outbox helper writes answers."""
    return project_dir(board, project) / "outbox" / agent


def outbox_sent_dir(board: Path, project: str, agent: str) -> Path:
    """Return the folder of posted outbox answers."""
    return outbox_dir(board, project, agent) / "sent"


def outbox_rejected_dir(board: Path, project: str, agent: str) -> Path:
    """Return the folder of refused outbox answers."""
    return outbox_dir(board, project, agent) / "rejected"


def outbox_processing_dir(board: Path, project: str, agent: str) -> Path:
    """Return the folder used to claim an outbox file while posting it."""
    return outbox_dir(board, project, agent) / "processing"


def native_path(path: Path | str, *, windows: bool | None = None) -> str:
    """Return a path string with native separators.

    A Windows path (drive letter or backslashes) keeps backslashes even when
    this code is running on macOS or Linux, so a skill exported for a Windows
    board stays readable there.
    """
    text = os.fspath(path)
    if windows is None:
        windows = os.name == "nt" or _looks_windows(text)
    if windows:
        return text.replace("/", "\\")
    return text


def _looks_windows(text: str) -> bool:
    if "\\" in text:
        return True
    return len(text) >= 2 and text[0].isalpha() and text[1] == ":"


def ensure_outbox_dirs(board: Path, project: str, agent: str) -> None:
    """Create the outbox folders an outbox helper needs."""
    for folder in (
        outbox_dir(board, project, agent),
        outbox_sent_dir(board, project, agent),
        outbox_rejected_dir(board, project, agent),
        outbox_processing_dir(board, project, agent),
        inbox_new_md(board, project, agent).parent,
    ):
        private_dir(folder)


def private_dir(folder: Path) -> None:
    """Create a folder and, on POSIX, keep it private to this account."""
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, 0o700)


def ensure_agent_dirs(board: Path, project: str, agent: str) -> None:
    """Create the folders only this agent, or its senders, write."""
    for folder in (
        inbox_msgs(board, project, agent),
        inbox_done(board, project, agent),
        sent_dir(board, project, agent),
        files_dir(board, project, agent),
        archive_dir(board, project, agent) / "msgs",
        archive_dir(board, project, agent) / "done",
    ):
        private_dir(folder)


def quote_path(path: str) -> str:
    """Quote a path for a command line."""
    if os.name == "nt":
        if any(char in path for char in ' "'):
            return '"' + path.replace('"', '\\"') + '"'
        return path
    if path and all(char not in path for char in " \t\n\"'\\$"):
        return path
    return "'" + path.replace("'", "'\"'\"'") + "'"


def agentcoop_command(board: Path) -> str:
    """Return the command skills should run.

    Prefer the wrapper or binary in board/bin. Fall back to Python plus the zipapp.
    """
    from agentcoop.pathenv import agentcoop_command as _command

    return _command(board)
