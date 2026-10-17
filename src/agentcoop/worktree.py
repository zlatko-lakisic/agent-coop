"""The driver's separate git worktree on the auto branch."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.projects import load_config


def auto_branch(board: Path) -> str:
    """Return the configured auto branch name."""
    config = load_config(board)
    return str(config.get("auto_branch") or "agentcoop/auto")


def worktree_path(board: Path, project: str) -> Path:
    """Return the worktree folder for a project."""
    return board / "worktrees" / project


def inside_git(folder: Path) -> bool:
    """Return True when folder is inside a git work tree."""
    completed = subprocess.run(
        ["git", "-C", str(folder), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0 and (completed.stdout or "").strip() == "true"


def current_branch(folder: Path) -> str:
    """Return the current branch name."""
    completed = subprocess.run(
        ["git", "-C", str(folder), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise CoopError("Could not read the current branch in " + str(folder) + ".")
    return (completed.stdout or "").strip()


def prepare_worktree(board: Path, project: str, project_folder: Path) -> tuple[Path, str, str]:
    """Create or update the auto worktree. Return path, person branch, diverged note."""
    if not inside_git(project_folder):
        raise CoopError(
            "Scheduled driver runs need a git repository, so the driver answers when you open it."
        )
    branch = auto_branch(board)
    person = current_branch(project_folder)
    dest = worktree_path(board, project)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        if _branch_exists(project_folder, branch):
            argv = ["git", "-C", str(project_folder), "worktree", "add", str(dest), branch]
        else:
            argv = ["git", "-C", str(project_folder), "worktree", "add", "-b", branch, str(dest), "HEAD"]
        _git(argv, "Could not create the worktree for " + project + ".")
    _return_to_auto(dest, branch)
    _commit_dirty(dest)
    note = ""
    merged = subprocess.run(
        ["git", "-C", str(dest), "merge", "--ff-only", person],
        capture_output=True,
        text=True,
    )
    if merged.returncode != 0:
        note = "The auto branch has diverged from " + person + ". Keep working on the auto branch."
    _copy_instruction_files(board, project, project_folder, dest)
    return dest, person, note


def finish_worktree(dest: Path, auto: str | None = None) -> str:
    """Commit leftover changes, then return the worktree to the auto branch.

    Returns a status note when the run ended on a different branch.
    """
    ended = current_branch(dest)
    _commit_dirty(dest)
    if not auto or ended == auto:
        return ""
    _git(
        ["git", "-C", str(dest), "checkout", auto],
        "Could not return the worktree to " + auto + ".",
    )
    return (
        "The driver switched to the branch "
        + ended
        + " during its run. Its work is on that branch. Review it with: git diff "
        + auto
        + "..."
        + ended
    )


def _return_to_auto(dest: Path, branch: str) -> None:
    """Switch an existing worktree back to the auto branch before a run."""
    if not (dest / ".git").exists():
        return
    if current_branch(dest) == branch:
        return
    _commit_dirty(dest)
    _git(
        ["git", "-C", str(dest), "checkout", branch],
        "Could not return the worktree to " + branch + ".",
    )


def remove_worktree(board: Path, project: str, project_folder: Path) -> str | None:
    """Remove the worktree and keep the branch. Return a note when a branch remains."""
    dest = worktree_path(board, project)
    branch = auto_branch(board)
    if dest.exists() and inside_git(project_folder):
        subprocess.run(
            ["git", "-C", str(project_folder), "worktree", "remove", "--force", str(dest)],
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(project_folder), "worktree", "prune"],
            capture_output=True,
            text=True,
        )
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    if inside_git(project_folder) and _branch_exists(project_folder, branch):
        return (
            "The branch "
            + branch
            + " in "
            + str(project_folder)
            + " was kept. Delete it with git branch -D "
            + branch
            + " when you no longer need it."
        )
    return None


def _branch_exists(folder: Path, branch: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(folder), "rev-parse", "--verify", "--quiet", branch],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _git(argv: list[str], message: str) -> None:
    completed = subprocess.run(argv, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise CoopError(message + (" " + detail if detail else ""))


def _commit_dirty(dest: Path) -> None:
    status = subprocess.run(
        ["git", "-C", str(dest), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if status.returncode != 0 or not (status.stdout or "").strip():
        return
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    message = "Agent Co-op scheduled run " + stamp + " (changes left uncommitted by the agent)"
    subprocess.run(["git", "-C", str(dest), "add", "-A"], capture_output=True, text=True)
    identity = ["-c", "user.name=Agent Co-op", "-c", "user.email=agentcoop@localhost"]
    committed = subprocess.run(
        ["git", "-C", str(dest), *identity, "commit", "-m", message],
        capture_output=True,
        text=True,
    )
    if committed.returncode != 0:
        subprocess.run(
            ["git", "-C", str(dest), "commit", "-m", message],
            capture_output=True,
            text=True,
        )


def _copy_instruction_files(board: Path, project: str, source: Path, dest: Path) -> None:
    """Copy the driver's installed project instruction files into the worktree."""
    from agentcoop.projects import load_projects

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    driver = str(record.get("driver") or "")
    for entry in record.get("installed") or []:
        if entry.get("agent") not in (None, "", driver):
            continue
        if entry.get("scope") not in (None, "", "project"):
            continue
        raw = entry.get("path") or ""
        if not raw:
            continue
        path = Path(raw)
        try:
            relative = path.relative_to(source)
        except ValueError:
            continue
        if not path.is_file():
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
