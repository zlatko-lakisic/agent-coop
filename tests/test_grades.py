"""Grades are written once, by the driver at that time, and totaled when read."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone

import support

from unittest.mock import patch

from agentcoop.cli import main
from agentcoop.core import check_inbox, render_check, send
from agentcoop.errors import CoopError
from agentcoop.grades import (
    display_summary,
    find_grade,
    summarize,
    summarize_agent,
    summarize_project,
    write_grade,
)
from agentcoop.outbox import write_new_md
from agentcoop.paths import project_dir
from agentcoop.projects import mutate_projects
from agentcoop.statusfile import render_status


def _quiet(argv: list[str]) -> str:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = main(argv)
    if code != 0:
        raise AssertionError(out.getvalue() or "command failed")
    return out.getvalue()


def _ready(env: support.TempEnv) -> None:
    _quiet(["setup", "--connect", "cursor,claude-code,claude-desktop", "--schedule", "no", "--yes"])
    folder = env.workspace / "demo"
    folder.mkdir()
    _quiet(
        [
            "project",
            "add",
            str(folder),
            "--name",
            "demo",
            "--driver",
            "cursor",
            "--helper",
            "claude_code=edits",
            "--helper",
            "claude_desktop=thinks",
            "--gitignore",
            "no",
            "--yes",
        ]
    )


def _answer(env: support.TempEnv, helper: str, when: datetime, body: str = "Here.") -> str:
    asked = send(
        env.board,
        project="demo",
        sender="cursor",
        recipient=helper,
        subject="Look",
        body="Please look.",
        type="question",
        needs_reply=True,
        now=when,
    )
    return send(
        env.board,
        project="demo",
        sender=helper,
        recipient="cursor",
        subject="Re: Look",
        body=body,
        type="answer",
        re=asked,
        now=when + timedelta(minutes=6),
    )


class GradeTests(unittest.TestCase):
    def test_only_the_driver_writes_one_grade_and_the_fields_are_filled(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            when = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            asked = send(
                env.board,
                project="demo",
                sender="cursor",
                recipient="claude_code",
                subject="Look",
                body="Please look.",
                type="question",
                needs_reply=True,
                now=when,
            )
            answer = send(
                env.board,
                project="demo",
                sender="claude_code",
                recipient="cursor",
                subject="Re: Look",
                body="The fix is in the loop.",
                type="answer",
                re=asked,
                now=when + timedelta(minutes=6),
            )
            progress = project_dir(env.board, "demo") / "progress" / "helpers" / "claude_code"
            progress.mkdir(parents=True)
            (progress / ("20261025T120100Z_" + asked + ".md")).write_text("one\n", encoding="utf-8")
            (progress / ("20261025T120200Z_" + asked + ".md")).write_text("two\n", encoding="utf-8")
            with self.assertRaises(CoopError) as helper_grade:
                write_grade(
                    env.board,
                    "demo",
                    "claude_code",
                    answer,
                    score=4,
                    outcome="used",
                    note="It helped.",
                    now=when + timedelta(hours=1),
                )
            self.assertIn("Only the driver", str(helper_grade.exception))
            payload = write_grade(
                env.board,
                "demo",
                "cursor",
                answer,
                score=4,
                outcome="used",
                note="It helped.",
                correct=5,
                useful=4,
                followed_ask=5,
                tags=["code-review"],
                now=when + timedelta(hours=1),
            )
            self.assertEqual(payload["reply_minutes"], 6)
            self.assertEqual(payload["progress_updates"], 2)
            self.assertFalse(payload["late"])
            self.assertFalse(payload["declined"])
            path = project_dir(env.board, "demo") / "grades" / "claude_code"
            stored = list(path.glob("*.json"))
            self.assertEqual(len(stored), 1)
            before = stored[0].read_bytes()
            with self.assertRaises(CoopError) as again:
                write_grade(
                    env.board,
                    "demo",
                    "cursor",
                    answer,
                    score=1,
                    outcome="wrong",
                    note="A second grade.",
                    now=when + timedelta(hours=2),
                )
            self.assertIn("already has a grade", str(again.exception))
            self.assertEqual(stored[0].read_bytes(), before)
            self.assertEqual(json.loads(before.decode("utf-8"))["score"], 4)

    def test_history_not_the_driver_field_decides_who_can_grade(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            when = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            answer = _answer(env, "claude_code", when)

            def change(data: dict) -> None:
                record = data["projects"]["demo"]
                record["roles_history"] = [
                    {
                        "since": "2020-01-01T00:00:00Z",
                        "driver": "claude_desktop",
                        "helpers": ["claude_code"],
                    }
                ]

            mutate_projects(env.board, change)
            with self.assertRaises(CoopError) as refused:
                write_grade(
                    env.board,
                    "demo",
                    "cursor",
                    answer,
                    score=3,
                    outcome="partly_used",
                    note="The field still says cursor.",
                    now=when + timedelta(hours=1),
                )
            self.assertIn("Only the driver", str(refused.exception))
            self.assertIsNone(find_grade(env.board, "demo", answer))

    def test_totals_use_the_last_20_per_tag_and_across_projects(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            other = env.workspace / "other"
            other.mkdir()
            _quiet(
                [
                    "project",
                    "add",
                    str(other),
                    "--name",
                    "other",
                    "--driver",
                    "cursor",
                    "--helper",
                    "claude_code=edits",
                    "--gitignore",
                    "no",
                    "--yes",
                ]
            )
            folder = project_dir(env.board, "demo") / "grades" / "claude_code"
            folder.mkdir(parents=True)
            for index in range(25):
                score = 1 if index < 5 else 5
                tag = "reasoning" if index % 2 == 0 else "review"
                payload = {
                    "v": 1,
                    "answer": "m" + str(index),
                    "helper": "claude_code",
                    "project": "demo",
                    "by": "cursor",
                    "ts": "2026-10-25T12:{:02d}:00Z".format(index),
                    "score": score,
                    "outcome": "used",
                    "tags": [tag],
                    "note": "n" + str(index),
                    "reply_minutes": index + 1,
                    "progress_updates": 0,
                    "late": index == 24,
                    "declined": index == 23,
                    "second_opinion_group": "",
                }
                (folder / ("20261025T12{:02d}00Z_m{}.json".format(index, index))).write_text(
                    json.dumps(payload),
                    encoding="utf-8",
                )
            summary = summarize_project(env.board, "demo", "claude_code")
            self.assertEqual(summary["total"], 25)
            self.assertEqual(summary["count"], 20)
            self.assertEqual(summary["average"], 5.0)
            self.assertEqual(summary["per_tag"]["reasoning"], 5.0)
            self.assertEqual(summary["late"], 1)
            self.assertEqual(summary["declined"], 1)
            self.assertEqual(display_summary(summary), "5.0 (20). replies in " + str(summary["median_reply"]) + " min")
            other_folder = project_dir(env.board, "other") / "grades" / "claude_code"
            other_folder.mkdir(parents=True)
            for index in range(15):
                payload = {
                    "v": 1,
                    "answer": "o" + str(index),
                    "helper": "claude_code",
                    "score": 1,
                    "ts": "2026-10-26T12:{:02d}:00Z".format(index),
                    "outcome": "used",
                    "tags": [],
                    "note": "later",
                    "reply_minutes": 1,
                    "late": False,
                    "declined": False,
                }
                (other_folder / ("f" + str(index) + ".json")).write_text(json.dumps(payload), encoding="utf-8")
            across = summarize_agent(env.board, "claude_code")
            self.assertEqual(across["count"], 20)
            self.assertEqual(across["average"], 2.0)

    def test_a_helper_sees_only_its_own_grades_and_removal_moves_the_file(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            when = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)
            first = _answer(env, "claude_code", when, "code answer")
            second = _answer(env, "claude_desktop", when + timedelta(minutes=1), "desktop answer")
            write_grade(
                env.board,
                "demo",
                "cursor",
                first,
                score=5,
                outcome="used",
                note="Code found it.",
                now=when + timedelta(hours=1),
            )
            write_grade(
                env.board,
                "demo",
                "cursor",
                second,
                score=2,
                outcome="not_used",
                note="Desktop missed it.",
                now=when + timedelta(hours=1, minutes=1),
            )
            own = render_check(check_inbox(env.board, "demo", "claude_code"))
            self.assertIn("Code found it.", own)
            self.assertNotIn("Desktop missed it.", own)
            self.assertIn("Your grades: new", own)
            write_new_md(env.board, "demo", "claude_desktop")
            page = (project_dir(env.board, "demo") / "inbox" / "claude_desktop" / "NEW.md").read_text(encoding="utf-8")
            self.assertIn("Desktop missed it.", page)
            self.assertNotIn("Code found it.", page)
            driver = render_check(check_inbox(env.board, "demo", "cursor"))
            self.assertIn("claude_code: offline. new.", driver)
            self.assertIn("claude_desktop: offline. new.", driver)
            status = render_status(env.board)
            self.assertIn("claude_code grade: new", status)
            with patch("agentcoop.confirm.confirm_person", side_effect=CoopError("Cancelled.", code=1)):
                code = main(["grade", "remove", first, "--as", "cursor", "--project", "demo"])
            self.assertNotEqual(code, 0)
            self.assertIsNotNone(find_grade(env.board, "demo", first))
            with support.person(first):
                text = _quiet(["grade", "remove", first, "--as", "cursor", "--project", "demo"])
            self.assertIn("removed", text)
            self.assertIsNone(find_grade(env.board, "demo", first))
            moved = list((project_dir(env.board, "demo") / "grades" / "removed" / "claude_code").glob("*.json"))
            self.assertEqual(len(moved), 1)
            self.assertTrue(moved[0].is_file())

    def test_three_grades_show_a_number(self) -> None:
        grades = [
            {"score": 4, "reply_minutes": 6, "tags": ["reasoning"], "late": False, "declined": False, "ts": "1"},
            {"score": 5, "reply_minutes": 6, "tags": ["reasoning"], "late": False, "declined": False, "ts": "2"},
            {"score": 3, "reply_minutes": 6, "tags": ["review"], "late": False, "declined": True, "ts": "3"},
        ]
        summary = summarize(grades)
        self.assertEqual(summary["average"], 4.0)
        self.assertEqual(summary["per_tag"]["reasoning"], 4.5)
        self.assertEqual(summary["declined"], 1)
        self.assertEqual(display_summary(summary), "4.0 (3). replies in 6 min")
        self.assertEqual(display_summary(summarize(grades[:2])), "new")
