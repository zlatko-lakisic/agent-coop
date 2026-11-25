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


def confirm_summary(project: str, repo: str, board: Path | None = None) -> str:
    """Return the sentence the person is asked to allow."""
    text = (
        "This will send every message in "
        + project
        + " to the private GitHub repository "
        + repo
        + ". Agents in "
        + project
        + " will then talk through GitHub and not through files."
    )
    if board is None:
        return text
    from agentcoop.ghhistory import counts

    messages, files = counts(board, project)
    if messages:
        text += " Its " + _plural(messages, "earlier message") + " will be copied there first"
        if files:
            text += ", with " + _plural(files, "attached file")
        text += "."
    return text


def _plural(count: int, noun: str) -> str:
    return str(count) + " " + noun + ("" if count == 1 else "s")


def check_can_use_github(board: Path, project: str, repo_text: str) -> str:
    """Check the repository and the project before asking the person. Return owner/name."""
    from agentcoop import github
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
    repo = check_repo(repo)
    from agentcoop.ghsync import read_roster

    roster, _issue, closed = read_roster(repo, project)
    if roster is not None and not closed:
        raise CoopError(
            "A project named "
            + project
            + " already talks through "
            + repo
            + ", from another computer. To take part from this one, run: agentcoop join --as <agent> --project "
            + project
            + " --github "
            + repo
            + ". Nothing was changed."
        )
    return repo


def use_github(board: Path, project: str, repo: str) -> str:
    """Make a project talk through a private repository. The caller has asked the person."""
    from agentcoop.errors import TransportDown
    from agentcoop.ghhistory import post_history
    from agentcoop.ghsync import _with_state, load_state, reset
    from agentcoop.statusfile import refresh_status

    _record(board, project)
    if str(load_state(board, project).get("repo") or "").lower() != repo.lower():
        # Another repository, or the first time. Start from a clean record.
        reset(board, project)
    try:
        copied = post_history(board, project, repo)
    except TransportDown as exc:
        raise TransportDown(
            str(exc)
            + " "
            + project
            + " still talks through files. The messages copied so far stay on GitHub. "
            + "Run the same command again to carry on.",
            code=exc.code,
        ) from exc
    _with_state(board, project, lambda state: (state.pop("closed", None), state.pop("blocked", None)))

    def change(data: dict) -> None:
        data["projects"][project]["github"] = {"repo": repo}

    mutate_projects(board, change)
    refresh_status(board)
    from agentcoop.ghsync import announce

    try:
        announce(board, project, repo)
    except CoopError:
        # The member list is posted with the first message or the next read.
        pass
    text = (
        project
        + " now talks through GitHub: "
        + repo
        + ". Each conversation is one issue there. A message file written by hand is not delivered."
    )
    if copied.messages:
        text += " " + _plural(copied.messages, "earlier message") + " copied"
        if copied.files:
            text += ", with " + _plural(copied.files, "file")
        text += "."
    if copied.missing_files:
        text += " " + _plural(copied.missing_files, "file") + " could not be copied: missing here, or over the size limit."
    if copied.too_long:
        text += (
            " "
            + _plural(copied.too_long, "message")
            + " too long for GitHub stayed on this computer only."
        )
    return text


def use_files(board: Path, project: str) -> str:
    """Make a project talk through files again. The caller has asked the person."""
    from agentcoop.statusfile import refresh_status

    record = _record(board, project)
    repo = github_repo(record)
    if not repo:
        return project + " already talks through files. Nothing was changed."
    from agentcoop.ghsync import NotPrivate, ProjectClosed, post_closed, pull
    from agentcoop.projects import _record_membership, driver_is_here

    require_driver_here(board, project, record)
    leaving = away_helpers(board, record)

    note = ""
    try:
        pull(board, project, repo, force=True)
    except (NotPrivate, ProjectClosed):
        note = " GitHub was not read first."
    except CoopError:
        note = (
            " GitHub could not be read first, so a message posted there in the last "
            + "few minutes may be missing here."
        )
    else:
        if driver_is_here(board, record):
            try:
                post_closed(board, project, repo)
            except CoopError:
                note = " Other computers in this project could not be told."

    def change(data: dict) -> None:
        current = data["projects"][project]
        before_helpers = dict(current.get("helpers") or {})
        current["helpers"] = {name: text for name, text in before_helpers.items() if name not in leaving}
        current.pop("github", None)
        _record_membership(current, str(current.get("driver")), before_helpers)

    mutate_projects(board, change, remote=True)
    refresh_status(board)
    if leaving:
        note = " " + ", ".join(leaving) + " worked on another computer and was removed from " + project + "." + note
    return (
        project
        + " now talks through files on this computer. Its earlier messages are still here, and "
        + "the issues in "
        + repo
        + " are left as they are."
        + note
    )


def away_members(board: Path, record: dict) -> list[str]:
    """Return the members that work on another computer, driver first."""
    from agentcoop.projects import agent_is_here

    names = [str(record.get("driver") or ""), *[str(name) for name in (record.get("helpers") or {})]]
    return [name for name in names if name and not agent_is_here(board, record, name)]


def away_helpers(board: Path, record: dict) -> list[str]:
    """Return the helpers that work on another computer."""
    from agentcoop.projects import agent_is_here

    return [str(name) for name in (record.get("helpers") or {}) if not agent_is_here(board, record, str(name))]


