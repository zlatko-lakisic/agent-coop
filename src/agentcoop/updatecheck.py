"""Tell the person when a newer stable release exists. Never install it."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.projects import atomic_write_bytes, board_is_ready, dump_json, load_config, mutate_config
from agentcoop.releases import fetch_latest_tag
from agentcoop.version import parse_version, read_installed, read_installed_version, version_text

CHECK_TIMEOUT = 5.0
CHECK_INTERVAL = timedelta(days=1)
STATE_NAME = "update-check.json"


def state_path(board: Path) -> Path:
    """Return the daily check record."""
    return board / "update-check.json"


def checks_enabled(board: Path) -> bool:
    """Return False only when config turns the check off. Missing means on."""
    if not board_is_ready(board):
        return False
    try:
        data = load_config(board)
    except CoopError:
        return False
    return data.get("update_check") is not False


def read_state(board: Path) -> dict:
    """Return the check record, or an empty object."""
    path = state_path(board)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(board: Path, data: dict) -> None:
    """Replace the check record."""
    path = state_path(board)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, dump_json(data))


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parsed_stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def checked_recently(state: dict, now: datetime) -> bool:
    """Return True when a check was recorded less than a day ago."""
    seen = _parsed_stamp(state.get("last_checked"))
    if seen is None:
        return False
    moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc) - seen < CHECK_INTERVAL


def notice_text(latest: str, installed: str) -> str:
    """Return the one line shown when a newer version exists."""
    return (
        "Agent Co-op "
        + latest
        + " is out (installed "
        + installed
        + "). Say: Update Agent Co-op."
    )


def _save_latest(board: Path, latest: str | None, now: datetime) -> None:
    state = read_state(board)
    notified = list(state.get("notified_by") or [])
    if not isinstance(state.get("notified_by"), list):
        notified = []
    previous = state.get("latest")
    if latest != previous:
        notified = []
    payload = {
        "last_checked": _stamp(now),
        "latest": latest if isinstance(latest, str) else state.get("latest") or "",
        "notified_by": [item for item in notified if isinstance(item, str)],
    }
    if not payload["latest"]:
        payload["latest"] = ""
    write_state(board, payload)
    if latest and _newer_than_installed(board, latest):
        _refresh(board)


def _newer_than_installed(board: Path, latest: str) -> bool:
    try:
        return parse_version(latest) > read_installed_version(board)
    except CoopError:
        return False


def _refresh(board: Path) -> None:
    try:
        from agentcoop.statusfile import refresh_status

        refresh_status(board)
    except Exception:
        return


def perform_check(board: Path, fetch=None, timeout: float = CHECK_TIMEOUT, now: datetime | None = None) -> None:
    """Ask GitHub for the latest tag. Errors and the timeout change nothing but the clock.

    A fetch that outlives the timeout does not get to write the result later.
    """
    moment = now or datetime.now(timezone.utc)
    box: dict[str, object] = {}

    def inner() -> None:
        try:
            box["value"] = fetch_latest_tag(fetch=fetch, timeout=timeout)
            box["ok"] = True
        except Exception:
            box["ok"] = False

    worker = threading.Thread(target=inner, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive() or not box.get("ok"):
        state = read_state(board)
        state["last_checked"] = _stamp(moment)
        if "latest" not in state:
            state["latest"] = ""
        if not isinstance(state.get("notified_by"), list):
            state["notified_by"] = []
        write_state(board, state)
        return
    latest = box.get("value")
    _save_latest(board, latest if isinstance(latest, str) else None, moment)


def consider_update_check(
    board: Path,
    *,
    fetch=None,
    now: datetime | None = None,
    spawn=None,
    timeout: float = CHECK_TIMEOUT,
) -> None:
    """Start the daily check when it is on and due. Return without waiting.

    spawn receives a zero-argument function. The default starts a daemon thread
    so check, watch, and tick are not slowed down.
    """
    if not checks_enabled(board):
        return
    moment = now or datetime.now(timezone.utc)
    state = read_state(board)
    if checked_recently(state, moment):
        return
    state["last_checked"] = _stamp(moment)
    if "latest" not in state:
        state["latest"] = ""
    if not isinstance(state.get("notified_by"), list):
        state["notified_by"] = []
    write_state(board, state)

    def worker() -> None:
        try:
            perform_check(board, fetch=fetch, timeout=timeout, now=moment)
        except Exception:
            return

    if spawn is None:
        threading.Thread(target=worker, daemon=True).start()
        return
    spawn(worker)


def quiet_update_check(board: Path) -> None:
    """Run the daily check and ignore every error.

    Tests set AGENTCOOP_TEST_OFFLINE so check, watch, and tick do not call GitHub.
    """
    if os.environ.get("AGENTCOOP_TEST_OFFLINE") == "1":
        return
    try:
        consider_update_check(board)
    except Exception:
        return


def header_line(board: Path, agent: str, *, record: bool) -> str:
    """Return the update line, and remember this agent when record is set."""
    if not board_is_ready(board):
        return ""
    state = read_state(board)
    latest = state.get("latest")
    if not isinstance(latest, str) or not latest:
        return ""
    try:
        if parse_version(latest) <= read_installed_version(board):
            return ""
    except CoopError:
        return ""
    notified = state.get("notified_by")
    names = [item for item in notified if isinstance(item, str)] if isinstance(notified, list) else []
    if agent and agent in names:
        return ""
    if record and agent:
        names.append(agent)
        state["notified_by"] = names
        write_state(board, state)
    return notice_text(latest, version_text(read_installed_version(board)))


def status_lines(board: Path) -> list[str]:
    """Return status lines for the installed version and a newer release."""
    installed = read_installed(board)
    lines = ["Installed: " + installed]
    state = read_state(board)
    latest = state.get("latest")
    if isinstance(latest, str) and latest:
        try:
            if parse_version(latest) > parse_version(installed):
                lines.append(notice_text(latest, installed))
        except CoopError:
            pass
    marker = _desktop_marker(board)
    if marker and marker == installed:
        lines.append(
            "The Claude desktop skill changed in "
            + installed
            + ". In the Claude desktop app, say: Update Agent Co-op."
        )
    return lines


def mark_desktop_skill(board: Path, version: str) -> None:
    """Remember that this version changed the Claude desktop skill."""
    path = board / ".state" / "desktop-skill.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, dump_json({"version": version}))


def clear_desktop_skill(board: Path) -> None:
    """Drop the desktop skill note."""
    path = board / ".state" / "desktop-skill.json"
    if path.is_file():
        path.unlink()


def _desktop_marker(board: Path) -> str:
    path = board / ".state" / "desktop-skill.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    version = data.get("version")
    return version if isinstance(version, str) else ""


def set_update_checks(board: Path, enabled: bool) -> str:
    """Turn the daily check on or off. This is a self action."""

    def change(config: dict) -> None:
        config["update_check"] = enabled

    mutate_config(board, change)
    if enabled:
        return "Update checks are on."
    return "Update checks are off."
