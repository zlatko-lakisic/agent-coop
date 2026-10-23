"""Say whether a list of paths needs the full CI test matrix."""

from __future__ import annotations

import sys

CODE_FILES = frozenset(
    {
        "requirements-release.txt",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
    }
)
CODE_DIRS = (
    "src/",
    "tests/",
    "scripts/",
    ".github/workflows/",
)
CODE_DIR_NAMES = frozenset({"src", "tests", "scripts", ".github/workflows"})
# A wiki pull request may change these and still skip the code matrix.
WIKI_FILES = frozenset(
    {
        "scripts/gen_wiki.py",
        "scripts/check_wiki_links.py",
        "scripts/check_wiki_note.py",
        "tests/test_wiki_scripts.py",
        "src/agentcoop/data/phrases.json",
        ".github/workflows/wiki.yml",
        ".cursor/skills/wiki/SKILL.md",
    }
)
WIKI_PREFIXES = (".cursor/skills/wiki/",)
# These may ride along with a wiki pull request without starting the code matrix.
WIKI_ACCOMPANY = frozenset(
    {
        ".github/workflows/test.yml",
        "scripts/ci_changes.py",
        "tests/test_ci_changes.py",
    }
)


def normalize(path: str) -> str:
    """Return a repo-relative path with forward slashes."""
    text = path.replace("\\", "/").strip()
    if text.startswith("./"):
        text = text[2:]
    return text


def is_code_path(path: str) -> bool:
    """Return True when this path should start the full test matrix."""
    text = normalize(path)
    if not text:
        return False
    if text in CODE_FILES or text in CODE_DIR_NAMES:
        return True
    return any(text.startswith(prefix) for prefix in CODE_DIRS)


def is_wiki_path(path: str) -> bool:
    """Return True when this path belongs to the wiki generator."""
    text = normalize(path)
    if text in WIKI_FILES:
        return True
    return any(text.startswith(prefix) for prefix in WIKI_PREFIXES)


def wiki_only(paths: list[str]) -> bool:
    """Return True when this change is a wiki pull request.

    Wiki files, docs, and the test workflow note do not need the code matrix
    or a new binary. A program change in the same pull request still does.
    """
    saw_wiki = False
    for path in paths:
        if is_wiki_path(path):
            saw_wiki = True
            continue
        text = normalize(path)
        if text in WIKI_ACCOMPANY:
            continue
        if is_code_path(path):
            return False
    return saw_wiki


def code_changed(paths: list[str]) -> bool:
    """Return True when any path needs the full test matrix."""
    if wiki_only(paths):
        return False
    return any(is_code_path(path) for path in paths)


def output_line(paths: list[str]) -> str:
    """Return the GITHUB_OUTPUT line for these paths."""
    return "code=true" if code_changed(paths) else "code=false"


def main() -> int:
    """Read paths on stdin and print code=true or code=false."""
    paths = [line.strip() for line in sys.stdin if line.strip()]
    print(output_line(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
