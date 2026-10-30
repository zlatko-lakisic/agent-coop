"""Build a board the viewer tests and screenshots can open.

Run: PYTHONPATH=src python scripts/sim/viewer_fixture.py <board> <workspace>
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentcoop.core import send
from agentcoop.projects import add_helper, add_project, connect_agent_record, init_board_files, save_projects, load_projects


def build_demo(board: Path, workspace: Path) -> str:
    """Create one project covering three days and the event kinds the viewer reads."""
    init_board_files(board)
    connect_agent_record(board, "cursor", "cursor", display="Cursor")
    connect_agent_record(board, "claude_desktop", "claude-desktop", display="Claude")
    connect_agent_record(board, "claude_code", "claude-code", display="Claude Code")
    connect_agent_record(board, "gemini", "gemini-cli", display="Gemini")
    folder = workspace / "demo"
    folder.mkdir(parents=True, exist_ok=True)
    add_project(board, "demo", folder, "cursor", {"claude_desktop": "answers", "claude_code": "reviews"})
    add_helper(board, "demo", "gemini", "second opinion")
    _backdate_roles(board)
    start = datetime(2026, 10, 25, 15, 0, 0, tzinfo=timezone.utc)
    question = send(
        board,
        project="demo",
        sender="cursor",
        recipient="claude_desktop",
        subject="Review the run",
        body="Please look at the last run.",
        type="question",
        now=start,
    )
    send(
        board,
        project="demo",
        sender="claude_desktop",
        recipient="cursor",
        subject="Re: Review the run",
        body="I looked. The first scene is fine.",
        type="answer",
        re=question,
        now=start + timedelta(minutes=5),
    )
    to_code = send(
        board,
        project="demo",
        sender="cursor",
        recipient="claude_code",
        subject="Second look",
        body="Please look too.",
        type="question",
        now=start + timedelta(minutes=6),
    )
    to_gemini = send(
        board,
        project="demo",
        sender="cursor",
        recipient="gemini",
        subject="Third look",
        body="Please look as well.",
        type="question",
        now=start + timedelta(minutes=7),
    )
    same = datetime(2026, 10, 27, 15, 22, 57, tzinfo=timezone.utc)
    send(
        board,
        project="demo",
        sender="claude_code",
        recipient="cursor",
        subject="Same second A",
        body="first of the pair",
        type="answer",
        re=to_code,
        now=same,
    )
    send(
        board,
        project="demo",
        sender="gemini",
        recipient="cursor",
        subject="Same second B",
        body="second of the pair",
        type="answer",
        re=to_gemini,
        group="second-opinion",
        now=same,
    )
    for index, (kind, subject, body) in enumerate(
        (
            ("task", "Do the next scene", "Build scene two."),
            ("status", "Status", "Still working."),
            ("ack", "Ack", "Got it."),
        )
    ):
        send(
            board,
            project="demo",
            sender="cursor",
            recipient="claude_desktop",
            subject=subject,
            body=body,
            type=kind,
            now=start + timedelta(hours=index + 1),
        )
    send(
        board,
        project="demo",
        sender="cursor",
        recipient="claude_desktop",
        subject="Cancel",
        body="Stop that request.",
        type="cancel",
        re=question,
        now=start + timedelta(hours=4),
    )
    send(
        board,
        project="demo",
        sender="claude_desktop",
        recipient="cursor",
        subject="Patch",
        body="A small text change.",
        type="patch",
        re=question,
        now=start + timedelta(hours=5),
    )
    send(
        board,
        project="demo",
        sender="claude_desktop",
        recipient="cursor",
        subject="Need context",
        body="What is the goal?",
        type="context_request",
        now=start + timedelta(hours=6),
    )
    send(
        board,
        project="demo",
        sender="claude_desktop",
        recipient="cursor",
        subject="Nudge",
        body="Still there?",
        type="nudge",
        now=start + timedelta(hours=7),
    )
    ping = send(
        board,
        project="demo",
        sender="claude_desktop",
        recipient="cursor",
        subject="Ping",
        body="Are you watching?",
        type="ping",
        now=start + timedelta(hours=8),
    )
    send(
        board,
        project="demo",
        sender="cursor",
        recipient="claude_desktop",
        subject="Heartbeat",
        body="Still here.",
        type="heartbeat",
        re=ping,
        now=start + timedelta(hours=8, minutes=1),
    )
    send(
        board,
        project="demo",
        sender="claude_desktop",
        recipient="cursor",
        subject="Decline",
        body="I cannot take this.",
        type="decline",
        re=question,
        now=start + timedelta(hours=9),
    )
    review = send(
        board,
        project="demo",
        sender="cursor",
        recipient="claude_code",
        subject="Please review",
        body="Review the scene.",
        type="review",
        now=start + timedelta(days=1),
    )
    send(
        board,
        project="demo",
        sender="claude_code",
        recipient="cursor",
        subject="Review verdict",
        body="Needs a change.",
        type="review",
        verdict="changes",
        re=review,
        now=start + timedelta(days=1, hours=1),
    )
    send(
        board,
        project="demo",
        sender="claude_code",
        recipient="cursor",
        subject="Review verdict",
        body="Approved.",
        type="review",
        verdict="approve",
        re=review,
        now=start + timedelta(days=1, hours=2),
    )
    send(
        board,
        project="demo",
        sender="cursor",
        recipient="claude_desktop",
        subject="Moved",
        body="This moved.",
        type="status",
        moved_from=question,
        now=start + timedelta(days=1, hours=3),
    )
    send(
        board,
        project="demo",
        sender="cursor",
        recipient="claude_desktop",
        subject="Secret note",
        body="The key is sk-abcdefghijklmnop",
        type="status",
        allow_secret=True,
        now=start + timedelta(days=1, hours=4),
    )
    root = board / "projects" / "demo"
    _progress(root)
    _context(root)
    _files(board)
    _outbox(root)
    _grades(root)
    _reviews(root)
    _requests(root)
    _board_files(board)
    data = load_projects(board)
    data["projects"]["demo"]["review"] = "changes"
    data["projects"]["demo"]["notices"] = ["claude_desktop joined as a helper."]
    save_projects(board, data)
    return "demo"


def _backdate_roles(board: Path) -> None:
    data = load_projects(board)
    history = data["projects"]["demo"].get("roles_history") or []
    base = datetime(2026, 10, 25, 14, 0, 0, tzinfo=timezone.utc)
    for index, row in enumerate(history):
        if isinstance(row, dict):
            row["since"] = (base + timedelta(minutes=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_projects(board, data)


def build_large(board: Path, workspace: Path, count: int = 20000) -> str:
    """Write many message files so a page can be timed."""
    init_board_files(board)
    connect_agent_record(board, "cursor", "cursor", display="Cursor")
    folder = workspace / "wide"
    folder.mkdir(parents=True, exist_ok=True)
    add_project(board, "wide", folder, "cursor", {})
    inbox = board / "projects" / "wide" / "inbox" / "cursor" / "msgs"
    inbox.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(count):
        when = start + timedelta(seconds=index)
        stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        mid = "m" + str(index).zfill(6)
        payload = {
            "v": 1,
            "id": mid,
            "ts": stamp,
            "project": "wide",
            "sender": "cursor",
            "recipient": "cursor",
            "type": "status",
            "subject": "Item " + str(index),
            "body": "Body " + str(index),
            "seq": index + 1,
        }
        (inbox / (mid + ".json")).write_text(json.dumps(payload), encoding="utf-8")
    return "wide"


def _progress(root: Path) -> None:
    folder = root / "progress"
    folder.mkdir(parents=True, exist_ok=True)
    samples = {
        "20261025T150000Z_cursor.md": "Status: working\nStep: 1 of 3\nPercent: 20\nNow: opening scene\n",
        "20261026T150000Z_cursor.md": "Status: review\nNow: waiting for review\n",
        "20261027T120000Z_cursor.md": "Status: waiting\nNow: waiting on you\n",
        "20261027T180000Z_cursor.md": "Status: done\nNow: scene two\n\n## Asked\n- [x] scene\n\n## Claims\n- done\n",
    }
    for name, text in samples.items():
        (folder / name).write_text(text, encoding="utf-8")


def _context(root: Path) -> None:
    folder = root / "context"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "20261025T150000Z_cursor.md").write_text("# Project context: demo\n\nA small game.\n", encoding="utf-8")


def _files(board: Path) -> None:
    shared = board / "projects" / "demo" / "files" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (shared / "page.html").write_text("<p>hi</p>", encoding="utf-8")
    (shared / "icon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
    (shared / "tool.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    index = {
        "files": [
            {"name": "shot.png", "kind": "image", "bytes": 8, "sender": "cursor", "message": "m1", "shared_on": "2026-10-26T12:00:00Z", "description": "a shot", "path": str(shared / "shot.png")},
            {"name": "page.html", "kind": "document", "bytes": 10, "sender": "cursor", "shared_on": "2026-10-26T12:01:00Z", "path": str(shared / "page.html")},
            {"name": "icon.svg", "kind": "image", "bytes": 40, "sender": "cursor", "shared_on": "2026-10-26T12:02:00Z", "path": str(shared / "icon.svg")},
            {"name": "tool.sh", "kind": "program", "bytes": 20, "sender": "cursor", "shared_on": "2026-10-26T12:03:00Z", "held": True, "path": str(shared / "tool.sh")},
        ]
    }
    (shared / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (shared / "pending.json").write_text(json.dumps([{"name": "tool.sh"}]), encoding="utf-8")
    draft = board / "projects" / "demo" / "files" / "claude_desktop"
    draft.mkdir(parents=True, exist_ok=True)
    (draft / "draft-note.txt").write_text("helper draft\n", encoding="utf-8")
    out_files = board / "projects" / "demo" / "outbox" / "claude_desktop" / "answer.files"
    out_files.mkdir(parents=True, exist_ok=True)
    (out_files / "extra.bin").write_bytes(b"outbox-bytes")


def _outbox(root: Path) -> None:
    sent = root / "outbox" / "claude_desktop" / "sent"
    rejected = root / "outbox" / "claude_desktop" / "rejected"
    sent.mkdir(parents=True, exist_ok=True)
    rejected.mkdir(parents=True, exist_ok=True)
    (sent / "answer.md").write_text("posted\n", encoding="utf-8")
    (rejected / "nope.md").write_text("not posted\n", encoding="utf-8")
    (rejected / "nope.md.reason.txt").write_text("The driver has not picked up mail.\n", encoding="utf-8")


def _grades(root: Path) -> None:
    folder = root / "grades" / "claude_desktop"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "g1.json").write_text(json.dumps({"score": 4, "note": "useful", "late": True}), encoding="utf-8")


def _reviews(root: Path) -> None:
    folder = root / "reviews"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "m1.json").write_text(json.dumps({"reviewer": "claude_code", "verdict": "approve"}), encoding="utf-8")
    (folder / "override.json").write_text(json.dumps({"by": "person"}), encoding="utf-8")


def _requests(root: Path) -> None:
    folder = root / "requests" / "done"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "stop.json").write_text(json.dumps({"action": "stop_watching"}), encoding="utf-8")
    (root / "approvals.json").write_text(json.dumps([{"by": "person", "text": "May edit scene files."}]), encoding="utf-8")
    (root / "watch.json").write_text("{}\n", encoding="utf-8")


def _board_files(board: Path) -> None:
    log = board / "logs" / "schedule"
    log.mkdir(parents=True, exist_ok=True)
    (log / "round.log").write_text("2026-10-27T16:00:00Z background round finished\n", encoding="utf-8")
    (board / "logs" / "trace.jsonl").write_text('{"ts":"2026-10-27T16:00:01Z","msg":"tick"}\n', encoding="utf-8")
    state = board / ".state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "cursor-mail.json").write_text('{"waiting": true}\n', encoding="utf-8")
    (board / "update-check.json").write_text('{"current": "0.2.9"}\n', encoding="utf-8")
    previous = board / "bin" / "previous" / "0.2.8"
    previous.mkdir(parents=True, exist_ok=True)
    (previous / "VERSION").write_text("0.2.8\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: viewer_fixture.py <board> <workspace> [large]", file=sys.stderr)
        return 2
    board = Path(sys.argv[1])
    workspace = Path(sys.argv[2])
    if len(sys.argv) > 3 and sys.argv[3] == "large":
        print(build_large(board, workspace))
    else:
        print(build_demo(board, workspace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
