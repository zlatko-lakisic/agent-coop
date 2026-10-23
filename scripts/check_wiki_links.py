"""Check wiki page links, the sidebar, and links back to this repo."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOB = "https://github.com/zlatko-lakisic/agent-coop/blob/main/"
RAW = "https://raw.githubusercontent.com/zlatko-lakisic/agent-coop/main/"
FENCE_RE = re.compile(r"```.*?```", re.S)
MD_LINK_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)|\[[^\]]*\]\(([^)]+)\)")
HTML_ATTR_RE = re.compile(r"""(?:src|srcset|href)\s*=\s*["']([^"']+)["']""", re.I)
SIDEBAR_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _clean(raw: str) -> str:
    text = raw.strip()
    if text.startswith("<") and ">" in text:
        text = text[1 : text.index(">")].strip()
    text = text.split()[0].strip().strip("\"'") if text else ""
    if "#" in text:
        text = text.split("#", 1)[0]
    return text


def _refs(text: str) -> list[str]:
    stripped = FENCE_RE.sub("", text)
    found: list[str] = []
    for match in MD_LINK_RE.finditer(stripped):
        found.append(match.group(1) or match.group(2) or "")
    for match in HTML_ATTR_RE.finditer(stripped):
        for part in match.group(1).split(","):
            found.append(part.strip())
    return [_clean(item) for item in found if _clean(item)]


def _repo_path(url: str) -> str | None:
    if url.startswith(BLOB):
        return url[len(BLOB) :]
    if url.startswith(RAW):
        return url[len(RAW) :]
    return None


def _page_name(ref: str) -> str | None:
    if not ref or "://" in ref or ref.startswith("mailto:"):
        return None
    if "/" in ref or ref.endswith(".md"):
        return None
    return ref


def broken(wiki: Path, repo: Path | None = None) -> list[str]:
    """Return one line per broken link."""
    wiki = wiki.resolve()
    source = (repo or ROOT).resolve()
    problems: list[str] = []
    pages = {path.stem: path.name for path in wiki.glob("*.md")}
    sidebar = wiki / "_Sidebar.md"
    if not sidebar.is_file():
        problems.append("_Sidebar.md is missing")
    else:
        for ref in SIDEBAR_RE.findall(sidebar.read_text(encoding="utf-8")):
            name = _clean(ref)
            page = _page_name(name)
            if page and page not in pages and not (wiki / (page + ".md")).is_file():
                problems.append("_Sidebar.md links to missing page " + page)
    for path in sorted(wiki.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        for ref in _refs(text):
            repo_file = _repo_path(ref)
            if repo_file is not None:
                if not (source / repo_file).is_file():
                    problems.append(path.name + " links to missing repo file " + repo_file)
                continue
            page = _page_name(ref)
            if page is None:
                continue
            if page not in pages:
                problems.append(path.name + " links to missing page " + page)
    return problems


def main(argv: list[str] | None = None) -> int:
    """Exit 1 when a wiki link is broken."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: python3 scripts/check_wiki_links.py <wiki-folder>", file=sys.stderr)
        return 2
    wiki = Path(args[0]).expanduser()
    problems = broken(wiki)
    if problems:
        print("Wiki links that do not resolve:", file=sys.stderr)
        for line in problems:
            print(line, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
