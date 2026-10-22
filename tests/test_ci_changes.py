"""Tests for scripts/ci_changes.py."""

from __future__ import annotations

import sys
import unittest

import support

sys.path.insert(0, str(support.ROOT / "scripts"))

import ci_changes  # noqa: E402


class CiChangesTests(unittest.TestCase):
    def test_source_paths_are_code(self) -> None:
        for path in (
            "src/agentcoop/cli.py",
            "tests/x.py",
            "scripts/build_pyz.py",
            ".github/workflows/test.yml",
            "requirements-release.txt",
        ):
            with self.subTest(path=path):
                self.assertTrue(ci_changes.code_changed([path]))
                self.assertEqual(ci_changes.output_line([path]), "code=true")

    def test_doc_and_editor_paths_are_not_code(self) -> None:
        for path in (
            "README.md",
            "docs/schedule.md",
            "assets/roles.jpeg",
            "examples/vision-review/README.md",
            "LICENSE",
            ".gitignore",
            "my.code-workspace",
        ):
            with self.subTest(path=path):
                self.assertFalse(ci_changes.code_changed([path]))
                self.assertEqual(ci_changes.output_line([path]), "code=false")

    def test_mix_of_doc_and_source_is_code(self) -> None:
        paths = ["README.md", "src/agentcoop/cli.py"]
        self.assertTrue(ci_changes.code_changed(paths))
        self.assertEqual(ci_changes.output_line(paths), "code=true")

    def test_empty_list_is_not_code(self) -> None:
        self.assertFalse(ci_changes.code_changed([]))
        self.assertEqual(ci_changes.output_line([]), "code=false")


if __name__ == "__main__":
    unittest.main()
