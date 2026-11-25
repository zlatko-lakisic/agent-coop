"""Keep one project's board folders in step with its private GitHub repository.

In a project that names a repository, GitHub is the record and the only way
agents talk. The inbox, done, and sent folders stay on disk as a copy that
only agentcoop writes, so every reader of those folders keeps working.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from agentcoop import github
from agentcoop.errors import CoopError, TransportDown
from agentcoop.ghwire import (
    done_event,
    parse_event,
    render_done,
    render_message,
    render_thread,
    thread_title,
)
from agentcoop.paths import archive_dir, ensure_agent_dirs, inbox_done, inbox_msgs, project_dir, sent_dir
from agentcoop.projects import atomic_write_bytes, board_lock, dump_json, load_config, load_projects, now_stamp
from agentcoop.schema import AGENT_NAME_RE

POLL_SECONDS = 20
TRUST_SECONDS = 300
LABEL = "agentcoop"
NOT_SENT = (
    "This file was not sent through GitHub. This project talks through GitHub, "
    "so it was not delivered."
)
EDITED = "This message was edited on GitHub after it was sent."

_index_cache: dict[str, tuple[tuple[int, int], dict]] = {}


class NotPrivate(TransportDown):
    """The repository can be read by people outside it, so nothing is sent or read."""

    def __init__(self, repo: str, project: str) -> None:
        super().__init__(
            repo
            + " is not a private repository. Agent Co-op only posts to a private repository, "
            + "so nothing was sent or read. Make it private on GitHub, or run: agentcoop project set "
            + project
            + " --files",
            code=1,
        )


class PullResult:
    """What one pull changed on this computer."""

    def __init__(self) -> None:
        self.messages = 0
        self.done = 0
        self.skipped = False


def sync_dir(board: Path, project: str) -> Path:
    """Return the folder that holds this project's GitHub state."""
    return project_dir(board, project) / "github"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_state(board: Path, project: str) -> dict:
    """Return the saved sync state. Empty when the project has never been in step."""
    return _read(sync_dir(board, project) / "state.json")


def _save_state(board: Path, project: str, state: dict) -> None:
    atomic_write_bytes(sync_dir(board, project) / "state.json", dump_json(state))


def _shape(index: dict) -> dict:
    for key in ("messages", "done", "groups"):
        if not isinstance(index.get(key), dict):
            index[key] = {}
    index["v"] = 1
    return index


def load_index(board: Path, project: str) -> dict:
    """Return the map from message id to the issue and comment that hold it."""
    return _shape(_read(sync_dir(board, project) / "index.json"))


def _save_index(board: Path, project: str, index: dict) -> None:
    path = sync_dir(board, project) / "index.json"
    atomic_write_bytes(path, dump_json(index))
    _index_cache.pop(str(path), None)


def _cached_index(board: Path, project: str) -> dict:
    path = sync_dir(board, project) / "index.json"
    try:
        stat = path.stat()
    except OSError:
        return _shape({})
    signature = (stat.st_mtime_ns, stat.st_size)
    hit = _index_cache.get(str(path))
    if hit is not None and hit[0] == signature:
        return hit[1]
    data = load_index(board, project)
    _index_cache[str(path)] = (signature, data)
    return data


def reset(board: Path, project: str) -> None:
    """Forget the saved sync state. Used when a project starts using a repository."""
    folder = sync_dir(board, project)
    for name in ("state.json", "index.json"):
        try:
            (folder / name).unlink()
        except OSError:
            pass
    _index_cache.pop(str(folder / "index.json"), None)


def copy_problem(board: Path, project: str, message_id: str) -> str | None:
    """Say why a message file in a GitHub project must not be trusted, if it must not."""
    entry = _cached_index(board, project)["messages"].get(message_id)
    if not isinstance(entry, dict):
        return NOT_SENT
    if entry.get("edited"):
        return EDITED
    return None


def is_private(data: object) -> bool:
    """Say whether GitHub's description of a repository means only its members can read it."""
    if not isinstance(data, dict):
        return False
    return data.get("private") is True and data.get("visibility", "private") == "private"


def check_repo(repo: str) -> str:
    """Check that a repository can carry a project. Return its name as GitHub spells it."""
    client = github.client()
    try:
        reply = client.call("GET", "/repos/" + repo)
    except github.NotFound as exc:
        raise CoopError(
            repo
            + " was not found, or this sign-in cannot see it. Check the name, and check "
            + "that the sign-in can open that repository. Nothing was changed.",
            code=1,
        ) from exc
    data = reply.data if isinstance(reply.data, dict) else {}
    if not is_private(data):
        raise CoopError(
            repo
            + " is not a private repository. Agent Co-op only posts to a private repository. "
            + "Nothing was changed.",
            code=1,
        )
    if data.get("archived"):
        raise CoopError(repo + " is archived on GitHub, so nothing can be posted there. Nothing was changed.")
    if data.get("has_issues") is False:
        raise CoopError(
            "Issues are turned off in "
            + repo
            + ". Turn them on in the repository settings on GitHub, then try again. Nothing was changed."
        )
    full = data.get("full_name")
    if isinstance(full, str) and github.valid_repo(full):
        return full
    return repo


