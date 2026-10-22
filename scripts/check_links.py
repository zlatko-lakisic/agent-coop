"""Check that relative links and image paths in the docs point at real files."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ROOT_MARKDOWN = ("README.md", "SPEC.md", "CHANGELOG.md", "AGENTS-SUPPORT.md")
DOC_FOLDERS = ("docs", "examples")

FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`]+`")
MD_LINK_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)|\[[^\]]*\]\(([^)]+)\)")
HTML_ATTR_RE = re.compile(r"""(?:src|srcset|href)\s*=\s*["']([^"']+)["']""", re.I)

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "data:", "irc:")


def markdown_paths(root: Path) -> list[Path]:
    """Return the markdown files this check covers."""
    found: list[Path] = []
    for name in ROOT_MARKDOWN:
        path = root / name
        if path.is_file():
            found.append(path)
    for folder in DOC_FOLDERS:
        base = root / folder
        if base.is_dir():
            found.extend(sorted(p for p in base.rglob("*.md") if p.is_file()))
    return found


def _looks_external(ref: str) -> bool:
    lower = ref.lower()
    return lower.startswith(EXTERNAL_PREFIXES) or ref.startswith("#") or ref.startswith("//")


def _clean_ref(raw: str) -> str:
    text = raw.strip()
    if text.startswith("<") and ">" in text:
        text = text[1 : text.index(">")].strip()
    elif text:
        text = text.split()[0].strip().strip("\"'")
    if "#" in text:
        text = text.split("#", 1)[0]
    return text


def local_refs(text: str) -> list[str]:
    """Return relative link and image targets from markdown or HTML."""
    stripped = INLINE_CODE_RE.sub("", FENCE_RE.sub("", text))
    refs: list[str] = []
    for match in MD_LINK_RE.finditer(stripped):
        raw = match.group(1) or match.group(2) or ""
        refs.append(raw)
    for match in HTML_ATTR_RE.finditer(stripped):
        for part in match.group(1).split(","):
            refs.append(part.strip())
    out: list[str] = []
    for raw in refs:
        ref = _clean_ref(raw)
        if not ref or _looks_external(ref):
            continue
        out.append(ref)
    return out


def resolve_ref(source: Path, ref: str) -> Path:
    """Resolve a relative link against the file that contains it."""
    return (source.parent / ref).resolve()


def broken_links(root: Path) -> list[str]:
    """Return 'file: ref' for each relative target that is missing."""
    root = root.resolve()
    broken: list[str] = []
    for path in markdown_paths(root):
        text = path.read_text(encoding="utf-8")
        rel_source = path.relative_to(root).as_posix()
        for ref in local_refs(text):
            target = resolve_ref(path, ref)
            if not target.exists():
                broken.append(rel_source + ": " + ref)
    return broken


def main(argv: list[str] | None = None) -> int:
    """List every broken relative link. Exit 1 when any are found."""
    args = list(sys.argv[1:] if argv is None else argv)
    root = ROOT
    if args[:1] == ["--root"] and len(args) >= 2:
        root = Path(args[1])
    broken = broken_links(root)
    if not broken:
        return 0
    print("Broken relative links:", file=sys.stderr)
    for item in broken:
        print(item, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
