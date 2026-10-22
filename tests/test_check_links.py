"""Tests for scripts/check_links.py."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import support

sys.path.insert(0, str(support.ROOT / "scripts"))

import check_links  # noqa: E402


class CheckLinksTests(unittest.TestCase):
    def test_good_link_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.md").write_text("ok\n", encoding="utf-8")
            (root / "README.md").write_text("[ok](target.md)\n", encoding="utf-8")
            self.assertEqual(check_links.broken_links(root), [])
            self.assertEqual(check_links.main(["--root", str(root)]), 0)

    def test_missing_file_fails_and_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("[gone](missing.md)\n", encoding="utf-8")
            broken = check_links.broken_links(root)
            self.assertTrue(any("missing.md" in item for item in broken))
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(check_links.main(["--root", str(root)]), 1)
            self.assertIn("missing.md", err.getvalue())

    def test_external_https_link_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("[site](https://example.com/nope)\n", encoding="utf-8")
            self.assertEqual(check_links.broken_links(root), [])
            self.assertEqual(check_links.main(["--root", str(root)]), 0)


if __name__ == "__main__":
    unittest.main()