def _require_private(client: github.Client, repo: str, project: str, state: dict) -> None:
    try:
        reply = client.call("GET", "/repos/" + repo, etag=str(state.get("repo_etag") or ""))
    except github.NotFound as exc:
        raise TransportDown(repo + " was not found, or this sign-in cannot see it.", code=1) from exc
    if reply.status == 304:
        if state.get("blocked"):
            raise NotPrivate(repo, project)
        return
    if not is_private(reply.data):
        raise NotPrivate(repo, project)
    state["repo_etag"] = reply.etag


def _private_for_post(client: github.Client, board: Path, project: str, repo: str) -> None:
    state = load_state(board, project)
    recent = time.time() - float(state.get("last_ok_at") or 0) < TRUST_SECONDS
    if recent and state.get("repo") == repo and not state.get("blocked"):
        return
    _require_private(client, repo, project, {})


def _poll_seconds(board: Path) -> int:
    try:
        value = int(load_config(board).get("github_poll_seconds") or POLL_SECONDS)
    except (TypeError, ValueError):
        value = POLL_SECONDS
    return max(5, value)


def _members(board: Path, project: str) -> set[str]:
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    names = {str(record.get("driver") or "")}
    names.update(str(name) for name in (record.get("helpers") or {}))
    names.discard("")
    return names


def _issue_number(url: object) -> int | None:
    if not isinstance(url, str):
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _edited(comment: dict) -> bool:
    created = comment.get("created_at")
    updated = comment.get("updated_at")
    return isinstance(created, str) and isinstance(updated, str) and created != updated


def _login(comment: dict) -> str:
    user = comment.get("user")
    if isinstance(user, dict) and isinstance(user.get("login"), str):
        return user["login"]
    return ""


def _has_copy(board: Path, project: str, recipient: str, message_id: str) -> bool:
    name = message_id + ".json"
    if (inbox_msgs(board, project, recipient) / name).is_file():
        return True
    return (archive_dir(board, project, recipient) / "msgs" / name).is_file()


def _parse_message(raw: object, project: str, members: set[str]):
    from agentcoop.core import _message_from_dict

    if not isinstance(raw, dict):
        return None
    try:
        parsed = _message_from_dict(raw, None)
    except (ValueError, TypeError):
        return None
    if parsed.project != project:
        return None
    if not AGENT_NAME_RE.fullmatch(parsed.sender) or not AGENT_NAME_RE.fullmatch(parsed.recipient):
        return None
    if parsed.recipient not in members:
        return None
    return parsed


def _apply_page(
    board: Path,
    project: str,
    members: set[str],
    comments: list,
    result: PullResult,
    touched: set[str],
) -> None:
    from agentcoop.core import ID_RE, write_message_file

    writes = []
    dones = []
    with board_lock(board):
        index = load_index(board, project)
        changed = False
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            event = parse_event(comment.get("body"))
            comment_id = comment.get("id")
            issue = _issue_number(comment.get("issue_url"))
            if event is None or not isinstance(comment_id, int) or issue is None:
                continue
            if event["kind"] == "done":
                dones.append((comment_id, event))
                continue
            if event["kind"] != "message":
                continue
            raw = event.get("message")
            parsed = _parse_message(raw, project, members)
            if parsed is None:
                continue
            entry = index["messages"].get(parsed.id)
            if not isinstance(entry, dict):
                entry = {"issue": issue, "comment": comment_id, "login": _login(comment)}
                if _edited(comment):
                    entry["edited"] = True
                index["messages"][parsed.id] = entry
                if parsed.group:
                    index["groups"].setdefault(parsed.group, issue)
                changed = True
                writes.append((parsed, raw))
            elif entry.get("comment") == comment_id:
                if _edited(comment) and not entry.get("edited") and not entry.get("changed_later"):
                    entry["changed_later"] = True
                    changed = True
                if not _has_copy(board, project, parsed.recipient, parsed.id):
                    writes.append((parsed, raw))
            # Otherwise a later comment reused an id. The first comment keeps it.
        if changed:
            _save_index(board, project, index)
    for parsed, raw in writes:
        ensure_agent_dirs(board, project, parsed.recipient)
        try:
            write_message_file(inbox_msgs(board, project, parsed.recipient), raw)
        except FileExistsError:
            pass
        else:
            result.messages += 1
            touched.add(parsed.recipient)
        if parsed.sender in members:
            ensure_agent_dirs(board, project, parsed.sender)
            try:
                write_message_file(sent_dir(board, project, parsed.sender), raw)
            except FileExistsError:
                pass
    if not dones:
        return
    with board_lock(board):
        index = load_index(board, project)
        changed = False
        for comment_id, event in dones:
            agent = event.get("agent")
            ids = event.get("ids")
            if event.get("project") != project or not isinstance(ids, list):
                continue
            if not isinstance(agent, str) or not AGENT_NAME_RE.fullmatch(agent) or agent not in members:
                continue
            for mid in ids:
                if not isinstance(mid, str) or not ID_RE.fullmatch(mid) or mid not in index["messages"]:
                    continue
                key = agent + "/" + mid
                if key not in index["done"]:
                    index["done"][key] = comment_id
                    changed = True
                if not (inbox_msgs(board, project, agent) / (mid + ".json")).is_file():
                    continue
                marker = inbox_done(board, project, agent) / mid
                if not marker.exists():
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.touch()
                    result.done += 1
                    touched.add(agent)
        if changed:
            _save_index(board, project, index)


