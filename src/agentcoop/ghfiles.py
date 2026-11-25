"""Carry attached files for a project that talks through GitHub.

A file is committed to its own branch of the private repository, so it never
touches the branches people work on. A computer that receives the message
downloads the file, checks it, and keeps a copy in the shared folder.
"""

from __future__ import annotations

import base64
import hashlib
import re
import urllib.parse
from pathlib import Path

from agentcoop import github
from agentcoop.errors import CoopError, TransportDown
from agentcoop.projects import load_config

BRANCH = "agentcoop-files"
PREFIX = "github:"
DEFAULT_MAX_MB = 25
ON_GITHUB = "This file is on GitHub and was not copied to this computer."
REF_RE = re.compile(r"^([a-z0-9][a-z0-9-]{0,47})/([0-9a-f]{2})/([0-9a-f]{64})/([^/\\\x00-\x1f]{1,200})$")
_README = (
    "Files that agents shared through Agent Co-op live on this branch.\n\n"
    "The agentcoop command writes it. Do not merge it into another branch.\n"
)


def max_bytes(board: Path) -> int:
    """Return the largest file that may be sent through GitHub."""
    try:
        value = int(load_config(board).get("github_attach_max_mb") or DEFAULT_MAX_MB)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_MB
    return max(1, value) * 1024 * 1024


def too_big_line(name: str, board: Path) -> str:
    """Return the sentence for a file over the limit."""
    return (
        name
        + " is over "
        + str(max_bytes(board) // (1024 * 1024))
        + " MB, which is the most that can be sent through GitHub. It was not sent."
    )


def is_ref(text: object) -> bool:
    """Say whether an attachment path points at a file on GitHub."""
    return isinstance(text, str) and text.startswith(PREFIX)


def make_ref(project: str, digest: str, name: str) -> str:
    """Return the portable name of a file: where it lives on the files branch."""
    return PREFIX + project + "/" + digest[:2] + "/" + digest + "/" + name


def parse_ref(text: object, project: str) -> tuple[str, str, str] | None:
    """Return the path on the branch, the hash, and the file name. None when the name is not safe."""
    if not is_ref(text):
        return None
    path = str(text)[len(PREFIX) :]
    match = REF_RE.fullmatch(path)
    if match is None or match.group(1) != project or match.group(2) != match.group(3)[:2]:
        return None
    name = match.group(4)
    if name in (".", "..") or name.startswith(".") or name != name.strip():
        return None
    return path, match.group(3), name


def _contents(repo: str, path: str) -> str:
    return "/repos/" + repo + "/contents/" + urllib.parse.quote(path, safe="/")


def _ensure_branch(client: github.Client, repo: str) -> None:
    base = "/repos/" + repo + "/git/"
    try:
        client.call("GET", base + "ref/heads/" + BRANCH)
        return
    except github.NotFound:
        pass
    except github.Conflict as exc:
        raise _empty(repo) from exc
    try:
        tree = client.call(
            "POST",
            base + "trees",
            body={"tree": [{"path": "README.md", "mode": "100644", "type": "blob", "content": _README}]},
        )
        commit = client.call(
            "POST",
            base + "commits",
            body={"message": "Start the Agent Co-op files branch", "tree": (tree.data or {}).get("sha"), "parents": []},
        )
        client.call("POST", base + "refs", body={"ref": "refs/heads/" + BRANCH, "sha": (commit.data or {}).get("sha")})
    except (github.Conflict, github.NotFound) as exc:
        raise _empty(repo) from exc
    except CoopError as exc:
        if "422" in str(exc):
            # Another computer made the branch a moment ago.
            return
        raise


def _empty(repo: str) -> TransportDown:
    return TransportDown(
        repo
        + " has no commits yet, so a file cannot be stored there. Add a README to it on GitHub, then send the file again."
    )


def upload(repo: str, project: str, source: Path, digest: str, name: str) -> str:
    """Commit one file to the files branch. Returns its portable name."""
    ref = make_ref(project, digest, name)
    parsed = parse_ref(ref, project)
    if parsed is None:
        raise CoopError(name + " has a name that cannot be sent through GitHub. Rename the file and send it again.")
    path = parsed[0]
    client = github.client()
    try:
        _ensure_branch(client, repo)
        try:
            client.call("GET", _contents(repo, path), query={"ref": BRANCH}, accept="application/vnd.github.object+json")
            return ref
        except github.NotFound:
            pass
        body = {
            "message": "Share " + name,
            "content": base64.b64encode(source.read_bytes()).decode("ascii"),
            "branch": BRANCH,
        }
        for attempt in range(4):
            try:
                client.call("PUT", _contents(repo, path), body=body, timeout=300)
                return ref
            except github.Conflict:
                # Another computer committed at the same moment. Try again.
                if attempt == 3:
                    raise
        return ref
    except TransportDown as exc:
        text = str(exc)
        if text.startswith("GitHub refused that."):
            text += " To send a file, the sign-in must be allowed to write the contents of " + repo + "."
        raise TransportDown(text + " The message was not sent.", code=exc.code) from exc


def download(repo: str, path: str, digest: str, limit: int) -> bytes | None:
    """Fetch one file. Returns None when it is missing, too big, or not the file that was sent."""
    client = github.client()
    try:
        reply = client.call(
            "GET", _contents(repo, path), query={"ref": BRANCH}, accept="application/vnd.github.raw+json", timeout=300
        )
    except github.NotFound:
        return None
    data = reply.raw
    if len(data) > limit or hashlib.sha256(data).hexdigest() != digest:
        return None
    return data
