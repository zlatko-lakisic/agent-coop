"""Ask a helper to check a done report before the task counts as done."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import files_dir, private_dir
from agentcoop.projects import atomic_write_bytes, dump_json, load_projects, mutate_projects, role_of

MODES = ("off", "changes", "always")
VERDICTS = ("approve", "changes", "cannot_verify")
OVERRIDE_LINE = "Override: the person marked this done anyway."


def review_mode(board: Path, project: str) -> str:
    """Return off, changes, or always. A helper tagged review defaults to changes."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    chosen = record.get("review")
    if chosen in MODES:
        return str(chosen)
    for profile in (record.get("profiles") or {}).values():
        if isinstance(profile, dict) and "review" in (profile.get("tags") or []):
            return "changes"
    return "off"


def set_review_mode(board: Path, project: str, mode: str) -> str:
    """Save the review setting."""
    if mode not in MODES:
        raise CoopError("Review must be off, changes, or always.", code=2)

    def change(projects: dict) -> None:
        record = (projects.get("projects") or {}).get(project)
        if record is None:
            raise CoopError("There is no project named " + project + ".")
        record["review"] = mode

    mutate_projects(board, change)
    if mode == "off":
        return "Review is off. A task can be marked done without a reviewer."
    if mode == "always":
        return "Review is on for every finished task."
    return "Review is on when project files change."


def review_applies(board: Path, project: str) -> bool:
    """Return True when Status: done must wait for approval or an override."""
    mode = review_mode(board, project)
    if mode == "off":
        return False
    if mode == "always":
        return True
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    return _files_changed(Path(str(record.get("path") or "")))


def assert_done_allowed(board: Path, project: str, body: str) -> None:
    """Refuse Status: done while a required review is still open."""
    if not review_applies(board, project):
        return
    if OVERRIDE_LINE in body and override_recorded(board, project):
        return
    if latest_approved(board, project):
        return
    raise CoopError(
        "This task needs a review before it counts as done. "
        "Post Status: review, then run agentcoop review request.",
        code=2,
    )


def task_bundle(board: Path, project: str, task: str) -> list[Path]:
    """Write the diff and return the files a reviewer can open."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    driver = str(record.get("driver") or "cursor")
    folder = files_dir(board, project, driver)
    private_dir(folder)
    progress = board / "projects" / project / "progress" / task
    if not progress.is_file():
        raise CoopError("There is no progress update named " + task + ".", code=2)
    diff_path = folder / (task + ".diff.txt")
    diff_path.write_text(_diff_text(Path(str(record.get("path") or ""))), encoding="utf-8")
    return [progress.resolve(), diff_path.resolve()]


def record_request(board: Path, project: str, task: str, helper: str, message_id: str) -> None:
    """Remember which review request covers a progress update."""
    path = _open_path(board, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load(path)
    data["task"] = task
    data["helper"] = helper
    data["request"] = message_id
    data["approved"] = False
    data["rounds"] = list(data.get("rounds") or [])
    atomic_write_bytes(path, dump_json(data))
    _refresh(board)


def record_verdict(
    board: Path,
    project: str,
    *,
    reviewer: str,
    verdict: str,
    body: str,
    message_id: str,
) -> None:
    """Store one review result. Approve opens the way to Status: done."""
    if verdict not in VERDICTS:
        raise CoopError("A review verdict must be approve, changes, or cannot_verify.", code=2)
    path = _open_path(board, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load(path)
    rounds = list(data.get("rounds") or [])
    refuted, skipped, surprises = _counts(body)
    first = verdict == "approve" and not any(
        item.get("verdict") in ("changes", "cannot_verify") for item in rounds
    )
    rounds.append(
        {
            "id": message_id,
            "reviewer": reviewer,
            "verdict": verdict,
            "refuted": refuted,
            "skipped": skipped,
            "surprises": surprises,
            "first_pass": first,
        }
    )
    data["rounds"] = rounds
    data["approved"] = verdict == "approve"
    atomic_write_bytes(path, dump_json(data))
    metric = board / "projects" / project / "reviews" / (message_id + ".json")
    metric.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        metric,
        dump_json(
            {
                "task": data.get("task") or "",
                "driver": str((load_projects(board).get("projects") or {}).get(project, {}).get("driver") or ""),
                "reviewer": reviewer,
                "verdict": verdict,
                "refuted": refuted,
                "skipped": skipped,
                "surprises": surprises,
                "first_pass": first,
            }
        ),
    )
    _refresh(board)


def latest_approved(board: Path, project: str) -> bool:
    """Return True when the open review was approved."""
    return _load(_open_path(board, project)).get("approved") is True


def record_override(board: Path, project: str, by: str, now: datetime | None = None) -> None:
    """Remember that the person marked the task done without approval."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    path = board / "projects" / project / "reviews" / "override.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        path,
        dump_json(
            {
                "by": by,
                "ts": moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "line": OVERRIDE_LINE,
            }
        ),
    )
    _refresh(board)


