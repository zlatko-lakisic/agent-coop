"""Tests for local model request building. No real model is called."""

from __future__ import annotations

import json
import unittest

import support

from agentcoop.cli import main
from agentcoop.core import check_inbox
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


if __name__ == "__main__":
    unittest.main()
