"""Tests for the compatibility table, the generator, and the pull request note."""

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


class TableTests(unittest.TestCase):
    def test_generated_page_matches_and_names_the_script(self) -> None:
        current = (ROOT / "COMPATIBILITY.md").read_text(encoding="utf-8")
        self.assertTrue(current.startswith("# Compatibility"))
        self.assertIn("src/agentcoop/data/compat.json", current)
        self.assertIn("Do not edit by hand", current)
        fresh = _run("gen_compat.py", ["--check"])
        self.assertEqual(fresh.returncode, 0, fresh.stderr)

    def test_check_fails_when_the_page_is_stale(self) -> None:
        page = ROOT / "COMPATIBILITY.md"
        original = page.read_text(encoding="utf-8")
        try:
            page.write_text(original + "\nextra\n", encoding="utf-8")
            stale = _run("gen_compat.py", ["--check"])
            self.assertEqual(stale.returncode, 1)
            self.assertIn("COMPATIBILITY.md", stale.stderr)
        finally:
            page.write_text(original, encoding="utf-8")

    def test_newest_row_matches_the_code(self) -> None:
        result = _run("check_compat.py", ["--code"])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_message_v_in_core_matches_the_table(self) -> None:
        core = (ROOT / "src" / "agentcoop" / "core.py").read_text(encoding="utf-8")
        self.assertIn('"v": 1', core)


class LossTests(unittest.TestCase):
    def test_downgrade_lists_versions_left_behind(self) -> None:
        sys.path.insert(0, str(ROOT / "src"))
        from agentcoop.compat import losses_on_downgrade

        losses = losses_on_downgrade("0.2.5", "0.2.3")
        versions = [item[0] for item in losses]
        self.assertEqual(versions, ["0.2.5", "0.2.4"])
        self.assertNotIn("0.2.3", versions)
        self.assertTrue(losses[1][1])


class NoteTests(unittest.TestCase):
    def _event(self, folder: Path, body: str) -> dict:
        path = folder / "event.json"
        path.write_text(json.dumps({"pull_request": {"body": body}}), encoding="utf-8")
        return {"GITHUB_EVENT_PATH": str(path)}

    def test_matching_file_without_a_table_edit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = _run(
                "check_compat.py",
                [],
                stdin="src/agentcoop/schema.py\n",
                env=self._event(Path(folder), "Summary only"),
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("schema.py", result.stderr)
            self.assertIn(".cursor/skills/compatibility/SKILL.md", result.stderr)

    def test_table_edit_passes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = _run(
                "check_compat.py",
                [],
                stdin="src/agentcoop/schema.py\nsrc/agentcoop/data/compat.json\n",
                env=self._event(Path(folder), ""),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_opt_out_passes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = _run(
                "check_compat.py",
                [],
                stdin="src/agentcoop/templates/SKILL.md.tmpl\n",
                env=self._event(Path(folder), "No compatibility change: comment only"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_readme_does_not_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = _run(
                "check_compat.py",
                [],
                stdin="README.md\n",
                env=self._event(Path(folder), ""),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
