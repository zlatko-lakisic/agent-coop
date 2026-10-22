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
    new_messages,
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
    send_cmd.add_argument("--with-progress", action="store_true")
    send_cmd.add_argument("--urgent", action="store_true")
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
    setup_cmd.add_argument("--schedule", choices=("yes", "no"), default="yes")
    setup_cmd.add_argument("--every", type=int, default=5)
    setup_cmd.set_defaults(func=cmd_setup)

    connect_cmd = sub.add_parser("connect", help="Connect one agent")
    connect_cmd.add_argument("tool")
    _add_as(connect_cmd)
    connect_cmd.add_argument("--tool", dest="self_tool", default="")
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
    _add_as(project_add)
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
    _add_as(helper_add_cmd)
    helper_add_cmd.set_defaults(func=cmd_helper_add)
    helper_remove_cmd = helper_sub.add_parser("remove")
    helper_remove_cmd.add_argument("name")
    helper_remove_cmd.add_argument("agent")
    _add_as(helper_remove_cmd)
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
    project_set = project_sub.add_parser("set", help="Change a project setting")
    project_set.add_argument("name")
    project_set.add_argument("--driver-shell", required=True)
    project_set.set_defaults(func=cmd_project_set)

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

    context_cmd = sub.add_parser("context", help="Read and write the project context")
    context_sub = context_cmd.add_subparsers(dest="context_cmd", required=True)
    context_show = context_sub.add_parser("show", help="Show the project context")
    _add_project(context_show)
    _add_as(context_show)
    context_show.add_argument("--version", dest="version_name")
    context_show.set_defaults(func=cmd_context_show)
    context_write = context_sub.add_parser("write", help="Write a new project context")
    _add_as(context_write)
    _add_project(context_write)
    context_write.add_argument("--body")
    context_write.add_argument("--body-file")
    context_write.add_argument("--allow-secret", action="store_true")
    context_write.set_defaults(func=cmd_context_write)
    context_template = context_sub.add_parser("template", help="Print the context template")
    context_template.set_defaults(func=cmd_context_template)
    context_list = context_sub.add_parser("list", help="List context versions")
    _add_project(context_list)
    context_list.set_defaults(func=cmd_context_list)

    progress_cmd = sub.add_parser("progress", help="Read and write driver progress")
    progress_sub = progress_cmd.add_subparsers(dest="progress_cmd", required=True)
    progress_write = progress_sub.add_parser("write", help="Write a progress update")
    _add_as(progress_write)
    _add_project(progress_write)
    progress_write.add_argument("--body")
    progress_write.add_argument("--body-file")
    progress_write.add_argument("--allow-secret", action="store_true")
    progress_write.set_defaults(func=cmd_progress_write)
    progress_show = progress_sub.add_parser("show", help="Show a progress update")
    _add_as(progress_show)
    _add_project(progress_show)
    progress_show.add_argument("--version", dest="version_name")
    progress_show.set_defaults(func=cmd_progress_show)
    progress_template = progress_sub.add_parser("template", help="Print the progress template")
    progress_template.set_defaults(func=cmd_progress_template)
    progress_list = progress_sub.add_parser("list", help="List progress versions")
    _add_as(progress_list)
    _add_project(progress_list)
    progress_list.add_argument("--limit", type=int)
    progress_list.set_defaults(func=cmd_progress_list)

    wait_cmd = sub.add_parser("wait", help="Wait for a reply")
    _add_as(wait_cmd)
    _add_project(wait_cmd)
    wait_cmd.add_argument("--for", dest="for_id", required=True)
    wait_cmd.add_argument("--timeout", type=int, default=10)
    wait_cmd.set_defaults(func=cmd_wait)

    watch_cmd = sub.add_parser(
        "watch",
        help="Wait for new mail, or stop and start checking for a project",
    )
    _add_as(watch_cmd)
    _add_project(watch_cmd)
    watch_cmd.add_argument(
        "watch_action",
        nargs="?",
        default="",
        help="stop sets the project flag, start clears it",
    )
    watch_cmd.add_argument("--timeout", type=int, default=10)
    watch_cmd.set_defaults(func=cmd_watch)

    start_cmd = sub.add_parser("start", help="Turn on background checks")
    start_cmd.add_argument("--every", type=int, default=5)
    start_cmd.set_defaults(func=cmd_start)
    stop_cmd = sub.add_parser("stop", help="Turn off background checks")
    stop_cmd.set_defaults(func=cmd_stop)
    now_cmd = sub.add_parser("now", help="Start one background round")
    now_cmd.set_defaults(func=cmd_now)
    status_cmd = sub.add_parser("status", help="Show the background schedule")
    status_cmd.set_defaults(func=cmd_status)
    tick_cmd = sub.add_parser("tick", help="Used by the background schedule")
    tick_cmd.set_defaults(func=cmd_tick)
    run_agent_cmd = sub.add_parser("run-agent", help="Used by the background schedule")
    _add_as(run_agent_cmd)
    run_agent_cmd.add_argument("--project", required=True)
    run_agent_cmd.set_defaults(func=cmd_run_agent)
    touch_cmd = sub.add_parser("touch", help="Used by hooks")
    _add_as(touch_cmd)
    touch_cmd.set_defaults(func=cmd_touch)
    draft_cmd = sub.add_parser("draft", help="Write a draft in your files folder")
    _add_as(draft_cmd)
    draft_cmd.add_argument("--name", required=True)
    draft_cmd.add_argument("--body")
    draft_cmd.add_argument("--body-file")
    draft_cmd.set_defaults(func=cmd_draft)

    join_cmd = sub.add_parser("join", help="Join the current project as yourself")
    _add_as(join_cmd)
    _add_project(join_cmd)
    join_cmd.add_argument("--note", default="")
    join_cmd.add_argument("words", nargs="*")
    join_cmd.set_defaults(func=cmd_join)
    leave_cmd = sub.add_parser("leave", help="Leave a project you help with")
    _add_as(leave_cmd)
    _add_project(leave_cmd)
    leave_cmd.add_argument("words", nargs="*")
    leave_cmd.set_defaults(func=cmd_leave)

    skill_cmd = sub.add_parser("skill", help="Export a helper skill zip")
    skill_sub = skill_cmd.add_subparsers(dest="skill_cmd", required=True)
    skill_export = skill_sub.add_parser("export", help="Write a Claude desktop skill zip")
    skill_export.add_argument("--as", dest="as_name", required=True)
    skill_export.add_argument("--project", required=True)
    skill_export.add_argument("--out", dest="out_dir", help="Folder for the zip")
    skill_export.set_defaults(func=cmd_skill_export)

    desktop_cmd = sub.add_parser("desktop", help="Set up Claude desktop as a helper")
    desktop_sub = desktop_cmd.add_subparsers(dest="desktop_cmd", required=True)
    desktop_add_cmd = desktop_sub.add_parser("add", help="Add Claude desktop on a project")
    desktop_add_cmd.add_argument("name", nargs="?")
    desktop_add_cmd.add_argument("--project")
    desktop_add_cmd.add_argument("--as", dest="as_name")
    desktop_add_cmd.add_argument("--note")
    desktop_add_cmd.set_defaults(func=cmd_desktop_add)
    desktop_prepare_cmd = desktop_sub.add_parser("prepare", help="Write the Claude desktop skill zip")
    desktop_prepare_cmd.add_argument("--out", dest="out_dir")
    desktop_prepare_cmd.set_defaults(func=cmd_desktop_prepare)
    desktop_remove_cmd = desktop_sub.add_parser("remove", help="Remove Claude desktop from a project")
    desktop_remove_cmd.add_argument("name", nargs="?")
    desktop_remove_cmd.add_argument("--project")
    desktop_remove_cmd.add_argument("--as", dest="as_name")
    desktop_remove_cmd.set_defaults(func=cmd_desktop_remove)
    desktop_prompt_cmd = desktop_sub.add_parser("prompt", help="Print the scheduled-task prompt")
    desktop_prompt_cmd.add_argument("--project", required=True)
    desktop_prompt_cmd.add_argument("--as", dest="as_name")
    desktop_prompt_cmd.set_defaults(func=cmd_desktop_prompt)

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


