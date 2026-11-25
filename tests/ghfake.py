"""An in-memory GitHub for tests. Nothing here touches the network."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop import github
from agentcoop.errors import TransportDown

TOKEN = "test-token-for-the-fake"


class FakeGitHub:
    """Answers the few API calls Agent Co-op makes, for one repository."""

    def __init__(self, repo: str = "octo/board", *, private: bool = True, visibility: str = "private") -> None:
        self.repo = repo
        self.private = private
        self.visibility = visibility
        self.has_issues = True
        self.archived = False
        self.issues: list[dict] = []
        self.comments: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.auth: list[str] = []
        self.down = False
        self.fail_comments = False
        self.token = TOKEN
        self.empty = False
        self.label_names: set[str] = set()
        self.drop_labels = False
        self.branches: dict[str, str] = {"main": "c0"}
        self.files: dict[tuple[str, str], bytes] = {}
        self.objects = 0
        self._moment = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _stamp(self) -> str:
        self._moment += timedelta(seconds=1)
        return self._moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    def events(self, kind: str) -> list[dict]:
        """Return the comments that carry one kind of event, oldest first."""
        from agentcoop.ghwire import parse_event

        found = []
        for comment in self.comments:
            event = parse_event(comment["body"])
            if event is not None and event["kind"] == kind:
                found.append(comment)
        return found

    def messages(self) -> list[dict]:
        """Return the comments that are messages or done markers, oldest first."""
        from agentcoop.ghwire import parse_event

        return [c for c in self.comments if (parse_event(c["body"]) or {}).get("kind") in ("message", "done")]

    def threads(self) -> list[dict]:
        """Return the conversation issues, without the project's own issue."""
        return [issue for issue in self.issues if issue.get("title") != "Project members and requests"]

    def labels_of(self, issue: dict) -> list[str]:
        """Return the label names on an issue."""
        return [label["name"] for label in issue.get("labels") or []]

    def count(self, method: str, ending: str) -> int:
        """Count calls whose path ends with some text."""
        return sum(1 for verb, path in self.calls if verb == method and path.endswith(ending))

    def edit_comment(self, comment_id: int, body: str) -> None:
        """Change a comment the way a person would on the site."""
        for comment in self.comments:
            if comment["id"] == comment_id:
                comment["body"] = body
                comment["updated_at"] = self._stamp()

    def hand_comment(self, issue: int, body: str) -> dict:
        """Add a comment a person typed on the site."""
        return self._add_comment(issue, body)

    def _add_comment(self, issue: int, body: str) -> dict:
        stamp = self._stamp()
        comment = {
            "id": 1000 + len(self.comments) + 1,
            "body": body,
            "created_at": stamp,
            "updated_at": stamp,
            "user": {"login": "octo"},
            "issue_url": "https://api.github.com/repos/" + self.repo + "/issues/" + str(issue),
        }
        self.comments.append(comment)
        return comment

    def _json(self, status: int, data: object, etag_for: bool = False, extra: dict | None = None):
        raw = json.dumps(data).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if etag_for:
            headers["ETag"] = '"' + hashlib.sha256(raw).hexdigest()[:16] + '"'
        headers.update(extra or {})
        return status, headers, raw

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None, timeout: float):
        if self.down:
            raise TransportDown(github.UNREACHABLE, code=1)
        parts = urllib.parse.urlsplit(url)
        assert parts.netloc == "api.github.com", url
        path = parts.path
        query = dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append((method, path))
        self.auth.append(str(headers.get("Authorization") or ""))
        if headers.get("Authorization") != "Bearer " + self.token:
            return self._json(401, {"message": "Bad credentials"})
        payload = json.loads(body.decode("utf-8")) if body else {}
        base = "/repos/" + self.repo
        if not path.lower().startswith(base.lower()):
            return self._json(404, {"message": "Not Found"})
        rest = path[len(base) :]
        if method == "GET" and rest == "":
            return self._conditional(
                headers,
                {
                    "full_name": self.repo,
                    "private": self.private,
                    "visibility": self.visibility,
                    "has_issues": self.has_issues,
                    "archived": self.archived,
                },
            )
        if method == "POST" and rest == "/labels":
            name = str(payload.get("name") or "")
            if name.lower() in self.label_names:
                return self._json(422, {"message": "Validation Failed"})
            self.label_names.add(name.lower())
            return self._json(201, {"name": name})
        if method == "POST" and rest == "/issues":
            issue = {"number": len(self.issues) + 1, "state": "open", **payload}
            wanted = [str(name) for name in payload.get("labels") or []]
            if self.drop_labels:
                wanted = []
            self.label_names.update(name.lower() for name in wanted)
            issue["labels"] = [{"name": name} for name in wanted]
            self.issues.append(issue)
            return self._json(201, issue)
        if method == "GET" and rest == "/issues/comments":
            return self._list_comments(headers, query)
        if rest.startswith("/git/") or rest.startswith("/contents/"):
            return self._git(method, rest, headers, query, payload)
        pieces = rest.strip("/").split("/")
        if len(pieces) >= 2 and pieces[0] == "issues" and pieces[1].isdigit():
            number = int(pieces[1])
            found = [issue for issue in self.issues if issue["number"] == number]
            if not found:
                return self._json(404, {"message": "Not Found"})
            if method == "GET" and len(pieces) == 2:
                return self._json(200, found[0])
            if method == "POST" and pieces[2:] == ["labels"]:
                have = {label["name"] for label in found[0]["labels"]}
                found[0]["labels"] += [{"name": str(n)} for n in payload.get("labels") or [] if str(n) not in have]
                return self._json(200, found[0]["labels"])
            if method == "PATCH" and len(pieces) == 2:
                found[0].update(payload)
                return self._json(200, found[0])
            if method == "POST" and pieces[2:] == ["comments"]:
                if self.fail_comments:
                    return self._json(500, {"message": "Server Error"})
                return self._json(201, self._add_comment(number, str(payload.get("body") or "")))
        return self._json(404, {"message": "Not Found"})

    def _git(self, method: str, rest: str, headers: dict, query: dict, payload: dict):
        import base64

        if self.empty:
            return self._json(409, {"message": "Git Repository is empty."})
        if method == "GET" and rest.startswith("/git/ref/heads/"):
            name = rest[len("/git/ref/heads/") :]
            if name not in self.branches:
                return self._json(404, {"message": "Not Found"})
            return self._json(200, {"ref": "refs/heads/" + name, "object": {"sha": self.branches[name]}})
        if method == "POST" and rest in ("/git/trees", "/git/commits"):
            self.objects += 1
            return self._json(201, {"sha": "obj" + str(self.objects)})
        if method == "POST" and rest == "/git/refs":
            name = str(payload.get("ref") or "")[len("refs/heads/") :]
            if name in self.branches:
                return self._json(422, {"message": "Reference already exists"})
            self.branches[name] = str(payload.get("sha"))
            return self._json(201, {"ref": payload.get("ref")})
        if rest.startswith("/contents/"):
            path = urllib.parse.unquote(rest[len("/contents/") :])
            if method == "GET":
                data = self.files.get((query.get("ref", "main"), path))
                if data is None:
                    return self._json(404, {"message": "Not Found"})
                if "raw" in str(headers.get("Accept")):
                    return 200, {"Content-Type": "application/octet-stream"}, data
                return self._json(200, {"path": path, "size": len(data)})
            if method == "PUT":
                branch = str(payload.get("branch") or "main")
                if branch not in self.branches:
                    return self._json(404, {"message": "Branch not found"})
                if (branch, path) in self.files:
                    return self._json(422, {"message": "sha wasn't supplied"})
                self.files[(branch, path)] = base64.b64decode(str(payload.get("content") or ""))
                return self._json(201, {"content": {"path": path}})
        return self._json(404, {"message": "Not Found"})

    def _conditional(self, headers: dict, data: object, extra: dict | None = None):
        status, found, raw = self._json(200, data, etag_for=True, extra=extra)
        if headers.get("If-None-Match") == found["ETag"]:
            return 304, {"ETag": found["ETag"]}, b""
        return status, found, raw

    def _list_comments(self, headers: dict, query: dict):
        since = query.get("since", "")
        rows = [comment for comment in self.comments if comment["updated_at"] >= since]
        rows.sort(key=lambda comment: (comment["created_at"], comment["id"]))
        per_page = int(query.get("per_page") or 30)
        page = int(query.get("page") or 1)
        chunk = rows[(page - 1) * per_page : page * per_page]
        extra = {}
        if page * per_page < len(rows):
            following = dict(query)
            following["page"] = str(page + 1)
            link = "https://api.github.com/repos/" + self.repo + "/issues/comments?"
            extra["Link"] = "<" + link + urllib.parse.urlencode(following) + '>; rel="next"'
        return self._conditional(headers, chunk, extra)


@contextmanager
def installed(fake: FakeGitHub, token: str | None = TOKEN):
    """Route Agent Co-op's GitHub calls to the fake, with a test sign-in."""
    old = os.environ.get(github.TOKEN_ENV)
    github.set_transport(fake)
    if token is None:
        os.environ.pop(github.TOKEN_ENV, None)
    else:
        os.environ[github.TOKEN_ENV] = token
    try:
        yield fake
    finally:
        github.set_transport(None)
        github.set_gh_runner(None)
        if old is None:
            os.environ.pop(github.TOKEN_ENV, None)
        else:
            os.environ[github.TOKEN_ENV] = old


@contextmanager
def computer(root: Path, name: str):
    """Act as another computer: its own home, board, and workspace under root."""
    home = root / (name + "-home")
    work = root / (name + "-work")
    board = root / (name + "-board")
    home.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    keys = {"HOME": str(home), "USERPROFILE": str(home), "AGENTCOOP_BOARD": str(board)}
    old = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield board, work
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
