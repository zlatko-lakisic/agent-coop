"""Sign in to GitHub and call its API with the Python standard library.

Only a project that names a private repository uses this module. A project
that talks through files never imports it.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from agentcoop.errors import CoopError, TransportDown
from agentcoop.schema import GITHUB_REPO_RE as REPO_RE

API_HOST = "api.github.com"
API_URL = "https://" + API_HOST
TOKEN_ENV = "AGENTCOOP_GITHUB_TOKEN"
USER_AGENT = "agentcoop"

NO_SIGN_IN = (
    "Agent Co-op is not signed in to GitHub. Run: gh auth login. "
    "Or set " + TOKEN_ENV + " to a token that can read and write issues in that repository."
)
SIGN_IN_REFUSED = "GitHub did not accept the sign-in. Run: gh auth login"
UNREACHABLE = "Could not reach GitHub."
TOO_SLOW = "GitHub did not answer in time."

_TOKEN_SECONDS = 300
_transport = None
_gh_runner = None
_token_cache: tuple[str, str, float] | None = None


class NotFound(TransportDown):
    """GitHub has nothing at that address, or the sign-in cannot see it."""

    def __init__(self, message: str = "GitHub has nothing at that address.") -> None:
        super().__init__(message, code=1)


class LimitHit(TransportDown):
    """GitHub refused the request because a limit was hit."""

    def __init__(self, until: str = "") -> None:
        text = "GitHub's limit was hit."
        if until:
            text += " Try again after " + until + "."
        else:
            text += " Try again in a few minutes."
        super().__init__(text, code=1)


class Conflict(TransportDown):
    """GitHub could not do that in the repository's present state."""

    def __init__(self, detail: str = "") -> None:
        line = "GitHub could not do that right now."
        if detail:
            line += " " + detail
        super().__init__(line, code=1)
        self.detail = detail


class Reply:
    """One answer from the API. Header names are lowercase."""

    def __init__(self, status: int, data: object, headers: dict[str, str], raw: bytes = b"") -> None:
        self.status = status
        self.data = data
        self.headers = headers
        self.raw = raw

    @property
    def etag(self) -> str:
        return self.headers.get("etag", "")


def set_transport(transport) -> None:
    """Replace the network call. Tests pass a fake. None restores the real one."""
    global _transport, _token_cache
    _transport = transport
    _token_cache = None


def set_gh_runner(runner) -> None:
    """Replace the call to the gh program. Tests pass a fake. None restores the real one."""
    global _gh_runner, _token_cache
    _gh_runner = runner
    _token_cache = None


def _offline() -> bool:
    return os.environ.get("AGENTCOOP_TEST_OFFLINE") == "1"


def valid_repo(text: str) -> bool:
    """Say whether text looks like owner/name."""
    if not isinstance(text, str) or not REPO_RE.fullmatch(text):
        return False
    name = text.split("/", 1)[1]
    return name not in (".", "..") and not name.endswith(".git")


def require_repo(text: str) -> str:
    """Return owner/name, or stop with a plain sentence."""
    value = str(text or "").strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
    value = value.strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not valid_repo(value):
        raise CoopError(
            "Name the repository as owner/name. Example: --github your-name/agent-board",
            code=2,
        )
    return value