def _print_sent(recipient: str, subject: str, body: str) -> None:
    """Print a one-line summary on standard error so a captured id stays one line."""
    from agentcoop.chatlines import sent_line

    print(sent_line(recipient, subject, body), file=sys.stderr, flush=True)


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
    """Send one message, or one message to each helper in a list."""
    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    if args.urgent and role_of(record, agent) != "driver":
        raise CoopError("Only the driver of " + project + " can send an urgent request.")
    attachments = list(args.attach or [])
    if args.with_progress:
        from agentcoop.progress import progress_attachment

        attachments.append(str(progress_attachment(board, project)))
    recipients = [part.strip() for part in str(args.to).split(",") if part.strip()]
    if not recipients:
        raise CoopError("Name who should receive the message with --to.", code=2)
    body = _read_body(args)
    ids = []
    for recipient in recipients:
        mid = send(
            board,
            project=project,
            sender=agent,
            recipient=recipient,
            subject=args.subject,
            body=body,
            type=args.type,
            re=args.re_id,
            attachments=list(attachments),
            needs_reply=bool(args.needs_reply),
            allow_secret=bool(args.allow_secret),
        )
        ids.append(mid)
        _print_sent(recipient, args.subject, body)
        if args.urgent:
            from agentcoop.progress import record_urgent

            record_urgent(board, project, mid)
    for mid in ids:
        print(mid)
    if args.urgent:
        from agentcoop.rounds import load_schedule, spawn_round

        if load_schedule(board):
            spawn_round(board)
        else:
            print(
                "The schedule is off, so the helper starts when someone opens it.",
                file=sys.stderr,
            )
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
        _refresh_after_check(board_root())
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
    clearable: list[str] = []
    for name in names:
        report = check_inbox(board, name, agent)
        if not report.integrity_ok:
            broken = True
        elif report.role == "driver" and report.notices:
            clearable.append(name)
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
    from agentcoop.projects import clear_notices

    for name in clearable:
        clear_notices(board, name)
    _refresh_after_check(board)
    if args.strict and broken:
        return 5
    return 0


