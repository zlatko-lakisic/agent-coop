"""Build a fresh test board and run one scenario with a fake driver.

The fake driver follows the same agentcoop steps a Cursor driver would, without
starting Cursor. Real Cursor runs stay manual. See docs/debug/harness.md.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SIM = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))

from agentcoop.cli import main  # noqa: E402
from agentcoop.core import new_messages  # noqa: E402
from agentcoop.cursorloop import KEEP_AWAKE, TICK_PREFIX, cursor_stop_followup, run_cursor_loop  # noqa: E402
from agentcoop.progress import progress_dir  # noqa: E402
from helper import load_scenario, run_timeline  # noqa: E402


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return main(argv)


def build_board(board: Path, project_dir: Path) -> None:
    """Create a cursor driver and a claude desktop helper on a fresh board."""
    import os

    os.environ["AGENTCOOP_BOARD"] = str(board)
    os.environ["AGENTCOOP_TEST_OFFLINE"] = "1"
    os.environ["HOME"] = str(board.parent / "home")
    os.environ["USERPROFILE"] = os.environ["HOME"]
    Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)
    _quiet(["setup", "--connect", "cursor,claude-desktop", "--schedule", "no", "--yes"])
    code = _quiet(
        [
            "project",
            "add",
            str(project_dir),
            "--name",
            "demo",
            "--driver",
            "cursor",
            "--helper",
            "claude_desktop=helps the driver reason",
            "--gitignore",
            "no",
            "--yes",
        ]
    )
    if code != 0:
        raise SystemExit("Could not add the test project.")
    _quiet(["start", "--every", "1"])


def play(board: Path, scenario: dict, re_id: str) -> list[str]:
    """Write the helper files and return their names."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    origin = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    paths = run_timeline(
        board,
        "demo",
        "claude_desktop",
        list(scenario.get("events") or []),
        re_id,
        origin,
    )
    return [path.name for path in paths]


def fake_driver(board: Path, scenario_id: str) -> dict:
    """Run the agentcoop side of one scenario and return a timeline report."""
    report: dict = {"scenario": scenario_id, "driver": "fake", "events": []}
    if scenario_id == "s13":
        from agentcoop.watch import write_heartbeat

        activity = board / ".state" / "activity" / "cursor.json"
        if activity.is_file():
            activity.unlink()
        clock = _Clock()
        start = clock.now

        def nap(seconds: float) -> None:
            clock.advance(seconds)
            if clock.now >= start + timedelta(seconds=2):
                write_heartbeat(
                    board,
                    "demo",
                    "cursor",
                    since=clock.now,
                    seen=clock.now,
                    how="cursor-loop",
                )

        silent = cursor_stop_followup(
            board,
            "cursor",
            json.dumps(
                {
                    "status": "completed",
                    "generation_id": "gen-1",
                    "conversation_id": "conv-1",
                    "loop_count": 0,
                }
            ),
            now=clock,
            sleep=nap,
            timeout_seconds=30,
            reserve_seconds=5,
        )
        if activity.is_file():
            activity.unlink()
        idle_clock = _Clock()
        idle = cursor_stop_followup(
            board,
            "cursor",
            "{}",
            now=idle_clock,
            sleep=lambda seconds: idle_clock.advance(seconds),
            timeout_seconds=6,
            reserve_seconds=5,
        )
        report["silent"] = silent == {}
        report["keep_awake"] = idle.get("followup_message") == KEEP_AWAKE
        report["events"].append({"kind": "stale-hook", "silent": report["silent"]})
        return report
    if scenario_id == "s12":
        folder = progress_dir(board, "demo")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "20261025T120000Z.md").write_text("Now: Run the game script.\n", encoding="utf-8")
        clock = _Clock()
        out = io.StringIO()
        code = run_cursor_loop(
            board,
            "demo",
            "cursor",
            now=clock,
            sleep=lambda _seconds: clock.advance(2),
            out=out,
            max_passes=1,
        )
        report["exit"] = code
        report["ticked"] = "continue your current task" in out.getvalue()
        report["events"].append({"kind": "cursor-loop", "text": out.getvalue().strip()})
        return report
    if scenario_id == "s10":
        version = board / "bin" / "VERSION"
        version.parent.mkdir(parents=True, exist_ok=True)
        version.write_text("0.2.7\n", encoding="utf-8")
        clock = _Clock()
        out = io.StringIO()

        def bump(_seconds: float) -> None:
            version.write_text("0.2.8\n", encoding="utf-8")
            clock.advance(2)

        code = run_cursor_loop(
            board,
            "demo",
            "cursor",
            now=clock,
            sleep=bump,
            out=out,
            max_passes=3,
        )
        report["exit"] = code
        report["events"].append({"kind": "cursor-loop", "text": out.getvalue().strip()})
        return report
    asked = _quiet(
        [
            "send",
            "--as",
            "cursor",
            "--project",
            "demo",
            "--to",
            "claude_desktop",
            "--type",
            "question",
            "--needs-reply",
            "--subject",
            "Review the run",
            "--body",
            "What should the driver do next?",
        ]
    )
    report["events"].append({"kind": "send", "exit": asked})
    from agentcoop.core import file_id

    messages, _issues = new_messages(board, "demo", "claude_desktop")
    re_id = file_id(messages[-1]) if messages else "missing"
    scenario_path = Path(__file__).resolve().parent / "scenarios" / (scenario_id + ".json")
    scenario = load_scenario(scenario_path) if scenario_path.is_file() else {"events": []}
    names = play(board, scenario, re_id)
    report["events"].append({"kind": "helper-files", "names": names})
    future = (datetime.now(timezone.utc) + timedelta(seconds=30)).timestamp()
    from agentcoop.core import check_inbox

    check_inbox(board, "demo", "cursor", now=future)
    delivered, _issues = new_messages(board, "demo", "cursor")
    report["delivered"] = [item.body.splitlines()[0] if item.body else "" for item in delivered]
    report["events"].append({"kind": "check", "count": len(delivered)})
    if scenario_id == "s4":
        text = _quiet_text(["check", "--as", "cursor", "--project", "demo"])
        report["standing"] = "Standing approvals:" in text
        report["events"].append({"kind": "check-text", "has_approval": report["standing"]})
    return report


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _quiet_text(argv: list[str]) -> str:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        main(argv)
    return out.getvalue()


def main_harness(argv: list[str] | None = None) -> int:
    """Run one fast scenario and write a timeline JSON file."""
    parser = argparse.ArgumentParser(description="Run one Agent Co-op stop scenario.")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--driver", choices=("fake",), default="fake")
    args = parser.parse_args(argv)
    board = Path(args.board)
    project = Path(args.project)
    build_board(board, project)
    if args.scenario == "s4":
        _quiet(
            [
                "allow",
                "--as",
                "cursor",
                "--project",
                "demo",
                "--text",
                "restart the game and run the spy tests",
            ]
        )
    report = fake_driver(board, args.scenario)
    report["tick_prefix"] = TICK_PREFIX
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_harness())
