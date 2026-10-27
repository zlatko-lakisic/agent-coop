"""Check the structure of a done report. The evidence itself is for a reviewer."""

from __future__ import annotations

import re

from agentcoop.errors import CoopError

SECTIONS = ("Asked", "Claims", "Not verified", "Surprises", "Risks")
MARKS = ("done", "partly", "not done", "deferred")
_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_ASKED = re.compile(r"^[-*]\s+(done|partly|not done|deferred)\s*:\s*(.+)$", re.I)
_CLAIM = re.compile(r"^[-*]\s+(.+)$")


def done_template() -> str:
    """Return the blank done report."""
    return """Status: done
Now:

Task: <the reviewed progress file, on Status: done>

## Asked
- done: <copy one requested item>
- partly: <copy one requested item>. Missing: <what is left>
- not done: <copy one requested item>. Why: <why>
- deferred: <copy one requested item>. Why: <why>. Where: <where it went>

## Claims
- <what works>. Evidence: <test name and result, command output, trace line, file path, or link>

## Not verified
None

## Surprises
None

## Risks
None
"""


def check_done_report(body: str) -> None:
    """Refuse a done or review report whose sections are incomplete."""
    sections = _sections(body)
    for name in SECTIONS:
        if name not in sections:
            raise CoopError("The done report is missing the " + name + " section.", code=2)
    asked = _asked(sections["Asked"])
    if not asked:
        raise CoopError("An Asked item has no mark.", code=2)
    for line in sections["Claims"].splitlines():
        text = line.strip()
        if not text or text.lower() == "none":
            continue
        if _CLAIM.match(text) and "evidence:" not in text.lower():
            raise CoopError("A claim has no evidence. Put it under Not verified instead.", code=2)
    open_items = [item for item in asked if item[0] != "done"]
    if open_items and _is_none(sections["Not verified"]):
        raise CoopError(
            "Not verified cannot be None while an Asked item is partly, not done, or deferred.",
            code=2,
        )


def _sections(body: str) -> dict[str, str]:
    found: dict[str, list[str]] = {}
    current = ""
    for line in body.splitlines():
        match = _HEADING.match(line.strip())
        if match and match.group(1) in SECTIONS:
            current = match.group(1)
            found.setdefault(current, [])
            continue
        if current:
            found[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in found.items()}


def _asked(text: str) -> list[tuple[str, str]]:
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower() == "none":
            continue
        match = _ASKED.match(stripped)
        if match is None:
            raise CoopError("An Asked item has no mark.", code=2)
        items.append((match.group(1).lower(), match.group(2).strip()))
    return items


def _is_none(text: str) -> bool:
    body = text.strip()
    return body == "" or body.lower() == "none"
