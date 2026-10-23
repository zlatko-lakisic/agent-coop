"""Download a published release with the Python standard library."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.version import (
    API_ROOT,
    RELEASES_PAGE,
    parse_version,
    tag_name,
    version_text,
)

RATE_LIMIT_LINE = (
    "GitHub's API limit was hit. Download a file from "
    + RELEASES_PAGE
    + " and run: agentcoop update --from-file <path>"
)
USER_AGENT = "agentcoop"
_SUM_NAME = re.compile(r"^[0-9a-fA-F]{64}$")


class RateLimitError(CoopError):
    """GitHub refused the request because the API limit was hit."""

    def __init__(self) -> None:
        super().__init__(RATE_LIMIT_LINE, code=1)


class Release:
    """One published GitHub release."""

    def __init__(self, version: tuple[int, int, int], prerelease: bool, assets: dict[str, str]) -> None:
        self.version = version
        self.prerelease = prerelease
        self.assets = assets

    @property
    def text(self) -> str:
        return version_text(self.version)


def http_get(url: str, timeout: float = 30) -> bytes:
    """GET a URL. urllib uses the system proxy settings.

    AGENTCOOP_GITHUB_TOKEN is optional. The upgrade job sets it so GitHub's
    shared runner limit does not stop the test. A normal install does not.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("AGENTCOOP_GITHUB_TOKEN") or ""
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        remaining = ""
        if exc.headers is not None:
            remaining = str(exc.headers.get("X-RateLimit-Remaining") or "")
        if exc.code == 429 or remaining == "0" or "rate limit" in detail.lower():
            raise RateLimitError() from exc
        if exc.code == 404:
            raise CoopError("GitHub has no file at that address.", code=1) from exc
        raise CoopError("GitHub returned " + str(exc.code) + ".", code=1) from exc
    except urllib.error.URLError as exc:
        raise CoopError("Could not reach GitHub.", code=1) from exc
    except TimeoutError as exc:
        raise CoopError("GitHub did not answer in time.", code=1) from exc


def parse_sums(text: str) -> dict[str, str]:
    """Return filename to lowercase hash from a SHA256SUMS file."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split()
        if len(parts) < 2 or not _SUM_NAME.fullmatch(parts[0]):
            continue
        name = parts[-1]
        if name.startswith("*"):
            name = name[1:]
        name = name.replace("\\", "/").split("/")[-1]
        if name:
            found[name] = parts[0].lower()
    return found


def file_hash(path: Path) -> str:
    """Return the lowercase SHA-256 hex digest of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_hash(path: Path, sums_text: str) -> None:
    """Stop when SHA256SUMS is missing a line or the hash does not match."""
    if not sums_text.strip():
        raise CoopError("SHA256SUMS is missing. Nothing was changed.", code=1)
    found = parse_sums(sums_text)
    if not found:
        raise CoopError("SHA256SUMS is missing. Nothing was changed.", code=1)
    expected = found.get(path.name)
    if not expected:
        raise CoopError("SHA256SUMS has no line for " + path.name + ". Nothing was changed.", code=1)
    actual = file_hash(path)
    if actual != expected:
        raise CoopError("The download hash does not match. Nothing was changed.", code=1)


def _release_from_api(item: object) -> Release | None:
    if not isinstance(item, dict):
        return None
    tag = str(item.get("tag_name") or "")
    try:
        version = parse_version(tag)
    except CoopError:
        return None
    assets: dict[str, str] = {}
    for asset in item.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name and url:
            assets[name] = url
    return Release(version, bool(item.get("prerelease")), assets)


def fetch_releases(fetch=None, timeout: float = 30) -> list[Release]:
    """Return published releases, newest first. Pre-releases stay in the list."""
    get = fetch or http_get
    raw = get(API_ROOT + "/releases?per_page=100", timeout)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CoopError("GitHub sent a release list that could not be read.", code=1) from exc
    if not isinstance(payload, list):
        raise CoopError("GitHub sent a release list that could not be read.", code=1)
    found: list[Release] = []
    for item in payload:
        release = _release_from_api(item)
        if release is not None:
            found.append(release)
    found.sort(key=lambda item: item.version, reverse=True)
    return found


def stable_releases(releases: list[Release]) -> list[Release]:
    """Drop pre-releases. The newest stable release stays first."""
    return [item for item in releases if not item.prerelease]


def fetch_latest_tag(fetch=None, timeout: float = 5) -> str | None:
    """Return the newest stable tag, or None when the answer is not usable.

    This is the only request the daily check makes.
    """
    get = fetch or http_get
    raw = get(API_ROOT + "/releases/latest", timeout)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CoopError("GitHub sent a release that could not be read.", code=1) from exc
    release = _release_from_api(payload)
    if release is None or release.prerelease:
        return None
    return release.text


def fetch_tag(version: tuple[int, int, int], fetch=None, timeout: float = 30) -> Release:
    """Return one release by tag."""
    get = fetch or http_get
    raw = get(API_ROOT + "/releases/tags/" + tag_name(version), timeout)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CoopError("GitHub sent a release that could not be read.", code=1) from exc
    release = _release_from_api(payload)
    if release is None:
        raise CoopError(version_text(version) + " is not a published version.", code=1)
    return release


def download_named(release: Release, name: str, dest: Path, fetch=None, timeout: float = 60) -> Path:
    """Download one asset from its browser_download_url into dest."""
    url = release.assets.get(name)
    if not url:
        raise CoopError(
            "The " + tag_name(release.version) + " release has no " + name + ".",
            code=1,
        )
    get = fetch or http_get
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(get(url, timeout))
    return dest


_HEADING = re.compile(r"^##\s+v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\b")


def headings_between(changelog: str, old: tuple[int, int, int], new: tuple[int, int, int]) -> list[str]:
    """Return changelog headings for versions after old, up to and including new."""
    lines: list[str] = []
    for line in changelog.splitlines():
        match = _HEADING.match(line.strip())
        if match is None:
            continue
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if old < version <= new:
            lines.append(line.strip())
    return lines


def changelog_url(version: tuple[int, int, int]) -> str:
    """Return the raw changelog URL for a tag."""
    return (
        "https://raw.githubusercontent.com/zlatko-lakisic/agent-coop/"
        + tag_name(version)
        + "/CHANGELOG.md"
    )


def fetch_changelog(version: tuple[int, int, int], fetch=None, timeout: float = 30) -> str:
    """Return the changelog text, or empty when it cannot be read."""
    get = fetch or http_get
    try:
        raw = get(changelog_url(version), timeout)
    except CoopError:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        return ""