def require_driver_here(board: Path, project: str, record: dict) -> None:
    """Stop when this computer does not host the driver, so it cannot run the project through files."""
    from agentcoop.projects import driver_is_here

    if not driver_is_here(board, record):
        raise CoopError(
            "The driver of "
            + project
            + " works on another computer, so "
            + project
            + " cannot use files here. To take this computer out of the project, run: agentcoop project remove "
            + project
        )


def files_summary(board: Path, project: str) -> str:
    """Return the sentence the person is asked to allow before a project goes back to files."""
    record = _record(board, project)
    require_driver_here(board, project, record)
    text = "This will stop " + project + " talking through GitHub. Its agents will use files on this computer."
    leaving = away_helpers(board, record)
    if leaving:
        text += " " + ", ".join(leaving) + " works on another computer and will be removed from " + project + "."
    return text


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


class JoinPlan:
    """What joining a GitHub project from this computer will do."""

    def __init__(self, project: str, repo: str, agent: str, folder: Path, roster: dict | None, issue: int | None) -> None:
        self.project = project
        self.repo = repo
        self.agent = agent
        self.folder = folder
        self.roster = roster
        self.issue = issue
        self.new_here = roster is not None

    @property
    def summary(self) -> str:
        return (
            "This will send every message "
            + self.agent
            + " writes in "
            + self.project
            + " to the private GitHub repository "
            + self.repo
            + ", and copy that project's messages to this computer."
        )


def plan_join(board: Path, agent: str, repo_text: str, project: str | None, folder: Path) -> JoinPlan:
    """Check that an agent on this computer can join a project another computer started."""
    from agentcoop import github
    from agentcoop.ghsync import check_repo, read_roster
    from agentcoop.projects import computer_id, load_agents, validate_agent_name, validate_project_name

    validate_agent_name(agent)
    if not project:
        raise CoopError("Name the project. Example: agentcoop join --as " + agent + " --project demo --github " + repo_text, code=2)
    validate_project_name(project)
    if agent not in (load_agents(board).get("agents") or {}):
        raise CoopError(agent + " is not connected yet. Run: agentcoop connect self --tool <tool>")
    repo = check_repo(github.require_repo(repo_text))
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is not None:
        current = github_repo(record)
        if not current or current.lower() != repo.lower():
            raise CoopError(
                "This computer already has a project named " + project + " that does not talk through " + repo + "."
            )
        return JoinPlan(project, current, agent, Path(str(record.get("path"))), None, None)
    roster, issue, closed = read_roster(repo, project)
    if roster is None or closed:
        raise CoopError(
            "No project named "
            + project
            + " talks through "
            + repo
            + ". Check the name, or start it on the driver's computer first."
        )
    home = roster["homes"].get(agent)
    if home not in (None, "", computer_id(board)):
        raise CoopError(
            agent
            + " already works in "
            + project
            + " from another computer. Two computers cannot use the same agent name in one project. "
            + "Connect this agent under another name with --as, then join with that name."
        )
    if not folder.is_dir():
        raise CoopError("That folder does not exist.")
    return JoinPlan(project, repo, agent, folder, roster, issue)


def finish_join(board: Path, plan: JoinPlan, note: str = "") -> str:
    """Join after the person allowed it. Creates the project here when it is new to this computer."""
    from agentcoop.ghsync import post_request, pull, remember_project_issue, reset
    from agentcoop.paths import ensure_agent_dirs
    from agentcoop.projects import add_allowed_root, computer_id, load_agents, now_stamp, refuse_broad_folder
    from agentcoop.statusfile import refresh_status

    project, repo, agent = plan.project, plan.repo, plan.agent
    if plan.new_here:
        roster = plan.roster or {}
        folder = plan.folder.expanduser().resolve()
        refuse_broad_folder(folder, board)
        helpers = dict(roster.get("helpers") or {})
        record = {
            "path": str(folder),
            "driver": str(roster.get("driver")),
            "helpers": helpers,
            "created": now_stamp(),
            "installed": [],
            "github": {"repo": repo, "homes": dict(roster.get("homes") or {})},
        }
        reset(board, project)

        def change(data: dict) -> None:
            if project in data["projects"]:
                raise CoopError("A project named " + project + " already exists. Run: agentcoop project show " + project)
            data["projects"][project] = record

        mutate_projects(board, change, remote=True)
        add_allowed_root(board, str(folder))
        for name in [record["driver"], *helpers]:
            ensure_agent_dirs(board, project, str(name))
        ensure_agent_dirs(board, project, agent)
        remember_project_issue(board, project, plan.issue)
    record = _record(board, project)
    members = [str(record.get("driver")), *(record.get("helpers") or {})]
    from agentcoop.projects import agent_is_here

    asked = False
    if agent not in members or not agent_is_here(board, record, agent):
        if not note:
            from agentcoop.membership import _default_note

            note = _default_note(board, agent)
        post_request(board, project, repo, "join", agent, note)
        asked = True
    tool_key = ((load_agents(board).get("agents") or {}).get(agent) or {}).get("tool", "")
    try:
        from agentcoop.install import install_project_files
        from agentcoop.tools import get_tool

        if tool_key and tool_key != "runner" and get_tool(tool_key).project_files_for == "members":
            install_project_files(board, project, agent, yes=False, gitignore=False)
    except (CoopError, KeyError):
        pass
    try:
        pull(board, project, repo, force=True)
    except CoopError:
        pass
    refresh_status(board)
    if asked:
        return (
            "Asked the driver's computer to add "
            + agent
            + " to "
            + project
            + ". It joins when that computer next checks GitHub. Then run: agentcoop check --as "
            + agent
            + " --project "
            + project
        )
    return agent + " is in " + project + " and talks through GitHub: " + repo + "."
