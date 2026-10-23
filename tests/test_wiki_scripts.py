"""Tests for the wiki generator, the link check, and the pull request note."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support

ROOT = support.ROOT
SCRIPTS = ROOT / "scripts"


def _run(script: str, args: list[str], stdin: str = "", env: dict | None = None) -> subprocess.CompletedProcess:
    full = dict(**{key: value for key, value in __import__("os").environ.items()})
    if env:
        full.update(env)
    full["PYTHONPATH"] = str(ROOT / "src") + ":" + str(ROOT / "tests")
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=full,
    )


class PhraseTests(unittest.TestCase):
    def test_every_phrase_is_in_its_templates(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        import gen_wiki

        data = gen_wiki.load_phrases()
        missing = []
        for group in data["groups"]:
            for row in group["rows"]:
                needle = str(row["say"])
                for rel in gen_wiki.phrase_files(row):
                    text = (ROOT / rel).read_text(encoding="utf-8")
                    if needle not in text:
                        missing.append(rel + " missing " + needle)
                also = str(row.get("also") or "")
                if also:
                    for rel in gen_wiki.phrase_files(row, "also_in" if row.get("also_in") else "in"):
                        text = (ROOT / rel).read_text(encoding="utf-8")
                        if also not in text:
                            missing.append(rel + " missing " + also)
        self.assertEqual(missing, [])


class GeneratorTests(unittest.TestCase):
    def test_check_fails_when_a_page_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            wiki = Path(folder)
            wrote = _run("gen_wiki.py", ["--wiki", str(wiki)])
            self.assertEqual(wrote.returncode, 0, wrote.stderr)
            fresh = _run("gen_wiki.py", ["--check", "--wiki", str(wiki)])
            self.assertEqual(fresh.returncode, 0, fresh.stderr)
            page = wiki / "Chat-phrases.md"
            page.write_text(page.read_text(encoding="utf-8") + "\nextra\n", encoding="utf-8")
            stale = _run("gen_wiki.py", ["--check", "--wiki", str(wiki)])
            self.assertEqual(stale.returncode, 1)
            self.assertIn("Chat-phrases.md", stale.stderr)

    def test_generated_pages_name_the_script(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            wiki = Path(folder)
            self.assertEqual(_run("gen_wiki.py", ["--wiki", str(wiki)]).returncode, 0)
            for name in ("Chat-phrases.md", "Command-reference.md"):
                first = (wiki / name).read_text(encoding="utf-8").splitlines()[0]
                self.assertIn("scripts/gen_wiki.py", first)
                self.assertIn("Do not edit by hand", first)


class LinkTests(unittest.TestCase):
    def test_missing_page_and_missing_repo_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            wiki = Path(folder)
            (wiki / "_Sidebar.md").write_text("* [Home](Home)\n* [Missing](Missing)\n", encoding="utf-8")
            (wiki / "Home.md").write_text(
                "See [Missing](Missing) and "
                "[spec](https://github.com/zlatko-lakisic/agent-coop/blob/main/SPEC.md).\n",
                encoding="utf-8",
            )
            (wiki / "Gone.md").write_text(
                "[nope](https://github.com/zlatko-lakisic/agent-coop/blob/main/docs/no-such-file.md)\n",
                encoding="utf-8",
            )
            result = _run("check_wiki_links.py", [str(wiki)])
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("Missing", result.stderr)
            self.assertIn("docs/no-such-file.md", result.stderr)
            self.assertNotIn("SPEC.md", result.stderr)


class NoteTests(unittest.TestCase):
    def _event(self, folder: Path, body: str | None) -> dict:
        payload = {"pull_request": {"body": body}}
        path = folder / "event.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return {"GITHUB_EVENT_PATH": str(path)}

    def test_src_change_without_a_line_fails(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = _run(
                "check_wiki_note.py",
                [],
                stdin="src/agentcoop/cli.py\n",
                env=self._event(Path(folder), "Summary only"),
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("src/agentcoop/cli.py", result.stderr)
            self.assertIn(".cursor/skills/wiki/SKILL.md", result.stderr)

    def test_wiki_line_passes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = _run(
                "check_wiki_note.py",
                [],
                stdin="docs/agent-setup.md\n",
                env=self._event(Path(folder), "Hello\nWiki: Updating\n"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_opt_out_line_passes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = _run(
                "check_wiki_note.py",
                [],
                stdin="skills/agent-coop/SKILL.md\n",
                env=self._event(Path(folder), "No wiki change: comment only"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_readme_only_does_not_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = _run(
                "check_wiki_note.py",
                [],
                stdin="README.md\n",
                env=self._event(Path(folder), ""),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
