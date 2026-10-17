"""Tests for local model request building. No real model is called."""

from __future__ import annotations

import json
import os
import unittest

import support

from agentcoop.cli import main
from agentcoop.core import check_inbox, write_message_file
from agentcoop.paths import inbox_msgs
from agentcoop.runner import ollama_payload, openai_payload, run_runner


class _Response:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _Opener:
    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self.payload = payload or {}
        self.error = error
        self.requests = []

    def __call__(self, request, timeout: int = 120):
        self.requests.append(request)
        if self.error:
            raise self.error
        return _Response(self.payload)


def _prepare(env: support.TempEnv) -> tuple[str, str]:
    main(["setup", "--connect", "cursor", "--yes"])
    main(["connect", "ollama", "--model", "llava", "--as", "ollama_llava", "--yes"])
    folder = env.workspace / "frontend"
    folder.mkdir()
    image = folder / "screen.png"
    image.write_bytes(b"\x89PNG fake")
    note = folder / "mockup.txt"
    note.write_text("padding should be 16px", encoding="utf-8")
    main(
        [
            "project",
            "add",
            str(folder),
            "--name",
            "frontend",
            "--driver",
            "cursor",
            "--helper",
            "ollama_llava=checks screenshots",
            "--yes",
        ]
    )
    main(
        [
            "send",
            "--as",
            "cursor",
            "--project",
            "frontend",
            "--to",
            "ollama_llava",
            "--subject",
            "Compare",
            "--body",
            "Check the screenshot.",
            "--attach",
            str(image),
            "--attach",
            str(note),
        ]
    )
    return str(image), str(note)


