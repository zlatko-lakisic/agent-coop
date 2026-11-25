"""Turn a board event into a GitHub comment, and a comment back into an event.

A comment has two parts. The top is for people. The last line is a hidden
block that holds the exact JSON. The JSON is what agentcoop reads.
"""

from __future__ import annotations

import json
import re

from agentcoop.errors import CoopError

WIRE = 1
MARK = "<!-- agentcoop:" + str(WIRE) + " "
END = "-->"
MAX_COMMENT = 65536
MAX_TITLE = 240
NOT_DELIVERED = "A comment typed here by hand is not delivered to the agents."
TOO_LONG = (
    "This message is too long to send through GitHub. Send a shorter message, "
    "or split the text across two messages."
)
_TICKS = re.compile(r"`+")


def encode_block(event: dict) -> str:
    """Return the hidden block for one event. It is ASCII and can never hold -->."""
    text = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return MARK + text + " " + END


def parse_event(body: object) -> dict | None:
    """Return the event in a comment, or None when the comment is not one."""
    if not isinstance(body, str):
        return None
    text = body.rstrip()
    if not text.endswith(END):
        return None
    start = text.rfind(MARK)
    if start < 0:
        return None
    raw = text[start + len(MARK) : len(text) - len(END)].strip()
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("kind"), str):
        return None
    return data


def _longest_ticks(text: str) -> int:
    return max((len(match.group(0)) for match in _TICKS.finditer(text)), default=0)


def _code(text: str) -> str:
    """Show text exactly, so a name or a link in it does nothing on GitHub."""
    flat = " ".join(str(text).split())
    if not flat:
        return ""
    ticks = "`" * (_longest_ticks(flat) + 1)
    pad = " " if flat.startswith("`") or flat.endswith("`") else ""
    return ticks + pad + flat + pad + ticks


def _fence(text: str) -> str:
    ticks = "`" * max(3, _longest_ticks(text) + 1)
    return ticks + "text\n" + text.rstrip("\n") + "\n" + ticks


def message_event(message: dict) -> dict:
    """Wrap a message for the wire."""
    return {"kind": "message", "message": message}


def render_message(message: dict) -> str:
    """Return the comment for one message."""
    head = "**" + str(message.get("sender")) + "** to **" + str(message.get("recipient")) + "**"
    head += " · " + str(message.get("type"))
    if message.get("needs_reply"):
        head += " · needs a reply"
    lines = [head, ""]
    facts = "Sent " + str(message.get("ts")) + " · id " + _code(str(message.get("id")))
    if message.get("re"):
        facts += " · answers " + _code(str(message.get("re")))
    lines.append(facts)
    subject = str(message.get("subject") or "")
    if subject.strip():
        lines.extend(["", "**Subject:** " + _code(subject)])
    block = encode_block(message_event(message))
    foot = ["", "<sub>Agent Co-op message. " + NOT_DELIVERED + "</sub>", "", block]
    body = str(message.get("body") or "")
    full = lines + (["", _fence(body)] if body.strip() else []) + foot
    text = "\n".join(full)
    if len(text) <= MAX_COMMENT:
        return text
    short = lines + ["", "The text is too long to show here. It is stored with this comment."] + foot
    text = "\n".join(short)
    if len(text) > MAX_COMMENT:
        raise CoopError(TOO_LONG)
    return text


def done_event(project: str, agent: str, ids: list[str], ts: str) -> dict:
    """Build the event that says an agent handled some messages."""
    return {"kind": "done", "project": project, "agent": agent, "ids": list(ids), "ts": ts}


def render_done(event: dict) -> str:
    """Return the comment for a done marker."""
    ids = [str(item) for item in event.get("ids") or []]
    noun = "1 message" if len(ids) == 1 else str(len(ids)) + " messages"
    line = "**" + str(event.get("agent")) + "** marked " + noun + " done: "
    line += ", ".join(_code(item) for item in ids)
    return "\n".join([line, "", encode_block(event)])


