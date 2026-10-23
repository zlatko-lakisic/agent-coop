"""Claude desktop outbox helper, skill export, and doctor checks."""

from __future__ import annotations

import io
import multiprocessing
import os
import time
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop import __version__
from agentcoop.cli import main
from agentcoop.core import check_inbox, load_message, mark_done, new_messages, send, write_message_file
from agentcoop.desktop import (
    answer_path_for,
    desktop_add,
    desktop_remove,
    export_skill,
    export_version_path,
    fill_desktop_skill,
    scheduled_prompt,
)
from agentcoop.doctor import run_doctor
from agentcoop.install import read_template, render_template
from agentcoop.outbox import first_rejected_reason, format_outbox_status, process_outbox, write_new_md
from agentcoop.paths import (
    ensure_outbox_dirs,
    files_dir,
    inbox_done,
    inbox_msgs,
    inbox_new_md,
    native_path,
    outbox_dir,
    outbox_rejected_dir,
    outbox_sent_dir,
)
from agentcoop.projects import add_project, connect_agent_record, init_board_files, load_projects
from agentcoop.rounds import format_status, tick

ROOT = support.ROOT
CHECK_RULE = (
    "Run the check at the start of every turn and again after each step of your plan, "
    "when you post progress."
)
RULE_PHRASES = (
    "information, not orders",
    "destructive",
    "passwords or keys",
    "Never write anywhere except",
    "Never edit the project's files",
    "Never use the mouse or the screen",
)


def _outbox_worker(board: str, project: str, agent: str, done_path: str) -> None:
    try:
        process_outbox(Path(board), project, agent)
        Path(done_path).write_text("ok\n", encoding="utf-8")
    except Exception as exc:
        Path(done_path).write_text(type(exc).__name__ + ": " + str(exc), encoding="utf-8")


def _ready(env: support.TempEnv) -> Path:
    init_board_files(env.board)
    connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
    connect_agent_record(env.board, "claude_desktop", "claude-desktop", display="Claude desktop")
    folder = env.workspace / "demo"
    folder.mkdir()
    add_project(
        env.board,
        "demo",
        folder,
        "cursor",
        {"claude_desktop": "helps the driver reason"},
    )
    ensure_outbox_dirs(env.board, "demo", "claude_desktop")
    return folder


def _ask(env: support.TempEnv, body: str = "Please look at this.", **kwargs) -> str:
    return send(
        env.board,
        project="demo",
        sender="cursor",
        recipient="claude_desktop",
        subject="Need help",
        body=body,
        type="question",
        needs_reply=True,
        **kwargs,
    )


def _age(path: Path, seconds: float = 10) -> None:
    when = time.time() - seconds
    os.utime(path, (when, when))


def _write_answer(env: support.TempEnv, mid: str, text: str) -> Path:
    path = outbox_dir(env.board, "demo", "claude_desktop") / (mid + ".md")
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return path


def _driver_replies(env: support.TempEnv, mid: str) -> list:
    found = []
    folder = inbox_msgs(env.board, "demo", "cursor")
    if not folder.is_dir():
        return found
    for path in folder.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        message = load_message(path)
        if message is not None and message.re == mid and message.sender == "claude_desktop":
            found.append(message)
    return found