class RunnerTests(unittest.TestCase):
    def test_ollama_and_openai_requests(self) -> None:
        images = [("image/png", b"\x89PNG fake")]
        ollama = ollama_payload("llava", "Subject: Compare\nFile mockup.txt:\npadding", images)
        self.assertEqual(ollama["model"], "llava")
        self.assertEqual(len(ollama["messages"][0]["images"]), 1)
        self.assertNotIn("data:", ollama["messages"][0]["images"][0])
        openai = openai_payload("llava", "hello", images)
        url = openai["messages"][0]["content"][1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))

    def test_success_marks_done_and_failure_does_not(self) -> None:
        with support.TempEnv() as env:
            _prepare(env)
            opener = _Opener({"message": {"content": "Padding on the card is off."}})
            code = run_runner(agent="ollama_llava", once=True, opener=opener, log=lambda *_a: None)
            self.assertEqual(code, 0)
            self.assertEqual(len(opener.requests), 1)
            sent = json.loads(opener.requests[0].data.decode("utf-8"))
            self.assertIn("padding should be 16px", sent["messages"][0]["content"])
            self.assertEqual(len(sent["messages"][0]["images"]), 1)
            self.assertEqual(check_inbox(env.board, "frontend", "ollama_llava").messages, [])
            driver = check_inbox(env.board, "frontend", "cursor")
            self.assertIn("Padding on the card is off.", driver.messages[0].message.body)

        with support.TempEnv() as env:
            _prepare(env)
            opener = _Opener(error=OSError("connection refused"))
            code = run_runner(agent="ollama_llava", once=True, opener=opener, log=lambda *_a: None)
            self.assertEqual(code, 1)
            self.assertEqual(len(check_inbox(env.board, "frontend", "ollama_llava").messages), 1)

    def test_openai_api_flag(self) -> None:
        with support.TempEnv() as env:
            _prepare(env)
            opener = _Opener({"choices": [{"message": {"content": "Looks aligned."}}]})
            code = run_runner(
                agent="ollama_llava",
                api="openai",
                host="http://localhost:1234",
                once=True,
                opener=opener,
                log=lambda *_a: None,
            )
            self.assertEqual(code, 0)
            sent = json.loads(opener.requests[0].data.decode("utf-8"))
            self.assertTrue(sent["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertIn("/v1/chat/completions", opener.requests[0].full_url)

    @unittest.skipIf(os.name == "nt", "named pipes are not available on Windows")
    def test_pipe_attachment_is_skipped_quickly(self) -> None:
        with support.TempEnv() as env:
            _prepare(env)
            folder = env.workspace / "frontend"
            pipe = folder / "pipe.txt"
            os.mkfifo(pipe)
            write_message_file(
                inbox_msgs(env.board, "frontend", "ollama_llava"),
                {
                    "v": 1,
                    "id": "20261017T000001Z_cursor_pipe",
                    "ts": "2026-10-17T00:00:01Z",
                    "project": "frontend",
                    "sender": "cursor",
                    "recipient": "ollama_llava",
                    "type": "question",
                    "re": None,
                    "subject": "Pipe",
                    "body": "Look at the pipe.",
                    "attachments": [str(pipe)],
                    "needs_reply": True,
                },
            )
            opener = _Opener({"message": {"content": "noted"}})
            import time

            started = time.time()
            code = run_runner(agent="ollama_llava", once=True, opener=opener, log=lambda *_a: None)
            self.assertLess(time.time() - started, 5)
            self.assertEqual(code, 0)
            prompts = [
                json.loads(request.data.decode("utf-8"))["messages"][0]["content"]
                for request in opener.requests
            ]
            self.assertTrue(any("Skipped" in prompt and "Pipe" in prompt for prompt in prompts))

    def test_huge_image_is_skipped_without_reading(self) -> None:
        with support.TempEnv() as env:
            _prepare(env)
            folder = env.workspace / "frontend"
            huge = folder / "huge.png"
            huge.write_bytes(b"")
            os.truncate(huge, 11 * 1024 * 1024)
            main(
                [
                    "send",
                    "--as",
                    "cursor",
                    "--project",
                    "frontend",
                    "--to",
                    "ollama_llava",
                    "--subject",
                    "Huge",
                    "--body",
                    "Too big.",
                    "--attach",
                    str(huge),
                ]
            )
            opener = _Opener({"message": {"content": "skipped the image"}})
            code = run_runner(agent="ollama_llava", once=True, opener=opener, log=lambda *_a: None)
            self.assertEqual(code, 0)
            prompts = [
                json.loads(request.data.decode("utf-8"))["messages"][0]
                for request in opener.requests
            ]
            huge_prompts = [item for item in prompts if "Huge" in item["content"]]
            self.assertEqual(len(huge_prompts), 1)
            self.assertIn("Skipped", huge_prompts[0]["content"])
            self.assertNotIn("images", huge_prompts[0])

    def test_secret_answer_is_not_sent(self) -> None:
        with support.TempEnv() as env:
            _prepare(env)
            key = "sk-abcdefghijklmnopqrstu"
            opener = _Opener({"message": {"content": "use " + key}})
            code = run_runner(agent="ollama_llava", once=True, opener=opener, log=lambda *_a: None)
            self.assertEqual(code, 0)
            self.assertEqual(check_inbox(env.board, "frontend", "ollama_llava").messages, [])
            driver = check_inbox(env.board, "frontend", "cursor")
            self.assertEqual(len(driver.messages), 1)
            notice = driver.messages[0].message
            self.assertEqual(notice.type, "status")
            self.assertTrue(notice.re)
            self.assertNotIn(key, notice.body)

    def test_status_message_does_not_call_the_model(self) -> None:
        with support.TempEnv() as env:
            _prepare(env)
            main(
                [
                    "send",
                    "--as",
                    "cursor",
                    "--project",
                    "frontend",
                    "--to",
                    "ollama_llava",
                    "--type",
                    "status",
                    "--subject",
                    "FYI",
                    "--body",
                    "No reply needed.",
                ]
            )
            opener = _Opener({"message": {"content": "should not run"}})
            # The prepared question is still in the inbox, so answer only that one first.
            run_runner(agent="ollama_llava", once=True, opener=opener, log=lambda *_a: None)
            calls_after_question = len(opener.requests)
            code = run_runner(agent="ollama_llava", once=True, opener=opener, log=lambda *_a: None)
            self.assertEqual(code, 0)
            self.assertEqual(len(opener.requests), calls_after_question)
            self.assertEqual(check_inbox(env.board, "frontend", "ollama_llava").messages, [])

    def test_model_failures_give_up_after_ten(self) -> None:
        with support.TempEnv() as env:
            _prepare(env)
            opener = _Opener(error=OSError("connection refused"))

            def stop_after_nine(_seconds: float) -> None:
                if len(opener.requests) >= 9:
                    raise KeyboardInterrupt

            code = run_runner(
                agent="ollama_llava",
                opener=opener,
                sleep_fn=stop_after_nine,
                log=lambda *_a: None,
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(opener.requests), 9)
            self.assertEqual(len(check_inbox(env.board, "frontend", "ollama_llava").messages), 1)

        with support.TempEnv() as env:
            _prepare(env)
            opener = _Opener(error=OSError("connection refused"))

            def stop_when_done(_seconds: float) -> None:
                if not check_inbox(env.board, "frontend", "ollama_llava").messages:
                    raise KeyboardInterrupt

            code = run_runner(
                agent="ollama_llava",
                opener=opener,
                sleep_fn=stop_when_done,
                log=lambda *_a: None,
            )
            self.assertEqual(code, 0)
            self.assertGreaterEqual(len(opener.requests), 10)
            self.assertEqual(check_inbox(env.board, "frontend", "ollama_llava").messages, [])
            driver = check_inbox(env.board, "frontend", "cursor")
            self.assertTrue(any(item.message.type == "status" for item in driver.messages))
            notice = [item.message for item in driver.messages if item.message.type == "status"][0]
            self.assertIn("10 tries", notice.body)


if __name__ == "__main__":
    unittest.main()