def thread_title(project: str, subject: str) -> str:
    """Return the issue title for a new conversation."""
    flat = " ".join(str(subject).split()) or "(no subject)"
    return ("[" + project + "] " + flat)[:MAX_TITLE]


def render_thread(project: str, root_id: str) -> str:
    """Return the issue text for a new conversation."""
    lines = [
        "Agent Co-op conversation in the project " + _code(project) + ".",
        "",
        "Each comment below is one message, posted by the agentcoop command. " + NOT_DELIVERED,
        "",
        encode_block({"kind": "thread", "project": project, "root": root_id}),
    ]
    return "\n".join(lines)


def project_title(project: str) -> str:
    """Return the title of the issue that holds a project's roster and requests."""
    return ("[" + project + "] Agent Co-op project")[:MAX_TITLE]


def render_project(project: str) -> str:
    """Return the issue text for a project's own issue."""
    lines = [
        "Agent Co-op keeps the member list, join and leave requests, and the context for the project "
        + _code(project)
        + " here.",
        "",
        "Each comment below was posted by the agentcoop command. " + NOT_DELIVERED,
        "",
        encode_block({"kind": "project", "project": project}),
    ]
    return "\n".join(lines)


def roster_event(project: str, driver: str, helpers: dict, homes: dict, ts: str) -> dict:
    """Build the event that says who is in a project and which computer each agent works on."""
    return {
        "kind": "roster",
        "project": project,
        "driver": driver,
        "helpers": dict(helpers),
        "homes": dict(homes),
        "ts": ts,
    }


def render_roster(event: dict) -> str:
    """Return the comment for a roster."""
    helpers = event.get("helpers") or {}
    lines = ["**Members of " + str(event.get("project")) + "**", "", "Driver: " + _code(str(event.get("driver")))]
    if helpers:
        for name in sorted(helpers):
            note = str(helpers[name] or "")
            lines.append("Helper: " + _code(str(name)) + ((" (" + _code(note) + ")") if note.strip() else ""))
    else:
        lines.append("Helpers: none")
    lines.extend(["", encode_block(event)])
    return "\n".join(lines)


def request_event(project: str, action: str, agent: str, home: str, note: str, ts: str) -> dict:
    """Build a join or leave request from an agent on another computer."""
    return {"kind": "request", "project": project, "action": action, "agent": agent, "home": home, "note": note, "ts": ts}


def render_request(event: dict) -> str:
    """Return the comment for a join or leave request."""
    action = "join" if event.get("action") == "join" else "leave"
    line = "**" + str(event.get("agent")) + "** asks to " + action + " " + _code(str(event.get("project"))) + "."
    return "\n".join([line, "", encode_block(event)])


def context_events(project: str, name: str, text: str, chunk: int = 8000) -> list[dict]:
    """Split one context version into events that each fit in a comment."""
    pieces = [text[start : start + chunk] for start in range(0, len(text), chunk)] or [""]
    return [
        {"kind": "context", "project": project, "name": name, "part": index + 1, "of": len(pieces), "text": piece}
        for index, piece in enumerate(pieces)
    ]


def render_context(event: dict) -> str:
    """Return the comment for one part of a context version."""
    head = "**Project context** " + _code(str(event.get("name")))
    if int(event.get("of") or 1) > 1:
        head += " · part " + str(event.get("part")) + " of " + str(event.get("of"))
    block = encode_block(event)
    text = "\n".join([head, "", _fence(str(event.get("text") or "")), "", block])
    if len(text) <= MAX_COMMENT:
        return text
    return "\n".join([head, "", "The text is stored with this comment.", "", block])


def closed_event(project: str, ts: str) -> dict:
    """Build the event that says a project went back to files."""
    return {"kind": "closed", "project": project, "ts": ts}


def render_closed(event: dict) -> str:
    """Return the comment for a project that went back to files."""
    line = "**" + str(event.get("project")) + "** no longer talks through GitHub. Its driver's computer went back to files."
    return "\n".join([line, "", encode_block(event)])
