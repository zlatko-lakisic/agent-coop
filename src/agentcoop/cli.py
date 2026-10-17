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
    file_id,
    log_messages,
    mark_done,
    projects_for_check,
    render_check,
    send,
    waiting,
)
from agentcoop.errors import CoopError
from agentcoop.paths import board_root, files_dir
from agentcoop.doctor import format_doctor, run_doctor
from agentcoop.runner import run_runner
from agentcoop.ui import serve
from agentcoop.install import disconnect_agent
from agentcoop.uninstall import run_uninstall
from agentcoop.projects import (
    INTEGRITY_WARNING,
    board_is_ready,
    integrity_ok,
    load_agents,
    load_projects,
    read_projects_checked,
    require_board,
    resolve_project,
    role_of,
    validate_agent_name,
)
from agentcoop.review import review_projects
from agentcoop.wizard import (
    Prompter,
    add_project_flow,
    change_driver,
    connect_one,
    format_agents,
    format_projects,
    format_setup_summary,
    helper_add,
    helper_remove,
    project_remove,
    project_repair,
    setup_board,
    show_board,
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
    except Exception as exc:
        if os.environ.get("AGENTCOOP_DEBUG") == "1":
            raise
        print(
            "Agent Co-op hit an unexpected error: "
            + type(exc).__name__
            + ": "
            + str(exc)
            + ". Run the same command with AGENTCOOP_DEBUG=1 to see details, "
            + "and report it at https://github.com/zlatko-lakisic/agent-coop/issues",
            file=sys.stderr,
        )
        return 1


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="agentcoop",
        epilog=(
            "Exit codes: 1 error, 2 usage, 3 not set up, 4 no project, "
            "5 roles paused, 6 settings file is not in the expected format."
        ),
    )
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
    send_cmd.add_argument("--allow-secret", action="store_true")
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

    setup_cmd = sub.add_parser("setup", help="Set up the board on this computer")
    setup_cmd.add_argument("--detect", action="store_true")
    setup_cmd.add_argument("--json", action="store_true")
    setup_cmd.add_argument("--connect", default="")
    setup_cmd.add_argument("--yes", action="store_true")
    setup_cmd.add_argument("--model", default="")
    setup_cmd.add_argument("--add-to-path", choices=("yes", "no"), default="no")
    setup_cmd.set_defaults(func=cmd_setup)

    connect_cmd = sub.add_parser("connect", help="Connect one agent")
    connect_cmd.add_argument("tool")
    _add_as(connect_cmd)
    connect_cmd.add_argument("--yes", action="store_true")
    connect_cmd.add_argument("--model", default="")
    connect_cmd.add_argument("--host", default="")
    connect_cmd.add_argument("--api", default="")
    connect_cmd.set_defaults(func=cmd_connect)

    disconnect_cmd = sub.add_parser("disconnect", help="Disconnect one agent")
    disconnect_cmd.add_argument("name")
    disconnect_cmd.set_defaults(func=cmd_disconnect)

    agents_cmd = sub.add_parser("agents", help="List connected agents")
    agents_cmd.set_defaults(func=cmd_agents)

    project_cmd = sub.add_parser("project", help="Add and edit projects")
    project_sub = project_cmd.add_subparsers(dest="project_cmd", required=True)
    project_add = project_sub.add_parser("add", help="Add a project")
    project_add.add_argument("path", nargs="?")
    project_add.add_argument("--name")
    project_add.add_argument("--driver")
    project_add.add_argument("--helper", action="append", default=[])
    project_add.add_argument("--lock-helpers", choices=("yes", "no"), default="no")
    project_add.add_argument("--gitignore", choices=("yes", "no"), default="yes")
    project_add.add_argument("--yes", action="store_true")
    project_add.set_defaults(func=cmd_project_add)
    project_list = project_sub.add_parser("list", help="List projects")
    project_list.set_defaults(func=cmd_project_list)
    project_show = project_sub.add_parser("show", help="Show one project")
    project_show.add_argument("name")
    project_show.set_defaults(func=cmd_project_show)
    project_driver = project_sub.add_parser("driver", help="Change the driver")
    project_driver.add_argument("name")
    project_driver.add_argument("agent")
    project_driver.add_argument("--yes", action="store_true")
    project_driver.set_defaults(func=cmd_project_driver)
    helper = project_sub.add_parser("helper", help="Add or remove a helper")
    helper_sub = helper.add_subparsers(dest="helper_cmd", required=True)
    helper_add_cmd = helper_sub.add_parser("add")
    helper_add_cmd.add_argument("name")
    helper_add_cmd.add_argument("agent")
    helper_add_cmd.add_argument("--note", required=True)
    helper_add_cmd.set_defaults(func=cmd_helper_add)
    helper_remove_cmd = helper_sub.add_parser("remove")
    helper_remove_cmd.add_argument("name")
    helper_remove_cmd.add_argument("agent")
    helper_remove_cmd.set_defaults(func=cmd_helper_remove)
    project_rm = project_sub.add_parser("remove", help="Remove a project")
    project_rm.add_argument("name")
    project_rm.add_argument("--yes", action="store_true")
    project_rm.set_defaults(func=cmd_project_remove)
    project_fix = project_sub.add_parser("repair", help="Reinstall missing project files")
    project_fix.add_argument("name")
    project_fix.set_defaults(func=cmd_project_repair)
    project_review = project_sub.add_parser("review", help="Review hand edits to projects.json")
    project_review.add_argument("--restore", action="store_true")
    project_review.add_argument("--accept", action="store_true")
    project_review.set_defaults(func=cmd_project_review)

    ui_cmd = sub.add_parser("ui", help="Open the local viewer")
    ui_cmd.set_defaults(func=cmd_ui)

    show_cmd = sub.add_parser("show-board", help="Open the board folder")
    show_cmd.set_defaults(func=cmd_show_board)

    runner_cmd = sub.add_parser("runner", help="Answer mail with a local model")
    _add_as(runner_cmd)
    runner_cmd.add_argument("--api", choices=("ollama", "openai"))
    runner_cmd.add_argument("--host")
    runner_cmd.add_argument("--model")
    runner_cmd.add_argument("--interval", type=int, default=5)
    runner_cmd.add_argument("--once", action="store_true")
    runner_cmd.add_argument("--system-prompt-file")
    runner_cmd.set_defaults(func=cmd_runner)

    doctor_cmd = sub.add_parser("doctor", help="Check that Agent Co-op is set up")
    _add_project(doctor_cmd)
    doctor_cmd.add_argument("--json", action="store_true")
    doctor_cmd.add_argument("--strict", action="store_true")
    doctor_cmd.set_defaults(func=cmd_doctor)

    version_cmd = sub.add_parser("version", help="Show the tool and spec versions")
    version_cmd.set_defaults(func=cmd_version)

    uninstall_cmd = sub.add_parser("uninstall", help="Remove Agent Co-op from this computer")
    uninstall_cmd.add_argument("--agent", action="store_true")
    uninstall_cmd.add_argument("--dry-run", action="store_true")
    uninstall_cmd.add_argument("--keep-board", action="store_true")
    uninstall_cmd.set_defaults(func=cmd_uninstall)
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
    mid = send(
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
        allow_secret=bool(args.allow_secret),
    )
    print(mid)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Print new messages."""
    agent = _agent(args)
    if args.summary:
        board = board_root()
        if not board_is_ready(board):
            return 0
        try:
            load_agents(board)
            names = projects_for_check(
                board,
                agent,
                args.project,
                Path.cwd(),
                args.all_projects,
            )
        except CoopError as exc:
            if exc.code in (3, 4):
                return 0
            if exc.code == 6:
                print("Agent Co-op: the board settings are damaged. Run: agentcoop doctor")
                return 0
            raise
        chunks: list[str] = []
        for name in names:
            try:
                report = check_inbox(board, name, agent)
            except CoopError as exc:
                if exc.code == 6:
                    print("Agent Co-op: the board settings are damaged. Run: agentcoop doctor")
                    return 0
                continue
            text = render_check(report, summary=True)
            if text:
                chunks.append(text.rstrip("\n"))
        if chunks:
            print("\n\n".join(chunks))
        return 0
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
            text = render_check(report, summary=False)
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
        row["id"] = file_id(item.message)
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
    projects, intact = read_projects_checked(board)
    project = projects["projects"][project_name]
    role = role_of(project, agent)
    if not role:
        raise CoopError(agent + " is not a member of " + project_name + ".")
    lines = []
    broken = not intact
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


def _tool_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _helpers(items: list[str]) -> dict[str, str]:
    result = {}
    for item in items:
        if "=" not in item:
            raise CoopError(
                "Write a helper as NAME=NOTE. Example: --helper claude_code=reviews code",
                code=2,
            )
        name, note = item.split("=", 1)
        result[name.strip()] = note.strip()
    return result


def cmd_setup(args: argparse.Namespace) -> int:
    """Set up or detect."""
    if args.detect:
        result = setup_board(detect_only=True)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            found = result["found"]
            if not found:
                print(
                    "Found no agents automatically. Connect one by name. "
                    "Example: agentcoop connect claude-code"
                )
            else:
                for item in found:
                    reason = item.get("reason") or ""
                    if reason:
                        print("Found: " + item["display"] + " (" + reason + ")")
                    else:
                        print("Found: " + item["display"])
        return 0
    tools = _tool_list(args.connect)
    interactive = (
        not tools
        and not args.yes
        and not args.json
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )
    prompter = Prompter() if interactive else None
    result = setup_board(
        connect=tools,
        yes=args.yes,
        model=args.model or None,
        prompter=prompter,
        add_to_path=(args.add_to_path == "yes") if not interactive else None,
    )
    if args.json:
        print(json.dumps({"board": result["board"], "connected": result["connected"]}, indent=2))
        return 0
    print("Agent Co-op is set up.")
    print("Board: " + result["board"])
    print(format_setup_summary(_board(), result["connected"]))
    left = result.get("left_alone") or []
    if left:
        print("Files you changed, left in place:")
        for path in left:
            print("  " + path)
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    """Connect one tool."""
    name = connect_one(
        args.tool,
        agent_name=args.as_name,
        yes=args.yes,
        model=args.model or None,
        host=args.host or None,
        api=args.api or None,
    )
    print(name + " is connected.")
    return 0


def _require_person(expected: str, summary: str, *, kind: str) -> None:
    """Ask a person to confirm a role change. Agents without a terminal stop here."""
    if not sys.stdin.isatty():
        raise CoopError(
            "This changes who can write in a project, so a person has to confirm it. "
            "Run it yourself in a terminal, or use: agentcoop ui",
            code=2,
        )
    print(summary)
    prompt = "Type the agent name to confirm: " if kind == "agent" else "Type the project name to confirm: "
    if kind == "review":
        prompt = "Type review to confirm: "
    answer = input(prompt)
    if answer.strip() != expected:
        raise CoopError("Cancelled.", code=1)


def cmd_disconnect(args: argparse.Namespace) -> int:
    """Disconnect one agent."""
    _require_person(
        args.name,
        "This will disconnect " + args.name + ".",
        kind="agent",
    )
    notes = disconnect_agent(_board(), args.name)
    print(args.name + " is disconnected.")
    for note in notes:
        print(note)
    return 0


def cmd_agents(_args: argparse.Namespace) -> int:
    """List agents."""
    print(format_agents(_board()))
    return 0


def cmd_project_add(args: argparse.Namespace) -> int:
    """Add a project from flags or by asking questions in a terminal."""
    prompter = None
    if not args.driver:
        if sys.stdin.isatty() and sys.stdout.isatty():
            prompter = Prompter()
        else:
            raise CoopError(
                "Run this in a terminal to answer questions, or pass the flags. "
                "Example: agentcoop project add . --driver cursor --yes",
                code=2,
            )
    if prompter is not None and not args.helper:
        helpers = None
    else:
        helpers = _helpers(args.helper)
    print(
        add_project_flow(
            path=Path(args.path) if args.path else None,
            name=args.name,
            driver=args.driver,
            helpers=helpers,
            lock_helpers=args.lock_helpers == "yes",
            gitignore=args.gitignore != "no",
            yes=args.yes,
            prompter=prompter,
        )
    )
    return 0


def cmd_project_list(_args: argparse.Namespace) -> int:
    """List projects."""
    print(format_projects(_board()))
    return 0


def cmd_project_show(args: argparse.Namespace) -> int:
    """Show one project."""
    print(format_projects(_board(), args.name))
    return 0


def cmd_project_driver(args: argparse.Namespace) -> int:
    """Change the driver."""
    _require_person(
        args.name,
        "This will make " + args.agent + " the driver of " + args.name + ".",
        kind="project",
    )
    print(change_driver(args.name, args.agent, yes=args.yes))
    return 0


def cmd_helper_add(args: argparse.Namespace) -> int:
    """Add a helper."""
    _require_person(
        args.name,
        "This will add " + args.agent + " as a helper on " + args.name + ".",
        kind="project",
    )
    print(helper_add(args.name, args.agent, args.note))
    return 0


def cmd_helper_remove(args: argparse.Namespace) -> int:
    """Remove a helper."""
    _require_person(
        args.name,
        "This will remove " + args.agent + " as a helper on " + args.name + ".",
        kind="project",
    )
    print(helper_remove(args.name, args.agent))
    return 0


def cmd_project_remove(args: argparse.Namespace) -> int:
    """Remove a project."""
    _require_person(
        args.name,
        "This will remove the " + args.name + " project.",
        kind="project",
    )
    print(project_remove(args.name, yes=True))
    return 0


def cmd_project_repair(args: argparse.Namespace) -> int:
    """Reinstall missing driver project files."""
    print(project_repair(args.name))
    return 0


def cmd_project_review(args: argparse.Namespace) -> int:
    """Review projects.json after a hand edit."""
    if args.restore and args.accept:
        raise CoopError("Use either --restore or --accept, not both.", code=2)
    if args.restore or args.accept:
        _require_person(
            "review",
            "This will restore the last saved roles." if args.restore else "This will accept the current projects.json.",
            kind="review",
        )
    print(review_projects(_board(), restore=args.restore, accept=args.accept))
    return 0


def cmd_ui(_args: argparse.Namespace) -> int:
    """Open the local viewer."""
    return serve()


def cmd_show_board(_args: argparse.Namespace) -> int:
    """Open the board folder."""
    print(show_board())
    return 0


def cmd_runner(args: argparse.Namespace) -> int:
    """Answer new messages with a local model."""
    agent = _agent(args)
    prompt = ""
    if args.system_prompt_file:
        path = Path(args.system_prompt_file)
        if not path.is_file():
            raise CoopError("Could not read the system prompt file: " + str(path))
        prompt = path.read_text(encoding="utf-8")
    return run_runner(
        agent=agent,
        api=args.api,
        host=args.host,
        model=args.model,
        interval=args.interval,
        once=args.once,
        system_prompt=prompt,
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    """Print health checks."""
    board = _board()
    items = run_doctor(board, project=args.project)
    if args.json:
        print(
            json.dumps(
                [{"level": item.level, "ok": item.ok, "text": item.text} for item in items],
                indent=2,
            )
        )
    else:
        print(format_doctor(items))
    broken = [item for item in items if item.level == "fix"]
    if args.strict and not integrity_ok(board):
        return 5
    if broken:
        return 1
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    """Print versions."""
    print("agentcoop " + __version__)
    print("spec " + SPEC_VERSION)
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove Agent Co-op from this computer."""
    return run_uninstall(
        _board(),
        agent=args.agent,
        dry_run=args.dry_run,
        keep_board=args.keep_board,
    )