def _pull_locked(board: Path, project: str, repo: str, state: dict, result: PullResult) -> None:
    client = github.client()
    _require_private(client, repo, project, state)
    members = _members(board, project)
    since = str(state.get("since") or "")
    query = {"sort": "created", "direction": "asc", "per_page": "100"}
    if since:
        query["since"] = since
    newest = since
    etag = str(state.get("etag") or "")
    touched: set[str] = set()
    first = True
    path = "/repos/" + repo + "/issues/comments"
    for reply in client.pages(path, query=query, etag=etag):
        if reply.status == 304:
            break
        if first:
            etag = reply.etag
            first = False
        comments = reply.data if isinstance(reply.data, list) else []
        for comment in comments:
            stamp = comment.get("updated_at") if isinstance(comment, dict) else None
            if isinstance(stamp, str) and stamp > newest:
                newest = stamp
        _apply_page(board, project, members, comments, result, touched)
    state["since"] = newest
    state["etag"] = etag
    if touched:
        from agentcoop.outbox import refresh_new_md_for_agent
        from agentcoop.statusfile import refresh_status

        for agent in sorted(touched):
            refresh_new_md_for_agent(board, project, agent)
        refresh_status(board)


def pull(board: Path, project: str, repo: str, *, force: bool = False) -> PullResult:
    """Copy new comments from GitHub into the board folders. Skips when one ran a moment ago."""
    result = PullResult()
    state = load_state(board, project)
    if state.get("repo") not in (None, repo):
        state = {}
    if not force and time.time() - float(state.get("last_pull") or 0) < _poll_seconds(board):
        result.skipped = True
        if state.get("blocked"):
            raise NotPrivate(repo, project)
        return result
    from agentcoop.rounds import _try_lock, _unlock

    lock = _try_lock(sync_dir(board, project) / "sync.lock")
    if lock is None:
        result.skipped = True
        return result
    try:
        state = load_state(board, project)
        if state.get("repo") not in (None, repo):
            state = {}
        state["repo"] = repo
        state["last_pull"] = time.time()
        try:
            _pull_locked(board, project, repo, state, result)
        except NotPrivate as exc:
            state["blocked"] = True
            state["last_error"] = str(exc)
            _save_state(board, project, state)
            raise
        except CoopError as exc:
            state["last_error"] = str(exc)
            _save_state(board, project, state)
            raise
        state.pop("blocked", None)
        state["last_error"] = ""
        state["last_ok"] = now_stamp()
        state["last_ok_at"] = time.time()
        _save_state(board, project, state)
    finally:
        _unlock(lock)
    return result


def stale_line(board: Path, project: str) -> str:
    """Return one line when the copy on this computer may be behind GitHub."""
    state = load_state(board, project)
    error = str(state.get("last_error") or "")
    if not error:
        return ""
    last = str(state.get("last_ok") or "")
    line = "GitHub could not be read. " + error
    if last:
        line += " The mail below is the copy from " + last + "."
    else:
        line += " Nothing has been read from GitHub yet."
    return line


def refresh(board: Path, project: str, repo: str, *, force: bool = False) -> str:
    """Pull when one is due. Return a line to show when the copy may be old.

    Reading keeps working from the last copy when GitHub cannot be reached.
    A repository that is no longer private stops the command.
    """
    try:
        pull(board, project, repo, force=force)
    except NotPrivate:
        raise
    except CoopError:
        pass
    return stale_line(board, project)