def _refresh_after_check(board: Path) -> None:
    from agentcoop.projects import board_is_ready as _ready
    from agentcoop.statusfile import refresh_status

    if _ready(board):
        refresh_status(board)


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
    from agentcoop.context import context_status
    from agentcoop.paths import board_root

    board = Path(getattr(report, "board_path", "") or board_root())
    return {
        "project": report.project,  # type: ignore[attr-defined]
        "agent": report.agent,  # type: ignore[attr-defined]
        "role": report.role,  # type: ignore[attr-defined]
        "integrity_ok": report.integrity_ok,  # type: ignore[attr-defined]
        "messages": messages,
        "issues": [str(issue.path) for issue in report.issues],  # type: ignore[attr-defined]
        "context": context_status(
            board,
            report.project,  # type: ignore[attr-defined]
            report.agent,  # type: ignore[attr-defined]
            report.role,  # type: ignore[attr-defined]
        ),
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


def cmd_progress_write(args: argparse.Namespace) -> int:
    from agentcoop.progress import section_text, write_progress

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    body = _read_body(args)
    print(write_progress(board, project, agent, body, allow_secret=bool(args.allow_secret)))
    working = section_text(body, "Working on now")
    _print_sent("the project", "Progress", working or body)
    return 0


def cmd_progress_show(args: argparse.Namespace) -> int:
    from agentcoop.progress import show_progress

    board = _board()
    agent = args.as_name
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    print(show_progress(board, project, version=args.version_name), end="")
    return 0


def cmd_progress_template(args: argparse.Namespace) -> int:
    from agentcoop.progress import progress_template_text

    print(progress_template_text(), end="")
    return 0


def cmd_progress_list(args: argparse.Namespace) -> int:
    from agentcoop.progress import list_progress_text

    board = _board()
    agent = args.as_name
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    print(list_progress_text(board, project, limit=args.limit), end="")
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    """Block until a reply arrives or the timeout passes."""
    import time

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    minutes = int(args.timeout)
    if minutes < 1 or minutes > 30:
        raise CoopError("The wait must be between 1 and 30 minutes.", code=2)
    deadline = time.monotonic() + minutes * 60
    while True:
        messages, _issues = new_messages(board, project, agent)
        for item in messages:
            if item.re == args.for_id:
                print(file_id(item) + "  " + item.subject)
                return 0
        if time.monotonic() >= deadline:
            print(
                "No reply to "
                + args.for_id
                + " after "
                + str(minutes)
                + " minutes. Keep working on something else, or post a progress update saying you are blocked."
            )
            return 3
        time.sleep(5)


def cmd_watch(args: argparse.Namespace) -> int:
    """Wait for mail, or set and clear the project stop flag."""
    from agentcoop.chatlines import sent_line
    from agentcoop.watch import (
        MAX_TIMEOUT_MINUTES,
        MIN_TIMEOUT_MINUTES,
        assert_member,
        run_watch,
        set_project_watching,
    )

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    assert_member(board, project, agent)
    action = args.watch_action or ""
    if action not in ("", "stop", "start"):
        raise CoopError("Use agentcoop watch, agentcoop watch stop, or agentcoop watch start.", code=2)
    if action == "stop":
        set_project_watching(board, project, False, agent)
        print(sent_line("the project", "Stop checking", "Checking is stopped for " + project + "."), flush=True)
        return 0
    if action == "start":
        set_project_watching(board, project, True, agent)
        print(sent_line("the project", "Start checking", "Checking is on for " + project + "."), flush=True)
        return 0
    minutes = int(args.timeout)
    if minutes < MIN_TIMEOUT_MINUTES or minutes > MAX_TIMEOUT_MINUTES:
        raise CoopError(
            "The wait must be between " + str(MIN_TIMEOUT_MINUTES) + " and " + str(MAX_TIMEOUT_MINUTES) + " minutes.",
            code=2,
        )
    return run_watch(board, project, agent, timeout_seconds=minutes * 60)


def cmd_context_show(args: argparse.Namespace) -> int:
    """Print a project context version."""
    from agentcoop.context import show_context

    board = _board()
    agent = args.as_name
    if agent:
        validate_agent_name(agent)
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    print(show_context(board, project, version=args.version_name, as_agent=agent), end="")
    return 0


def cmd_context_write(args: argparse.Namespace) -> int:
    """Write a new project context version."""
    from agentcoop.context import write_context

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    body = _read_body(args)
    name = write_context(
        board,
        project,
        agent,
        body,
        allow_secret=bool(args.allow_secret),
    )
    print(name)
    _print_sent("helpers", "Context", body)
    return 0


def cmd_context_template(args: argparse.Namespace) -> int:
    """Print the blank context template."""
    from agentcoop.context import context_template_text

    print(context_template_text(), end="")
    return 0


def cmd_context_list(args: argparse.Namespace) -> int:
    """List context versions."""
    from agentcoop.context import list_context

    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=None)
    print(list_context(board, project), end="")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Install the background schedule."""
    from agentcoop.rounds import start_schedule

    start_schedule(_board(), int(args.every))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    from agentcoop.rounds import stop_schedule

    stop_schedule(board_root() if board_root().exists() else _board())
    return 0


def cmd_now(args: argparse.Namespace) -> int:
    from agentcoop.rounds import spawn_round

    spawn_round(_board())
    print("Started a round. Run agentcoop status to see the result.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from agentcoop.schedule import format_status

    print(format_status(_board()), end="")
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    from agentcoop.rounds import tick

    return tick(_board())


def cmd_run_agent(args: argparse.Namespace) -> int:
    from agentcoop.rounds import run_agent

    agent = _agent(args)
    return run_agent(_board(), agent, args.project)


def cmd_touch(args: argparse.Namespace) -> int:
    agent = _agent(args)
    board = _board()
    folder = board / ".state"
    folder.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = folder / ("active-" + agent)
    path.write_text(stamp + "\n", encoding="utf-8")
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    from agentcoop.schedule import draft_file

    agent = _agent(args)
    board = _board()
    path = draft_file(board, agent, args.name, _read_body(args))
    print(str(path))
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
        schedule=None if interactive else args.schedule == "yes",
        every=args.every,
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
    """Connect one tool, or the tool that is running this command."""
    if args.tool == "self":
        if not args.self_tool:
            raise CoopError(
                "Say which tool you are. Example: agentcoop connect self --tool cursor",
                code=2,
            )
        from agentcoop.session import connect_self

        name = connect_self(
            args.self_tool,
            agent_name=args.as_name or None,
            model=args.model or None,
            yes=bool(args.yes),
        )
        print(name + " is connected.")
        return 0
    if args.self_tool:
        raise CoopError("Use --tool only with connect self.", code=2)
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


def _actor_name(args: argparse.Namespace, *, ignore_as: bool = False) -> str:
    """Return the agent that is asking, from --as or AGENTCOOP_AGENT."""
    if not ignore_as:
        name = getattr(args, "as_name", None)
        if name:
            return str(name).strip()
    return str(os.environ.get("AGENTCOOP_AGENT") or "").strip()


def _require_person(
    expected: str,
    summary: str,
    *,
    kind: str,
    actor: str = "",
    target: str = "",
) -> None:
    """Ask a person to confirm a change that affects someone else."""
    if target and actor and actor == target:
        return
    from agentcoop.confirm import confirm_person

    confirm_person(summary, expected=expected, kind=kind, asked_by=actor)


def cmd_disconnect(args: argparse.Namespace) -> int:
    """Disconnect one agent."""
    _require_person(
        args.name,
        "This will disconnect " + args.name + ".",
        kind="agent",
        actor=_actor_name(args),
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
    """Add a project from flags, as yourself, or by asking questions in a terminal."""
    if args.driver == "self":
        from agentcoop.membership import add_self_driver

        agent = _agent(args)
        start = Path(args.path) if args.path else Path.cwd()
        print(add_self_driver(agent, start, name=args.name))
        return 0
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
    text = add_project_flow(
        path=Path(args.path) if args.path else None,
        name=args.name,
        driver=args.driver,
        helpers=helpers,
        lock_helpers=args.lock_helpers == "yes",
        gitignore=args.gitignore != "no",
        yes=args.yes,
        prompter=prompter,
    )
    if text:
        print(text)
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
        actor=_actor_name(args),
    )
    print(change_driver(args.name, args.agent, yes=True))
    return 0


def cmd_helper_add(args: argparse.Namespace) -> int:
    """Add a helper. Adding yourself does not ask again."""
    _require_person(
        args.name,
        "This will add " + args.agent + " as a helper on " + args.name + ".",
        kind="project",
        actor=_actor_name(args),
        target=args.agent,
    )
    print(helper_add(args.name, args.agent, args.note))
    return 0


def cmd_helper_remove(args: argparse.Namespace) -> int:
    """Remove a helper. Removing yourself does not ask again."""
    _require_person(
        args.name,
        "This will remove " + args.agent + " as a helper on " + args.name + ".",
        kind="project",
        actor=_actor_name(args),
        target=args.agent,
    )
    print(helper_remove(args.name, args.agent))
    return 0


def cmd_project_remove(args: argparse.Namespace) -> int:
    """Remove a project."""
    _require_person(
        args.name,
        "This will remove the " + args.name + " project.",
        kind="project",
        actor=_actor_name(args),
    )
    print(project_remove(args.name, yes=True))
    return 0


def cmd_project_repair(args: argparse.Namespace) -> int:
    """Reinstall missing driver project files."""
    print(project_repair(args.name))
    return 0


def cmd_project_set(args: argparse.Namespace) -> int:
    """Set the extra shell commands a scheduled driver may run."""
    _require_person(
        args.name,
        "This changes which commands a scheduled driver may run in " + args.name + ".",
        kind="project",
        actor=_actor_name(args),
    )
    names = []
    for part in str(args.driver_shell).split(","):
        token = part.strip()
        if not token or any(char.isspace() for char in token):
            raise CoopError("Each shell command must be a single word, separated by commas.", code=2)
        names.append(token)
    from agentcoop.projects import load_projects, mutate_projects

    board = _board()
    if args.name not in (load_projects(board).get("projects") or {}):
        raise CoopError("There is no project named " + args.name + ". Run: agentcoop project list")

    def change(data: dict) -> None:
        data["projects"][args.name]["driver_shell_allow"] = names

    mutate_projects(board, change)
    print("Scheduled driver commands for " + args.name + ": " + ", ".join(names))
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
            actor=_actor_name(args),
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


def _desktop_project(args: argparse.Namespace) -> str:
    name = args.project or args.name
    if not name:
        raise CoopError(
            "Name the project. Example: agentcoop desktop add demo",
            code=2,
        )
    return name


def cmd_join(args: argparse.Namespace) -> int:
    """Join a project as the agent that is asking."""
    from agentcoop.membership import join_self

    agent = _agent(args)
    text = join_self(
        agent,
        project=args.project,
        cwd=Path.cwd(),
        words=" ".join(args.words or []),
        note=args.note or None,
    )
    print(text)
    _print_sent("the project", "Join", text)
    return 0


def cmd_leave(args: argparse.Namespace) -> int:
    """Leave a project as the agent that is asking."""
    from agentcoop.membership import leave_self

    agent = _agent(args)
    text = leave_self(
        agent,
        project=args.project,
        cwd=Path.cwd(),
        words=" ".join(args.words or []),
    )
    print(text)
    _print_sent("the project", "Leave", text)
    return 0


def cmd_skill_export(args: argparse.Namespace) -> int:
    """Write a skill zip for an outbox helper."""
    from agentcoop.desktop import export_skill

    dest = Path(args.out_dir) if args.out_dir else None
    path = export_skill(_board(), args.project, args.as_name, dest_dir=dest)
    print(str(path))
    print(
        "Upload it in the Claude app: Customize > Skills, then + Create skill, then Upload a skill."
    )
    return 0


def cmd_desktop_add(args: argparse.Namespace) -> int:
    """Add Claude desktop as a helper on a project."""
    from agentcoop.desktop import DEFAULT_AGENT, desktop_add

    project = _desktop_project(args)
    agent = args.as_name or DEFAULT_AGENT
    _require_person(
        project,
        "This will add " + agent + " as a helper on " + project + ".",
        kind="project",
        actor=_actor_name(args, ignore_as=True),
        target=agent,
    )
    print(desktop_add(_board(), project, note=args.note, agent=agent), end="")
    return 0


def cmd_desktop_prepare(args: argparse.Namespace) -> int:
    """Write the general Claude desktop skill zip."""
    from agentcoop.desktop import prepare_skill

    dest = Path(args.out_dir) if args.out_dir else None
    path = prepare_skill(_board(), dest_dir=dest)
    print(str(path))
    print("Upload it in the Claude app: Customize > Skills, then + Create skill, then Upload a skill.")
    return 0


def cmd_desktop_remove(args: argparse.Namespace) -> int:
    """Remove Claude desktop from a project. Keep sent answers."""
    from agentcoop.desktop import DEFAULT_AGENT, desktop_remove

    project = _desktop_project(args)
    agent = args.as_name or DEFAULT_AGENT
    _require_person(
        project,
        "This will remove " + agent + " as a helper on " + project + ".",
        kind="project",
        actor=_actor_name(args, ignore_as=True),
        target=agent,
    )
    print(desktop_remove(_board(), project, agent=agent))
    return 0


def cmd_desktop_prompt(args: argparse.Namespace) -> int:
    """Print the scheduled-task prompt."""
    from agentcoop.desktop import DEFAULT_AGENT, scheduled_prompt

    print(scheduled_prompt(args.project, args.as_name or DEFAULT_AGENT))
    return 0


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
    """Print versions. The smoke test can also import tkinter."""
    if os.environ.get("AGENTCOOP_IMPORT_TK") == "1":
        import tkinter

        print("tkinter ok")
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
