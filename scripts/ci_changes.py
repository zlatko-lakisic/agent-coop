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


def code_changed(paths: list[str]) -> bool:
    """Return True when any path is code."""
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
