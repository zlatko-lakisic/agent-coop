"""Chat commands, confirmation, requests, and the desktop skill."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.parse
import urllib.request
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.confirm import DENIED, TkUnavailable, ask_on_screen
from agentcoop.desktop import prepare_skill
from agentcoop.install import find_quiet_sibling, render_template
from agentcoop.projects import load_agents, load_projects
from agentcoop.requests import OTHER_ACTION, process_project_requests
from agentcoop.statusfile import status_path

ROOT = support.ROOT
PHRASES = (
    "Install Agent Co-op from github.com/zlatko-lakisic/agent-coop",
    "You are the driver for this project",
    "Join this project",
    "Start working",
    "Leave this project",
    "Who is in this project?",
    "What's the status?",
    "Start the co-op",
    "Stop the co-op",
    "Check the board now",
    "Show me the context",
    "Show me the progress",
    "Make <agent> the driver",
    "Add <agent> as a helper",
    "Remove <agent> from this project",
    "Remove this project",
    "Accept the changed roles",
    "Uninstall Agent Co-op",
)


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def _which(_name: str) -> str:
    return "scheduler"


class _Tk:
    last = None
    choice = "cancel"
    text_before_window = ""

    def __init__(self) -> None:
        self.buttons: dict[str, object] = {}
        self.after_ms = 0
        self.after_fn = None
        self.attrs = ()
        self.title_text = ""
        self.focused = ""
        _Tk.last = self

    def title(self, text: str) -> None:
        self.title_text = text

    def attributes(self, *args: object) -> None:
        self.attrs = args

    def bind(self, *_args: object) -> None:
        return None

    def protocol(self, *_args: object) -> None:
        return None

    def after(self, ms: int, fn: object) -> None:
        self.after_ms = ms
        self.after_fn = fn

    def mainloop(self) -> None:
        buf = sys.stdout
        if hasattr(buf, "getvalue"):
            _Tk.text_before_window = buf.getvalue()
        if _Tk.choice == "allow":
            self.buttons["Allow"]()
        elif _Tk.choice == "timeout":
            self.after_fn()
        else:
            self.buttons["Cancel"]()

    def destroy(self) -> None:
        return None


class _Widget:
    def __init__(self, *_args: object, **kwargs: object) -> None:
        self.kwargs = kwargs
        text = kwargs.get("text")
        command = kwargs.get("command")
        if text in ("Allow", "Cancel") and command is not None and _Tk.last is not None:
            _Tk.last.buttons[str(text)] = command

    def pack(self, **_kwargs: object) -> None:
        return None

    def focus_set(self) -> None:
        if _Tk.last is not None:
            _Tk.last.focused = str(self.kwargs.get("text"))


def _fake_tk():
    import types

    module = types.ModuleType("tkinter")
    module.Tk = _Tk
    module.Label = _Widget
    module.Button = _Widget
    module.Frame = _Widget
    module.LEFT = "left"
    return module


def _run(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _connect_two(env: support.TempEnv) -> Path:
    main(["setup", "--connect", "cursor,claude-code", "--schedule", "no", "--yes"])
    folder = env.workspace / "demo"
    folder.mkdir()
    main(
        [
            "project",
            "add",
            str(folder),
            "--name",
            "demo",
            "--driver",
            "cursor",
            "--helper",
            "claude_code=reviews",
            "--gitignore",
            "no",
            "--yes",
        ]
    )
    return folder


class ChatPermissionTests(unittest.TestCase):
    def test_self_actions_need_no_confirmation(self) -> None:
        with support.TempEnv() as env:
            def boom(*_args: object, **_kwargs: object) -> bool:
                raise AssertionError("confirmation was not needed")

            with patch("sys.stdin.isatty", return_value=False), patch(
                "agentcoop.confirm.ask_on_screen", side_effect=boom
            ), patch("agentcoop.rounds.shutil.which", side_effect=_which), patch(
                "agentcoop.rounds.subprocess.run", return_value=_Completed()
            ), patch("agentcoop.rounds.subprocess.Popen"):
                self.assertEqual(main(["connect", "self", "--tool", "cursor"]), 0)
                folder = env.workspace / "app"
                folder.mkdir()
                previous = os.getcwd()
                os.chdir(folder)
                try:
                    code, out, err = _run(["project", "add", "--driver", "self", "--as", "cursor"])
                finally:
                    os.chdir(previous)
                self.assertEqual(code, 0, err)
                self.assertIn("Created the project app", out)
                self.assertEqual(main(["connect", "self", "--tool", "claude-code"]), 0)
                self.assertEqual(main(["join", "--as", "claude_code", "--project", "app"]), 0)
                self.assertEqual(main(["leave", "--as", "claude_code", "--project", "app"]), 0)
                self.assertEqual(main(["start"]), 0)
                self.assertEqual(main(["stop"]), 0)
                self.assertEqual(main(["now"]), 0)
                self.assertEqual(main(["uninstall", "--agent", "--dry-run"]), 0)
            self.assertIn("claude_code", load_agents(env.board)["agents"])
            self.assertTrue((env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md").is_file())
            self.assertFalse((env.home / ".codex").exists())

    def test_other_agent_changes_need_confirmation(self) -> None:
        with support.TempEnv() as env:
            _connect_two(env)
            before = (env.board / "projects.json").read_bytes()
            commands = [
                ["project", "driver", "demo", "claude_code"],
                ["project", "helper", "add", "demo", "claude_code", "--note", "again"],
                ["project", "remove", "demo"],
            ]
            for command in commands:
                err = io.StringIO()
                with patch("sys.stdin.isatty", return_value=False), patch(
                    "agentcoop.confirm.ask_on_screen", return_value=False
                ), redirect_stderr(err), redirect_stdout(io.StringIO()):
                    code = main(command)
                self.assertEqual(code, 1, err.getvalue())
                self.assertIn(DENIED, err.getvalue())
                self.assertEqual((env.board / "projects.json").read_bytes(), before)


class DialogTests(unittest.TestCase):
    def test_allow_cancel_and_timeout(self) -> None:
        with support.TempEnv() as env:
            _connect_two(env)
            fake = _fake_tk()
            _Tk.choice = "allow"
            out = io.StringIO()
            with patch.dict(sys.modules, {"tkinter": fake}), patch(
                "sys.stdin.isatty", return_value=False
            ), redirect_stdout(out):
                self.assertEqual(main(["project", "driver", "demo", "claude_code"]), 0)
            self.assertEqual(load_projects(env.board)["projects"]["demo"]["driver"], "claude_code")
            self.assertIn(
                "Agent Co-op is showing a window on your screen. Click Allow to confirm.",
                _Tk.text_before_window,
            )
            self.assertIn(
                "Agent Co-op is showing a window on your screen. Click Allow to confirm.",
                out.getvalue(),
            )
            self.assertEqual(_Tk.last.after_ms, 120000)
            self.assertEqual(_Tk.last.attrs, ("-topmost", True))
            self.assertEqual(_Tk.last.focused, "Cancel")
            self.assertEqual(_Tk.last.title_text, "Agent Co-op")
            before = (env.board / "projects.json").read_bytes()
            _Tk.choice = "cancel"
            err = io.StringIO()
            with patch.dict(sys.modules, {"tkinter": fake}), patch(
                "sys.stdin.isatty", return_value=False
            ), redirect_stderr(err):
                self.assertEqual(main(["project", "driver", "demo", "cursor"]), 1)
            self.assertIn(DENIED, err.getvalue())
            self.assertEqual((env.board / "projects.json").read_bytes(), before)
            _Tk.choice = "timeout"
            err = io.StringIO()
            out = io.StringIO()
            with patch.dict(sys.modules, {"tkinter": fake}), patch(
                "sys.stdin.isatty", return_value=False
            ), redirect_stderr(err), redirect_stdout(out):
                self.assertEqual(main(["project", "remove", "demo"]), 1)
            self.assertIn("Nobody allowed the change in time, so nothing changed.", out.getvalue())
            self.assertIn("Nobody allowed the change in time, so nothing changed.", err.getvalue())
            self.assertEqual((env.board / "projects.json").read_bytes(), before)

    def test_page_fallback_allows_and_timeout_cancels(self) -> None:
        with support.TempEnv() as env:
            _connect_two(env)

            def allow(url: str) -> bool:
                target = url.replace("/confirm?", "/answer?")
                data = urllib.parse.urlencode({"choice": "allow"}).encode("utf-8")
                urllib.request.urlopen(target, data=data, timeout=5)
                return True

            out = io.StringIO()
            with patch("agentcoop.confirm._ask_tk", side_effect=TkUnavailable("no")), patch(
                "sys.stdin.isatty", return_value=False
            ), patch("agentcoop.confirm.webbrowser.open", side_effect=allow), redirect_stdout(out):
                self.assertEqual(main(["project", "driver", "demo", "claude_code"]), 0)
            self.assertIn("Agent Co-op needs you to allow this change:", out.getvalue())
            self.assertIn("http://127.0.0.1:", out.getvalue())
            self.assertIn("/confirm?", out.getvalue())
            self.assertEqual(load_projects(env.board)["projects"]["demo"]["driver"], "claude_code")
            before = (env.board / "projects.json").read_bytes()
            err = io.StringIO()
            out = io.StringIO()
            with patch("agentcoop.confirm._ask_tk", side_effect=TkUnavailable("no")), patch(
                "sys.stdin.isatty", return_value=False
            ), patch("agentcoop.confirm.webbrowser.open", return_value=True), patch(
                "agentcoop.confirm.CONFIRM_TIMEOUT_SECONDS", 0.05
            ), redirect_stderr(err), redirect_stdout(out):
                self.assertEqual(main(["project", "remove", "demo"]), 1)
            self.assertIn("Nobody allowed the change in time, so nothing changed.", out.getvalue())
            self.assertIn("Nobody allowed the change in time, so nothing changed.", err.getvalue())
            self.assertEqual((env.board / "projects.json").read_bytes(), before)

    def test_page_link_is_flushed_before_the_wait(self) -> None:
        with support.TempEnv() as env:
            _connect_two(env)
            before = (env.board / "projects.json").read_bytes()
            script = env.root / "ask.py"
            script.write_text(
                "import sys\n"
                "sys.modules['tkinter'] = None\n"
                "from agentcoop.cli import main\n"
                "raise SystemExit(main(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            child_env = os.environ.copy()
            child_env["PYTHONPATH"] = str(support.SRC)
            child_env["AGENTCOOP_BOARD"] = str(env.board)
            proc = subprocess.Popen(
                [sys.executable, str(script), "project", "remove", "demo"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                env=child_env,
            )
            box: list[bytes] = []

            def read() -> None:
                if proc.stdout is None:
                    return
                chunks = []
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    chunks.append(line)
                    if b"/confirm?" in b"".join(chunks):
                        break
                box.append(b"".join(chunks))

            thread = threading.Thread(target=read)
            thread.start()
            thread.join(2)
            try:
                self.assertFalse(thread.is_alive())
                text = (box[0] if box else b"").decode("utf-8", errors="replace")
            finally:
                proc.kill()
                proc.wait(timeout=5)
                for stream in (proc.stdout, proc.stderr, proc.stdin):
                    if stream is not None:
                        stream.close()
            self.assertIn("Agent Co-op needs you to allow this change:", text)
            self.assertIn("http://127.0.0.1:", text)
            self.assertIn("/confirm?", text)
            self.assertEqual((env.board / "projects.json").read_bytes(), before)

    def test_window_text_names_the_change_and_the_agent(self) -> None:
        _Tk.choice = "cancel"
        with patch.dict(sys.modules, {"tkinter": _fake_tk()}):
            self.assertEqual(ask_on_screen("This will remove the demo project.", "cursor"), "cancel")
        labels = []
        # The fake records buttons, not labels. Ask again and keep the widget texts via a list.
        seen: list[str] = []

        class Remember(_Widget):
            def __init__(self, *_args: object, **kwargs: object) -> None:
                super().__init__(*_args, **kwargs)
                if kwargs.get("text"):
                    seen.append(str(kwargs["text"]))

        fake = _fake_tk()
        fake.Label = Remember
        fake.Button = Remember
        fake.Frame = Remember
        _Tk.choice = "cancel"
        with patch.dict(sys.modules, {"tkinter": fake}):
            ask_on_screen("This will remove the demo project.", "cursor")
        self.assertIn("This will remove the demo project.", seen)
        self.assertIn("Asked by cursor.", seen)
        self.assertIn("Allow", seen)
        self.assertIn("Cancel", seen)


class ProjectSelfTests(unittest.TestCase):
    def test_name_existing_driver_other_driver_and_suffix(self) -> None:
        with support.TempEnv() as env:
            main(["connect", "self", "--tool", "cursor"])
            main(["connect", "self", "--tool", "claude-code"])
            named = env.workspace / "My App"
            named.mkdir()
            previous = os.getcwd()
            os.chdir(named)
            try:
                code, out, err = _run(["project", "add", "--driver", "self", "--as", "cursor"])
                self.assertEqual(code, 0, err)
                self.assertIn("Created the project my-app", out)
                code, out, err = _run(["project", "add", "--driver", "self", "--as", "cursor"])
                self.assertEqual(code, 0, err)
                self.assertIn("you are the driver", out)
                self.assertIn("Nothing was changed", out)
            finally:
                os.chdir(previous)
            self.assertEqual(load_projects(env.board)["projects"]["my-app"]["driver"], "cursor")
            os.chdir(named)
            try:
                with patch("sys.stdin.isatty", return_value=False), patch(
                    "agentcoop.confirm.ask_on_screen", return_value=False
                ):
                    code, out, err = _run(["project", "add", "--driver", "self", "--as", "claude_code"])
                self.assertEqual(code, 1, out)
                self.assertIn(DENIED, err)
                self.assertIn("The driver is cursor", out)
                with patch("sys.stdin.isatty", return_value=False), patch(
                    "agentcoop.confirm.ask_on_screen", return_value=True
                ):
                    code, out, err = _run(["project", "add", "--driver", "self", "--as", "claude_code"])
                self.assertEqual(code, 0, err)
                self.assertEqual(load_projects(env.board)["projects"]["my-app"]["driver"], "claude_code")
            finally:
                os.chdir(previous)
            other = env.workspace / "taken" / "demo"
            other.parent.mkdir()
            other.mkdir()
            first = env.workspace / "one" / "demo"
            first.parent.mkdir()
            first.mkdir()
            os.chdir(first)
            try:
                code, _out, err = _run(["project", "add", "--driver", "self", "--as", "cursor"])
            finally:
                os.chdir(previous)
            self.assertEqual(code, 0, err)
            os.chdir(other)
            try:
                code, out, err = _run(["project", "add", "--driver", "self", "--as", "claude_code"])
            finally:
                os.chdir(previous)
            self.assertEqual(code, 0, err)
            self.assertIn("Created the project demo-2", out)
            self.assertIn("demo was already used", out)
            self.assertIn("demo-2", load_projects(env.board)["projects"])


class JoinLeaveTests(unittest.TestCase):
    def test_folder_words_unclear_and_files(self) -> None:
        with support.TempEnv() as env:
            main(["setup", "--connect", "cursor,claude-code", "--schedule", "no", "--yes"])
            alpha = env.workspace / "alpha"
            beta = env.workspace / "beta"
            alpha.mkdir()
            beta.mkdir()
            main(["project", "add", str(alpha), "--name", "alpha", "--driver", "cursor", "--gitignore", "no", "--yes"])
            main(
                [
                    "project",
                    "add",
                    str(beta),
                    "--name",
                    "comstar-game-ai",
                    "--driver",
                    "cursor",
                    "--gitignore",
                    "no",
                    "--yes",
                ]
            )
            previous = os.getcwd()
            os.chdir(alpha)
            try:
                code, out, err = _run(["join", "--as", "claude_code", "comstar", "game"])
            finally:
                os.chdir(previous)
            self.assertEqual(code, 0, err)
            self.assertIn("You joined alpha", out)
            code, out, err = _run(["join", "--as", "claude_code", "Comstar", "Game", "AI"])
            self.assertEqual(code, 0, err)
            self.assertIn("You joined comstar-game-ai", out)
            code, out, err = _run(["join", "--as", "claude_code", "alpha", "comstar-game-ai"])
            self.assertEqual(code, 4, err)
            self.assertIn("could not tell which project", err)
            self.assertIn("alpha", err)
            code, out, err = _run(["join", "--as", "cursor", "--project", "alpha"])
            self.assertEqual(code, 1)
            self.assertIn("cannot join", err)
            code, out, err = _run(["leave", "--as", "cursor", "--project", "alpha"])
            self.assertEqual(code, 1)
            self.assertIn("Changing the driver needs the person to allow it", err)
            code, out, err = _run(["join", "--as", "claude_code", "--project", "alpha"])
            self.assertEqual(code, 0, err)
            self.assertIn("already a helper", out)
            rule = alpha / ".cursor" / "rules" / "agent-coop.mdc"
            # cursor is the driver, so the rule is the driver's file.
            # Join claude_code does not install a cursor rule. Install cursor as helper elsewhere.
            self.assertTrue(rule.is_file())
            code, out, err = _run(["leave", "--as", "claude_code", "--project", "alpha"])
            self.assertEqual(code, 0, err)
            self.assertIn("You left alpha", out)
            self.assertNotIn("claude_code", load_projects(env.board)["projects"]["alpha"]["helpers"])

    def test_project_files_and_check_header(self) -> None:
        with support.TempEnv() as env:
            main(["setup", "--connect", "cursor,claude-code", "--schedule", "no", "--yes"])
            folder = env.workspace / "work"
            folder.mkdir()
            main(
                [
                    "project",
                    "add",
                    str(folder),
                    "--name",
                    "work",
                    "--driver",
                    "claude_code",
                    "--gitignore",
                    "no",
                    "--yes",
                ]
            )
            code, _out, err = _run(["join", "--as", "cursor", "--project", "work"])
            self.assertEqual(code, 0, err)
            rule = folder / ".cursor" / "rules" / "agent-coop.mdc"
            self.assertTrue(rule.is_file())
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["check", "--as", "claude_code", "--project", "work"]), 0)
            self.assertIn("cursor joined as a helper.", out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["check", "--as", "claude_code", "--project", "work"]), 0)
            self.assertNotIn("joined as a helper", out.getvalue())
            rule.write_text(rule.read_text(encoding="utf-8") + "\nextra\n", encoding="utf-8")
            # Rejoin so leave has something to undo. The edited file is left in place.
            code, _out, err = _run(["join", "--as", "cursor", "--project", "work"])
            self.assertEqual(code, 0, err)
            code, out, err = _run(["leave", "--as", "cursor", "--project", "work"])
            self.assertEqual(code, 0, err)
            self.assertTrue(rule.is_file())
            self.assertIn("extra", rule.read_text(encoding="utf-8"))


class ConnectSelfTests(unittest.TestCase):
    def test_installs_one_tool_refuses_a_mismatch_and_repeats(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["connect", "self", "--tool", "claude-code"]), 0)
            self.assertTrue((env.home / ".claude" / "skills" / "agent-coop" / "SKILL.md").is_file())
            self.assertFalse((env.home / ".codex").exists())
            self.assertEqual(main(["connect", "self", "--tool", "claude-code"]), 0)
            self.assertEqual(list(load_agents(env.board)["agents"]), ["claude_code"])
            before = (env.board / "agents.json").read_bytes()
            err = io.StringIO()
            with patch.dict(os.environ, {"CLAUDECODE": "1"}), redirect_stderr(err):
                code = main(["connect", "self", "--tool", "cursor"])
            self.assertEqual(code, 1)
            self.assertIn("Claude Code", err.getvalue())
            self.assertIn("Nothing was changed", err.getvalue())
            self.assertEqual((env.board / "agents.json").read_bytes(), before)
            self.assertFalse((env.home / ".cursor").exists())


class RequestTests(unittest.TestCase):
    def test_join_leave_reject_and_young_files(self) -> None:
        with support.TempEnv() as env:
            folder = _connect_two(env)
            box = env.board / "projects" / "demo" / "requests" / "claude_desktop"
            box.mkdir(parents=True)
            young = box / "young.json"
            young.write_text('{"action": "join"}\n', encoding="utf-8")
            process_project_requests(env.board, "demo")
            self.assertTrue(young.is_file())
            young.unlink()
            old = box / "old.json"
            old.write_text('{"action": "join", "note": "thinks"}\n', encoding="utf-8")
            when = time_old()
            os.utime(old, (when, when))
            main(["connect", "self", "--tool", "claude-desktop"])
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["check", "--as", "cursor", "--project", "demo"]), 0)
            self.assertIn("claude_desktop joined as a helper.", out.getvalue())
            self.assertTrue((box / "done" / "old.json").is_file())
            self.assertIn("joined", (box / "done" / "old.json.result.txt").read_text(encoding="utf-8").lower())
            other = box / "nope.json"
            other.write_text('{"action": "remove"}\n', encoding="utf-8")
            os.utime(other, (when, when))
            process_project_requests(env.board, "demo")
            reason = (box / "rejected" / "nope.json.reason.txt").read_text(encoding="utf-8")
            self.assertIn(OTHER_ACTION, reason)
            leave = box / "leave.json"
            leave.write_text('{"action": "leave"}\n', encoding="utf-8")
            os.utime(leave, (when, when))
            from agentcoop.rounds import tick

            original = __import__("agentcoop.rounds", fromlist=["rounds"])
            # tick covers the project even when this test does not run check.
            started: list[str] = []
            real = original._start_detached
            original._start_detached = lambda *_args, **_kwargs: started.append("x")
            try:
                self.assertEqual(tick(env.board), 0)
            finally:
                original._start_detached = real
            self.assertTrue((box / "done" / "leave.json").is_file())
            self.assertNotIn("claude_desktop", load_projects(env.board)["projects"]["demo"]["helpers"])
            self.assertTrue(folder.is_dir())


def time_old() -> float:
    return (datetime.now() - timedelta(seconds=30)).timestamp()


class StatusTests(unittest.TestCase):
    def test_status_file_lists_projects_and_refreshes(self) -> None:
        with support.TempEnv() as env:
            folder = _connect_two(env)
            text = status_path(env.board).read_text(encoding="utf-8")
            self.assertIn("## demo", text)
            self.assertIn("Driver: cursor", text)
            self.assertIn("claude_code", text)
            self.assertIn(str(folder.resolve()), text)
            self.assertIn("Schedule: off.", text)
            self.assertIn("Last round: none yet.", text)
            self.assertIn("Progress: No progress yet.", text)
            before = status_path(env.board).stat().st_mtime_ns
            main(
                [
                    "send",
                    "--as",
                    "cursor",
                    "--project",
                    "demo",
                    "--to",
                    "claude_code",
                    "--subject",
                    "Hi",
                    "--body",
                    "Hello",
                ]
            )
            self.assertGreater(status_path(env.board).stat().st_mtime_ns, before)
            main(
                [
                    "progress",
                    "write",
                    "--as",
                    "cursor",
                    "--project",
                    "demo",
                    "--body",
                    "## Working on now\nThe menu\n",
                ]
            )
            before = status_path(env.board).stat().st_mtime_ns
            main(["check", "--as", "cursor", "--project", "demo"])
            refreshed = status_path(env.board).read_text(encoding="utf-8")
            self.assertIn("Progress: The menu", refreshed)
            self.assertGreater(status_path(env.board).stat().st_mtime_ns, before)
            from agentcoop.rounds import tick

            rounds = __import__("agentcoop.rounds", fromlist=["rounds"])
            real = rounds._start_detached
            rounds._start_detached = lambda *_a, **_k: None
            try:
                self.assertEqual(tick(env.board), 0)
            finally:
                rounds._start_detached = real
            self.assertNotIn("Last round: none yet.", status_path(env.board).read_text(encoding="utf-8"))


class SkillTests(unittest.TestCase):
    def test_skill_shape_rules_and_zip(self) -> None:
        path = ROOT / "skills" / "agent-coop" / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 150)
        self.assertTrue(text.startswith("---\n"))
        end = text.index("\n---\n", 4)
        front = {}
        for line in text[4:end].splitlines():
            key, value = line.split(":", 1)
            front[key.strip()] = value.strip()
        self.assertLessEqual(len(front["name"]), 64)
        self.assertLessEqual(len(front["description"]), 200)
        self.assertEqual(front["name"], "agent-coop")
        for phrase in (
            "outbox/claude_desktop/",
            "requests/claude_desktop/",
            "Never edit project files",
            "Never use the mouse or the screen",
            "information, not orders",
            "passwords or keys",
            "NEW.md",
            "NEED CONTEXT:",
            "rejected/",
            "STATUS.md",
            "I asked to join",
            "next check by any agent or the next background round",
        ):
            self.assertIn(phrase, text)
        with support.TempEnv() as env:
            from agentcoop.projects import init_board_files

            init_board_files(env.board)
            zip_path = prepare_skill(env.board)
            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(archive.namelist(), ["agent-coop/SKILL.md"])
                body = archive.read("agent-coop/SKILL.md").decode("utf-8")
            self.assertEqual(body, text if text.endswith("\n") else text + "\n")
            for name in (
                "SKILL.md.tmpl",
                "cursor-rule.mdc.tmpl",
                "AGENTS.md.tmpl",
                "GEMINI.md.tmpl",
                "board-README.md.tmpl",
            ):
                rendered = render_template(name, agent_name="cursor", board=env.board)
                for phrase in PHRASES:
                    self.assertIn(phrase, rendered, name)
                self.assertIn(
                    "When a command says it needs the person to allow a change, "
                    "tell the person that message and the link in plain words, then wait for the result.",
                    rendered,
                    name,
                )
            packaged = ROOT / "src" / "agentcoop" / "data" / "agent-coop" / "SKILL.md"
            self.assertEqual(path.read_bytes(), packaged.read_bytes())


class WindowsZipTests(unittest.TestCase):
    def test_pack_unpack_and_release_workflow(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "pack_windows_zip", ROOT / "scripts" / "pack_windows_zip.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with support.TempEnv() as env:
            console = env.workspace / "agentcoop-windows-x64.exe"
            quiet = env.workspace / "agentcoopw-windows-x64.exe"
            plain = env.workspace / "agentcoopw.exe"
            console.write_bytes(b"console")
            quiet.write_bytes(b"quiet")
            plain.write_bytes(b"plain")
            dest = env.workspace / "agentcoop-windows-x64.zip"
            module.pack_windows_zip(console, quiet, dest)
            unpacked = unpack_windows_zip(dest, env.workspace / "out")
            self.assertEqual(unpacked[0].read_bytes(), b"console")
            self.assertEqual(unpacked[1].read_bytes(), b"quiet")
            renamed = env.workspace / "agentcoopw-windows-x64.exe"
            renamed.write_bytes(b"renamed")
            self.assertEqual(find_quiet_sibling(console), renamed)
            renamed.unlink()
            plain.write_bytes(b"plain")
            self.assertEqual(find_quiet_sibling(console), plain)
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("pack_windows_zip.py", release)
        self.assertIn("agentcoop-windows-x64", release)
        self.assertIn("${{ matrix.artifact }}.zip", release)
        self.assertIn("--collect-all tkinter", release)
        self.assertIn("skills/agent-coop", release)
        smoke = (ROOT / "scripts" / "smoke_test.py").read_text(encoding="utf-8")
        self.assertIn("unpack_windows_zip", smoke)
        self.assertIn("AGENTCOOP_IMPORT_TK", smoke)
        self.assertIn("version", smoke)


def unpack_windows_zip(zip_path: Path, dest: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("smoke_test_mod", ROOT / "scripts" / "smoke_test.py")
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module.unpack_windows_zip(zip_path, dest)
