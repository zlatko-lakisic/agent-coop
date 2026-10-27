"""Ask a helper to check a done report before the task counts as done."""

from __future__ import annotations

import hashlib
import json
import os
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
    from agentcoop.fingerprint import changed

    return changed(board, project)


def assert_done_allowed(board: Path, project: str, body: str) -> None:
    """Refuse Status: done unless review is unnecessary, approved, or overridden."""
    if _override_valid(board, project) and OVERRIDE_LINE in body:
        return
    from agentcoop.fingerprint import current

    approval = _valid_approval(board, project)
    digest = current(board, project)["digest"]
    if approval:
        task = str(approval.get("task") or "")
        if _names_task(body, task) and digest == approval.get("fingerprint"):
            return
        if _names_task(body, task):
            raise CoopError(
                "The project changed after the review. Ask for review again.",
                code=2,
            )
        raise CoopError(
            "This is a new task. It needs its own review before it counts as done.",
            code=2,
        )
    if not review_applies(board, project):
        return
    raise CoopError(
        "This task needs a review before it counts as done. "
        "Post Status: review, then run agentcoop review request.",
        code=2,
    )


def finish_done(board: Path, project: str) -> None:
    """Close the fingerprint after Status: done was saved, and consume the approval."""
    from agentcoop.fingerprint import take_closed

    take_closed(board, project)
    path = _open_path(board, project)
    data = _load(path)
    if not data:
        return
    data["approved"] = False
    data["request"] = ""
    data.pop("seal", None)
    atomic_write_bytes(path, dump_json(data))
    _refresh(board)


def assert_requested_reviewer(board: Path, project: str, reviewer: str) -> None:
    """Refuse a verdict from a helper who was not asked."""
    data = _load(_open_path(board, project))
    helper = str(data.get("helper") or "")
    if not helper or reviewer != helper:
        raise CoopError(reviewer + " was not asked to review this task.", code=2)


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
    diff_path.write_text(_bundle_text(board, project), encoding="utf-8")
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
    from agentcoop.fingerprint import current

    scanned = current(board, project)
    data["fingerprint"] = scanned["digest"]
    data["files"] = scanned["files"]
    data.pop("seal", None)
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
    helper = str(data.get("helper") or "")
    if not helper or reviewer != helper:
        raise CoopError(reviewer + " was not asked to review this task.", code=2)
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
    if verdict == "approve":
        from agentcoop.fingerprint import accept_reviewed

        fields = _approval_fields(data)
        data["seal"] = _seal(board, fields)
        accept_reviewed(board, project, data.get("files") or {}, str(data.get("fingerprint") or ""))
    else:
        data.pop("seal", None)
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
    from agentcoop.fingerprint import take_closed

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    payload = {
        "by": by,
        "ts": moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "line": OVERRIDE_LINE,
    }
    payload["seal"] = _seal(board, payload)
    path = board / "projects" / project / "reviews" / "override.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, dump_json(payload))
    take_closed(board, project)
    _refresh(board)


def override_recorded(board: Path, project: str) -> bool:
    """Return True when the person already overrode the open review."""
    return _override_valid(board, project)


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


def _bundle_text(board: Path, project: str) -> str:
    """List added, changed, and deleted files, plus a git diff when there is one."""
    from agentcoop.fingerprint import closed_files, current

    folder = _project_folder(board, project)
    scanned = current(board, project)
    old = closed_files(board, project)
    now = scanned["files"] if isinstance(scanned.get("files"), dict) else {}
    parts = []
    for rel in sorted(set(now) - set(old)):
        parts.append(_file_section(board, folder, rel, "Added"))
    for rel in sorted(set(now) & set(old)):
        if now[rel] != old[rel]:
            parts.append(_file_section(board, folder, rel, "Changed"))
    for rel in sorted(set(old) - set(now)):
        parts.append("Deleted: " + rel + "\n")
    if folder.is_dir() and (folder / ".git").exists():
        diff = _run_git(folder, "--no-pager", "diff", "--no-ext-diff", "--no-color", "HEAD")
        staged = _run_git(folder, "--no-pager", "diff", "--cached", "--no-ext-diff", "--no-color", "HEAD")
        parts.append(_fit_share(board, diff.stdout, "git diff"))
        if staged.stdout.strip() and staged.stdout != diff.stdout:
            parts.append(_fit_share(board, staged.stdout, "git diff --cached"))
    text = "".join(parts)
    if not text.strip():
        return "No changes since the last closed fingerprint.\n"
    return text if text.endswith("\n") else text + "\n"


