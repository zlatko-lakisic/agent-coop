"""Shared files: copy, record, safety, size, retention, and the viewer."""

from __future__ import annotations

import io
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import support

from agentcoop.cli import main
from agentcoop.core import archive, check_inbox, load_message, mark_done, render_check, send
from agentcoop.errors import CoopError
from agentcoop.outbox import process_outbox
from agentcoop.paths import inbox_msgs, outbox_dir, outbox_rejected_dir
from agentcoop.projects import (
    add_project,
    connect_agent_record,
    init_board_files,
    load_config,
    save_config,
)
from agentcoop.share import clean_filename, place_file, prune, share_root, sniff_media
from agentcoop.statusfile import render_status
from agentcoop.ui import make_server

PNG = b"\x89PNG\r\n\x1a\n" + b"png-bytes"


def _ready(env: support.TempEnv) -> Path:
    init_board_files(env.board)
    connect_agent_record(env.board, "cursor", "cursor", display="Cursor")
    connect_agent_record(env.board, "claude_code", "claude-code", display="Claude Code")
    connect_agent_record(env.board, "claude_desktop", "claude-desktop", display="Claude desktop")
    folder = env.workspace / "frontend"
    folder.mkdir()
    add_project(
        env.board,
        "frontend",
        folder,
        "cursor",
        {"claude_code": "reviews code", "claude_desktop": "looks at the screen"},
    )
    return folder


def _limits(env: support.TempEnv, **values: int) -> None:
    config = load_config(env.board)
    config.update(values)
    save_config(env.board, config)


def _send(env: support.TempEnv, path: Path, **kwargs) -> str:
    return send(
        env.board,
        project="frontend",
        sender="cursor",
        recipient="claude_code",
        subject="A file",
        body="Please look.",
        attachments=[str(path)],
        **kwargs,
    )


