"""Short fields at the top of a progress update."""

from __future__ import annotations

import re

from agentcoop.errors import CoopError

STATUSES = ("working", "blocked", "waiting", "done", "review")
_STEP = re.compile(r"^(0|[1-9]\d*) of ([1-9]\d*)$")
_KEYS = ("Status", "Step", "Percent", "Now", "For")


def parse_status(value: str) -> str:
    """Return a status, or raise when it is not one of the four words."""
    text = (value or "").strip()
    if text not in STATUSES:
        raise CoopError(
            "Status must be working, blocked, waiting, review, or done. " + (text or "An empty status") + " is not one of those.",
            code=2,
        )
    return text


def parse_percent(value: str) -> int:
    """Return a whole percent from 0 to 100. Words and out-of-range numbers are refused."""
    text = (value or "").strip()
    if not text or not re.fullmatch(r"-?\d+", text):
        raise CoopError(
            "Percent must be a whole number from 0 to 100. " + (text or "An empty percent") + " is not one.",
            code=2,
        )
    number = int(text)
    if number < 0 or number > 100:
        raise CoopError("Percent must be a whole number from 0 to 100. " + text + " is not one.", code=2)
    return number


def parse_step(value: str) -> str:
    """Return a step such as 3 of 7."""
    text = " ".join((value or "").split())
    match = _STEP.fullmatch(text)
    if match is None:
        raise CoopError("Step looks like 3 of 7. " + (text or "An empty step") + " is not a step.", code=2)
    done = int(match.group(1))
    total = int(match.group(2))
    if total < 1 or done > total:
        raise CoopError("Step looks like 3 of 7. " + text + " is not a step.", code=2)
    return str(done) + " of " + str(total)


def parse_fields(body: str) -> dict:
    """Read the field lines at the top. A missing field stays empty.

    The rest of the file, from the first blank line or heading, is returned as rest.
    A bad value on a present field is refused.
    """
    found = {"Status": "", "Step": "", "Percent": "", "Now": "", "For": "", "rest": ""}
    rest: list[str] = []
    in_fields = True
    for line in (body or "").splitlines():
        if not in_fields:
            rest.append(line)
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            in_fields = False
            if stripped:
                rest.append(line)
            continue
        key, sep, value = stripped.partition(":")
        if sep and key in _KEYS:
            found[key] = value.strip()
            continue
        in_fields = False
        rest.append(line)
    if found["Status"]:
        found["Status"] = parse_status(found["Status"])
    if found["Step"]:
        found["Step"] = parse_step(found["Step"])
    if found["Percent"]:
        found["Percent"] = str(parse_percent(found["Percent"]))
    found["rest"] = "\n".join(rest).strip()
    return found


def format_fields(
    *,
    status: str = "",
    step: str = "",
    percent: str = "",
    now: str = "",
    for_id: str = "",
    rest: str = "",
) -> str:
    """Return a progress document with the short fields first."""
    lines = []
    if status:
        lines.append("Status: " + parse_status(status))
    if step:
        lines.append("Step: " + parse_step(step))
    if percent:
        lines.append("Percent: " + str(parse_percent(percent)))
    if now:
        lines.append("Now: " + " ".join(now.split()))
    if for_id:
        lines.append("For: " + for_id.strip())
    body = rest.strip()
    if lines and body:
        return "\n".join(lines) + "\n\n" + body + "\n"
    if lines:
        return "\n".join(lines) + "\n"
    return (body + "\n") if body else ""


def one_line(fields: dict) -> str:
    """Return the chat line: Progress: step 3 of 7 (43%). <Now>."""
    parts = ["Progress:"]
    step = str(fields.get("Step") or "")
    percent = str(fields.get("Percent") or "")
    if step and percent:
        parts.append("step " + step + " (" + percent + "%).")
    elif step:
        parts.append("step " + step + ".")
    elif percent:
        parts.append(percent + "%.")
    now = str(fields.get("Now") or "")
    if now:
        parts.append(now if now.endswith(".") else now + ".")
    status = str(fields.get("Status") or "")
    if status and status != "working":
        parts.append("Status: " + status + ".")
    return " ".join(parts)