def _approval_fields(data: dict) -> dict:
    return {
        "approved": True,
        "fingerprint": str(data.get("fingerprint") or ""),
        "helper": str(data.get("helper") or ""),
        "request": str(data.get("request") or ""),
        "task": str(data.get("task") or ""),
    }


def _valid_approval(board: Path, project: str) -> dict | None:
    data = _load(_open_path(board, project))
    if data.get("approved") is not True:
        return None
    fields = _approval_fields(data)
    if data.get("seal") != _seal(board, fields):
        return None
    return fields


def _override_valid(board: Path, project: str) -> bool:
    path = board / "projects" / project / "reviews" / "override.json"
    data = _load(path)
    if data.get("line") != OVERRIDE_LINE:
        return False
    payload = {
        "by": data.get("by"),
        "line": OVERRIDE_LINE,
        "ts": data.get("ts"),
    }
    return data.get("seal") == _seal(board, payload)


def _seal(board: Path, payload: dict) -> str:
    secret = _review_secret(board)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(secret + text.encode("utf-8")).hexdigest()


def _review_secret(board: Path) -> bytes:
    path = board / ".state" / "review-seal"
    if path.is_file():
        try:
            found = path.read_bytes()
        except OSError:
            found = b""
        if found:
            return found
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = os.urandom(32)
    atomic_write_bytes(path, secret)
    return secret


def _names_task(body: str, task: str) -> bool:
    if not task:
        return False
    for line in body.splitlines():
        if line.strip() == "Task: " + task:
            return True
    return False


def _project_folder(board: Path, project: str) -> Path:
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    return Path(str(record.get("path") or ""))


def _fit_share(board: Path, text: str, label: str) -> str:
    from agentcoop.safety import secret_warning
    from agentcoop.share import limits

    if not text.strip():
        return ""
    if secret_warning(text):
        return label + " looks like it contains a password or API key, so it was not included.\n"
    cap = limits(board)["max_bytes"]
    raw = text.encode("utf-8")
    if len(raw) > cap:
        return label + " is over the share limit, so its contents were not included.\n"
    return text if text.endswith("\n") else text + "\n"


def _file_section(board: Path, folder: Path, rel: str, label: str) -> str:
    from agentcoop.safety import secret_warning
    from agentcoop.share import is_program, limits

    path = folder / rel
    try:
        size = path.stat().st_size
    except OSError:
        return label + ": " + rel + " could not be read.\n"
    cap = limits(board)["max_bytes"]
    if size > cap:
        return label + ": " + rel + " is over the share limit, so its contents were not included.\n"
    if is_program(path):
        return label + ": " + rel + " is a program or script, so its contents were not included.\n"
    try:
        data = path.read_bytes()
    except OSError:
        return label + ": " + rel + " could not be read.\n"
    if b"\x00" in data[:8192]:
        return label + ": " + rel + " is not a text file, so its contents were not included.\n"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return label + ": " + rel + " is not a text file, so its contents were not included.\n"
    if secret_warning(text):
        return label + ": " + rel + " looks like it contains a password or API key, so it was not included.\n"
    return label + ": " + rel + "\n" + text + ("" if text.endswith("\n") else "\n")


def _run_git(folder: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(folder),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args=["git", *args], returncode=1, stdout="", stderr="")


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