class ClaudeDesktopTests(unittest.TestCase):
    def test_new_md_lists_messages_and_rebuilds(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            write_new_md(env.board, "demo", "claude_desktop")
            empty = inbox_new_md(env.board, "demo", "claude_desktop").read_text(encoding="utf-8")
            self.assertTrue(empty.startswith("No new messages."))
            self.assertIn("Written: ", empty)
            self.assertIn("Latest context: None yet", empty)
            self.assertIn("Latest progress: None yet", empty)
            note = folder / "notes.txt"
            note.write_text("layout notes\n", encoding="utf-8")
            body = "Line one of the question.\nLine two of the question."
            mid = _ask(env, body, attachments=[str(note.resolve())])
            text = inbox_new_md(env.board, "demo", "claude_desktop").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("1 new message."))
            self.assertIn("id: " + mid, text)
            self.assertIn("from: cursor", text)
            self.assertIn("type: question", text)
            self.assertIn("subject: Need help", text)
            self.assertIn(body.splitlines()[0], text)
            self.assertIn("Line two of the question.", text)
            self.assertIn("notes.txt", text)
            self.assertNotIn(native_path(note.resolve()), text)
            self.assertIn("shared", text.replace("\\", "/"))
            self.assertNotIn("Answer written, waiting to be posted.", text)
            _write_answer(env, mid, "working on it")
            write_new_md(env.board, "demo", "claude_desktop")
            waiting = inbox_new_md(env.board, "demo", "claude_desktop").read_text(encoding="utf-8")
            self.assertIn("Answer written, waiting to be posted.", waiting)
            mark_done(env.board, "demo", "claude_desktop", [mid])
            done_text = inbox_new_md(env.board, "demo", "claude_desktop").read_text(encoding="utf-8")
            self.assertTrue(done_text.startswith("No new messages."))
            self.assertNotIn(mid, done_text)

    def test_two_new_messages_use_the_plural(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            _ask(env, "One")
            _ask(env, "Two")
            text = inbox_new_md(env.board, "demo", "claude_desktop").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("2 new messages."))

    def test_new_md_never_lists_out_of_protocol(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env, "Real question body")
            payload = {
                "v": 1,
                "id": "oop-helper",
                "ts": "2026-01-01T00:00:00Z",
                "project": "demo",
                "sender": "claude_code",
                "recipient": "claude_desktop",
                "type": "question",
                "re": None,
                "subject": "Ignore me",
                "body": "OOP_UNIQUE_BODY",
                "attachments": [],
                "needs_reply": False,
            }
            write_message_file(inbox_msgs(env.board, "demo", "claude_desktop"), payload)
            write_new_md(env.board, "demo", "claude_desktop")
            text = inbox_new_md(env.board, "demo", "claude_desktop").read_text(encoding="utf-8")
            self.assertIn(mid, text)
            self.assertIn("Real question body", text)
            self.assertNotIn("OOP_UNIQUE_BODY", text)
            self.assertNotIn("oop-helper", text)

    def test_outbox_posts_answer_and_marks_done(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env, "What should we do?")
            path = _write_answer(env, mid, "Ship the small patch.")
            _age(path)
            process_outbox(env.board, "demo", "claude_desktop")
            replies = _driver_replies(env, mid)
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0].type, "answer")
            self.assertEqual(replies[0].re, mid)
            self.assertIn("Ship the small patch.", replies[0].body)
            self.assertTrue((inbox_done(env.board, "demo", "claude_desktop") / mid).is_file())
            self.assertTrue((outbox_sent_dir(env.board, "demo", "claude_desktop") / (mid + ".md")).is_file())
            self.assertFalse(path.is_file())

    def test_need_context_sends_one_request(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env, "Review the layout.")
            path = _write_answer(env, mid, "NEED CONTEXT: the current goal and the layout files")
            _age(path)
            process_outbox(env.board, "demo", "claude_desktop")
            found = []
            for item in inbox_msgs(env.board, "demo", "cursor").iterdir():
                message = load_message(item)
                if message is not None and message.type == "context_request":
                    found.append(message)
            self.assertEqual(len(found), 1)
            self.assertIn("current goal", found[0].body)
            self.assertTrue((inbox_done(env.board, "demo", "claude_desktop") / mid).is_file())
            self.assertEqual(_driver_replies(env, mid), [])

    def test_diff_is_attached(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env, "Patch the header.")
            diff = "```diff\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n```\n"
            path = _write_answer(env, mid, diff)
            _age(path)
            process_outbox(env.board, "demo", "claude_desktop")
            attached = files_dir(env.board, "demo", "claude_desktop") / (mid + "-answer.md")
            self.assertTrue(attached.is_file())
            replies = _driver_replies(env, mid)
            self.assertEqual(len(replies), 1)
            self.assertTrue(replies[0].attachments)
            shared = Path(replies[0].attachments[0])
            self.assertTrue(shared.is_file())
            self.assertEqual(shared.read_bytes(), attached.read_bytes())
            self.assertIn("shared", str(shared).replace("\\", "/"))

    def test_secret_is_rejected_and_stays_new(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env, "Where is the token?")
            path = _write_answer(env, mid, "The key is sk-abcdefghijk")
            _age(path)
            process_outbox(env.board, "demo", "claude_desktop")
            self.assertFalse((inbox_done(env.board, "demo", "claude_desktop") / mid).is_file())
            rejected = outbox_rejected_dir(env.board, "demo", "claude_desktop") / (mid + ".md")
            self.assertTrue(rejected.is_file())
            reason = (rejected.with_name(rejected.name + ".reason.txt")).read_text(encoding="utf-8")
            self.assertIn("password or API key", reason)
            self.assertNotIn("--allow-secret", reason)
            self.assertIn("was not posted", reason)
            self.assertEqual(_driver_replies(env, mid), [])
            left, _issues = new_messages(env.board, "demo", "claude_desktop")
            self.assertEqual([item.id for item in left], [mid])

    def test_unknown_id_is_rejected_and_a_done_message_can_be_followed_up(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env, "First question")
            mark_done(env.board, "demo", "claude_desktop", [mid])
            answered = _write_answer(env, mid, "a later correction")
            unknown = outbox_dir(env.board, "demo", "claude_desktop") / "missing.md"
            unknown.write_text("no such message\n", encoding="utf-8")
            _age(answered)
            _age(unknown)
            process_outbox(env.board, "demo", "claude_desktop")
            rejected = outbox_rejected_dir(env.board, "demo", "claude_desktop")
            self.assertFalse((rejected / (mid + ".md")).is_file())
            self.assertEqual(len(_driver_replies(env, mid)), 1)
            self.assertEqual(_driver_replies(env, mid)[0].subject, "Re: Need help")
            self.assertTrue((rejected / "missing.md").is_file())
            self.assertIn(
                "There is no message with this id for claude_desktop.",
                (rejected / "missing.md.reason.txt").read_text(encoding="utf-8"),
            )

    def test_existing_reply_moves_to_sent_without_sending_again(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env, "Already handled")
            send(
                env.board,
                project="demo",
                sender="claude_desktop",
                recipient="cursor",
                subject="Re: Need help",
                body="Posted by hand",
                type="answer",
                re=mid,
            )
            path = _write_answer(env, mid, "Posted by hand")
            _age(path)
            process_outbox(env.board, "demo", "claude_desktop")
            self.assertEqual(len(_driver_replies(env, mid)), 1)
            self.assertTrue((outbox_sent_dir(env.board, "demo", "claude_desktop") / (mid + ".md")).is_file())
            self.assertTrue((inbox_done(env.board, "demo", "claude_desktop") / mid).is_file())

    def test_young_file_is_skipped_until_old_enough(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env, "Wait a moment")
            path = _write_answer(env, mid, "soon")
            mtime = path.stat().st_mtime
            process_outbox(env.board, "demo", "claude_desktop", now=datetime.fromtimestamp(mtime))
            self.assertTrue(path.is_file())
            self.assertEqual(_driver_replies(env, mid), [])
            process_outbox(
                env.board,
                "demo",
                "claude_desktop",
                now=datetime.fromtimestamp(mtime + 10),
            )
            self.assertEqual(len(_driver_replies(env, mid)), 1)
            self.assertFalse(path.is_file())

    def test_two_processes_post_exactly_one_reply(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env, "Only once")
            path = _write_answer(env, mid, "one reply")
            _age(path)
            ctx = multiprocessing.get_context("spawn")
            done_a = env.root / "worker-a.txt"
            done_b = env.root / "worker-b.txt"
            procs = [
                ctx.Process(
                    target=_outbox_worker,
                    args=(str(env.board), "demo", "claude_desktop", str(done_a)),
                ),
                ctx.Process(
                    target=_outbox_worker,
                    args=(str(env.board), "demo", "claude_desktop", str(done_b)),
                ),
            ]
            try:
                for proc in procs:
                    proc.start()
                for proc in procs:
                    proc.join(60)
                    self.assertEqual(proc.exitcode, 0)
            finally:
                for proc in procs:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(5)
            self.assertEqual(done_a.read_text(encoding="utf-8").strip(), "ok")
            self.assertEqual(done_b.read_text(encoding="utf-8").strip(), "ok")
            self.assertEqual(len(_driver_replies(env, mid)), 1)

    def test_driver_check_and_tick_process_outbox(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            mid = _ask(env, "From check")
            path = _write_answer(env, mid, "posted during check")
            _age(path)
            check_inbox(env.board, "demo", "cursor")
            self.assertEqual(len(_driver_replies(env, mid)), 1)
            mid2 = _ask(env, "From tick")
            path2 = _write_answer(env, mid2, "posted during tick")
            _age(path2)
            tick(env.board)
            self.assertEqual(len(_driver_replies(env, mid2)), 1)

    def test_skill_export_zip_and_windows_paths(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            zip_path = export_skill(env.board, "demo", "claude_desktop")
            self.assertEqual(zip_path.name, "claude_desktop-demo.zip")
            self.assertEqual(export_version_path(zip_path).read_text(encoding="utf-8"), __version__ + "\n")
            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(archive.comment.decode("utf-8"), __version__)
                self.assertIn("agent-coop-demo/SKILL.md", archive.namelist())
                skill = archive.read("agent-coop-demo/SKILL.md").decode("utf-8")
            self.assertLess(len(skill.splitlines()), 120)
            self.assertIn(native_path(inbox_new_md(env.board, "demo", "claude_desktop").resolve()), skill)
            self.assertIn(native_path(outbox_dir(env.board, "demo", "claude_desktop").resolve()), skill)
            for phrase in RULE_PHRASES:
                self.assertIn(phrase, skill)
            self.assertIn("No new messages.", skill)
            self.assertIn("Answer written, waiting to be posted.", skill)
            self.assertIn("NEED CONTEXT:", skill)
            self.assertIn("unified diff", skill)
            self.assertIn("You never run the agentcoop command.", skill)
            win_new = native_path("C:/Users/you/.agent_board/projects/demo/inbox/claude_desktop/NEW.md")
            win_out = native_path("C:/Users/you/.agent_board/projects/demo/outbox/claude_desktop")
            self.assertIn("\\", win_new)
            self.assertIn("\\", win_out)
            rendered = fill_desktop_skill(
                {
                    "AGENT_NAME": "claude_desktop",
                    "PROJECT": "demo",
                    "DRIVER": "cursor",
                    "NEW_MD": win_new,
                    "OUTBOX": win_out,
                    "OUTBOX_SENT": native_path("C:/Users/you/.agent_board/projects/demo/outbox/claude_desktop/sent"),
                    "OUTBOX_REJECTED": native_path(
                        "C:/Users/you/.agent_board/projects/demo/outbox/claude_desktop/rejected"
                    ),
                    "PROJECT_BOARD": native_path("C:/Users/you/.agent_board/projects/demo"),
                    "PROJECT_PATH": native_path("C:/Users/you/project"),
                    "ANSWER_PATH": answer_path_for(win_out),
                    "SKILL_NAME": "agent-coop-demo",
                }
            )
            self.assertIn("C:\\Users\\you\\.agent_board", rendered)
            self.assertIn("<message id>.<UTC timestamp>.md", rendered)
            self.assertIn("\\", answer_path_for(win_out))

    def test_desktop_add_needs_person_and_prints_setup(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            connect_agent_record(env.board, "claude_desktop", "claude-desktop", display="Claude desktop")
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {})
            err = io.StringIO()
            with patch("sys.stdin.isatty", return_value=False), patch(
                "agentcoop.confirm.ask_on_screen", return_value=False
            ), redirect_stderr(err), redirect_stdout(io.StringIO()):
                self.assertEqual(main(["desktop", "add", "demo"]), 1)
            self.assertIn("The person did not allow this change.", err.getvalue())
            out = io.StringIO()
            with support.person("demo"), redirect_stdout(out):
                self.assertEqual(main(["desktop", "add", "demo"]), 0)
            text = out.getvalue()
            self.assertIn(native_path((env.board / "projects" / "demo").resolve()), text)
            self.assertIn(native_path(folder.resolve()), text)
            self.assertIn(".agent_board", text)
            self.assertIn("Customize > Skills", text)
            self.assertIn("+ Create skill", text)
            self.assertIn("Upload a skill", text)
            self.assertIn(scheduled_prompt("demo", "claude_desktop"), text)
            self.assertIn("progress", text)
            self.assertIn("no console window", text)
            helpers = load_projects(env.board)["projects"]["demo"]["helpers"]
            self.assertEqual(helpers["claude_desktop"], "helps the driver reason")
            self.assertTrue(outbox_dir(env.board, "demo", "claude_desktop").is_dir())
            self.assertTrue((env.board / "exports" / "agent-coop.zip").is_file())

    def test_desktop_remove_keeps_sent(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            export_skill(env.board, "demo", "claude_desktop")
            sent = outbox_sent_dir(env.board, "demo", "claude_desktop")
            kept = sent / "kept.md"
            kept.write_text("already posted\n", encoding="utf-8")
            write_new_md(env.board, "demo", "claude_desktop")
            out = io.StringIO()
            with support.person("demo"), redirect_stdout(out):
                self.assertEqual(main(["desktop", "remove", "demo"]), 0)
            self.assertNotIn("claude_desktop", load_projects(env.board)["projects"]["demo"]["helpers"])
            self.assertFalse(inbox_new_md(env.board, "demo", "claude_desktop").is_file())
            self.assertFalse((env.board / "exports" / "claude_desktop-demo.zip").is_file())
            self.assertTrue(kept.is_file())

    def test_status_and_doctor(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            export_skill(env.board, "demo", "claude_desktop")
            status = format_status(env.board)
            self.assertIn("no new messages", status)
            extra = "\n".join(format_outbox_status(env.board, "claude_desktop"))
            self.assertIn("demo", extra)
            when = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
            _ask(env, "Still waiting", now=when)
            items = run_doctor(env.board, now=when + timedelta(minutes=16))
            notes = "\n".join(item.text for item in items if item.level == "note")
            self.assertIn(
                "claude_desktop has not answered for 16 minutes. Check that the Claude app is open and its scheduled task is running.",
                notes,
            )
            unknown = outbox_dir(env.board, "demo", "claude_desktop") / "nope.md"
            unknown.write_text("gone\n", encoding="utf-8")
            _age(unknown)
            process_outbox(env.board, "demo", "claude_desktop")
            reason = first_rejected_reason(env.board, "demo", "claude_desktop")
            self.assertIsNotNone(reason)
            items = run_doctor(env.board, now=when + timedelta(minutes=16))
            fixes = "\n".join(item.text for item in items if item.level == "fix")
            self.assertIn("claude_desktop has rejected answers on demo. First reason: " + str(reason), fixes)
            sidecar = env.board / "exports" / "claude_desktop-demo.zip.version"
            sidecar.write_text("0.2.0\n", encoding="utf-8")
            items = run_doctor(env.board)
            fixes = "\n".join(item.text for item in items if item.level == "fix")
            self.assertIn(
                "Run: agentcoop desktop prepare, then upload the skill again.",
                fixes,
            )

    def test_old_skill_export_asks_for_the_new_name(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            folder = env.board / "exports"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "claude-desktop.zip").write_bytes(b"old")
            items = run_doctor(env.board)
            fixes = "\n".join(item.text for item in items if item.level == "fix")
            self.assertIn(
                "Re-export the Claude desktop skill with agentcoop desktop prepare and upload it again. "
                "The skill has a new name.",
                fixes,
            )

    def test_driver_templates_contain_the_check_rule(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            for name in ("SKILL.md.tmpl", "cursor-rule.mdc.tmpl", "AGENTS.md.tmpl", "GEMINI.md.tmpl"):
                rendered = render_template(name, agent_name="cursor", board=env.board)
                self.assertIn(CHECK_RULE, rendered)
            self.assertIn(CHECK_RULE, read_template("run-driver.txt.tmpl"))

    def test_release_and_smoke_include_agentcoopw(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("--noconsole", release)
        self.assertIn("agentcoopw", release)
        smoke = (ROOT / "scripts" / "smoke_test.py").read_text(encoding="utf-8")
        self.assertIn("agentcoopw", smoke)
        self.assertIn("version", smoke)

    def test_desktop_add_function_and_prompt(self) -> None:
        with support.TempEnv() as env:
            folder = env.workspace / "demo"
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            connect_agent_record(env.board, "claude_desktop", "claude-desktop", display="Claude desktop")
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {})
            text = desktop_add(env.board, "demo")
            self.assertIn("helps the driver reason", load_projects(env.board)["projects"]["demo"]["helpers"]["claude_desktop"])
            self.assertIn("Customize > Skills", text)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["desktop", "prompt", "--project", "demo"]), 0)
            self.assertEqual(out.getvalue().strip(), scheduled_prompt("demo", "claude_desktop"))
            desktop_remove(env.board, "demo")
            self.assertNotIn("claude_desktop", load_projects(env.board)["projects"]["demo"]["helpers"])


if __name__ == "__main__":
    unittest.main()
