"""Find agents that are already on this computer.

Filesystem detection paths are still TODO, so this only checks the local
model servers named in the spec. A quiet machine reports nothing.
"""

from __future__ import annotations

import json
from urllib.request import urlopen

from agentcoop.tools import get_tool


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


def detect_installed() -> list[dict]:
    """Return detected tools. Each item has tool, display, and maybe models."""
    found: list[dict] = []
    ollama = probe_json("http://localhost:11434/api/tags")
    if ollama is not None:
        models = []
        for item in ollama.get("models") or []:
            if isinstance(item, dict) and item.get("name"):
                models.append(str(item["name"]))
        found.append({"tool": "ollama", "display": get_tool("ollama").display, "models": models})
    studio = probe_json("http://localhost:1234/v1/models")
    if studio is not None:
        models = []
        for item in studio.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        found.append({"tool": "lmstudio", "display": get_tool("lmstudio").display, "models": models})
    return found
