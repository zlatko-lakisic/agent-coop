"""Board format migrations.

0.2.5 keeps board format 1. The new config keys (board_format, min_version,
update_check) are optional. Releases 0.2.0 through 0.2.4 ignore unknown keys
and ignore files they do not open, including watchers/, watch.json, and
update-check.json. Nothing on the board has to change for those versions to
keep reading it, so this version does not write format 2.

A later release that must change the board in a way an older version cannot
read adds a step here, raises min_version, and adds a down step when a
downgrade should undo that change.
"""

from __future__ import annotations

from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.install import backup_file
from agentcoop.projects import load_config, mutate_config
from agentcoop.version import BOARD_FORMAT, DEFAULT_MIN_VERSION, parse_version, version_text

_META_KEYS = ("board_format", "min_version", "update_check")


def read_format(board: Path) -> int:
    """Return the board format. A missing value is format 1."""
    try:
        data = load_config(board)
    except CoopError:
        return 1
    value = data.get("board_format", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def read_min_version(board: Path) -> tuple[int, int, int]:
    """Return the oldest version that can still read this board."""
    try:
        data = load_config(board)
    except CoopError:
        return DEFAULT_MIN_VERSION
    raw = data.get("min_version")
    if not isinstance(raw, str) or not raw.strip():
        return DEFAULT_MIN_VERSION
    try:
        return parse_version(raw)
    except CoopError:
        return DEFAULT_MIN_VERSION


def ensure_board_metadata(board: Path) -> None:
    """Add the optional keys when they are missing. A second run changes nothing."""
    data = load_config(board)
    if all(key in data for key in _META_KEYS):
        return
    backup_file(board / "config.json", board, "migrate")

    def change(config: dict) -> None:
        if "board_format" not in config:
            config["board_format"] = BOARD_FORMAT
        if "min_version" not in config:
            config["min_version"] = version_text(DEFAULT_MIN_VERSION)
        if "update_check" not in config:
            config["update_check"] = True

    mutate_config(board, change)


def migrate_1_to_2(board: Path) -> None:
    """Move format 1 to format 2. 0.2.5 does not call this.

    The step backs up config.json first and can run twice: the second run
    sees format 2 and leaves the board alone.
    """
    if read_format(board) >= 2:
        return
    backup_file(board / "config.json", board, "migrate")

    def change(config: dict) -> None:
        config["board_format"] = 2

    mutate_config(board, change)


def migrate_2_to_3(board: Path) -> None:
    """Move format 2 to format 3. No release calls this yet."""
    if read_format(board) >= 3:
        return
    backup_file(board / "config.json", board, "migrate")

    def change(config: dict) -> None:
        config["board_format"] = 3

    mutate_config(board, change)


# Up steps a future release registers. 0.2.5 leaves this empty on purpose.
UP_STEPS: dict[int, object] = {}
# Down steps undo a format an older program cannot read. Empty while format stays 1.
DOWN_STEPS: dict[int, object] = {}


def apply_migrations(board: Path, target: int = BOARD_FORMAT) -> None:
    """Run each up step until the board reaches target, then fill in missing keys."""
    current = read_format(board)
    while current < target:
        step = UP_STEPS.get(current)
        if step is None:
            break
        step(board)  # type: ignore[operator]
        nxt = read_format(board)
        if nxt <= current:
            break
        current = nxt
    ensure_board_metadata(board)


def assert_downgrade_allowed(board: Path, target: tuple[int, int, int]) -> None:
    """Refuse a target older than min_version when no down step can undo the board.

    A down step has to be registered and run before this returns. Format 1 has
    no down step, so a target below min_version is refused.
    """
    minimum = read_min_version(board)
    if target >= minimum:
        return
    raise CoopError(
        "This board needs Agent Co-op "
        + version_text(minimum)
        + " or newer. "
        + version_text(target)
        + " cannot read it.",
        code=1,
    )
