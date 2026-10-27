"""A fingerprint of the project folder. Git history is not part of it."""

from __future__ import annotations

import hashlib
import os
from fnmatch import fnmatch
from pathlib import Path

from agentcoop.projects import atomic_write_bytes, dump_json, load_projects

SKIP_DIRS = {
    ".git",
    ".eggs",
    ".gradle",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "bower_components",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "site-packages",
    "target",
    "vendor",
    "venv",
}

COOP_FILES = {
    ".cursor/cli.json",
    ".cursor/hooks.json",
    ".cursor/permissions.json",
    ".cursor/rules/agent-coop.mdc",
}


def take_closed(board: Path, project: str) -> str:
    """Save the current fingerprint as the last closed one. Return its digest."""
    scanned = _scan(board, project)
    state = _load(board, project)
    state["closed"] = scanned["digest"]
    state["closed_files"] = scanned["files"]
    state["cache"] = scanned["cache"]
    _save(board, project, state)
    return scanned["digest"]


def accept_reviewed(board: Path, project: str, files: dict, digest: str) -> None:
    """Close the task at the fingerprint the reviewer was shown."""
    state = _load(board, project)
    state["closed"] = digest
    state["closed_files"] = files if isinstance(files, dict) else {}
    _save(board, project, state)


def current(board: Path, project: str) -> dict:
    """Return the digest, the file hashes, and which paths were hashed this time."""
    return _scan(board, project)


def changed(board: Path, project: str) -> bool:
    """Return True when the folder differs from the last closed fingerprint."""
    state = _load(board, project)
    if "closed" not in state:
        return True
    scanned = _scan(board, project)
    return scanned["digest"] != state.get("closed")


def closed_files(board: Path, project: str) -> dict:
    """Return the path to hash map from the last closed fingerprint."""
    files = _load(board, project).get("closed_files")
    return files if isinstance(files, dict) else {}


def _scan(board: Path, project: str) -> dict:
    folder = _folder(board, project)
    state = _load(board, project)
    cache = state.get("cache") if isinstance(state.get("cache"), dict) else {}
    patterns = _patterns(folder)
    skip = _coop_paths(board, project, folder)
    files: dict[str, str] = {}
    hashed: list[str] = []
    if folder.is_dir():
        for rel, path in _walk(folder, patterns, skip):
            try:
                stat = path.stat()
            except OSError:
                continue
            size = int(stat.st_size)
            mtime = int(stat.st_mtime_ns)
            row = cache.get(rel) if isinstance(cache.get(rel), dict) else None
            if (
                row
                and row.get("size") == size
                and row.get("mtime_ns") == mtime
                and isinstance(row.get("sha256"), str)
                and row.get("sha256")
            ):
                sha = str(row["sha256"])
            else:
                sha = hash_file(path)
                hashed.append(rel)
            cache[rel] = {"size": size, "mtime_ns": mtime, "sha256": sha}
            files[rel] = sha
    for rel in list(cache):
        if rel not in files:
            cache.pop(rel, None)
    state["cache"] = cache
    _save(board, project, state)
    return {"digest": _digest(files), "files": files, "hashed": hashed, "cache": cache}


def hash_file(path: Path) -> str:
    """Return the SHA-256 of one file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _digest(files: dict) -> str:
    lines = [path + "\0" + files[path] for path in sorted(files)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _walk(folder: Path, patterns: list[str], skip: set[str]):
    for dirpath, dirnames, filenames in os.walk(folder):
        rel_dir = Path(dirpath).relative_to(folder).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        kept = []
        for name in dirnames:
            rel = name if not rel_dir else rel_dir + "/" + name
            if name in SKIP_DIRS or _ignored(rel, True, patterns):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            rel = name if not rel_dir else rel_dir + "/" + name
            path = Path(dirpath) / name
            if path.is_symlink() or rel in skip or _ignored(rel, False, patterns):
                continue
            yield rel, path


def _patterns(folder: Path) -> list[str]:
    path = folder / ".gitignore"
    if not path.is_file():
        return []
    found = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or text.startswith("!"):
            continue
        found.append(text)
    return found


def _ignored(rel: str, is_dir: bool, patterns: list[str]) -> bool:
    for pattern in patterns:
        if _one(rel, is_dir, pattern):
            return True
    return False


def _one(rel: str, is_dir: bool, pattern: str) -> bool:
    directory = pattern.endswith("/")
    text = pattern[1:] if pattern.startswith("/") else pattern
    text = text.rstrip("/")
    if not text:
        return False
    anchored = pattern.startswith("/")
    if directory and not is_dir:
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if not parent:
            return False
        return _one(parent, True, pattern)
    if "/" in text or anchored:
        if fnmatch(rel, text):
            return True
        return fnmatch(rel, text + "/*") or rel.startswith(text + "/")
    if fnmatch(Path(rel).name, text):
        return True
    return any(fnmatch(part, text) for part in rel.split("/"))


def _coop_paths(board: Path, project: str, folder: Path) -> set[str]:
    skip = set(COOP_FILES)
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    root = folder.resolve()
    for entry in record.get("installed") or []:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("path")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            skip.add(Path(raw).resolve().relative_to(root).as_posix())
        except (OSError, ValueError):
            continue
    return skip


def _folder(board: Path, project: str) -> Path:
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    return Path(str(record.get("path") or ""))


def _path(board: Path, project: str) -> Path:
    return board / "projects" / project / "reviews" / "fingerprint.json"


def _load(board: Path, project: str) -> dict:
    path = _path(board, project)
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(board: Path, project: str, state: dict) -> None:
    path = _path(board, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, dump_json(state))
