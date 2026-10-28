"""Command line for Agent Co-op."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
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
    load_config,
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
    started = time.perf_counter()
    command = str(getattr(args, "command", "") or "")
    captured = io.StringIO()
    capture = command in ("check", "watch", "wait", "cursor-loop")
    code = 1
    try:
        _note_run(args)
        _cleanup_replaced_binaries()
        if capture:
            from agentcoop.trace import Tee

            with contextlib.redirect_stdout(Tee(sys.stdout, captured)):
                code = int(args.func(args))
        else:
            code = int(args.func(args))
        return code
    except CoopError as exc:
        print(str(exc), file=sys.stderr)
        code = exc.code
        return code
    except KeyboardInterrupt:
        code = 0
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
        code = 1
        return code
    finally:
        _trace_command(argv, command, code, started, captured.getvalue())


def _note_run(args: argparse.Namespace) -> None:
    """Record that this agent ran a command. The waiting stop hook does not count."""
    try:
        command = str(getattr(args, "command", "") or "")
        if command == "hook" and str(getattr(args, "hook_name", "") or "") == "cursor-stop":
            return
        agent = getattr(args, "as_name", None) or os.environ.get("AGENTCOOP_AGENT")
        if not agent:
            return
        from agentcoop.activity import note_activity

        note_activity(board_root(), str(agent), command or "command")
        if command != "hook":
            from agentcoop.activity import note_attention

            note_attention(board_root(), str(agent), "command")
    except Exception:
        return


def _trace_command(argv: list[str] | None, command: str, code: int, started: float, printed: str) -> None:
    """Write a trace line when tracing is on. Never change the command result."""
    try:
        from agentcoop.trace import agent_from_args, note_command

        used = list(sys.argv[1:] if argv is None else argv)
        note_command(
            board_root(),
            used,
            agent=agent_from_args(used),
            command=command,
            code=code,
            started=started,
            printed=printed,
        )
    except Exception:
        return


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="agentcoop",
        epilog=(
            "Exit codes: 1 error, 2 usage, 3 not set up, 4 no project, "
            "5 roles paused (watch also uses 5 when Agent Co-op was updated), "
            "6 settings file is not in the expected format."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    send_cmd = sub.add_parser("send", help="Send a message to another agent")
    _add_as(send_cmd)
    _add_project(send_cmd)
    send_cmd.add_argument("--to", required=True)
    send_cmd.add_argument("--with-progress", action="store_true")
    send_cmd.add_argument("--urgent", action="store_true")
    send_cmd.add_argument("--subject")
    send_cmd.add_argument("--body")
    send_cmd.add_argument("--body-file")
    send_cmd.add_argument("--type", default="question")
    send_cmd.add_argument("--re", dest="re_id")
    send_cmd.add_argument("--attach", action="append", default=[])
    send_cmd.add_argument("--describe", action="append", default=[])
    send_cmd.add_argument("--kind", action="append", default=[])
    send_cmd.add_argument("--media-type", action="append", dest="media_type", default=[])
    send_cmd.add_argument("--alt-of", action="append", dest="alt_of", default=[])
    send_cmd.add_argument("--over-limit", action="store_true")
    send_cmd.add_argument("--needs-reply", action="store_true")
    send_cmd.add_argument("--about", default="", help="Message id this ping is about")
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
    setup_cmd.add_argument("--version", default="", dest="setup_version")
    setup_cmd.set_defaults(func=cmd_setup)

    connect_cmd = sub.add_parser("connect", help="Connect one agent")
    connect_cmd.add_argument("tool")
    _add_as(connect_cmd)
    connect_cmd.add_argument("--tool", dest="self_tool", default="")
    connect_cmd.add_argument("--yes", action="store_true")
    connect_cmd.add_argument("--model", default="")
    connect_cmd.add_argument("--host", default="")
    connect_cmd.add_argument("--api", default="")
    connect_cmd.add_argument("--accepts", default="")
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
    progress_write.add_argument("--for", dest="for_id", default="")
    progress_write.add_argument("--percent", default="")
    progress_write.add_argument("--step", default="")
    progress_write.add_argument("--status", default="")
    progress_write.add_argument("--now", default="")
    progress_write.add_argument("--allow-secret", action="store_true")
    progress_write.set_defaults(func=cmd_progress_write)
    progress_show = progress_sub.add_parser("show", help="Show a progress update")
    _add_as(progress_show)
    _add_project(progress_show)
    progress_show.add_argument("--version", dest="version_name")
    progress_show.set_defaults(func=cmd_progress_show)
    progress_template = progress_sub.add_parser("template", help="Print the progress template")
    _add_as(progress_template)
    _add_project(progress_template)
    progress_template.add_argument("--done", action="store_true", help="Print the done report template")
    progress_template.set_defaults(func=cmd_progress_template)
    progress_list = progress_sub.add_parser("list", help="List progress versions")
    _add_as(progress_list)
    _add_project(progress_list)
    progress_list.add_argument("--limit", type=int)
    progress_list.set_defaults(func=cmd_progress_list)

    review_cmd = sub.add_parser("review", help="Ask a helper to check finished work")
    review_sub = review_cmd.add_subparsers(dest="review_action", required=True)
    review_mode_cmd = review_sub.add_parser("mode", help="Set when review is required")
    _add_as(review_mode_cmd)
    _add_project(review_mode_cmd)
    review_mode_cmd.add_argument("review_mode", nargs="?", choices=("off", "changes", "always"))
    review_mode_cmd.set_defaults(func=cmd_review_mode)
    review_request = review_sub.add_parser("request", help="Ask a helper to review a done report")
    _add_as(review_request)
    _add_project(review_request)
    review_request.add_argument("--to", dest="review_to", required=True)
    review_request.add_argument("--task", dest="review_task", required=True)
    review_request.set_defaults(func=cmd_review_request)
    review_reply = review_sub.add_parser("reply", help="Send a review verdict")
    _add_as(review_reply)
    _add_project(review_reply)
    review_reply.add_argument("--re", dest="review_re", required=True)
    review_reply.add_argument("--verdict", dest="review_verdict", required=True)
    review_reply.add_argument("--body", default="")
    review_reply.set_defaults(func=cmd_review_reply)
    review_override = review_sub.add_parser("override", help="Mark the task done without approval")
    _add_as(review_override)
    _add_project(review_override)
    review_override.set_defaults(func=cmd_review_override)

    resume_cmd = sub.add_parser("resume", help="Show where a new chat should pick up")
    _add_as(resume_cmd)
    _add_project(resume_cmd)
    resume_cmd.set_defaults(func=cmd_resume)

    rejected_cmd = sub.add_parser("rejected", help="Clear old rejected answers")
    rejected_sub = rejected_cmd.add_subparsers(dest="rejected_action", required=True)
    rejected_clear = rejected_sub.add_parser("clear", help="Move rejected answers aside")
    _add_as(rejected_clear)
    _add_project(rejected_clear)
    rejected_clear.set_defaults(func=cmd_rejected_clear)

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

    loop_cmd = sub.add_parser("cursor-loop", help="Keep an open Cursor chat checking the board")
    _add_as(loop_cmd)
    _add_project(loop_cmd)
    loop_cmd.add_argument("loop_action", nargs="?", default="", help="stop ends the loop")
    loop_cmd.set_defaults(func=cmd_cursor_loop)

    hook_cmd = sub.add_parser("hook", help="Answer a Cursor hook")
    hook_cmd.add_argument("hook_name", help="cursor-stop or cursor-prompt")
    _add_as(hook_cmd)
    hook_cmd.set_defaults(func=cmd_hook)

    wake_cmd = sub.add_parser("wake-wait", help="Show or set how long an idle Cursor chat waits")
    wake_cmd.add_argument("minutes", nargs="?", type=int)
    wake_cmd.set_defaults(func=cmd_wake_wait)

    idle_cmd = sub.add_parser("idle", help="Choose what happens when Cursor is not watching")
    idle_cmd.add_argument("idle_mode", choices=("wait", "background"))
    idle_cmd.set_defaults(func=cmd_idle)

    trace_cmd = sub.add_parser("trace", help="Turn command tracing on or off")
    trace_cmd.add_argument("trace_action", choices=("on", "off"))
    trace_cmd.set_defaults(func=cmd_trace)

    allow_cmd = sub.add_parser("allow", help="Record what the driver may do while you are away")
    _add_as(allow_cmd)
    _add_project(allow_cmd)
    allow_cmd.add_argument("allow_action", nargs="?", default="add", choices=("add", "show", "clear"))
    allow_cmd.add_argument("--text", default="")
    allow_cmd.set_defaults(func=cmd_allow)

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

    share_cmd = sub.add_parser("share", help="Copy a file onto the board and print its path")
    _add_as(share_cmd)
    _add_project(share_cmd)
    share_cmd.add_argument("path")
    share_cmd.add_argument("--describe", default="")
    share_cmd.add_argument("--kind", default="")
    share_cmd.add_argument("--over-limit", action="store_true")
    share_cmd.set_defaults(func=cmd_share)

    files_cmd = sub.add_parser("files", help="List, pin, and remove shared files")
    files_sub = files_cmd.add_subparsers(dest="files_cmd", required=True)
    files_list = files_sub.add_parser("list", help="List shared files")
    _add_project(files_list)
    files_list.set_defaults(func=cmd_files_list)
    files_pin = files_sub.add_parser("pin", help="Keep a shared file")
    _add_as(files_pin)
    _add_project(files_pin)
    files_pin.add_argument("token")
    files_pin.set_defaults(func=cmd_files_pin)
    files_unpin = files_sub.add_parser("unpin", help="Let a shared file be removed later")
    _add_as(files_unpin)
    _add_project(files_unpin)
    files_unpin.add_argument("token")
    files_unpin.set_defaults(func=cmd_files_unpin)
    files_prune = files_sub.add_parser("prune", help="Remove shared files past their keep time")
    _add_project(files_prune)
    files_prune.set_defaults(func=cmd_files_prune)

    accepts_cmd = sub.add_parser("accepts", help="Say which file types you accept")
    _add_as(accepts_cmd)
    _add_project(accepts_cmd)
    accepts_cmd.add_argument("--for", dest="for_agent", default="")
    accepts_cmd.add_argument("patterns", nargs="?")
    accepts_cmd.set_defaults(func=cmd_accepts)

    join_cmd = sub.add_parser("join", help="Join the current project as yourself")
    _add_as(join_cmd)
    _add_project(join_cmd)
    join_cmd.add_argument("--note", default="")
    join_cmd.add_argument("--accepts", default="")
    join_cmd.add_argument("--tags", default="")
    join_cmd.add_argument("--can", default="")
    join_cmd.add_argument("--cost", default="")
    join_cmd.add_argument("words", nargs="*")
    join_cmd.set_defaults(func=cmd_join)
    leave_cmd = sub.add_parser("leave", help="Leave a project you help with")
    _add_as(leave_cmd)
    _add_project(leave_cmd)
    leave_cmd.add_argument("words", nargs="*")
    leave_cmd.set_defaults(func=cmd_leave)

    profile_cmd = sub.add_parser("profile", help="Set what a helper is good at")
    _add_as(profile_cmd)
    _add_project(profile_cmd)
    profile_cmd.add_argument("helper", nargs="?")
    profile_cmd.add_argument("--tags", default=None)
    profile_cmd.add_argument("--can", default=None)
    profile_cmd.add_argument("--cost", default="")
    profile_cmd.add_argument("--note", default=None)
    profile_cmd.set_defaults(func=cmd_profile)

    helpers_cmd = sub.add_parser("helpers", help="Show what each helper is good at, and their grades")
    _add_as(helpers_cmd)
    _add_project(helpers_cmd)
    helpers_cmd.add_argument("--for", dest="for_tag", default="", help="Rank helpers for this tag")
    helpers_cmd.set_defaults(func=cmd_helpers)

    grade_cmd = sub.add_parser("grade", help="Grade a helper answer")
    _add_as(grade_cmd)
    _add_project(grade_cmd)
    grade_cmd.add_argument("answer", nargs="*", help="Answer message id, or remove and that id")
    grade_cmd.add_argument("--score", type=int, default=None)
    grade_cmd.add_argument("--outcome", default="")
    grade_cmd.add_argument("--correct", type=int, default=None)
    grade_cmd.add_argument("--useful", type=int, default=None)
    grade_cmd.add_argument("--followed-ask", dest="followed_ask", type=int, default=None)
    grade_cmd.add_argument("--tags", default="")
    grade_cmd.add_argument("--note", default="")
    grade_cmd.add_argument("--remove", action="store_true")
    grade_cmd.set_defaults(func=cmd_grade)

    heartbeat_cmd = sub.add_parser("heartbeat", help="Answer a ping")
    _add_as(heartbeat_cmd)
    _add_project(heartbeat_cmd)
    heartbeat_cmd.add_argument("--re", dest="re_id", required=True)
    heartbeat_cmd.add_argument("--status", required=True)
    heartbeat_cmd.add_argument("--on", default="")
    heartbeat_cmd.add_argument("--percent", type=int, default=None)
    heartbeat_cmd.add_argument("--step", default="")
    heartbeat_cmd.add_argument("--next", dest="next_text", default="")
    heartbeat_cmd.add_argument("--eta", dest="eta_minutes", type=int, default=None)
    heartbeat_cmd.set_defaults(func=cmd_heartbeat)

    decline_cmd = sub.add_parser("decline", help="Decline a request")
    _add_as(decline_cmd)
    _add_project(decline_cmd)
    decline_cmd.add_argument("message", help="Request message id")
    decline_cmd.add_argument("--reason", required=True)
    decline_cmd.set_defaults(func=cmd_decline)

    cancel_cmd = sub.add_parser("cancel", help="Cancel a request you sent")
    _add_as(cancel_cmd)
    _add_project(cancel_cmd)
    cancel_cmd.add_argument("message", help="Request message id")
    cancel_cmd.add_argument("--reason", default="")
    cancel_cmd.set_defaults(func=cmd_cancel)

    move_cmd = sub.add_parser("move", help="Move a request to another helper")
    _add_as(move_cmd)
    _add_project(move_cmd)
    move_cmd.add_argument("message", help="Request message id")
    move_cmd.add_argument("--to", required=True)
    move_cmd.add_argument("--reason", default="")
    move_cmd.set_defaults(func=cmd_move)

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

    update_cmd = sub.add_parser("update", help="Install a published version")
    update_cmd.add_argument("--to", dest="to_version", default="")
    update_cmd.add_argument("--check", action="store_true")
    update_cmd.add_argument("--list", dest="list_versions", action="store_true")
    update_cmd.add_argument("--rollback", action="store_true")
    update_cmd.add_argument("--from-file", dest="from_file", default="")
    update_cmd.add_argument("--install-this", dest="install_this", action="store_true")
    update_cmd.add_argument("--notify", choices=("on", "off"), default="")
    update_cmd.set_defaults(func=cmd_update)

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


def _print_sent(recipient: str, subject: str, body: str, files: list | None = None) -> None:
    """Print a one-line summary on standard error so a captured id stays one line."""
    from agentcoop.chatlines import sent_line

    print(sent_line(recipient, subject, body, files=files), file=sys.stderr, flush=True)


def _aligned(flag: str, values: list[str], count: int) -> list[str]:
    if len(values) > count:
        raise CoopError(flag + " was given more times than --attach.", code=2)
    return list(values) + [""] * (count - len(values))


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


def _sent_files(board: Path, project: str, agent: str, mid: str) -> list:
    from agentcoop.core import read_message_file
    from agentcoop.paths import sent_dir

    path = sent_dir(board, project, agent) / (mid + ".json")
    message, _issue = read_message_file(path)
    if message is None:
        return []
    return list(message.attachment_info)


def cmd_share(args: argparse.Namespace) -> int:
    """Copy a file onto the board and print the shared path."""
    from agentcoop.share import share_ahead

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    path = share_ahead(
        board,
        project,
        args.path,
        sender=agent,
        description=args.describe or "",
        kind=args.kind or "",
        over_limit=bool(args.over_limit),
    )
    print(path)
    return 0


def cmd_files_list(args: argparse.Namespace) -> int:
    from agentcoop.share import list_rows

    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=None)
    rows = list_rows(board, project)
    if not rows:
        print("No shared files.")
        return 0
    for row in rows:
        print(row)
    return 0


def cmd_files_pin(args: argparse.Namespace) -> int:
    from agentcoop.share import set_pin

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    print(set_pin(board, project, args.token, pinned=True, actor=agent))
    return 0


def cmd_files_unpin(args: argparse.Namespace) -> int:
    from agentcoop.share import set_pin

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    print(set_pin(board, project, args.token, pinned=False, actor=agent))
    return 0


def cmd_files_prune(args: argparse.Namespace) -> int:
    from agentcoop.share import prune

    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=None)
    removed = prune(board, project)
    if not removed:
        print("No shared files were removed.")
        return 0
    for path in removed:
        print("Removed " + path)
    return 0


def cmd_accepts(args: argparse.Namespace) -> int:
    """Set what an agent accepts. Changing someone else needs the person."""
    from agentcoop.confirm import confirm_person
    from agentcoop.share import accepts_of, set_accepts

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    target = args.for_agent or agent
    if not args.patterns:
        current = accepts_of(board, project, target)
        if not current:
            print(target + " has not said what it accepts.")
        else:
            print(target + " accepts " + ", ".join(current) + ".")
        return 0
    if target != agent:
        confirm_person(
            "Change what " + target + " accepts to " + args.patterns + ".",
            expected=target,
            kind="agent",
            asked_by=agent,
        )
    patterns = [part.strip() for part in str(args.patterns).split(",") if part.strip()]
    set_accepts(board, project, target, patterns)
    print(target + " accepts " + ", ".join(patterns) + ".")
    return 0


def _save_declared_accepts(args: argparse.Namespace, name: str) -> None:
    patterns = [part.strip() for part in str(getattr(args, "accepts", "") or "").split(",") if part.strip()]
    if not patterns:
        return
    from agentcoop.share import remember_accepts

    remember_accepts(_board(), name, patterns)
    print(name + " accepts " + ", ".join(patterns) + ".")


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
    subject = args.subject
    if not subject:
        if args.type in ("nudge", "ping"):
            subject = "Attention"
        else:
            raise CoopError("Add a subject with --subject.", code=2)
    if len(recipients) > 3:
        raise CoopError("A second opinion can go to at most 3 helpers.")
    group = ""
    if len(recipients) > 1:
        import secrets

        group = "g" + secrets.token_hex(4)
        body = body + "\n\nOthers were asked the same question. Answer on your own."
    count = len(attachments)
    describes = _aligned("--describe", list(args.describe or []), count)
    kinds = _aligned("--kind", list(args.kind or []), count)
    media_types = _aligned("--media-type", list(args.media_type or []), count)
    alt_ofs = _aligned("--alt-of", list(args.alt_of or []), count)
    ids = []
    for recipient in recipients:
        mid = send(
            board,
            project=project,
            sender=agent,
            recipient=recipient,
            subject=subject,
            body=body,
            type=args.type,
            re=args.re_id,
            attachments=list(attachments),
            needs_reply=bool(args.needs_reply),
            allow_secret=bool(args.allow_secret),
            describes=describes,
            kinds=kinds,
            media_types=media_types,
            alt_ofs=alt_ofs,
            over_limit=bool(args.over_limit),
            group=group or None,
            about=getattr(args, "about", None) or None,
        )
        ids.append(mid)
        infos = _sent_files(board, project, agent, mid)
        _print_sent(recipient, subject, body, files=infos)
        for info in infos:
            if info.get("mismatch"):
                print(str(info["mismatch"]), file=sys.stderr)
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


def _cleanup_replaced_binaries() -> None:
    """Remove Windows binaries renamed aside during the previous update."""
    try:
        from agentcoop.update import cleanup_old_binaries

        board = board_root()
        if board.is_dir():
            cleanup_old_binaries(board)
    except Exception:
        return


def cmd_update(args: argparse.Namespace) -> int:
    """Install a published version, or turn the daily check on or off."""
    from agentcoop.update import run_update

    return run_update(args)


def cmd_check(args: argparse.Namespace) -> int:
    """Print new messages."""
    from agentcoop.updatecheck import quiet_update_check

    if board_is_ready(board_root()):
        quiet_update_check(board_root())
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
    from agentcoop.progress import section_text, write_helper_progress, write_progress
    from agentcoop.progressmeta import format_fields, one_line, parse_fields

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    body = _read_body(args)
    for_id = getattr(args, "for_id", "") or ""
    status = getattr(args, "status", "") or ""
    step = getattr(args, "step", "") or ""
    percent = getattr(args, "percent", "") or ""
    now = getattr(args, "now", "") or ""
    if for_id:
        name = write_helper_progress(
            board,
            project,
            agent,
            for_id,
            body,
            status=status or "working",
            step=step,
            percent=percent,
            now=now,
            allow_secret=bool(args.allow_secret),
        )
        print(name)
        fields = parse_fields(format_fields(status=status or "working", step=step, percent=percent, now=now, for_id=for_id, rest=body))
        _print_sent("the project", "Progress", one_line(fields))
        return 0
    if status or step or percent or now:
        body = format_fields(status=status or "working", step=step, percent=percent, now=now, rest=body)
    print(write_progress(board, project, agent, body, allow_secret=bool(args.allow_secret)))
    working = now or section_text(body, "Working on now") or section_text(body, "Now")
    if status or step or percent or now:
        _print_sent("the project", "Progress", one_line(parse_fields(body)))
    else:
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

    helper = False
    if getattr(args, "as_name", None):
        agent = _agent(args)
        board = _board()
        project_name = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
        project = (load_projects(board).get("projects") or {}).get(project_name) or {}
        helper = role_of(project, agent) == "helper"
    print(progress_template_text(helper=helper, done=bool(getattr(args, "done", False))), end="")
    return 0


def cmd_progress_list(args: argparse.Namespace) -> int:
    from agentcoop.progress import list_progress_text

    board = _board()
    agent = args.as_name
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    print(list_progress_text(board, project, limit=args.limit), end="")
    return 0


def cmd_review_mode(args: argparse.Namespace) -> int:
    from agentcoop.confirm import confirm_person
    from agentcoop.taskreview import review_mode, set_review_mode

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    if not args.review_mode:
        print(review_mode(board, project), flush=True)
        return 0
    if args.review_mode == "off":
        confirm_person(
            "This turns review off. A task can be marked done without a reviewer.",
            expected="review",
            kind="review",
            asked_by=agent,
        )
    print(set_review_mode(board, project, args.review_mode), flush=True)
    return 0


def cmd_review_request(args: argparse.Namespace) -> int:
    from agentcoop.core import send
    from agentcoop.taskreview import record_request, task_bundle

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    paths = task_bundle(board, project, args.review_task)
    progress = paths[0]
    body = progress.read_text(encoding="utf-8")
    message_id = send(
        board,
        project=project,
        sender=agent,
        recipient=args.review_to,
        subject="Review this task",
        body=body,
        type="review",
        attachments=[str(path) for path in paths],
        needs_reply=True,
    )
    record_request(board, project, args.review_task, args.review_to, message_id)
    print(message_id, flush=True)
    return 0


def cmd_review_reply(args: argparse.Namespace) -> int:
    from agentcoop.core import send
    from agentcoop.taskreview import assert_requested_reviewer, record_verdict

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    assert_requested_reviewer(board, project, agent)
    message_id = send(
        board,
        project=project,
        sender=agent,
        recipient=_driver_name(board, project),
        subject="Review",
        body=args.body or args.review_verdict,
        type="review",
        re=args.review_re,
        verdict=args.review_verdict,
    )
    record_verdict(
        board,
        project,
        reviewer=agent,
        verdict=args.review_verdict,
        body=args.body or "",
        message_id=message_id,
    )
    print(message_id, flush=True)
    return 0


def cmd_review_override(args: argparse.Namespace) -> int:
    from agentcoop.confirm import confirm_person
    from agentcoop.taskreview import record_override

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    confirm_person(
        "This marks the task done without a reviewer's approval.",
        expected="review",
        kind="review",
        asked_by=agent,
    )
    record_override(board, project, agent)
    print("Override recorded. The done report must include: Override: the person marked this done anyway.", flush=True)
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    from agentcoop.taskreview import resume_text

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    print(resume_text(board, project, agent), end="")
    return 0


def cmd_rejected_clear(args: argparse.Namespace) -> int:
    from agentcoop.outbox import clear_rejected

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    count = clear_rejected(board, project, agent)
    if count == 0:
        print("No rejected answers to clear.")
    elif count == 1:
        print("Moved 1 rejected answer aside. NEW.md will not list it.")
    else:
        print("Moved " + str(count) + " rejected answers aside. NEW.md will not list them.")
    return 0


def _driver_name(board: Path, project: str) -> str:
    from agentcoop.projects import load_projects

    record = (load_projects(board).get("projects") or {}).get(project) or {}
    return str(record.get("driver") or "")


def cmd_wait(args: argparse.Namespace) -> int:
    """Block until a reply arrives or the timeout passes."""
    from agentcoop.progress import wait_for_reply

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    minutes = int(args.timeout)
    if minutes < 1 or minutes > 30:
        raise CoopError("The wait must be between 1 and 30 minutes.", code=2)
    return wait_for_reply(board, project, agent, args.for_id, minutes)


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
    from agentcoop.updatecheck import quiet_update_check

    quiet_update_check(board)
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    assert_member(board, project, agent)
    action = args.watch_action or ""
    if action not in ("", "stop", "start"):
        raise CoopError("Use agentcoop watch, agentcoop watch stop, or agentcoop watch start.", code=2)
    if action == "stop":
        set_project_watching(board, project, False, agent)
        from agentcoop.cursorloop import set_agent_watching

        set_agent_watching(board, project, agent, False)
        print(sent_line("the project", "Stop checking", "Checking is stopped for " + project + "."), flush=True)
        return 0
    if action == "start":
        set_project_watching(board, project, True, agent)
        from agentcoop.cursorloop import set_agent_watching

        set_agent_watching(board, project, agent, True)
        print(sent_line("the project", "Start checking", "Checking is on for " + project + "."), flush=True)
        return 0
    minutes = int(args.timeout)
    if minutes < MIN_TIMEOUT_MINUTES or minutes > MAX_TIMEOUT_MINUTES:
        raise CoopError(
            "The wait must be between " + str(MIN_TIMEOUT_MINUTES) + " and " + str(MAX_TIMEOUT_MINUTES) + " minutes.",
            code=2,
        )
    return run_watch(board, project, agent, timeout_seconds=minutes * 60)


def cmd_cursor_loop(args: argparse.Namespace) -> int:
    """Keep printing ticks until checking stops or the program changes."""
    from agentcoop.cursorloop import run_cursor_loop, set_agent_watching
    from agentcoop.watch import assert_member

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    assert_member(board, project, agent)
    if args.loop_action == "stop":
        set_agent_watching(board, project, agent, False)
        print("Checking is stopped. cursor-loop will exit.", flush=True)
        return 0
    if args.loop_action:
        raise CoopError("Use agentcoop cursor-loop or agentcoop cursor-loop stop.", code=2)
    return run_cursor_loop(board, project, agent)


def cmd_hook(args: argparse.Namespace) -> int:
    """Read a Cursor hook payload and print the follow-up, if any."""
    import json
    import sys

    from agentcoop.activity import note_prompt
    from agentcoop.cursorloop import cursor_stop_followup

    raw = sys.stdin.read()
    if args.hook_name == "cursor-prompt":
        try:
            note_prompt(_board(), _agent(args), raw)
        except CoopError:
            pass
        print(json.dumps({"continue": True}), flush=True)
        return 0
    if args.hook_name != "cursor-stop":
        raise CoopError("The hook must be cursor-stop or cursor-prompt.", code=2)
    agent = _agent(args)
    board = _board()
    result = cursor_stop_followup(board, agent, raw)
    if result:
        print(json.dumps(result), flush=True)
    return 0


def cmd_wake_wait(args: argparse.Namespace) -> int:
    """Show the idle wait, or save a wait of 1 to 60 minutes."""
    from agentcoop.cursorloop import describe_hook_timeout, set_hook_timeout_minutes

    board = _board()
    if args.minutes is None:
        print(describe_hook_timeout(board), flush=True)
        return 0
    print(set_hook_timeout_minutes(board, args.minutes), flush=True)
    return 0


def cmd_idle(args: argparse.Namespace) -> int:
    """Save whether an idle Cursor chat waits or runs in the background."""
    from agentcoop.cursorloop import set_idle_mode

    mode = "wait_for_chat" if args.idle_mode == "wait" else "background_run"
    print(set_idle_mode(_board(), mode), flush=True)
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    """Turn the board trace on or off."""
    from agentcoop.projects import mutate_config

    enabled = args.trace_action == "on"

    def change(data: dict) -> None:
        data["trace"] = enabled

    mutate_config(_board(), change)
    if enabled:
        print("Tracing is on. Each command appends one line to logs/trace.jsonl.", flush=True)
    else:
        print("Tracing is off.", flush=True)
    return 0


def cmd_allow(args: argparse.Namespace) -> int:
    """Record, show, or clear standing approvals for the driver."""
    from agentcoop.approvals import add_approval, clear_approvals, load_approvals

    agent = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
    action = args.allow_action or "add"
    if action == "show":
        items = load_approvals(board, project)
        if not items:
            print("No standing approvals.", flush=True)
            return 0
        for item in items:
            print(str(item.get("text") or ""), flush=True)
        return 0
    if action == "clear":
        print(clear_approvals(board, project, agent), flush=True)
        return 0
    print(add_approval(board, project, agent, args.text), flush=True)
    return 0


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


def _print_update_check_line() -> None:
    """Tell the person the daily check is on, unless they turned it off."""
    try:
        if load_config(_board()).get("update_check") is not False:
            print(
                'Agent Co-op checks GitHub once a day for a new version. '
                'Say "Turn off update checks" to stop it.'
            )
    except CoopError:
        return


def cmd_setup(args: argparse.Namespace) -> int:
    """Set up or detect."""
    if getattr(args, "setup_version", ""):
        from agentcoop.update import setup_at_version

        return setup_at_version(args)
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
    if "cursor" in (result.get("connected") or []):
        print(
            "When Cursor is not watching, messages wait until you say Start working. "
            "To run the driver in the background instead, say: "
            "Run the driver in the background when the chat is idle."
        )
    left = result.get("left_alone") or []
    if left:
        print("Files you changed, left in place:")
        for path in left:
            print("  " + path)
    _print_update_check_line()
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
        _save_declared_accepts(args, name)
        if args.self_tool == "cursor":
            print(
                "When Cursor is not watching, messages wait until you say Start working. "
                "To run the driver in the background instead, say: "
                "Run the driver in the background when the chat is idle."
            )
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
    _save_declared_accepts(args, name)
    if args.tool == "cursor":
        print(
            "When Cursor is not watching, messages wait until you say Start working. "
            "To run the driver in the background instead, say: "
            "Run the driver in the background when the chat is idle."
        )
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


def _profile_list(text: str | None, *, kind: str) -> list[str] | None:
    """Return a parsed list, or None when the flag was left off."""
    if text is None or text == "":
        return None
    from agentcoop.profiles import parse_list

    return parse_list(text, kind="can" if kind == "can" else "tags")


def _profile_cost(text: str | None) -> str | None:
    if not text:
        return None
    from agentcoop.profiles import parse_cost

    return parse_cost(text)


def cmd_heartbeat(args: argparse.Namespace) -> int:
    """Answer a ping with what you are doing."""
    from agentcoop.core import load_message, send
    from agentcoop.paths import inbox_msgs

    agent = _agent(args)
    board = _board()
    project = _project(args, agent)
    status = str(args.status or "").strip().lower()
    if status not in ("working", "idle", "blocked", "waiting"):
        raise CoopError("Status must be working, idle, blocked, or waiting.", code=2)
    if not args.next_text:
        raise CoopError("Say what is next in one sentence. Add --next.", code=2)
    ping = load_message(inbox_msgs(board, project, agent) / (args.re_id + ".json"))
    if ping is None or ping.type not in ("ping", "nudge"):
        raise CoopError("A heartbeat answers a ping. " + args.re_id + " is not one.")
    mid = send(
        board,
        project=project,
        sender=agent,
        recipient=ping.sender,
        subject="Heartbeat",
        body=args.next_text,
        type="heartbeat",
        re=args.re_id,
        status=status,
        on=args.on or None,
        percent=args.percent,
        step=args.step or None,
        next_text=args.next_text,
        eta_minutes=args.eta_minutes,
    )
    print(mid)
    return 0


def cmd_decline(args: argparse.Namespace) -> int:
    """Decline one request."""
    from agentcoop.tasks import decline_request

    agent = _agent(args)
    project = _project(args, agent)
    print(decline_request(_board(), project, agent, args.message, args.reason))
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    """Cancel one request."""
    from agentcoop.tasks import cancel_request

    agent = _agent(args)
    project = _project(args, agent)
    print(cancel_request(_board(), project, agent, args.message, args.reason))
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    """Move one request to another helper."""
    from agentcoop.tasks import move_request

    agent = _agent(args)
    project = _project(args, agent)
    print(move_request(_board(), project, agent, args.message, args.to, args.reason))
    return 0


def cmd_helpers(args: argparse.Namespace) -> int:
    """List helpers ranked for a tag, with grade, availability, and cost."""
    from agentcoop.choice import choice_lines
    from agentcoop.projects import load_projects

    actor = _actor_name(args) or None
    project = _project(args, actor)
    record = (load_projects(_board()).get("projects") or {}).get(project) or {}
    if not (record.get("helpers") or {}):
        print("This project has no helpers.")
        return 0
    for line in choice_lines(_board(), project, args.for_tag or ""):
        print(line)
    return 0


def cmd_grade(args: argparse.Namespace) -> int:
    """Grade one helper answer, or remove a grade after the person allows it."""
    from agentcoop.grades import remove_grade, write_grade
    from agentcoop.profiles import parse_list

    parts = list(args.answer or [])
    removing = bool(args.remove) or (bool(parts) and parts[0] == "remove")
    if removing and parts and parts[0] == "remove":
        parts = parts[1:]
    if len(parts) != 1:
        raise CoopError(
            "Name the answer. Example: agentcoop grade --as cursor MESSAGE --score 4 --outcome used --note \"It helped.\"",
            code=2,
        )
    answer_id = parts[0]
    actor = _actor_name(args)
    if not actor:
        raise CoopError("Agent Co-op does not know which agent you are. Add --as NAME.", code=2)
    project = _project(args, actor)
    if removing:
        _require_person(
            answer_id,
            "This will remove the grade on " + answer_id + ".",
            kind="grade",
            actor=actor,
            target="grade",
        )
        dest = remove_grade(_board(), project, answer_id)
        print("Moved the grade to " + str(dest) + ".")
        return 0
    if args.score is None or not args.outcome:
        raise CoopError("A grade needs --score and --outcome.", code=2)
    tags = parse_list(args.tags, kind="tags") if args.tags else []
    payload = write_grade(
        _board(),
        project,
        actor,
        answer_id,
        score=args.score,
        outcome=args.outcome,
        note=args.note,
        correct=args.correct,
        useful=args.useful,
        followed_ask=args.followed_ask,
        tags=tags,
    )
    print("Graded " + answer_id + " " + str(payload["score"]) + " " + payload["outcome"] + ".")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    """Set a helper profile. Changing someone else asks the person to allow it."""
    from agentcoop.confirm import confirm_person
    from agentcoop.profiles import set_profile
    from agentcoop.watch import assert_member

    actor = _agent(args)
    board = _board()
    project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=actor)
    if not args.helper:
        from agentcoop.profiles import describe, profile_of
        from agentcoop.projects import load_projects

        record = (load_projects(board).get("projects") or {}).get(project) or {}
        helpers = record.get("helpers") or {}
        if not helpers:
            print("This project has no helpers.", flush=True)
            return 0
        for name in helpers:
            print(name + ": " + describe(profile_of(record, name)), flush=True)
        return 0
    helper = args.helper
    assert_member(board, project, helper)
    if actor != helper:
        confirm_person(
            "This will change " + helper + "'s profile on " + project + ".",
            expected=helper,
            kind="agent",
            asked_by=actor,
        )
    print(
        set_profile(
            board,
            project,
            helper,
            tags=_profile_list(args.tags, kind="tags"),
            can=_profile_list(args.can, kind="can"),
            cost=_profile_cost(args.cost),
            note=args.note,
        ),
        flush=True,
    )
    return 0


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
        tags=_profile_list(args.tags, kind="tags"),
        can=_profile_list(args.can, kind="can"),
        cost=_profile_cost(args.cost),
    )
    print(text)
    if args.accepts:
        from agentcoop.share import set_accepts

        board = _board()
        project = resolve_project(board, explicit=args.project, cwd=Path.cwd(), agent=agent)
        patterns = [part.strip() for part in str(args.accepts).split(",") if part.strip()]
        set_accepts(board, project, agent, patterns)
        print(agent + " accepts " + ", ".join(patterns) + ".")
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
