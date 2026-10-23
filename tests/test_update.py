"""Update, downgrade, the daily check, and watch exit 5. No test calls GitHub."""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.core import render_check
from agentcoop.errors import CoopError
from agentcoop.migrate import apply_migrations, migrate_1_to_2, migrate_2_to_3, read_format
from agentcoop.projects import add_project, connect_agent_record, init_board_files, load_config, mutate_config
from agentcoop.releases import RateLimitError, file_hash, headings_between, parse_sums, require_hash
from agentcoop.statusfile import render_status
from agentcoop.update import (
    _guard_downgrade,
    cleanup_old_binaries,
    forward_setup_argv,
    install_runtime,
    place_file,
    print_check,
    remember_previous,
    rollback,
    setup_at_version,
)
from agentcoop.updatecheck import header_line, notice_text, set_update_checks
from agentcoop.version import asset_name, parse_version, version_text
from agentcoop.watch import run_watch


def _ready(env: support.TempEnv) -> Path:
    init_board_files(env.board)
    connect_agent_record(env.board, "cursor", "cursor")
    connect_agent_record(env.board, "claude_code", "claude-code")
    folder = env.workspace / "demo"
    folder.mkdir()
    add_project(env.board, "demo", folder, "cursor", {"claude_code": "reviews"})
    bindir = env.board / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "VERSION").write_text("0.2.4\n", encoding="utf-8")
    program = bindir / "agentcoop"
    program.write_text("old\n", encoding="utf-8")
    return env.board


def _zipapp(path: Path) -> None:
    """Write a zipapp that exits 0. Windows cannot run a shell script."""
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("__main__.py", "raise SystemExit(0)\n")
    path.write_bytes(payload.getvalue())


class VersionTests(unittest.TestCase):
    def test_parse_and_assets(self) -> None:
        self.assertEqual(parse_version("v0.2.4"), (0, 2, 4))
        self.assertEqual(version_text((0, 2, 4)), "0.2.4")
        for bad in ("", "1.2", "01.2.3", "v1.2.3-rc1", "about half"):
            with self.assertRaises(CoopError):
                parse_version(bad)
        self.assertEqual(asset_name("zipapp", "linux", "x86_64"), "agentcoop.pyz")
        self.assertEqual(asset_name("binary", "windows", "amd64"), "agentcoop-windows-x64.zip")
        self.assertEqual(asset_name("binary", "darwin", "arm64"), "agentcoop-macos-arm64")
        self.assertEqual(asset_name("binary", "linux", "aarch64"), "agentcoop-linux-arm64")
        with self.assertRaises(CoopError):
            asset_name("binary", "windows", "arm64")


class HashTests(unittest.TestCase):
    def test_match_mismatch_and_missing(self) -> None:
        with support.TempEnv() as env:
            payload = env.workspace / "agentcoop.pyz"
            payload.write_bytes(b"program")
            digest = file_hash(payload)
            self.assertEqual(parse_sums(digest + "  agentcoop.pyz\n").get("agentcoop.pyz"), digest)
            self.assertEqual(parse_sums(digest + " *agentcoop.pyz\n").get("agentcoop.pyz"), digest)
            require_hash(payload, digest + "  agentcoop.pyz\n")
            with self.assertRaises(CoopError) as mismatch:
                require_hash(payload, "ab" * 32 + "  agentcoop.pyz\n")
            self.assertIn("does not match", str(mismatch.exception))
            with self.assertRaises(CoopError) as missing:
                require_hash(payload, "")
            self.assertIn("SHA256SUMS is missing", str(missing.exception))
            with self.assertRaises(CoopError) as unnamed:
                require_hash(payload, digest + "  other.bin\n")
            self.assertIn("no line", str(unnamed.exception))


