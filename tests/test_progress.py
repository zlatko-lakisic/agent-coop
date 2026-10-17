"""Driver progress documents and help requests."""

from __future__ import annotations

import io
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.core import log_messages
from agentcoop.doctor import run_doctor
from agentcoop.install import read_template
from agentcoop.progress import write_progress
from agentcoop.rounds import run_agent


def _ready(env: support.TempEnv) -> None:
    main(["setup", "--connect", "cursor,claude-code,codex", "--schedule", "no", "--yes"])
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
            "--helper",
            "codex=answers",
            "--gitignore",
            "no",
            "--yes",
        ]
    )


class ProgressTests(unittest.TestCase):
    def test_write_show_list_template_and_limits(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            body = "## Working on now\nThe demo.\n## Blocked or need help\nNone\n"
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["progress", "write", "--as", "cursor", "--project", "demo", "--body", body])
            self.assertEqual(code, 0)
            name = out.getvalue().strip()
            self.assertTrue(name.endswith("_cursor.md"))
            shown = io.StringIO()
            with redirect_stdout(shown):
                self.assertEqual(main(["progress", "show", "--as", "cursor", "--project", "demo"]), 0)
            self.assertIn("Progress for demo", shown.getvalue())
            self.assertIn("The demo.", shown.getvalue())
            listed = io.StringIO()
            with redirect_stdout(listed):
                self.assertEqual(main(["progress", "list", "--project", "demo"]), 0)
            self.assertIn(name, listed.getvalue())
            template = io.StringIO()
            with redirect_stdout(template):
                self.assertEqual(main(["progress", "template"]), 0)
            self.assertIn("## Working on now", template.getvalue())
            self.assertEqual(
                main(["progress", "write", "--as", "claude_code", "--project", "demo", "--body", "no"]),
                1,
            )
            self.assertEqual(
                main(["progress", "write", "--as", "cursor", "--project", "demo", "--body", "x" * 17000]),
                1,
            )
            self.assertEqual(
                main(
                    [
                        "progress",
                        "write",
                        "--as",
                        "cursor",
                        "--project",
                        "demo",
                        "--body",
                        "sk-abcdefghij",
                    ]
                ),
                1,
            )

    def test_progress_does_not_send_or_start_a_round(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            with patch("agentcoop.rounds.spawn_round") as started:
                main(["progress", "write", "--as", "cursor", "--project", "demo", "--body", "Working.\n"])
            started.assert_not_called()
            self.assertEqual(log_messages(env.board, "demo"), [])

    def test_send_flags(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            self.assertEqual(
                main(
                    [
                        "send",
                        "--as",
                        "cursor",
                        "--project",
                        "demo",
                        "--to",
                        "claude_code",
                        "--with-progress",
                        "--subject",
                        "Help",
                        "--body",
                        "Look",
                    ]
                ),
                1,
            )
            main(["progress", "write", "--as", "cursor", "--project", "demo", "--body", "## Working on now\nFiles.\n"])
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(
                    main(
                        [
                            "send",
                            "--as",
                            "cursor",
                            "--project",
                            "demo",
                            "--to",
                            "claude_code",
                            "--with-progress",
                            "--subject",
                            "Help",
                            "--body",
                            "Look",
                        ]
                    ),
                    0,
                )
            message = log_messages(env.board, "demo")[-1]
            self.assertTrue(any(part.endswith("_cursor.md") for part in message.attachments))
            self.assertEqual(
                main(
                    [
                        "send",
                        "--as",
                        "claude_code",
                        "--project",
                        "demo",
                        "--to",
                        "cursor",
                        "--urgent",
                        "--subject",
                        "No",
                        "--body",
                        "no",
                    ]
                ),
                1,
            )
            note = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(note), redirect_stderr(err), patch("agentcoop.rounds.spawn_round") as started:
                self.assertEqual(
                    main(
                        [
                            "send",
                            "--as",
                            "cursor",
                            "--project",
                            "demo",
                            "--to",
                            "claude_code",
                            "--urgent",
                            "--subject",
                            "Now",
                            "--body",
                            "please",
                        ]
                    ),
                    0,
                )
            started.assert_not_called()
            self.assertEqual(note.getvalue(), note.getvalue().strip() + "\n")
            self.assertNotIn("\n", note.getvalue().strip())
            self.assertIn("The schedule is off", err.getvalue())
            both = io.StringIO()
            with redirect_stdout(both):
                self.assertEqual(
                    main(
                        [
                            "send",
                            "--as",
                            "cursor",
                            "--project",
                            "demo",
                            "--to",
                            "claude_code,codex",
                            "--subject",
                            "Both",
                            "--body",
                            "two",
                        ]
                    ),
                    0,
                )
            ids = [line for line in both.getvalue().splitlines() if line.strip()]
            self.assertEqual(len(ids), 2)

    def test_wait_and_reminder_and_status(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            old = datetime.now(timezone.utc) - timedelta(minutes=40)
            write_progress(env.board, "demo", "cursor", "## Working on now\nOld work.\n", now=old)
            quiet = io.StringIO()
            with redirect_stdout(quiet):
                main(["check", "--as", "cursor", "--project", "demo"])
            self.assertNotIn("Your last progress update was", quiet.getvalue())
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            active = env.board / ".state" / "active-cursor"
            active.parent.mkdir(parents=True, exist_ok=True)
            active.write_text(stamp + "\n", encoding="utf-8")
            checked = io.StringIO()
            with redirect_stdout(checked):
                main(["check", "--as", "cursor", "--project", "demo"])
            self.assertIn("Your last progress update was", checked.getvalue())
            status = io.StringIO()
            with redirect_stdout(status):
                main(["status"])
            self.assertIn("Working on now", status.getvalue())
            sent = io.StringIO()
            with redirect_stdout(sent):
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
                        "Question",
                        "--body",
                        "Need a look",
                    ]
                )
            mid = sent.getvalue().strip()

            def reply() -> None:
                time.sleep(0.2)
                main(
                    [
                        "send",
                        "--as",
                        "claude_code",
                        "--project",
                        "demo",
                        "--to",
                        "cursor",
                        "--re",
                        mid,
                        "--type",
                        "answer",
                        "--subject",
                        "Re: Question",
                        "--body",
                        "Done",
                    ]
                )

            threading.Thread(target=reply).start()
            waited = io.StringIO()
            with redirect_stdout(waited):
                self.assertEqual(main(["wait", "--as", "cursor", "--project", "demo", "--for", mid, "--timeout", "1"]), 0)
            self.assertIn("Re: Question", waited.getvalue())

    def test_doctor_notes_and_templates(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            old = datetime.now(timezone.utc) - timedelta(hours=2)
            write_progress(
                env.board,
                "demo",
                "cursor",
                "## Blocked or need help\nThe build fails.\n",
                now=old,
            )
            from agentcoop.progress import record_urgent

            record_urgent(env.board, "demo", "old-id", when=old)
            items = run_doctor(env.board)
            notes = "\n".join(item.text for item in items if item.level == "note")
            self.assertIn("blocked", notes)
            self.assertIn("old-id", notes)
            sentence = "progress write --as"
            for name in ("SKILL.md.tmpl", "cursor-rule.mdc.tmpl", "AGENTS.md.tmpl", "GEMINI.md.tmpl", "run-driver.txt.tmpl"):
                self.assertIn(sentence, read_template(name))
            self.assertIn("read the attached progress update", read_template("SKILL.md.tmpl"))

    def test_scheduled_helper_sees_progress(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            main(["progress", "write", "--as", "cursor", "--project", "demo", "--body", "## Working on now\nThe header.\n"])
            main(
                [
                    "send",
                    "--as",
                    "cursor",
                    "--project",
                    "demo",
                    "--to",
                    "claude_code",
                    "--with-progress",
                    "--subject",
                    "Look",
                    "--body",
                    "Please look.",
                ]
            )
            from agentcoop.projects import load_agents, save_agents

            agents = load_agents(env.board)
            agents["agents"]["claude_code"]["cli_path"] = str(env.workspace / "claude")
            (env.workspace / "claude").write_text("x", encoding="utf-8")
            save_agents(env.board, agents)
            captured = []

            def launch(argv, **kwargs):
                captured.append(list(argv))

                class Proc:
                    returncode = 0
                    pid = 424242
                    stdout = None

                    def communicate(self, timeout=None):
                        return "ok\n", ""

                    def kill(self):
                        return None

                    def wait(self, timeout=None):
                        return 0

                return Proc()

            run_agent(env.board, "claude_code", "demo", popen=launch)
            self.assertIn("The header.", "\n".join(captured[0]))


    def test_archive_keeps_the_latest_20(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            start = datetime(2020, 1, 1, tzinfo=timezone.utc)
            for index in range(25):
                write_progress(
                    env.board,
                    "demo",
                    "cursor",
                    "version " + str(index) + "\n",
                    now=start + timedelta(days=index),
                )
            main(["archive", "--as", "cursor", "--project", "demo", "--older-than", "1"])
            folder = env.board / "projects" / "demo" / "progress"
            self.assertEqual(len(list(folder.glob("*.md"))), 20)


if __name__ == "__main__":
    unittest.main()
