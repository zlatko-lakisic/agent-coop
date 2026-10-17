"""Project summary is printed once, and prompts show one default."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

import support

from agentcoop.cli import main
from agentcoop.wizard import Prompter, add_project_flow


class PromptTests(unittest.TestCase):
    def test_summary_once_with_flags(self) -> None:
        with support.TempEnv() as env:
            self.assertEqual(main(["setup", "--connect", "cursor", "--schedule", "no", "--yes"]), 0)
            folder = env.workspace / "demo"
            folder.mkdir()
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(
                    [
                        "project",
                        "add",
                        str(folder),
                        "--name",
                        "demo",
                        "--driver",
                        "cursor",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue().count("is ready"), 1)

    def test_yes_prompt_and_text_prompt(self) -> None:
        seen = []

        def read(prompt: str) -> str:
            seen.append(prompt)
            return ""

        prompter = Prompter(input_fn=read, output_fn=lambda _text: None)
        self.assertTrue(
            prompter.yes("Check the board in the background every 5 minutes? (Y/n) ", default=True)
        )
        self.assertEqual(seen[-1], "Check the board in the background every 5 minutes? (Y/n) ")
        self.assertNotIn("[yes]", seen[-1])
        self.assertEqual(prompter.line("Folder", "/Users/you/demo"), "/Users/you/demo")
        self.assertIn("[/Users/you/demo]", seen[-1])

    def test_interactive_project_add_says_the_summary_once(self) -> None:
        said = []
        with support.TempEnv() as env:
            main(["setup", "--connect", "cursor", "--schedule", "no", "--yes"])
            folder = env.workspace / "demo"
            folder.mkdir()

            def read(prompt: str) -> str:
                return ""

            prompter = Prompter(input_fn=read, output_fn=lambda text: said.append(text))
            add_project_flow(
                path=folder,
                name="demo",
                driver="cursor",
                helpers={},
                yes=True,
                prompter=prompter,
            )
        ready = [line for line in said if "is ready" in line]
        self.assertEqual(len(ready), 1)