def _thread_for(index: dict, message: dict) -> int | None:
    for key in ("re", "about", "moved_from"):
        value = message.get(key)
        entry = index["messages"].get(value) if isinstance(value, str) else None
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int):
            return entry["issue"]
    group = message.get("group")
    if isinstance(group, str) and isinstance(index["groups"].get(group), int):
        return index["groups"][group]
    return None


def _open_issue(client: github.Client, repo: str, project: str, message: dict) -> int:
    reply = client.call(
        "POST",
        "/repos/" + repo + "/issues",
        body={
            "title": thread_title(project, str(message.get("subject") or "")),
            "body": render_thread(project, str(message.get("id"))),
            "labels": [LABEL],
        },
    )
    number = reply.data.get("number") if isinstance(reply.data, dict) else None
    if not isinstance(number, int):
        raise TransportDown("GitHub did not open the conversation. The message was not sent.")
    return number


def _close_issue(client: github.Client, repo: str, issue: int) -> None:
    try:
        client.call(
            "PATCH",
            "/repos/" + repo + "/issues/" + str(issue),
            body={"state": "closed", "state_reason": "not_planned"},
        )
    except CoopError:
        pass


def post_message(board: Path, project: str, repo: str, message: dict) -> None:
    """Post one message. Raises when GitHub did not take it, and then nothing is written here."""
    text = render_message(message)
    client = github.client()
    _private_for_post(client, board, project, repo)
    index = load_index(board, project)
    issue = _thread_for(index, message)
    opened = False
    if issue is None:
        issue = _open_issue(client, repo, project, message)
        opened = True
    path = "/repos/" + repo + "/issues/"
    try:
        try:
            reply = client.call("POST", path + str(issue) + "/comments", body={"body": text})
        except github.NotFound:
            if opened:
                raise
            # The conversation was deleted on GitHub. Start a new one.
            issue = _open_issue(client, repo, project, message)
            opened = True
            reply = client.call("POST", path + str(issue) + "/comments", body={"body": text})
    except CoopError:
        if opened:
            _close_issue(client, repo, issue)
        raise
    data = reply.data if isinstance(reply.data, dict) else {}
    comment_id = data.get("id")
    if not isinstance(comment_id, int):
        raise TransportDown("GitHub did not confirm the message. Run: agentcoop sync")
    with board_lock(board):
        index = load_index(board, project)
        index["messages"].setdefault(
            str(message.get("id")),
            {"issue": issue, "comment": comment_id, "login": _login(data)},
        )
        group = message.get("group")
        if isinstance(group, str) and group:
            index["groups"].setdefault(group, issue)
        _save_index(board, project, index)


def post_done(board: Path, project: str, repo: str, agent: str, ids: list[str]) -> None:
    """Post done markers for messages that came through GitHub."""
    index = load_index(board, project)
    by_issue: dict[int, list[str]] = {}
    for mid in ids:
        entry = index["messages"].get(mid)
        if not isinstance(entry, dict) or not isinstance(entry.get("issue"), int):
            continue
        if (agent + "/" + mid) in index["done"]:
            continue
        by_issue.setdefault(entry["issue"], []).append(mid)
    if not by_issue:
        return
    client = github.client()
    _private_for_post(client, board, project, repo)
    for issue, group in by_issue.items():
        event = done_event(project, agent, group, now_stamp())
        reply = client.call(
            "POST",
            "/repos/" + repo + "/issues/" + str(issue) + "/comments",
            body={"body": render_done(event)},
        )
        data = reply.data if isinstance(reply.data, dict) else {}
        with board_lock(board):
            fresh = load_index(board, project)
            for mid in group:
                fresh["done"].setdefault(agent + "/" + mid, data.get("id") or 0)
            _save_index(board, project, fresh)


def local_only(board: Path, project: str) -> list[Path]:
    """Return message files in this project's inboxes that never went through GitHub."""
    index = _cached_index(board, project)
    found = []
    root = project_dir(board, project) / "inbox"
    if not root.is_dir():
        return found
    for folder in sorted(root.glob("*/msgs")):
        for path in sorted(folder.glob("*.json")):
            if path.name.startswith("."):
                continue
            if path.stem not in index["messages"]:
                found.append(path)
    return found


def edited_ids(board: Path, project: str) -> list[str]:
    """Return ids of messages whose comment was changed on GitHub after it was posted."""
    index = _cached_index(board, project)
    return sorted(
        mid
        for mid, entry in index["messages"].items()
        if isinstance(entry, dict) and (entry.get("edited") or entry.get("changed_later"))
    )
