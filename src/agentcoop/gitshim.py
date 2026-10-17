"""Block dangerous git commands during a scheduled run.

The installed copy of this file is <board>/bin/shims/git. It must stay
runnable on its own, with no import of the agentcoop package.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _skip_global_options(argv: list[str]) -> list[str]:
    """Drop -C, --git-dir, --work-tree, and -c before the subcommand."""
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "-C":
            index += 2
            continue
        if item in ("--git-dir", "--work-tree"):
            index += 2
            continue
        if item.startswith("--git-dir=") or item.startswith("--work-tree="):
            index += 1
            continue
        if item == "-c":
            index += 2
            continue
        if item.startswith("-c") and len(item) > 2:
            index += 1
            continue
        if item.startswith("-") and item != "-":
            index += 1
            continue
        break
    return argv[index:]


def blocked_action(argv: list[str]) -> str | None:
    """Return the blocked action name, or None when git may run."""
    rest = _skip_global_options(list(argv))
    if not rest:
        return None
    command = rest[0]
    if command == "push":
        return "push"
    if command == "switch":
        return "switch"
    if command == "rebase":
        return "rebase"
    if command == "worktree":
        return "worktree"
    if command == "checkout":
        return _blocked_checkout(rest[1:])
    if command == "reset" and "--hard" in rest[1:]:
        return "reset --hard"
    if command == "branch" and ("-D" in rest[1:] or "-d" in rest[1:]):
        return "branch -D" if "-D" in rest[1:] else "branch -d"
    return None


def _blocked_checkout(args: list[str]) -> str | None:
    """Block branch checkouts. Allow restoring paths that exist in the work tree."""
    flags = {"-b", "-B", "--orphan", "--detach"}
    for item in args:
        if item in flags or item.startswith("--orphan="):
            return "checkout"
    if "--" in args:
        return None
    paths = [item for item in args if not item.startswith("-")]
    if not paths:
        return None
    if all(_existing_worktree_path(item) for item in paths):
        return None
    return "checkout"


def _existing_worktree_path(token: str) -> bool:
    """Return True when token is an existing path inside the current work tree."""
    path = Path(token)
    if not path.exists():
        return False
    try:
        path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return False
    return True


def real_git(path_value: str, shim_dir: Path) -> str | None:
    """Find git on PATH, skipping the shims folder."""
    shim = shim_dir.resolve()
    for entry in path_value.split(os.pathsep):
        if not entry:
            continue
        folder = Path(entry)
        try:
            if folder.resolve() == shim:
                continue
        except OSError:
            continue
        candidate = folder / "git"
        if os.name == "nt":
            for suffix in (".exe", ".cmd", ".bat"):
                named = folder / ("git" + suffix)
                if named.is_file():
                    return str(named)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def main(argv: list[str] | None = None) -> int:
    """Run real git, or refuse a blocked command when unattended."""
    args = list(sys.argv[1:] if argv is None else argv)
    shim_dir = Path(__file__).resolve().parent
    found = real_git(os.environ.get("PATH", ""), shim_dir)
    if not found:
        print("git was not found.", file=sys.stderr)
        return 127
    if os.environ.get("AGENTCOOP_UNATTENDED") == "1":
        action = blocked_action(args)
        if action:
            print(
                action + " is blocked during scheduled Agent Co-op runs. A person can do it later.",
                file=sys.stderr,
            )
            return 1
    os.execv(found, [found, *args])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