class InstallTests(unittest.TestCase):
    def test_failure_after_place_restores_the_old_files(self) -> None:
        with support.TempEnv() as env:
            board = _ready(env)
            original = (board / "bin" / "agentcoop").read_text(encoding="utf-8")
            new_file = env.workspace / "new-agentcoop"
            new_file.write_text("new\n", encoding="utf-8")
            with self.assertRaises(CoopError) as caught:
                install_runtime(board, {"agentcoop": new_file}, "0.2.5", fail_after="placed", finish=False)
            self.assertIn("The old files were put back.", str(caught.exception))
            self.assertEqual((board / "bin" / "agentcoop").read_text(encoding="utf-8"), original)
            self.assertFalse((board / "bin" / "VERSION").read_text(encoding="utf-8").startswith("0.2.5"))

    def test_previous_keeps_the_last_two(self) -> None:
        with support.TempEnv() as env:
            board = _ready(env)
            for name in ("0.2.0", "0.2.1", "0.2.2"):
                (board / "bin" / "agentcoop").write_text(name + "\n", encoding="utf-8")
                remember_previous(board, name)
            root = board / "bin" / "previous"
            self.assertEqual((root / "last").read_text(encoding="utf-8").strip(), "0.2.2")
            self.assertTrue((root / "0.2.2").is_dir())
            self.assertTrue((root / "0.2.1").is_dir())
            self.assertFalse((root / "0.2.0").exists())

    def test_windows_rename_and_cleanup(self) -> None:
        with support.TempEnv() as env:
            board = _ready(env)
            dest = board / "bin" / "agentcoop.exe"
            dest.write_bytes(b"running")
            src = env.workspace / "agentcoop.exe"
            src.write_bytes(b"next")
            place_file(src, dest, windows=True)
            self.assertEqual(dest.read_bytes(), b"next")
            self.assertEqual((board / "bin" / "agentcoop.exe.old").read_bytes(), b"running")
            cleanup_old_binaries(board)
            self.assertFalse((board / "bin" / "agentcoop.exe.old").exists())


class DowngradeTests(unittest.TestCase):
    def test_older_version_asks_and_warns(self) -> None:
        with support.TempEnv() as env:
            board = _ready(env)
            out = io.StringIO()
            with support.person("0.2.3"), redirect_stdout(out):
                _guard_downgrade(board, (0, 2, 3), (0, 2, 4))
            text = out.getvalue()
            self.assertIn("0.2.3 has no update command.", text)
            self.assertIn("Stop the co-op no longer stops agents.", text)
            with support.person("no"):
                with self.assertRaises(CoopError):
                    _guard_downgrade(board, (0, 2, 3), (0, 2, 4))

    def test_below_min_version_is_refused(self) -> None:
        with support.TempEnv() as env:
            board = _ready(env)

            def change(config: dict) -> None:
                config["min_version"] = "0.2.4"

            mutate_config(board, change)
            with self.assertRaises(CoopError) as caught:
                _guard_downgrade(board, (0, 2, 0), (0, 2, 4))
            self.assertIn("0.2.4 or newer", str(caught.exception))

    def test_rollback_confirms_and_runs_the_old_program(self) -> None:
        with support.TempEnv() as env:
            board = _ready(env)
            folder = board / "bin" / "previous" / "0.2.3"
            folder.mkdir(parents=True)
            _zipapp(folder / "agentcoop.pyz")
            (board / "bin" / "previous" / "last").write_text("0.2.3\n", encoding="utf-8")
            (board / "bin" / "previous" / "order.txt").write_text("0.2.3\n", encoding="utf-8")
            out = io.StringIO()
            with support.person("0.2.3"), redirect_stdout(out):
                code = rollback(board)
            self.assertEqual(code, 0)
            text = out.getvalue()
            self.assertIn("0.2.3 has no update command.", text)
            self.assertIn("Updated Agent Co-op from 0.2.4 to 0.2.3.", text)


class MigrationTests(unittest.TestCase):
    def test_steps_are_idempotent_and_backup_first(self) -> None:
        with support.TempEnv() as env:
            board = _ready(env)
            config = board / "config.json"
            data = json.loads(config.read_text(encoding="utf-8"))
            for key in ("board_format", "min_version", "update_check"):
                data.pop(key, None)
            config.write_text(json.dumps(data), encoding="utf-8")
            apply_migrations(board)
            restored = load_config(board)
            self.assertEqual(restored["board_format"], 1)
            self.assertEqual(restored["min_version"], "0.2.0")
            self.assertIs(restored["update_check"], True)
            backups = list((board / ".state" / "backups").rglob("config.json.*"))
            self.assertGreaterEqual(len(backups), 1)
            apply_migrations(board)
            again = list((board / ".state" / "backups").rglob("config.json.*"))
            self.assertEqual(len(again), len(backups))
            migrate_1_to_2(board)
            migrate_1_to_2(board)
            self.assertEqual(read_format(board), 2)
            migrate_2_to_3(board)
            migrate_2_to_3(board)
            self.assertEqual(read_format(board), 3)


