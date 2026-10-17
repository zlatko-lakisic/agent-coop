"""Find agents that are already on this computer."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

from agentcoop.paths import home_dir
from agentcoop.tools import TOOLS, get_tool

_WIN_ENV = re.compile(r"%([^%]+)%")


def _expand_path(raw: str) -> Path:
    """Expand ~, $VAR, and Windows-style %VAR% in a detect path."""
    text = raw
    while True:
        match = _WIN_ENV.search(text)
        if match is None:
            break
        value = os.environ.get(match.group(1), "")
        text = text[: match.start()] + value + text[match.end() :]
    return Path(os.path.expanduser(os.path.expandvars(text)))


def probe_json(url: str, timeout: float = 0.4) -> dict | None:
    """GET a JSON URL, or return None when the server is not there."""
    try:
        with urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _match_rule(
    rule: dict,
    home: Path,
    platform: str,
    which: Callable[[str], str | None],
) -> str | None:
    """Return a reason string when a detect rule matches, else None."""
    if "home_dir" in rule:
        name = str(rule["home_dir"])
        if (home / name).is_dir():
            return "found ~/" + name
        return None
    if "command" in rule:
        name = str(rule["command"])
        if which(name):
            return "found command " + name
        return None
    if "app" in rule:
        apps = rule["app"]
        if not isinstance(apps, dict):
            return None
        raw = apps.get(platform)
        if raw is None:
            return None
        if _expand_path(str(raw)).exists():
            return "found " + str(raw)
        return None
    return None


def detect_tools(
    home: Path,
    platform: str,
    which: Callable[[str], str | None] = shutil.which,
) -> list[dict]:
    """Apply each tool's detect rules and return matches with reasons."""
    found: list[dict] = []
    for tool in TOOLS:
        reason = None
        for rule in tool.detect:
            reason = _match_rule(rule, home, platform, which)
            if reason:
                break
        if reason:
            found.append(
                {"tool": tool.key, "display": tool.display, "reason": reason}
            )
    return found


def detect_installed(
    home: Path | None = None,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[dict]:
    """Return detected tools. Each item has tool, display, reason, and maybe models."""
    found = detect_tools(
        home if home is not None else home_dir(),
        platform if platform is not None else sys.platform,
        which,
    )
    ollama = probe_json("http://localhost:11434/api/tags")
    if ollama is not None:
        models = []
        for item in ollama.get("models") or []:
            if isinstance(item, dict) and item.get("name"):
                models.append(str(item["name"]))
        found.append(
            {
                "tool": "ollama",
                "display": get_tool("ollama").display,
                "reason": "found local server",
                "models": models,
            }
        )
    studio = probe_json("http://localhost:1234/v1/models")
    if studio is not None:
        models = []
        for item in studio.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        found.append(
            {
                "tool": "lmstudio",
                "display": get_tool("lmstudio").display,
                "reason": "found local server",
                "models": models,
            }
        )
    return found