def override_recorded(board: Path, project: str) -> bool:
    """Return True when the person already overrode the open review."""
    return (board / "projects" / project / "reviews" / "override.json").is_file()


def review_summary(board: Path, project: str, agent: str) -> str:
    """Return one short line for the last 20 reviews of this driver."""
    folder = board / "projects" / project / "reviews"
    if not folder.is_dir():
        return ""
    rows = []
    for path in sorted(folder.glob("*.json")):
        if path.name in ("override.json", "open.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("driver") == agent:
            rows.append(data)
    rows = rows[-20:]
    if not rows:
        return ""
    passed = sum(1 for item in rows if item.get("first_pass") is True and item.get("verdict") == "approve")
    refuted = sum(int(item.get("refuted") or 0) for item in rows)
    skipped = sum(int(item.get("skipped") or 0) for item in rows)
    return (
        agent
        + ": "
        + str(passed)
        + " of "
        + str(len(rows))
        + " reviews passed first time. "
        + str(refuted)
        + " claims refuted, "
        + str(skipped)
        + " skipped item in the last "
        + str(len(rows))
        + "."
    )


def open_review_lines(board: Path, project: str) -> list[str]:
    """Return a line when a review is still open."""
    data = _load(_open_path(board, project))
    if not data or data.get("approved") is True or not data.get("request"):
        return []
    return [
        "Open review: "
        + str(data.get("task") or data.get("request"))
        + " with "
        + str(data.get("helper") or "a helper")
        + "."
    ]


def _counts(body: str) -> tuple[int, int, int]:
    found = {"refuted": 0, "skipped": 0, "surprises": 0}
    for line in body.splitlines():
        name, _, value = line.partition(":")
        key = name.strip().lower()
        if key in found:
            try:
                found[key] = int(value.strip())
            except ValueError:
                continue
    return found["refuted"], found["skipped"], found["surprises"]


def _files_changed(folder: Path) -> bool:
    if not folder.is_dir() or not (folder / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(folder),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(result.stdout.strip())


def _diff_text(folder: Path) -> str:
    if not folder.is_dir() or not (folder / ".git").exists():
        return "This folder is not a git repository. Changed files were not listed.\n"
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(folder),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        diff = subprocess.run(
            ["git", "diff"],
            cwd=str(folder),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "git could not be run.\n"
    listed = status.stdout.strip() or "(no changed files)"
    return "Changed files:\n" + listed + "\n\n" + diff.stdout


def _refresh(board: Path) -> None:
    """Rewrite STATUS.md after a review file changes."""
    from agentcoop.statusfile import refresh_status

    refresh_status(board)


def _open_path(board: Path, project: str) -> Path:
    return board / "projects" / project / "reviews" / "open.json"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def resume_text(board: Path, project: str, agent: str) -> str:
    """Return where a new chat should pick up. Finished parts stay finished."""
    from agentcoop.context import latest_version
    from agentcoop.core import new_messages
    from agentcoop.progress import latest_progress

    lines = [
        "Continue the co-op work here.",
        "Do not redo parts already marked done.",
        "",
    ]
    context = latest_version(board, project)
    if context is not None:
        lines.append("Context: " + context.name)
    progress = latest_progress(board, project)
    if progress is not None:
        lines.append("Progress: " + progress.name)
        lines.append(progress.read_text(encoding="utf-8").strip())
    lines.extend(open_review_lines(board, project))
    messages, _issues = new_messages(board, project, agent)
    for message in messages:
        lines.append("Open: " + message.subject)
    return "\n".join(lines).strip() + "\n"
