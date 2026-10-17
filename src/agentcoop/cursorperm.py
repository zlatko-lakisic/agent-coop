"""Cursor CLI permission files for scheduled driver and helper runs."""

from __future__ import annotations

import json
from pathlib import Path

def cursor_permissions(worktree: Path, board: Path, shell_allow: list[str]) -> dict:
    """Return the cli.json object for one driver worktree."""
    work = str(worktree.resolve())
    board_path = str(board.resolve())
    allow = [
        "Read(**)",
        "Write(" + work + "/**)",
        "Write(" + board_path + "/**)",
        "Shell(git)",
        "Shell(agentcoop)",
    ]
    for name in shell_allow:
        token = "Shell(" + name + ")"
        if token not in allow:
            allow.append(token)
    return {
        "permissions": {
            "allow": allow,
            "deny": [
                "Write(**/.env)",
                "Write(**/.env.*)",
                "Write(**/*.pem)",
                "Write(**/*.key)",
                "Write(" + board_path + "/projects.json)",
                "Write(" + board_path + "/agents.json)",
                "Write(" + board_path + "/config.json)",
                "Write(" + board_path + "/.state/**)",
            ],
        }
    }


def cursor_helper_permissions() -> dict:
    """Return the read-only cli.json object for a helper run."""
    return {"permissions": {"allow": ["Read(**)"], "deny": ["Write(**)", "Shell(*)"]}}


def write_cursor_helper_cli(project_dir: Path) -> Path:
    """Write the helper permissions file under the project's board folder."""
    folder = project_dir / ".cursor"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "cli.json"
    path.write_text(json.dumps(cursor_helper_permissions(), indent=2) + "\n", encoding="utf-8")
    return path


def write_cursor_cli(worktree: Path, board: Path, shell_allow: list[str]) -> Path:
    """Write .cursor/cli.json and keep it out of git."""
    folder = worktree / ".cursor"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "cli.json"
    path.write_text(
        json.dumps(cursor_permissions(worktree, board, shell_allow), indent=2) + "\n",
        encoding="utf-8",
    )
    _exclude(worktree, ".cursor/cli.json")
    return path


def _exclude(worktree: Path, line: str) -> None:
    import subprocess

    dirs = []
    for flag in ("--git-dir", "--git-common-dir"):
        found = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", flag],
            capture_output=True,
            text=True,
        )
        if found.returncode == 0 and (found.stdout or "").strip():
            path = Path((found.stdout or "").strip())
            if not path.is_absolute():
                path = (worktree / path).resolve()
            dirs.append(path)
    if not dirs:
        git = worktree / ".git"
        dirs.append(git if git.is_dir() else worktree)
    for git in dirs:
        info = git / "info"
        info.mkdir(parents=True, exist_ok=True)
        exclude = info / "exclude"
        current = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        if line in current.splitlines():
            continue
        if current and not current.endswith("\n"):
            current += "\n"
        exclude.write_text(current + line + "\n", encoding="utf-8")