class NoticeTests(unittest.TestCase):
    def test_watch_exits_5_when_the_version_changes(self) -> None:
        with support.TempEnv() as env:
            board = _ready(env)
            version = board / "bin" / "VERSION"
            out = io.StringIO()

            def pause(_seconds: float) -> None:
                version.write_text("0.2.5\n", encoding="utf-8")

            code = run_watch(
                board,
                "demo",
                "cursor",
                timeout_seconds=60,
                poll_seconds=1,
                heartbeat_seconds=3600,
                sleep=pause,
                out=out,
            )
            self.assertEqual(code, 5)
            self.assertIn(
                "Agent Co-op was updated to 0.2.5. Run agentcoop watch again.",
                out.getvalue(),
            )

    def test_status_and_check_show_the_newer_version_once(self) -> None:
        with support.TempEnv() as env:
            board = _ready(env)
            state = {
                "last_checked": "2026-10-22T00:00:00Z",
                "latest": "0.2.9",
                "notified_by": [],
            }
            (board / "update-check.json").write_text(json.dumps(state), encoding="utf-8")
            (board / ".state" / "desktop-skill.json").write_text(
                json.dumps({"version": "0.2.4"}),
                encoding="utf-8",
            )
            page = render_status(board)
            self.assertIn("Installed: 0.2.4", page)
            self.assertIn(notice_text("0.2.9", "0.2.4"), page)
            self.assertIn("Claude desktop skill changed", page)
            from agentcoop.core import check_inbox

            report = check_inbox(board, "demo", "cursor")
            first = render_check(report, summary=True)
            second = render_check(report, summary=True)
            self.assertIn(notice_text("0.2.9", "0.2.4"), first)
            self.assertNotIn("0.2.9", second)
            self.assertEqual(header_line(board, "cursor", record=True), "")


class CommandTests(unittest.TestCase):
    def test_setup_without_version_makes_no_network_call(self) -> None:
        def boom(*_args: object, **_kwargs: object) -> bytes:
            raise AssertionError("setup called the network")

        with support.TempEnv() as env:
            with patch("urllib.request.urlopen", side_effect=boom):
                code = main(
                    [
                        "setup",
                        "--connect",
                        "cursor",
                        "--schedule",
                        "no",
                        "--add-to-path",
                        "no",
                        "--yes",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue((env.board / "config.json").is_file())
            text = io.StringIO()
            with redirect_stdout(text):
                pass
            self.assertIn("update_check", load_config(env.board))

    def test_missing_version_lists_published_ones(self) -> None:
        def fetch(url: str, timeout: float = 30) -> bytes:
            del timeout
            if "/releases/tags/" in url:
                return b"{}"
            return json.dumps(
                [
                    {
                        "tag_name": "v0.2.4",
                        "prerelease": False,
                        "assets": {"agentcoop.pyz": "https://example.invalid/a"},
                    }
                ]
            ).encode("utf-8")

        with support.TempEnv():
            out = io.StringIO()
            args = type(
                "Args",
                (),
                {
                    "setup_version": "9.9.9",
                    "detect": False,
                    "json": False,
                    "connect": "",
                    "yes": True,
                    "model": "",
                    "add_to_path": "no",
                    "schedule": "no",
                    "every": 5,
                },
            )()
            with redirect_stdout(out):
                with self.assertRaises(CoopError) as caught:
                    setup_at_version(args, fetch=fetch)
            self.assertIn("9.9.9 is not a published version.", str(caught.exception))
            self.assertIn("Published versions:", out.getvalue())
            self.assertIn("0.2.4", out.getvalue())

    def test_check_on_rate_limit_still_prints_the_installed_version(self) -> None:
        def fetch(url: str, timeout: float = 30) -> bytes:
            del url, timeout
            raise RateLimitError()

        with support.TempEnv() as env:
            board = _ready(env)
            out = io.StringIO()
            with redirect_stdout(out):
                code = print_check(board, fetch=fetch)
            self.assertEqual(code, 0)
            self.assertIn("Installed: 0.2.4", out.getvalue())
            self.assertIn("API limit", out.getvalue())

    def test_notify_and_setup_flags(self) -> None:
        with support.TempEnv() as env:
            _ready(env)
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["update", "--notify", "off"])
            self.assertEqual(code, 0)
            self.assertIn("Update checks are off.", out.getvalue())
            self.assertIs(load_config(env.board)["update_check"], False)
            self.assertEqual(set_update_checks(env.board, True), "Update checks are on.")
            argv = forward_setup_argv(["agentcoop"], type("Args", (), {"schedule": "no", "every": 5, "add_to_path": "no", "yes": True, "connect": "", "detect": False, "json": False, "model": ""})())
            self.assertNotIn("--version", argv)
            self.assertIn("--schedule", argv)

    def test_changelog_headings_between_versions(self) -> None:
        text = "## 0.2.3 - 2026-10-22\n\n- One.\n\n## 0.2.4 - 2026-10-22\n\n- Two.\n"
        found = headings_between(text, (0, 2, 3), (0, 2, 4))
        self.assertEqual(found, ["## 0.2.4 - 2026-10-22"])


class DocsTests(unittest.TestCase):
    def test_the_three_pages_exist(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for name in ("docs/agent-setup.md", "docs/agent-update.md", "skills/agent-coop/SKILL.md"):
            self.assertTrue((root / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
