"""Tests for the board lock and strict settings formats."""

from __future__ import annotations

import io
import json
import multiprocessing
import queue
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import support

from agentcoop.cli import main
from agentcoop.errors import CoopError
from agentcoop.projects import add_helper, add_project, connect_agent_record, init_board_files, load_projects


def _worker(board: str, project: str, agent: str, start, done, errors) -> None:
    for index in range(10):
        start.wait(30)
        try:
            add_helper(Path(board), project, agent, "round-" + str(index))
        except CoopError as exc:
            errors.put(exc.code)
        done.wait(30)


class LockTests(unittest.TestCase):
    def test_concurrent_helpers_are_all_kept(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            names = ["helper" + str(index) for index in range(5)]
            for name in names:
                connect_agent_record(env.board, name, "claude-code", display=name)
            folder = env.workspace / "demo"
            folder.mkdir()
            add_project(env.board, "demo", folder, "cursor", {})
            ctx = multiprocessing.get_context("spawn")
            errors = ctx.Queue()
            start = ctx.Barrier(5)
            done = ctx.Barrier(5)
            procs = [
                ctx.Process(
                    target=_worker,
                    args=(str(env.board), "demo", name, start, done, errors),
                )
                for name in names
            ]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(60)
                self.assertEqual(proc.exitcode, 0)
            codes = []
            while True:
                try:
                    codes.append(errors.get_nowait())
                except queue.Empty:
                    break
            self.assertEqual(codes, [])
            helpers = load_projects(env.board)["projects"]["demo"]["helpers"]
            for name in names:
                self.assertEqual(helpers.get(name), "round-9")

    def test_incomplete_projects_file_is_refused(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            path = env.board / "projects.json"
            path.write_text('{"version": 1}\n', encoding="utf-8")
            err = io.StringIO()
            with support.person("review"), redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(["project", "review", "--accept"])
            self.assertEqual(code, 1)
            self.assertIn("expected format", err.getvalue())
            folder = env.workspace / "demo"
            folder.mkdir()
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "project",
                        "add",
                        str(folder),
                        "--name",
                        "demo",
                        "--driver",
                        "cursor",
                        "--yes",
                    ]
                )
            self.assertEqual(code, 6)
            self.assertIn("expected format", err.getvalue())
            self.assertNotIn("KeyError", err.getvalue())

    def test_helpers_must_be_an_object(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            raw = {
                "version": 1,
                "projects": {
                    "demo": {
                        "path": str(env.workspace),
                        "driver": "cursor",
                        "helpers": ["claude_code"],
                        "created": "2026-10-17T00:00:00Z",
                        "installed": [],
                    }
                },
            }
            (env.board / "projects.json").write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(CoopError) as raised:
                load_projects(env.board)
            self.assertEqual(raised.exception.code, 6)
            self.assertIn("expected format", str(raised.exception))

    def test_summary_reports_damaged_settings(self) -> None:
        with support.TempEnv() as env:
            init_board_files(env.board)
            connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
            (env.board / "agents.json").write_text('{"version": 1}\n', encoding="utf-8")
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["check", "--as", "cursor", "--summary"])
            self.assertEqual(code, 0)
            self.assertEqual(err.getvalue(), "")
            self.assertIn("board settings are damaged", out.getvalue())
            self.assertEqual(out.getvalue().strip().count("\n"), 0)


if __name__ == "__main__":
    unittest.main()