class ShareCopyTests(unittest.TestCase):
    def test_copy_record_temp_name_and_older_reader(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            source = folder / "my shot.png"
            source.write_bytes(PNG)
            replaced: list[tuple[str, str]] = []
            real_replace = os.replace

            def spy(src, dst):
                replaced.append((str(src), str(dst)))
                return real_replace(src, dst)

            with patch("agentcoop.share.os.replace", side_effect=spy):
                mid = _send(
                    env,
                    source,
                    describes=["battle"],
                    kinds=["screenshot"],
                )
            message = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (mid + ".json"))
            self.assertIsNotNone(message)
            assert message is not None
            shared = Path(message.attachments[0])
            self.assertTrue(shared.is_file())
            self.assertEqual(shared.read_bytes(), PNG)
            self.assertNotEqual(shared.resolve(), source.resolve())
            self.assertFalse(any(path.name.endswith(".partial") for path in shared.parent.iterdir()))
            self.assertTrue(any(src.endswith(".partial") for src, _dst in replaced))
            info = message.attachment_info[0]
            self.assertEqual(info["name"], "my shot.png")
            self.assertEqual(info["kind"], "screenshot")
            self.assertEqual(info["description"], "battle")
            self.assertEqual(info["media_type"], "image/png")
            self.assertEqual(info["bytes"], len(PNG))
            self.assertEqual(len(info["sha256"]), 64)
            self.assertEqual(info["source_path"], str(source.resolve()))
            raw = json.loads((inbox_msgs(env.board, "frontend", "claude_code") / (mid + ".json")).read_text(encoding="utf-8"))
            self.assertIsInstance(raw["attachments"][0], str)
            self.assertTrue(Path(raw["attachments"][0]).is_file())
            older = dict(raw)
            older.pop("attachment_info")
            self.assertTrue(Path(older["attachments"][0]).is_file())

    def test_size_change_is_refused(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            source = folder / "grow.txt"
            source.write_text("hello", encoding="utf-8")
            real_stat = os.stat

            def fake(path, *args, **kwargs):
                result = real_stat(path, *args, **kwargs)
                if Path(path).name == "grow.txt":
                    fake.calls += 1
                    if fake.calls >= 3:
                        return os.stat_result(
                            (
                                result.st_mode,
                                result.st_ino,
                                result.st_dev,
                                result.st_nlink,
                                result.st_uid,
                                result.st_gid,
                                result.st_size + 3,
                                result.st_atime,
                                result.st_mtime,
                                result.st_ctime,
                            )
                        )
                return result

            fake.calls = 0
            with patch("agentcoop.share.os.stat", side_effect=fake):
                with self.assertRaises(CoopError) as caught:
                    _send(env, source)
            self.assertIn("changed while it was being copied", str(caught.exception))
            self.assertFalse(any(path.suffix == ".partial" or ".partial" in path.name for path in share_root(env.board, "frontend").rglob("*")))

    def test_same_hash_is_not_copied_twice(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            source = folder / "notes.txt"
            source.write_text("same bytes\n", encoding="utf-8")
            first = _send(env, source)
            second = _send(env, source)
            one = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (first + ".json"))
            two = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (second + ".json"))
            assert one is not None and two is not None
            self.assertEqual(one.attachments[0], two.attachments[0])
            copies = list(share_root(env.board, "frontend").rglob("notes.txt"))
            self.assertEqual(len(copies), 1)

    def test_names(self) -> None:
        self.assertEqual(clean_filename("CON"), "CON_")
        self.assertEqual(clean_filename("con.txt"), "con.txt_")
        self.assertNotIn(":", clean_filename("a:b.txt"))
        self.assertNotIn("<", clean_filename("a<b>.txt"))
        self.assertEqual(clean_filename("café shot.png"), "café shot.png")
        self.assertEqual(clean_filename(r"C:\temp\my file.txt"), "my file.txt")
        with support.TempEnv() as env:
            folder = _ready(env)
            first = folder / "notes.txt"
            second = folder / "more" / "notes.txt"
            second.parent.mkdir()
            first.write_text("one", encoding="utf-8")
            second.write_text("two", encoding="utf-8")
            send(
                env.board,
                project="frontend",
                sender="cursor",
                recipient="claude_code",
                subject="Two",
                body="Both.",
                attachments=[str(first), str(second)],
            )
            names = sorted(
                path.name
                for path in share_root(env.board, "frontend").rglob("*")
                if path.is_file() and path.suffix == ".txt" and "note" in path.name
            )
            self.assertEqual(names, ["notes.2.txt", "notes.txt"])

    def test_media_type_follows_the_bytes(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            source = folder / "notes.txt"
            source.write_bytes(PNG)
            self.assertEqual(sniff_media(source), "image/png")
            mid = _send(env, source)
            message = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (mid + ".json"))
            assert message is not None
            self.assertEqual(message.attachment_info[0]["media_type"], "image/png")


