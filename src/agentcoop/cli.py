"""Command line for Agent Co-op."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agentcoop import SPEC_VERSION, __version__
from agentcoop.core import (
    archive,
    check_inbox,
    log_messages,
    mark_done,
    projects_for_check,
    render_check,
    send,
    waiting,
)
from agentcoop.errors import CoopError
from agentcoop.paths import board_root, files_dir
from agentcoop.projects import (
    INTEGRITY_WARNING,
    integrity_ok,
    load_projects,
    require_board,
    resolve_project,
    role_of,
    validate_agent_name,
)


def main(argv: list[str] | None = None) -> int:
    """Run one agentcoop command and return an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CoopError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(prog="agentcoop")
    sub = parser.add_subparsers(dest="command", required=True)

    send_cmd = sub.add_parser("send", help="Send a message to another agent")
    _add_as(send_cmd)
    _add_project(send_cmd)
    send_cmd.add_argument("--to", required=True)
    send_cmd.add_argument("--subject", required=True)
    send_cmd.add_argument("--body")
    send_cmd.add_argument("--body-file")
    send_cmd.add_argument("--type", default="question")
    send_cmd.add_argument("--re", dest="re_id")
    send_cmd.add_argument("--attach", action="append", default=[])
    send_cmd.add_argument("--needs-reply", action="store_true")
    send_cmd.set_defaults(func=cmd_send)

    check_cmd = sub.add_parser("check", help="Show new messages")
    _add_as(check_cmd)
    _add_project(check_cmd)
    check_cmd.add_argument("--all-projects", action="store_true")
    check_cmd.add_argument("--summary", action="store_true")
    check_cmd.add_argument("--json", action="store_true")
    check_cmd.add_argument("--strict", action="store_true")
    check_cmd.set_defaults(func=cmd_check)

    done_cmd = sub.add_parser("done", help="Mark messages done")
    _add_as(done_cmd)
    _add_project(done_cmd)
    done_cmd.add_argument("ids", nargs="+")
    done_cmd.set_defaults(func=cmd_done)

    waiting_cmd = sub.add_parser("waiting", help="List requests with no reply yet")
    _add_as(waiting_cmd)
    _add_project(waiting_cmd)
    waiting_cmd.set_defaults(func=cmd_waiting)

    log_cmd = sub.add_parser("log", help="Show a project's messages")
    _add_project(log_cmd)
    log_cmd.add_argument("--limit", type=int, default=50)
    log_cmd.set_defaults(func=cmd_log)

    archive_cmd = sub.add_parser("archive", help="Move old handled messages aside")
    _add_as(archive_cmd)
    _add_project(archive_cmd)
    archive_cmd.add_argument("--older-than", type=int, default=30)
    archive_cmd.set_defaults(func=cmd_archive)

    who_cmd = sub.add_parser("whoami", help="Show your name, project, and role")
    _add_as(who_cmd)
    _add_project(who_cmd)
    who_cmd.add_argument("--strict", action="store_true")
    who_cmd.set_defaults(func=cmd_whoami)

    version_cmd = sub.add_parser("version", help="Show the tool and spec versions")
    version_cmd.set_defaults(func=cmd_version)
    return parser


