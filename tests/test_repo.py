"""Repo checks: punctuation, and the generated support page."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import support

ROOT = support.ROOT
SKIP = {".git", "__pycache__", "dist", "build", ".venv", "node_modules"}
BAD = ("\u2014", "\u2013")


class RepoTests(unittest.TestCase):
    def test_no_em_or_en_dash(self) -> None:
        offenders = []
        for path in ROOT.rglob("*"):
            if any(part in SKIP for part in path.parts):
                continue
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for mark in BAD:
                if mark in text:
                    offenders.append(str(path.relative_to(ROOT)))
                    break
        self.assertEqual(offenders, [])

    def test_update_docs_exist(self) -> None:
        for name in (
            "docs/agent-setup.md",
            "docs/agent-update.md",
            "skills/agent-coop/SKILL.md",
        ):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_agents_support_is_current(self) -> None:
        script = ROOT / "scripts" / "gen_agents_support.py"
        result = subprocess.run([sys.executable, str(script), "--check"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