def _quiet() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _gh_token() -> str:
    runner = _gh_runner
    if runner is None:
        if _offline():
            return ""
        runner = subprocess.run
    program = shutil.which("gh") if _gh_runner is None else "gh"
    if not program:
        return ""
    try:
        result = runner(
            [program, "auth", "token", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            timeout=15,
            **_quiet(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if getattr(result, "returncode", 1) != 0:
        return ""
    lines = str(getattr(result, "stdout", "") or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def find_token() -> tuple[str, str]:
    """Return the token and where it came from. The token is never stored on the board."""
    global _token_cache
    env = (os.environ.get(TOKEN_ENV) or "").strip()
    if env:
        return env, TOKEN_ENV
    now = time.monotonic()
    if _token_cache is not None and _token_cache[2] > now:
        return _token_cache[0], _token_cache[1]
    token = _gh_token()
    if not token:
        raise TransportDown(NO_SIGN_IN, code=1)
    _token_cache = (token, "gh", now + _TOKEN_SECONDS)
    return token, "gh"


def sign_in_source() -> str:
    """Return a few words on where the sign-in comes from, or an empty string."""
    try:
        _token, source = find_token()
    except CoopError:
        return ""
    return "the gh program" if source == "gh" else source


def _urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        if key.lower() == "authorization":
            # Never repeated when GitHub sends the request to another address.
            request.add_unredirected_header(key, value)
        else:
            request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        found = dict(exc.headers.items()) if exc.headers is not None else {}
        return exc.code, found, raw
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TransportDown(TOO_SLOW, code=1) from exc
        raise TransportDown(UNREACHABLE, code=1) from exc
    except TimeoutError as exc:
        raise TransportDown(TOO_SLOW, code=1) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise TransportDown(UNREACHABLE, code=1) from exc


def _reset_text(headers: dict[str, str]) -> str:
    raw = headers.get("x-ratelimit-reset", "")
    wait = headers.get("retry-after", "")
    moment = None
    try:
        if wait:
            moment = datetime.fromtimestamp(time.time() + int(wait), timezone.utc)
        elif raw:
            moment = datetime.fromtimestamp(int(raw), timezone.utc)
    except (ValueError, OverflowError, OSError):
        moment = None
    if moment is None:
        return ""
    return moment.strftime("%H:%M") + " UTC"


class Client:
    """A small GitHub API client. It talks to api.github.com and nowhere else."""

    def __init__(self, token: str, *, timeout: float = 30) -> None:
        self._token = token
        self.timeout = timeout

    def _url(self, path: str, query: dict | None) -> str:
        if path.startswith("https://"):
            url = path
        else:
            url = API_URL + path
        if urllib.parse.urlsplit(url).netloc.lower() != API_HOST:
            raise TransportDown("That address is not on GitHub's API.", code=1)
        if query:
            joiner = "&" if "?" in url else "?"
            url += joiner + urllib.parse.urlencode(query)
        return url

    def call(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        query: dict | None = None,
        etag: str = "",
        accept: str = "",
        timeout: float | None = None,
    ) -> Reply:
        """Send one request. A 304 comes back as a Reply. Other failures raise.

        accept asks for another form of the answer, such as a file's own bytes.
        """
        transport = _transport
        if transport is None:
            if _offline():
                raise TransportDown(UNREACHABLE, code=1)
            transport = _urllib_transport
        headers = {
            "Accept": accept or "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": "Bearer " + self._token,
        }
        if etag:
            headers["If-None-Match"] = etag
        raw_body = None
        if body is not None:
            raw_body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, found, raw = transport(method, self._url(path, query), headers, raw_body, timeout or self.timeout)
        lowered = {str(key).lower(): str(value) for key, value in (found or {}).items()}
        data: object = None
        text = ""
        wants_bytes = bool(accept) and 200 <= status < 300
        if raw and not wants_bytes:
            text = raw.decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
            except ValueError:
                data = None
        if status == 304 or 200 <= status < 300:
            return Reply(status, data, lowered, raw or b"")
        detail = ""
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            detail = data["message"]
        if status == 401:
            raise TransportDown(SIGN_IN_REFUSED, code=1)
        limited = (
            status == 429
            or lowered.get("x-ratelimit-remaining") == "0"
            or "retry-after" in lowered
            or "rate limit" in detail.lower()
        )
        if status in (403, 429) and limited:
            raise LimitHit(_reset_text(lowered))
        if status in (404, 410):
            raise NotFound()
        if status == 409:
            raise Conflict(detail.splitlines()[0][:200] if detail else "")
        if status == 403:
            reason = detail.splitlines()[0][:200] if detail else ""
            raise TransportDown(
                "GitHub refused that. "
                + (reason if reason else "The sign-in may not be allowed to use this repository."),
                code=1,
            )
        line = "GitHub returned " + str(status) + "."
        if detail:
            line += " " + detail.splitlines()[0][:200]
        raise TransportDown(line, code=1)

    def pages(self, path: str, *, query: dict | None = None, etag: str = "", limit: int = 200):
        """Yield one Reply per page, following GitHub's next links."""
        reply = self.call("GET", path, query=query, etag=etag)
        yield reply
        count = 1
        while reply.status != 304 and count < limit:
            following = _next_link(reply.headers.get("link", ""))
            if not following:
                return
            reply = self.call("GET", following)
            yield reply
            count += 1


def _next_link(header: str) -> str:
    for part in header.split(","):
        section = part.strip()
        if 'rel="next"' not in section:
            continue
        start = section.find("<")
        end = section.find(">", start + 1)
        if start >= 0 and end > start:
            return section[start + 1 : end]
    return ""


def client() -> Client:
    """Return a client for the saved sign-in, or stop with a plain sentence."""
    token, _source = find_token()
    return Client(token)