class ShareDesktopTests(unittest.TestCase):
    def test_files_folder_and_rejected_file(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            mid = send(
                env.board,
                project="frontend",
                sender="cursor",
                recipient="claude_desktop",
                subject="Need help",
                body="Look.",
                needs_reply=True,
            )
            answer = outbox_dir(env.board, "frontend", "claude_desktop") / (mid + ".1.md")
            answer.parent.mkdir(parents=True, exist_ok=True)
            answer.write_text("Here is the picture.\n", encoding="utf-8")
            files = answer.parent / (mid + ".1.files")
            files.mkdir()
            (files / "shot.png").write_bytes(PNG)
            (files / "credentials.json").write_text("{}\n", encoding="utf-8")
            payload = '{"shot.png": {"description": "battle", "kind": "screenshot"}}\n'
            (files / "files.json").write_bytes(b"\xef\xbb\xbf" + payload.replace("\n", "\r\n").encode("utf-8"))
            when = time.time() - 10
            os.utime(answer, (when, when))
            process_outbox(env.board, "frontend", "claude_desktop")
            replies = []
            inbox = inbox_msgs(env.board, "frontend", "cursor")
            for path in inbox.iterdir():
                message = load_message(path)
                if message is not None and message.sender == "claude_desktop":
                    replies.append(message)
            self.assertEqual(len(replies), 1)
            self.assertIn("battle", replies[0].attachment_info[0]["description"])
            self.assertEqual(replies[0].attachment_info[0]["kind"], "screenshot")
            self.assertIn("held back", replies[0].body)
            reasons = list(outbox_rejected_dir(env.board, "frontend", "claude_desktop").glob("*.reason.txt"))
            self.assertTrue(reasons)
            self.assertTrue(reasons[0].read_text(encoding="utf-8").strip())
            self.assertTrue(folder)


class ShareAcceptTests(unittest.TestCase):
    def test_mismatch_does_not_block_and_alts_group(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            self.assertEqual(
                main(["accepts", "--as", "claude_code", "--project", "frontend", "image/*"]),
                0,
            )
            image = folder / "shot.png"
            image.write_bytes(PNG)
            note = folder / "notes.md"
            note.write_text("words\n", encoding="utf-8")
            image_id = _send(env, image, kinds=["image"])
            image_message = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (image_id + ".json"))
            assert image_message is not None
            digest = image_message.attachment_info[0]["sha256"]
            text_id = send(
                env.board,
                project="frontend",
                sender="cursor",
                recipient="claude_code",
                subject="Forms",
                body="A transcript.",
                attachments=[str(note)],
                alt_ofs=[digest],
            )
            text_message = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (text_id + ".json"))
            assert text_message is not None
            self.assertIn("does not list text/*", text_message.attachment_info[0]["mismatch"])
            self.assertTrue(Path(text_message.attachments[0]).is_file())
            report = check_inbox(env.board, "frontend", "claude_code")
            text = render_check(report)
            self.assertIn("does not list text/*", text)
            self.assertIn("other form:", text)
            err = io.StringIO()
            with support.person("review"):
                pass
            self.assertEqual(err.getvalue(), "")


class ShareLimitTests(unittest.TestCase):
    def test_over_limit_and_hard_ceiling(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            _limits(env, share_max_mb=1, share_hard_max_mb=2)
            small = folder / "small.txt"
            small.write_text("ok\n", encoding="utf-8")
            self.assertTrue(_send(env, small))
            big = folder / "big.bin"
            with big.open("wb") as handle:
                handle.seek(1024 * 1024)
                handle.write(b"x")
            with self.assertRaises(CoopError) as refused:
                _send(env, big)
            self.assertIn("Do not split it", str(refused.exception))
            with patch("agentcoop.share.confirm_share", return_value="allow"):
                allowed = _send(env, big, over_limit=True)
            message = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (allowed + ".json"))
            assert message is not None
            self.assertEqual(message.attachment_info[0]["bytes"], big.stat().st_size)
            huge = folder / "huge.bin"
            with huge.open("wb") as handle:
                handle.seek(2 * 1024 * 1024)
                handle.write(b"x")
            with patch("agentcoop.share.confirm_share", return_value="allow") as asked:
                with self.assertRaises(CoopError) as too_big:
                    _send(env, huge, over_limit=True)
            self.assertIn("too big to share", str(too_big.exception))
            asked.assert_not_called()
            other = folder / "other.bin"
            with other.open("wb") as handle:
                handle.seek(1024 * 1024)
                handle.write(b"y")
            with patch("agentcoop.share.confirm_share", return_value="cancel"):
                with self.assertRaises(CoopError) as cancelled:
                    _send(env, other, over_limit=True)
            self.assertIn("was not allowed", str(cancelled.exception))
            with patch("agentcoop.share.confirm_share", return_value="timeout"):
                with self.assertRaises(CoopError) as timed:
                    _send(env, other, over_limit=True)
            self.assertIn("Nobody allowed", str(timed.exception))
            again = folder / "again.bin"
            with again.open("wb") as handle:
                handle.seek(1024 * 1024)
                handle.write(b"z")
            with patch("agentcoop.share.confirm_share", return_value="allow") as first:
                _send(env, again, over_limit=True)
            with patch("agentcoop.share.confirm_share", return_value="cancel") as second:
                _send(env, again, over_limit=True)
            first.assert_called()
            second.assert_not_called()


