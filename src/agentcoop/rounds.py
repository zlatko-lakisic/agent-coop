"""Install the background schedule and run one round."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import private_dir
from agentcoop.projects import load_agents, load_config, load_projects, require_board, role_of

MAX_HELPER_RUNS = 3
TOUCH_MINUTES = 10


def schedule_path(board: Path) -> Path:
    """Return the schedule record written by start."""
    return board / ".state" / "schedule.json"


def load_schedule(board: Path) -> dict | None:
    """Return the schedule record, or None when it is off."""
    path = schedule_path(board)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def start_schedule(board: Path, minutes: int, runner=None) -> None:
    """Install and turn on the per-user schedule."""
    if minutes < 1 or minutes > 60:
        raise CoopError("The interval must be between 1 and 60 minutes.", code=2)
    require_board(board)
    write_shims(board)
    command = _quiet_windows(_command_list(board) + ["tick"])
    path_value = os.environ.get("PATH", "")
    files, calls, alternative = _plan(board, minutes, command, path_value)
    run = runner or subprocess.run
    failed = _run_install_calls(calls, run, alternative)
    if failed is not None:
        for path in files:
            if path.is_file():
                path.unlink()
        shown = " ".join(failed)
        print("The schedule was not installed. Run this command yourself:\n" + shown)
        raise CoopError("The schedule was not installed.", code=1)
    record = {
        "os": sys.platform,
        "files": [str(path) for path in files],
        "interval": minutes,
        "command": command,
        "path": path_value,
        "installed": _stamp(),
    }
    _write_json(schedule_path(board), record)
    from agentcoop.watch import set_all_projects

    set_all_projects(board, True, "person")
    print("Background checks are on, every " + str(minutes) + " minutes.")


def stop_schedule(board: Path, runner=None) -> None:
    """Turn off the schedule. Messages and worktrees stay."""
    record = load_schedule(board)
    run = runner or subprocess.run
    if record:
        for call in _stop_calls(record):
            _invoke(run, call)
        files = [str(item) for item in (record.get("files") or [])]
        if any(item.endswith("crontab.txt") for item in files):
            try:
                _remove_cron_lines(run)
            except FileNotFoundError:
                pass
        for raw in record.get("files") or []:
            path = Path(raw)
            if path.is_file():
                path.unlink()
    path = schedule_path(board)
    if path.is_file():
        path.unlink()
    from agentcoop.watch import set_all_projects

    set_all_projects(board, False, "person")
    print("Background checks are off.")


def spawn_round(board: Path) -> None:
    """Start one round in the background and return at once."""
    require_board(board)
    argv = _command_list(board) + ["tick"]
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


def refresh_schedule_command(board: Path) -> None:
    """Rewrite the schedule entry when its command line changed. Leave it off when it is off."""
    record = load_schedule(board)
    if not record:
        return
    command = _quiet_windows(_command_list(board) + ["tick"])
    current = record.get("command")
    if isinstance(current, list) and list(current) == command:
        return
    minutes = int(record.get("interval") or 5)
    path_value = str(record.get("path") or os.environ.get("PATH", ""))
    files, calls, alternative = _plan(board, minutes, command, path_value)
    failed = _run_install_calls(calls, subprocess.run, alternative)
    if failed is not None:
        raise CoopError("The schedule was not updated.", code=1)
    record["command"] = command
    record["files"] = [str(path) for path in files]
    _write_json(schedule_path(board), record)


def tick(board: Path) -> int:
    """Run one round in the foreground. Exit 0 when another round holds the lock."""
    require_board(board)
    from agentcoop.updatecheck import quiet_update_check

    quiet_update_check(board)
    lock = _try_lock(board / ".state" / "tick.lock")
    if lock is None:
        return 0
    try:
        _write_text(board / ".state" / "last-tick", _stamp() + "\n")
        from agentcoop.requests import process_board_requests
        from agentcoop.outbox import sync_board_outboxes
        from agentcoop.statusfile import refresh_status

        process_board_requests(board)
        sync_board_outboxes(board)
        from agentcoop.ping import post_due_heartbeats

        for project_name in load_projects(board).get("projects") or {}:
            post_due_heartbeats(board, project_name)
        from agentcoop.share import prune_due

        prune_due(board)
        refresh_status(board)
        agents = load_agents(board).get("agents") or {}
        projects = load_projects(board).get("projects") or {}
        for name in agents:
            for project, record in projects.items():
                role = role_of(record, name)
                if not role:
                    continue
                if not _should_start(board, name, project, role):
                    continue
                _start_detached(board, ["run-agent", "--as", name, "--project", project])
        return 0
    finally:
        _unlock(lock)


def run_agent(board: Path, agent: str, project: str, *, popen=None, timeout_minutes: float | None = None) -> int:
    """Start one scheduled run for an agent in one project."""
    require_board(board)
    lock = _try_lock(board / ".state" / ("run-" + agent + ".lock"))
    if lock is None:
        return 0
    try:
        projects = load_projects(board)
        record = (projects.get("projects") or {}).get(project)
        if record is None:
            raise CoopError("There is no project named " + project + ".")
        role = role_of(record, agent)
        if role not in ("driver", "helper"):
            return 0
        reason = _watch_skip_reason(board, agent, project)
        if reason:
            _log_round(board, "skip " + agent + " in " + project + ": " + reason)
            return 0
        if role == "driver":
            return _run_driver(board, agent, project, record, popen=popen, timeout_minutes=timeout_minutes)
        return _run_helper(board, agent, project, record, popen=popen, timeout_minutes=timeout_minutes)
    finally:
        _unlock(lock)


def format_status(board: Path) -> str:
    """Show the schedule, each agent, and work waiting on the auto branch."""
    require_board(board)
    from agentcoop.schedule import schedulable_reason
    from agentcoop.worktree import auto_branch, inside_git, worktree_path

    record = load_schedule(board)
    lines = []
    if record:
        lines.append("Schedule: on, every " + str(record.get("interval")) + " minutes.")
    else:
        lines.append("Schedule: off.")
    last = board / ".state" / "last-tick"
    if last.is_file():
        lines.append("Last round: " + last.read_text(encoding="utf-8").strip())
    else:
        lines.append("Last round: none yet.")
    config = load_config(board)
    lines.append(
        "Progress: long task "
        + str(config.get("progress_long_task_minutes") or 5)
        + " minutes, post every "
        + str(config.get("progress_every_minutes") or 10)
        + " minutes."
    )
    from agentcoop.share import limits as share_limits

    cap = share_limits(board)
    lines.append("Shared files: up to " + str(cap["max_mb"]) + " MB each.")
    for project_name, project in (load_projects(board).get("projects") or {}).items():
        for notice in project.get("notices") or []:
            if isinstance(notice, str) and notice:
                lines.append(project_name + ": " + notice)
    agents = load_agents(board).get("agents") or {}
    projects = load_projects(board).get("projects") or {}
    runs = _load_runs(board)
    today = _today()
    for name, agent in agents.items():
        display = str(agent.get("display") or name)
        from agentcoop.tools import get_tool

        join = ""
        try:
            join = get_tool(str(agent.get("tool") or "")).join_method
        except KeyError:
            join = ""
        if join == "outbox":
            from agentcoop.outbox import format_outbox_status

            lines.append(
                display + ": Started by you or by a scheduled task in the Claude app."
            )
            lines.extend(format_outbox_status(board, name))
            continue
        helper_reason = schedulable_reason(board, name, role="helper")
        driver_reason = schedulable_reason(board, name, role="driver")
        if helper_reason and driver_reason:
            lines.append(display + ": " + helper_reason)
        elif helper_reason:
            lines.append(display + " as a helper: " + helper_reason)
        else:
            lines.append(display + ": can run in the background as a helper.")
        if driver_reason and driver_reason.startswith(display + " cannot be scheduled as the driver"):
            lines.append(driver_reason)
        elif driver_reason and not helper_reason:
            lines.append(display + " as a driver: " + driver_reason)
        elif driver_reason is None and helper_reason is None:
            lines.append(display + ": can run in the background as a driver.")
        mail = _has_mail(board, name)
        lines.append("  New mail: " + ("yes" if mail else "no"))
        info = (runs.get("agents") or {}).get(name) or {}
        lines.append("  Last run: " + str(info.get("last") or "none") + " " + str(info.get("result") or ""))
        if (runs.get("signin") or {}).get(name):
            from agentcoop.tools import get_tool

            hint = ""
            try:
                hint = get_tool(str(agent.get("tool") or "")).login_hint
            except KeyError:
                hint = ""
            lines.append(
                display
                + " is not signed in, so it cannot run in the background. Run: "
                + (hint or "the tool's sign-in command")
            )
        count = int((info.get("days") or {}).get(today) or 0)
        limit = int(load_config(board).get("max_runs_per_day") or 48)
        lines.append("  Runs today: " + str(count) + " of " + str(limit))
    branch = auto_branch(board)
    for project, item in projects.items():
        folder = Path(str(item.get("path") or ""))
        dest = worktree_path(board, project)
        from agentcoop.progress import status_lines

        lines.extend(status_lines(board, project))
        from agentcoop.progress import task_lines

        lines.extend(task_lines(board, project))
        from agentcoop.grades import status_lines as grade_status_lines

        lines.extend(grade_status_lines(board, project))
        from agentcoop.taskreview import review_summary

        summary = review_summary(board, project, str(item.get("driver") or ""))
        if summary:
            lines.append(summary)
        from agentcoop.taskreview import open_review_lines

        lines.extend(open_review_lines(board, project))
        if not dest.exists():
            if item.get("driver") and not inside_git(folder):
                driver = str(item.get("driver"))
                lines.append(
                    "Scheduled driver runs need a git repository, so "
                    + driver
                    + " answers when you open it."
                )
            continue
        person = _person_branch(board, project) or "HEAD"
        switched = str((runs.get("switches") or {}).get(project) or "")
        if switched:
            lines.append(switched)
        lines.append(
            project
            + ": commits on "
            + branch
            + " not in "
            + person
            + ". Review with: git diff "
            + person
            + "..."
            + branch
        )
        summary = _driver_summary(board, str(item.get("driver") or ""))
        if summary:
            lines.append(summary)
    return "\n".join(lines) + "\n"


def write_shims(board: Path) -> None:
    """Write the git shim used during scheduled runs."""
    from agentcoop.pathenv import pick_launcher

    folder = board / "bin" / "shims"
    private_dir(folder)
    from importlib import resources

    text = resources.files("agentcoop").joinpath("gitshim.py").read_text(encoding="utf-8")
    launcher = pick_launcher()
    if os.name == "nt":
        script = folder / "gitshim.py"
        script.write_text(text, encoding="utf-8")
        cmd = folder / "git.cmd"
        cmd.write_text('@"' + launcher + '" "' + str(script) + '" %*\r\n', encoding="utf-8")
        return
    path = folder / "git"
    payload = "#!" + launcher + "\n" + text
    path.write_text(payload, encoding="utf-8")
    os.chmod(path, 0o700)


def _run_helper(board, agent, project, record, *, popen, timeout_minutes) -> int:
    from agentcoop.context import latest_version, write_marker
    from agentcoop.core import check_inbox, file_id, mark_done, send
    from agentcoop.runner import _give_up, deliver_answer
    from agentcoop.schedule import build_argv, schedulable_reason

    if schedulable_reason(board, agent, role="helper"):
        return 0
    report = check_inbox(board, project, agent)
    handled = 0
    for item in report.messages:
        if handled >= MAX_HELPER_RUNS:
            break
        if item.violation:
            continue
        message = item.message
        if _runs_reached(board, agent):
            return 0
        prompt, version = _helper_prompt(board, agent, project, record, message)
        cwd = board / "projects" / project
        cwd.mkdir(parents=True, exist_ok=True)
        if _tool_key(board, agent) == "cursor":
            from agentcoop.cursorperm import write_cursor_helper_cli

            write_cursor_helper_cli(cwd)
        argv = build_argv(
            board,
            agent,
            role="helper",
            prompt=prompt,
            cwd=cwd,
            project_path=Path(record["path"]),
        )
        code, output = _execute(board, agent, project, argv, cwd, popen=popen, timeout_minutes=timeout_minutes)
        if _needs_login(board, agent, output):
            _set_signin(board, agent, True)
            _count_run(board, agent, "not signed in")
            continue
        _set_signin(board, agent, False)
        _count_run(board, agent, "exit " + str(code))
        text = (output or "").strip()
        if code != 0 or not text:
            fails = _bump_failure(board, file_id(message))
            if fails >= 3:
                _give_up(board, agent, project, message, "the scheduled run failed 3 times", print)
                _clear_failure(board, file_id(message))
            continue
        _clear_failure(board, file_id(message))
        if version:
            write_marker(board, project, agent, version)
        if text.startswith("NEED CONTEXT:"):
            from agentcoop.context import helper_has_open_context_request

            if not helper_has_open_context_request(board, project, agent):
                body = text.split(":", 1)[1].strip()
                try:
                    send(
                        board,
                        project=project,
                        sender=agent,
                        recipient=str(record.get("driver") or ""),
                        type="context_request",
                        subject="Context for " + project,
                        body=body or "Need project context",
                    )
                except CoopError as exc:
                    _give_up(board, agent, project, message, str(exc), print)
                    handled += 1
                    continue
            mark_done(board, project, agent, [file_id(message)])
        else:
            deliver_answer(board, agent=agent, project=project, message=message, answer=text)
        handled += 1
    return 0


def _run_driver(board, agent, project, record, *, popen, timeout_minutes) -> int:
    from agentcoop.schedule import build_argv, schedulable_reason
    from agentcoop.worktree import auto_branch, finish_worktree, prepare_worktree

    if schedulable_reason(board, agent, role="driver"):
        return 0
    if not _has_mail_in(board, agent, project):
        return 0
    folder = Path(record["path"])
    try:
        dest, person, note = prepare_worktree(board, project, folder)
    except CoopError as exc:
        print(str(exc))
        return 1
    _remember_branch(board, project, person)
    command = None
    if _tool_key(board, agent) == "cursor":
        from agentcoop.cursorperm import write_cursor_cli

        shells = [str(item) for item in (record.get("driver_shell_allow") or [])]
        write_cursor_cli(dest, board, shells)
        command = "agentcoop"
    prompt = _driver_prompt(board, agent, project, dest, person, note, command=command)
    argv = build_argv(
        board,
        agent,
        role="driver",
        prompt=prompt,
        cwd=dest,
        project_path=folder,
    )
    code, output = _execute(board, agent, project, argv, dest, popen=popen, timeout_minutes=timeout_minutes)
    if _needs_login(board, agent, output):
        _set_signin(board, agent, True)
        _count_run(board, agent, "not signed in")
        finish_worktree(dest, auto_branch(board))
        return 0
    _set_signin(board, agent, False)
    switched = finish_worktree(dest, auto_branch(board))
    if switched:
        _remember_switch(board, project, switched)
        _append_run_note(board, agent, switched)
    _count_run(board, agent, "exit " + str(code))
    _write_summary(board, agent, output)
    return 0


def _attached_progress(folder: Path, message) -> str:
    """Return progress text copied with a message, when the source was the progress file."""
    try:
        root = folder.resolve()
    except OSError:
        return ""
    candidates: list[tuple[str, str]] = []
    for info in getattr(message, "attachment_info", None) or []:
        if isinstance(info, dict):
            candidates.append((str(info.get("source_path") or ""), str(info.get("path") or "")))
    for raw in message.attachments:
        candidates.append((raw, raw))
    for source, shared in candidates:
        try:
            if not source or not Path(source).resolve().is_relative_to(root):
                continue
            text_path = Path(shared or source)
            return text_path.read_text(encoding="utf-8").rstrip()
        except (OSError, ValueError):
            continue
    return ""


def _helper_prompt(board, agent, project, record, message) -> tuple[str, str | None]:
    from agentcoop.context import latest_version
    from agentcoop.core import file_id
    from agentcoop.install import read_template
    from agentcoop.safety import check_attachment

    latest = latest_version(board, project)
    if latest is None:
        context = "There is no project context yet."
        version = None
    else:
        context = latest.read_text(encoding="utf-8")
        version = latest.name
    config = load_config(board)
    lines = [
        "You are " + agent + ', a helper in the Agent Co-op project "' + project + '".',
        "You were started automatically because you have a new message. Nobody is watching this run.",
        "",
        "Project context:",
        context.rstrip(),
        "",
    ]
    from agentcoop.progress import progress_dir

    folder = progress_dir(board, project)
    progress_text = _attached_progress(folder, message)
    if progress_text:
        lines.extend(["Driver progress:", progress_text, ""])
    lines.extend(
        [
            "Message " + file_id(message),
            "From: " + message.sender,
            "Type: " + message.type,
            "Subject: " + message.subject,
            "Body:",
            message.body,
        ]
    )
    for raw in message.attachments:
        reason = check_attachment(
            raw,
            project_path=Path(record["path"]),
            board=board,
            project=project,
            allowed_roots=list(config.get("allowed_roots") or []),
        )
        if reason:
            lines.append("Attachment blocked, do not open: " + raw)
        else:
            lines.append("Attachment: " + raw)
    lines.append("")
    lines.append(read_template("run-helper.txt.tmpl").rstrip())
    return "\n".join(lines) + "\n", version


def _tool_key(board, agent: str) -> str:
    agents = load_agents(board).get("agents") or {}
    return str((agents.get(agent) or {}).get("tool") or "")


def _driver_prompt(board, agent, project, dest, person, note, command: str | None = None) -> str:
    from agentcoop.install import read_template
    from agentcoop.pathenv import agentcoop_command
    from agentcoop.worktree import auto_branch

    text = read_template("run-driver.txt.tmpl")
    return (
        text.replace("{{AGENT_NAME}}", agent)
        .replace("{{PROJECT}}", project)
        .replace("{{WORKTREE_PATH}}", str(dest))
        .replace("{{AUTO_BRANCH}}", auto_branch(board))
        .replace("{{PERSON_BRANCH}}", person)
        .replace("{{DIVERGED_NOTE}}", note)
        .replace("{{AGENTCOOP}}", command or agentcoop_command(board))
    )


def _execute(board, agent, project, argv, cwd, *, popen, timeout_minutes) -> tuple[int, str]:
    config = load_config(board)
    minutes = timeout_minutes
    if minutes is None:
        minutes = float(config.get("run_timeout_minutes") or 15)
    env = dict(os.environ)
    env["AGENTCOOP_UNATTENDED"] = "1"
    env["AGENTCOOP_AGENT"] = agent
    shims = str(board / "bin" / "shims")
    bindir = str(board / "bin")
    schedule = load_schedule(board) or {}
    rest = str(schedule.get("path") or env.get("PATH", ""))
    env["PATH"] = shims + os.pathsep + bindir + os.pathsep + rest
    started = _stamp()
    launcher = popen or subprocess.Popen
    kwargs = {
        "cwd": str(cwd),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    proc = launcher(argv, **kwargs)
    output = ""
    code = 1
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=max(minutes, 0.01) * 60)
        code = int(proc.returncode or 0)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
        if proc.stdout is not None:
            proc.stdout.close()
        output = ""
        if proc.stdout is not None:
            try:
                output = proc.stdout.read() or ""
            except Exception:
                output = ""
        code = 124
    _write_log(board, agent, project, argv, started, _stamp(), code, output, timed_out)
    return code, output or ""


def _watch_skip_reason(board, agent, project) -> str:
    """Return why this agent should not run, or an empty string."""
    from agentcoop.watch import checking_enabled, heartbeat_fresh

    if not checking_enabled(board, project):
        return "checking is stopped"
    if heartbeat_fresh(board, project, agent):
        return "heartbeat is fresh"
    return ""


def _log_round(board, line: str) -> None:
    """Append one line to the round log."""
    folder = board / "logs" / "schedule"
    private_dir(folder)
    with (folder / "round.log").open("a", encoding="utf-8") as handle:
        handle.write(_stamp() + " " + line + "\n")


def _should_start(board, agent, project, role) -> bool:
    from agentcoop.schedule import schedulable_reason

    if not _has_mail_in(board, agent, project):
        return False
    from agentcoop.cursorloop import leave_cursor_idle

    if leave_cursor_idle(board, project, agent, role):
        from agentcoop.cursorloop import maybe_notify, status_idle_line

        line = status_idle_line(board, project, agent)
        if line:
            maybe_notify(board, line)
        return False
    if schedulable_reason(board, agent, role=role):
        return False
    reason = _watch_skip_reason(board, agent, project)
    if reason:
        _log_round(board, "skip " + agent + " in " + project + ": " + reason)
        return False
    if _lock_held(board / ".state" / ("run-" + agent + ".lock")):
        return False
    if _touched_recently(board, agent):
        return False
    if _runs_reached(board, agent):
        return False
    return True


def _has_mail(board, agent) -> bool:
    projects = load_projects(board).get("projects") or {}
    return any(_has_mail_in(board, agent, name) for name, record in projects.items() if role_of(record, agent))


def _has_mail_in(board, agent, project) -> bool:
    from agentcoop.core import new_messages

    messages, _issues = new_messages(board, project, agent)
    return bool(messages)


def _touched_recently(board, agent) -> bool:
    path = board / ".state" / ("active-" + agent)
    if not path.is_file():
        return False
    try:
        when = datetime.strptime(path.read_text(encoding="utf-8").strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - when).total_seconds() < TOUCH_MINUTES * 60


def _runs_reached(board, agent) -> bool:
    limit = int(load_config(board).get("max_runs_per_day") or 48)
    info = (_load_runs(board).get("agents") or {}).get(agent) or {}
    return int((info.get("days") or {}).get(_today()) or 0) >= limit


def _count_run(board, agent, result) -> None:
    data = _load_runs(board)
    agents = data.setdefault("agents", {})
    info = agents.setdefault(agent, {"days": {}})
    days = info.setdefault("days", {})
    days[_today()] = int(days.get(_today()) or 0) + 1
    info["last"] = _stamp()
    info["result"] = result
    _write_json(board / ".state" / "runs.json", data)


def _needs_login(board, agent: str, output: str) -> bool:
    from agentcoop.tools import get_tool

    lowered = (output or "").lower()
    phrases = ("authentication required", "not logged in", "please log in", "login required")
    if any(phrase in lowered for phrase in phrases):
        return True
    try:
        tool = get_tool(_tool_key(board, agent))
    except KeyError:
        return False
    message = str(getattr(tool, "login_message", "") or "").lower()
    return bool(message) and message in lowered


def _set_signin(board, agent: str, failed: bool) -> None:
    data = _load_runs(board)
    signin = data.setdefault("signin", {})
    if failed:
        signin[agent] = True
    else:
        signin.pop(agent, None)
    _write_json(board / ".state" / "runs.json", data)


def _bump_failure(board, mid: str) -> int:
    data = _load_runs(board)
    fails = data.setdefault("failures", {})
    fails[mid] = int(fails.get(mid) or 0) + 1
    _write_json(board / ".state" / "runs.json", data)
    return fails[mid]


def _clear_failure(board, mid: str) -> None:
    data = _load_runs(board)
    fails = data.setdefault("failures", {})
    fails.pop(mid, None)
    _write_json(board / ".state" / "runs.json", data)


def _load_runs(board: Path) -> dict:
    path = board / ".state" / "runs.json"
    if not path.is_file():
        return {"agents": {}, "failures": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"agents": {}, "failures": {}}
    if not isinstance(data, dict):
        return {"agents": {}, "failures": {}}
    data.setdefault("agents", {})
    data.setdefault("failures", {})
    data.setdefault("switches", {})
    data.setdefault("signin", {})
    return data


def _write_summary(board, agent, output) -> None:
    folder = board / "logs" / agent
    private_dir(folder)
    _write_text(folder / "latest-summary.txt", "\n".join((output or "").splitlines()[:3]) + "\n")


def _driver_summary(board, agent) -> str:
    path = board / "logs" / agent / "latest-summary.txt"
    if not agent or not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8").splitlines()[:3])


def _remember_switch(board, project, note: str) -> None:
    data = _load_runs(board)
    data.setdefault("switches", {})[project] = note
    _write_json(board / ".state" / "runs.json", data)


def _append_run_note(board, agent, note: str) -> None:
    folder = board / "logs" / agent
    if not folder.is_dir():
        return
    logs = sorted(path for path in folder.glob("*.log") if path.is_file())
    if not logs:
        return
    with logs[-1].open("a", encoding="utf-8") as handle:
        handle.write(note + "\n")


def _remember_branch(board, project, person) -> None:
    data = _load_runs(board)
    data.setdefault("branches", {})[project] = person
    _write_json(board / ".state" / "runs.json", data)


def _person_branch(board, project) -> str:
    return str((_load_runs(board).get("branches") or {}).get(project) or "")


def _write_log(board, agent, project, argv, started, ended, code, output, timed_out) -> None:
    folder = board / "logs" / agent
    private_dir(folder)
    name = started.replace(":", "").replace("-", "") + "-" + project + ".log"
    lines = [
        "command: " + " ".join(argv),
        "start: " + started,
        "end: " + ended,
        "exit: " + str(code),
        "timeout: " + ("yes" if timed_out else "no"),
        "",
    ]
    tail = (output or "").splitlines()[-200:]
    text = "\n".join(lines + tail) + "\n"
    _write_text(folder / name, text)


def _kill_tree(proc) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()


def _start_detached(board, args: list[str]) -> None:
    argv = _command_list(board) + args
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


def _quiet_windows(command: list[str]) -> list[str]:
    """Prefer a no-console launcher for the Windows scheduled task."""
    if os.name != "nt" or not command:
        return command
    exe = Path(command[0])
    quiet_name = ""
    if exe.name.lower() in ("python.exe", "python3.exe"):
        quiet_name = "pythonw.exe"
    elif exe.name.lower() == "py.exe":
        quiet_name = "pyw.exe"
    elif exe.name.lower() == "agentcoop.exe":
        quiet_name = "agentcoopw.exe"
    if not quiet_name:
        return command
    quiet = exe.with_name(quiet_name)
    if quiet.is_file():
        return [str(quiet), *command[1:]]
    return command


def _command_list(board: Path) -> list[str]:
    from agentcoop.pathenv import command_argv

    return command_argv(board)


def _run_install_calls(calls, run, alternative: bool):
    """Return the failing command, or None when the install succeeded."""
    if alternative:
        last = calls[-1] if calls else []
        for call in calls:
            last = call
            completed = _invoke(run, call)
            if completed is not None and getattr(completed, "returncode", 1) == 0:
                return None
        return last
    for call in calls:
        completed = _invoke(run, call)
        if completed is None or getattr(completed, "returncode", 1) != 0:
            return call
    return None


def _invoke(run, call):
    """Run one scheduler command. A missing program returns None."""
    if not call or shutil.which(str(call[0])) is None:
        return None
    try:
        return run(call, capture_output=True, text=True)
    except FileNotFoundError:
        return None


def _scheduler_error(command: list[str]) -> CoopError:
    return CoopError(
        "Agent Co-op could not find a scheduler on this computer. Install cron, "
        "or run this every 5 minutes some other way: "
        + " ".join(command),
        code=1,
    )


def _plan(board: Path, minutes: int, command: list[str], path_value: str):
    if sys.platform == "darwin":
        if shutil.which("launchctl") is None:
            raise _scheduler_error(command)
        path = _launchd_plist()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_plist(minutes, command, path_value, board))
        uid = str(os.getuid())
        calls = [
            ["launchctl", "bootstrap", "gui/" + uid, str(path)],
            ["launchctl", "load", str(path)],
        ]
        return [path], calls, True
    if sys.platform.startswith("linux"):
        if shutil.which("systemctl"):
            try:
                probe = subprocess.run(
                    ["systemctl", "--user", "show-environment"],
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                probe = None
            if probe is not None and probe.returncode == 0:
                files, calls = _systemd_plan(board, minutes, command, path_value)
                return files, calls, False
        if shutil.which("crontab") is None:
            raise _scheduler_error(command)
        files, calls = _cron_plan(board, minutes, command, path_value)
        return files, calls, False
    if shutil.which("schtasks") is None:
        raise _scheduler_error(command)
    task = ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes), "/TN", "AgentCoop\\Tick", "/TR", " ".join(command)]
    return [], [task], False


def _systemd_plan(board, minutes, command, path_value):
    folder = Path.home() / ".config" / "systemd" / "user"
    folder.mkdir(parents=True, exist_ok=True)
    service = folder / "agentcoop-tick.service"
    timer = folder / "agentcoop-tick.timer"
    service.write_text(
        "[Service]\nType=oneshot\nEnvironment=PATH="
        + path_value
        + "\nExecStart="
        + " ".join(command)
        + "\n",
        encoding="utf-8",
    )
    timer.write_text(
        "[Timer]\nOnBootSec=1min\nOnUnitActiveSec="
        + str(minutes)
        + "min\nPersistent=true\n[Install]\nWantedBy=timers.target\n",
        encoding="utf-8",
    )
    calls = [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "agentcoop-tick.timer"],
    ]
    return [service, timer], calls


def _cron_plan(board, minutes, command, path_value):
    line = "*/" + str(minutes) + " * * * * PATH=" + path_value + " " + " ".join(command) + " # agentcoop"
    listed = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    rows = []
    if listed.returncode == 0:
        rows = [row for row in listed.stdout.splitlines() if row and not row.endswith("# agentcoop")]
    rows.append(line)
    path = board / ".state" / "crontab.txt"
    private_dir(path.parent)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return [path], [["crontab", str(path)]]


def _remove_cron_lines(run) -> None:
    if shutil.which("crontab") is None:
        return
    try:
        listed = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except FileNotFoundError:
        return
    if listed.returncode != 0:
        return
    rows = [row for row in listed.stdout.splitlines() if row and not row.endswith("# agentcoop")]
    payload = "\n".join(rows) + ("\n" if rows else "")
    path = Path.home() / ".agentcoop-crontab-next"
    path.write_text(payload, encoding="utf-8")
    run(["crontab", str(path)], capture_output=True, text=True)
    if path.is_file():
        path.unlink()


def _stop_calls(record: dict) -> list[list[str]]:
    if record.get("os") == "darwin":
        files = record.get("files") or []
        if not files:
            return []
        uid = str(os.getuid()) if os.name != "nt" else "0"
        return [
            ["launchctl", "bootout", "gui/" + uid, files[0]],
            ["launchctl", "unload", files[0]],
        ]
    if str(record.get("os", "")).startswith("linux"):
        files = " ".join(record.get("files") or [])
        if "systemd" in files or "agentcoop-tick" in files:
            return [
                ["systemctl", "--user", "disable", "--now", "agentcoop-tick.timer"],
                ["systemctl", "--user", "daemon-reload"],
            ]
        return []
    return [["schtasks", "/Delete", "/F", "/TN", "AgentCoop\\Tick"]]


def _plist(minutes: int, command: list[str], path_value: str, board: Path) -> bytes:
    import plistlib

    logs = board / "logs" / "schedule"
    private_dir(logs)
    data = {
        "Label": "com.agentcoop.tick",
        "ProgramArguments": command,
        "StartInterval": int(minutes) * 60,
        "RunAtLoad": True,
        "StandardOutPath": str(logs / "tick.out.log"),
        "StandardErrorPath": str(logs / "tick.err.log"),
        "EnvironmentVariables": {"PATH": path_value},
    }
    return plistlib.dumps(data)


def _launchd_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.agentcoop.tick.plist"


def _try_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _lock_held(path: Path) -> bool:
    if not path.exists():
        return False
    fd = _try_lock(path)
    if fd is None:
        return True
    _unlock(fd)
    return False


def _unlock(fd) -> None:
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _write_json(path: Path, data: dict) -> None:
    private_dir(path.parent)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    private_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def _stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
