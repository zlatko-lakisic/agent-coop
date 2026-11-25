"""Choose how a project's agents talk: files on this computer, or a private GitHub repository.

A project uses one or the other, never both. Files are the default. This module
imports nothing from the network code until a project names a repository.
"""

from __future__ import annotations

from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.projects import github_repo, load_projects, mutate_projects

NO_PROJECT = "There is no project named {name}. Run: agentcoop project list"


def _record(board: Path, project: str) -> dict:
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is None:
        raise CoopError(NO_PROJECT.format(name=project))
    return record


def talks_line(record: object) -> str:
    """Return a line for a project that talks through GitHub. Empty for a files project."""
    repo = github_repo(record)
    if not repo:
        return ""
    return "Talks through: GitHub " + repo


def github_projects(board: Path) -> dict[str, str]:
    """Return project name to repository for every project that talks through GitHub."""
    found = {}
    for name, record in (load_projects(board).get("projects") or {}).items():
        repo = github_repo(record)
        if repo:
            found[str(name)] = repo
    return found


def confirm_summary(project: str, repo: str) -> str:
    """Return the sentence the person is asked to allow."""
    return (
        "This will send every message in "
        + project
        + " to the private GitHub repository "
        + repo
        + ". Agents in "
        + project
        + " will then talk through GitHub and not through files."
    )


def check_can_use_github(board: Path, project: str, repo_text: str) -> str:
    """Check the repository and the project before asking the person. Return owner/name."""
    from agentcoop import github
    from agentcoop.core import log_messages
    from agentcoop.ghsync import check_repo

    repo = github.require_repo(repo_text)
    record = _record(board, project)
    current = github_repo(record)
    if current and current.lower() == repo.lower():
        raise CoopError(project + " already talks through GitHub " + current + ". Nothing was changed.")
    if current:
        raise CoopError(
            project
            + " talks through GitHub "
            + current
            + ". Moving it to another repository is not available yet. Nothing was changed."
        )
    if log_messages(board, project):
        raise CoopError(
            project
            + " already has messages, and copying earlier messages to GitHub is not available yet. "
            + "Add a new project with --github. Nothing was changed."
        )
    return check_repo(repo)


def use_github(board: Path, project: str, repo: str) -> str:
    """Make a project talk through a private repository. The caller has asked the person."""
    from agentcoop.ghsync import reset
    from agentcoop.statusfile import refresh_status

    _record(board, project)
    reset(board, project)

    def change(data: dict) -> None:
        data["projects"][project]["github"] = {"repo": repo}

    mutate_projects(board, change)
    refresh_status(board)
    return (
        project
        + " now talks through GitHub: "
        + repo
        + ". Each conversation is one issue there. A message file written by hand is not delivered."
    )


def use_files(board: Path, project: str) -> str:
    """Make a project talk through files again. The caller has asked the person."""
    from agentcoop.statusfile import refresh_status

    record = _record(board, project)
    repo = github_repo(record)
    if not repo:
        return project + " already talks through files. Nothing was changed."
    from agentcoop.ghsync import pull

    note = ""
    try:
        pull(board, project, repo, force=True)
    except CoopError as exc:
        note = (
            " GitHub could not be read first ("
            + str(exc).rstrip(".")
            + "), so a message posted there in the last few minutes may be missing here."
        )

    def change(data: dict) -> None:
        data["projects"][project].pop("github", None)

    mutate_projects(board, change)
    refresh_status(board)
    return (
        project
        + " now talks through files on this computer. Its earlier messages are still here, and "
        + "the issues in "
        + repo
        + " are left as they are."
        + note
    )


def sync_now(board: Path, project: str | None = None) -> list[str]:
    """Pull every GitHub project now, or one. Return one line per project."""
    names = github_projects(board)
    if project is not None:
        record = _record(board, project)
        if not github_repo(record):
            return [project + " talks through files. There is nothing to sync."]
        names = {project: names[project]}
    if not names:
        return ["No project talks through GitHub. There is nothing to sync."]
    from agentcoop.ghsync import pull

    lines = []
    for name, repo in names.items():
        result = pull(board, name, repo, force=True)
        if result.skipped:
            lines.append(name + ": another command is reading " + repo + " right now.")
            continue
        noun = "message" if result.messages == 1 else "messages"
        lines.append(name + ": in step with " + repo + ". " + str(result.messages) + " new " + noun + ".")
    return lines