class ShareSafetyTests(unittest.TestCase):
    def test_programs_private_links_and_archives(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            script = folder / "run.bat"
            script.write_text("echo hi\n", encoding="utf-8")
            with patch("agentcoop.share.confirm_share", return_value="cancel"):
                with self.assertRaises(CoopError):
                    _send(env, script)
            elf = folder / "notes.txt"
            elf.write_bytes(b"\x7fELF" + b"\x00" * 8)
            with patch("agentcoop.share.confirm_share", return_value="allow") as asked:
                mid = _send(env, elf)
            asked.assert_called()
            message = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (mid + ".json"))
            assert message is not None
            self.assertIn("was held until it was allowed", message.body)
            private = folder / ".env"
            private.write_text("TOKEN=1\n", encoding="utf-8")
            with patch("agentcoop.share.confirm_share", return_value="allow") as asked:
                with self.assertRaises(CoopError) as blocked:
                    _send(env, private)
            asked.assert_not_called()
            self.assertIn("not allowed", str(blocked.exception))
            try:
                link = folder / "outside.txt"
                link.symlink_to(env.home / ".ssh")
            except OSError:
                link = None
            if link is not None:
                with self.assertRaises(CoopError):
                    _send(env, link)
            archive = folder / "pack.zip"
            archive.write_bytes(b"PK\x03\x04" + b"data")
            archived = _send(env, archive)
            stored = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (archived + ".json"))
            assert stored is not None
            self.assertEqual(stored.attachment_info[0]["contents"], "not checked")
            self.assertFalse((share_root(env.board, "frontend") / "unpacked").exists())


class ShareRetentionTests(unittest.TestCase):
    def test_prune_keeps_pinned_and_live_files(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            _limits(env, share_keep_days=30)
            live = folder / "live.txt"
            old = folder / "old.txt"
            pinned = folder / "pinned.txt"
            live.write_text("live\n", encoding="utf-8")
            old.write_text("old\n", encoding="utf-8")
            pinned.write_text("pin\n", encoding="utf-8")
            live_id = _send(env, live)
            old_id = _send(env, old)
            pin_id = _send(env, pinned)
            self.assertEqual(main(["files", "pin", "--as", "cursor", "--project", "frontend", pin_id and load_message(inbox_msgs(env.board, "frontend", "claude_code") / (pin_id + ".json")).attachment_info[0]["sha256"]]), 0)
            mark_done(env.board, "frontend", "claude_code", [old_id, pin_id])
            later = datetime.now(timezone.utc) + timedelta(days=40)
            archive(env.board, "frontend", "claude_code", older_than_days=0, now=later)
            self.assertEqual(prune(env.board, "frontend", now=later), [])
            removed = prune(env.board, "frontend", now=later + timedelta(days=31))
            self.assertTrue(any(path.endswith("old.txt") for path in removed))
            pin_message = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (pin_id + ".json"))
            if pin_message is None:
                from agentcoop.paths import archive_dir

                pin_message = load_message(archive_dir(env.board, "frontend", "claude_code") / "msgs" / (pin_id + ".json"))
            assert pin_message is not None
            self.assertTrue(Path(pin_message.attachments[0]).is_file())
            live_message = load_message(inbox_msgs(env.board, "frontend", "claude_code") / (live_id + ".json"))
            assert live_message is not None
            self.assertTrue(Path(live_message.attachments[0]).is_file())


class ShareViewTests(unittest.TestCase):
    def test_status_and_viewer_list_files(self) -> None:
        with support.TempEnv() as env:
            folder = _ready(env)
            source = folder / "shot.png"
            source.write_bytes(PNG)
            _send(env, source, kinds=["image"], describes=["battle"])
            status = render_status(env.board)
            self.assertIn("shot.png", status)
            self.assertIn("Shared files: up to", status)
            self.assertIn("image", status)
            server = make_server(env.board, "secret")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:" + str(port) + "/api/state?t=secret",
                    timeout=2,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                files = payload["projects"][0]["files"]
                self.assertEqual(files[0]["name"], "shot.png")
                with urllib.request.urlopen(
                    "http://127.0.0.1:" + str(port) + files[0]["url"],
                    timeout=2,
                ) as response:
                    self.assertEqual(response.read(), PNG)
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    urllib.request.urlopen(
                        "http://127.0.0.1:" + str(port) + "/files/frontend/index.json?t=secret",
                        timeout=2,
                    )
                missing.exception.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
