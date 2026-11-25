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