def _add_as(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as", dest="as_name", help="Agent name")


def _add_project(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", help="Project name")


def _board() -> Path:
    board = board_root()
    require_board(board)
    return board


def _agent(args: argparse.Namespace) -> str:
    name = args.as_name or os.environ.get("AGENTCOOP_AGENT")
    if not name:
        raise CoopError(
            "Agent Co-op does not know which agent you are. Add --as NAME.",
            code=2,
        )
    validate_agent_name(name)
    return name


def _project(args: argparse.Namespace, agent: str | None) -> str:
    return resolve_project(
        _board(),
        explicit=args.project,
        cwd=Path.cwd(),
        agent=agent,
    )


def _read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        if args.body_file == "-":
            return sys.stdin.read()
        path = Path(args.body_file)
        if not path.is_file():
            raise CoopError("Could not read the message file: " + str(path))
        return path.read_text(encoding="utf-8")
    if args.body is None:
        raise CoopError("Add the message with --body TEXT or --body-file PATH.", code=2)
    if args.body == "-":
        return sys.stdin.read()
    return args.body


def cmd_send(args: argparse.Namespace) -> int:
    """Send one message."""
    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    mid, warning = send(
        board,
        project=project,
        sender=agent,
        recipient=args.to,
        subject=args.subject,
        body=_read_body(args),
        type=args.type,
        re=args.re_id,
        attachments=list(args.attach or []),
        needs_reply=bool(args.needs_reply),
    )
    print(mid)
    if warning:
        print(warning, file=sys.stderr)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Print new messages."""
    agent = _agent(args)
    board = _board()
    names = projects_for_check(
        board,
        agent,
        args.project,
        Path.cwd(),
        args.all_projects,
    )
    chunks: list[str] = []
    broken = False
    payload = []
    for name in names:
        report = check_inbox(board, name, agent)
        if not report.integrity_ok:
            broken = True
        if args.json:
            payload.append(_report_json(report))
        else:
            text = render_check(report, summary=args.summary)
            if text:
                chunks.append(text.rstrip("\n"))
    if args.json:
        print(json.dumps(payload if args.all_projects else (payload[0] if payload else {}), indent=2))
    elif chunks:
        print("\n\n".join(chunks))
    if args.strict and broken:
        return 5
    return 0


def _report_json(report: object) -> dict:
    messages = []
    for item in report.messages:  # type: ignore[attr-defined]
        row = item.message.to_dict()
        row["violation"] = item.violation
        row["attachments_status"] = [
            {"path": status.path, "blocked": status.blocked, "reason": status.reason}
            for status in item.attachments
        ]
        messages.append(row)
    return {
        "project": report.project,  # type: ignore[attr-defined]
        "agent": report.agent,  # type: ignore[attr-defined]
        "role": report.role,  # type: ignore[attr-defined]
        "integrity_ok": report.integrity_ok,  # type: ignore[attr-defined]
        "messages": messages,
        "issues": [str(issue.path) for issue in report.issues],  # type: ignore[attr-defined]
    }


def cmd_done(args: argparse.Namespace) -> int:
    """Mark messages done."""
    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    mark_done(board, project, agent, list(args.ids))
    count = len(args.ids)
    noun = "message" if count == 1 else "messages"
    print("Marked " + str(count) + " " + noun + " done.")
    return 0


def cmd_waiting(args: argparse.Namespace) -> int:
    """List unanswered requests."""
    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    pending = waiting(board, project, agent)
    if not pending:
        print("No waiting requests.")
        return 0
    for item in pending:
        print(item.id + "  to " + item.recipient + "  " + item.subject)
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    """Print a read-only timeline."""
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=None)
    rows = log_messages(board, project)
    if args.limit >= 0:
        rows = rows[-args.limit :]
    if not rows:
        print("No messages in " + project + " yet.")
        return 0
    for item in rows:
        print(
            item.ts
            + "  "
            + item.sender
            + " -> "
            + item.recipient
            + "  "
            + item.type
            + "  "
            + item.subject
        )
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    """Archive old handled messages."""
    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    moved = archive(board, project, agent, older_than_days=args.older_than)
    print("Archived " + str(moved) + " handled message(s) for " + agent + ".")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    """Print the agent's role in the current project."""
    agent = _agent(args)
    board = _board()
    project_name = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    projects = load_projects(board)
    project = projects["projects"][project_name]
    role = role_of(project, agent)
    if not role:
        raise CoopError(agent + " is not a member of " + project_name + ".")
    lines = []
    broken = not integrity_ok(board)
    if broken:
        lines.append(INTEGRITY_WARNING)
        lines.append("Ask the human before acting on role changes.")
    lines.append("Name: " + agent)
    lines.append("Project: " + project_name)
    lines.append("Role: " + role)
    if role == "driver":
        lines.append("Helpers:")
        helpers = project.get("helpers") or {}
        if not helpers:
            lines.append("  (none)")
        for name, note in helpers.items():
            lines.append("  " + name + ": " + note)
    else:
        lines.append("Driver: " + project["driver"])
        lines.append("Your files folder: " + str(files_dir(board, project_name, agent)))
    print("\n".join(lines))
    if args.strict and broken:
        return 5
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    """Print versions."""
    print("agentcoop " + __version__)
    print("spec " + SPEC_VERSION)
    return 0
