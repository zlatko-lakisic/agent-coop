"""Install each published release, move it to this build, then move it back.

This job uses the network. Unit tests do not.
"""

from __future__ import annotations

import hashlib
import json
import os
import pty
import select
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
REPO = "zlatko-lakisic/agent-coop"
VERSIONS = ("0.2.0", "0.2.1", "0.2.2", "0.2.3", "0.2.4")
API = "https://api.github.com/repos/" + REPO


def _get(url: str, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "agentcoop", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _download_pyz(version: str, dest: Path) -> Path:
    payload = json.loads(_get(API + "/releases/tags/v" + version).decode("utf-8"))
    assets = {item["name"]: item["browser_download_url"] for item in payload.get("assets") or []}
    if "agentcoop.pyz" not in assets or "SHA256SUMS" not in assets:
        raise SystemExit(version + " has no zipapp or no SHA256SUMS.")
    dest.mkdir(parents=True, exist_ok=True)
    pyz = dest / "agentcoop.pyz"
    pyz.write_bytes(_get(assets["agentcoop.pyz"], timeout=120))
    sums = _get(assets["SHA256SUMS"]).decode("utf-8")
    expected = ""
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == "agentcoop.pyz":
            expected = parts[0].lower()
    actual = hashlib.sha256(pyz.read_bytes()).hexdigest()
    if not expected or actual != expected:
        raise SystemExit(version + " zipapp hash does not match.")
    return pyz


def _run(argv: list[str], env: dict, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, env=env, cwd=str(cwd), capture_output=True, text=True)


def _need(completed: subprocess.CompletedProcess[str], label: str) -> None:
    if completed.returncode != 0:
        sys.stderr.write(label + " failed\n")
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode or 1)


def _home_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = str(ROOT / "src")
    env.pop("AGENTCOOP_TEST_OFFLINE", None)
    env.pop("AGENTCOOP_BOARD", None)
    return env


def _new_argv(*args: str) -> list[str]:
    return [sys.executable, "-m", "agentcoop", *args]


def _old_argv(pyz: Path, *args: str) -> list[str]:
    return [sys.executable, str(pyz), *args]


def _older(left: str, right: str) -> bool:
    return tuple(int(part) for part in left.split(".")) < tuple(int(part) for part in right.split("."))


def _confirm(argv: list[str], env: dict, cwd: Path, typed: str) -> None:
    """Run a command on a terminal and type the version when asked."""
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(argv[0], argv, env)
    sent = False
    collected = b""
    status = 0
    deadline = time.time() + 300
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 1)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            if chunk:
                collected += chunk
                if not sent and b"Type the version to confirm:" in collected:
                    os.write(fd, (typed + "\n").encode("utf-8"))
                    sent = True
        done, status = os.waitpid(pid, os.WNOHANG)
        if done != 0:
            break
    else:
        os.kill(pid, 15)
        os.waitpid(pid, 0)
        raise SystemExit("Timed out waiting for " + " ".join(argv))
    code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (status >> 8)
    if code != 0 or not sent:
        sys.stderr.write(collected.decode("utf-8", errors="replace"))
        raise SystemExit("Downgrade to " + typed + " failed (exit " + str(code) + ").")


def _private(board: Path) -> None:
    """Drop group and other bits. A temp folder is created with the process umask."""
    if os.name == "nt" or not board.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(board):
        os.chmod(dirpath, os.stat(dirpath).st_mode & ~0o077)
        for name in dirnames:
            path = os.path.join(dirpath, name)
            os.chmod(path, os.stat(path).st_mode & ~0o077)
        for name in filenames:
            path = os.path.join(dirpath, name)
            os.chmod(path, os.stat(path).st_mode & ~0o077)


def _marker_files(board: Path) -> list[Path]:
    """Return messages, context, and progress. Generated pages may be rewritten."""
    found: list[Path] = []
    projects = board / "projects"
    if not projects.is_dir():
        return found
    for path in projects.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(projects).parts
        if "context" in parts or "progress" in parts or "msgs" in parts:
            found.append(path)
    return found


def _seed(pyz: Path, env: dict, home: Path, work: Path) -> Path:
    board = home / ".agent_board"
    setup = _run(
        _old_argv(
            pyz,
            "setup",
            "--connect",
            "cursor,claude-code",
            "--schedule",
            "no",
            "--add-to-path",
            "no",
            "--yes",
        ),
        env,
        work,
    )
    _need(setup, "old setup")
    project = work / "demo"
    project.mkdir()
    added = _run(
        _old_argv(
            pyz,
            "project",
            "add",
            str(project),
            "--name",
            "demo",
            "--driver",
            "cursor",
            "--helper",
            "claude_code=reviews",
            "--gitignore",
            "no",
            "--yes",
        ),
        env,
        project,
    )
    _need(added, "project add")
    sent = _run(
        _old_argv(
            pyz,
            "send",
            "--as",
            "cursor",
            "--project",
            "demo",
            "--to",
            "claude_code",
            "--subject",
            "Hello",
            "--body",
            "Please look.",
        ),
        env,
        project,
    )
    _need(sent, "send")
    context = _run(
        _old_argv(
            pyz,
            "context",
            "write",
            "--as",
            "cursor",
            "--project",
            "demo",
            "--body",
            "The goal is a demo.",
        ),
        env,
        project,
    )
    _need(context, "context")
    progress = _run(
        _old_argv(
            pyz,
            "progress",
            "write",
            "--as",
            "cursor",
            "--project",
            "demo",
            "--body",
            "Working on the demo.",
        ),
        env,
        project,
    )
    _need(progress, "progress")
    if not _marker_files(board):
        raise SystemExit("The old board has no message, context, or progress file.")
    return board


def _version_line(argv: list[str], env: dict, cwd: Path) -> str:
    completed = _run(argv + ["version"], env, cwd)
    _need(completed, "version")
    for line in completed.stdout.splitlines():
        if line.startswith("agentcoop "):
            return line.split()[1]
    raise SystemExit("No version line in " + completed.stdout)


def _stable_sentence(version: str) -> str:
    raw = _get(
        "https://raw.githubusercontent.com/zlatko-lakisic/agent-coop/v"
        + version
        + "/src/agentcoop/templates/SKILL.md.tmpl"
    ).decode("utf-8")
    for line in raw.splitlines():
        text = line.strip()
        if len(text) > 40 and "{" not in text and not text.startswith("#") and not text.startswith("*"):
            return text
    raise SystemExit(version + " skill template has no stable sentence.")


def _upgrade_and_return(version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="agentcoop-upgrade-") as raw:
        root = Path(raw)
        home = root / "home"
        home.mkdir()
        work = root / "work"
        work.mkdir()
        env = _home_env(home)
        pyz = _download_pyz(version, root / "old")
        board = _seed(pyz, env, home, work)
        before = {str(path.relative_to(board)): path.read_bytes() for path in _marker_files(board)}
        installed = _run(_new_argv("update", "--install-this"), env, work)
        _need(installed, "install-this")
        _private(board)
        doctor = _run(_new_argv("doctor"), env, work)
        _need(doctor, "doctor")
        for rel, body in before.items():
            path = board / rel
            if not path.is_file() or path.read_bytes() != body:
                raise SystemExit("Board data changed during the update: " + rel)
        from agentcoop import __version__

        if _older(version, __version__):
            _confirm(_new_argv("update", "--to", version), env, work, version)
        else:
            same = _run(_new_argv("update", "--to", version), env, work)
            _need(same, "update --to")
        old_version = _version_line(_old_argv(pyz), env, work)
        if old_version != version:
            raise SystemExit("After downgrade the old program says " + old_version + ".")
        checked = _run(_old_argv(pyz, "check", "--as", "cursor", "--project", "demo"), env, work)
        _need(checked, "old check")
        for rel, body in before.items():
            path = board / rel
            if not path.is_file() or path.read_bytes() != body:
                raise SystemExit("Board data changed during the downgrade: " + rel)


def _fresh_setup(version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="agentcoop-fresh-") as raw:
        home = Path(raw) / "home"
        home.mkdir()
        work = Path(raw) / "work"
        work.mkdir()
        env = _home_env(home)
        completed = _run(
            _new_argv(
                "setup",
                "--version",
                version,
                "--connect",
                "claude-code",
                "--schedule",
                "no",
                "--add-to-path",
                "no",
                "--yes",
            ),
            env,
            work,
        )
        _need(completed, "setup --version")
        board = home / ".agent_board"
        command = board / "bin" / "agentcoop"
        if not command.is_file():
            command = board / "bin" / "agentcoop.pyz"
        seen = _version_line([sys.executable, str(command)] if command.suffix == ".pyz" else [str(command)], env, work)
        if seen != version:
            raise SystemExit("setup --version " + version + " installed " + seen + ".")
        skill = home / ".claude" / "skills" / "agent-coop" / "SKILL.md"
        if not skill.is_file():
            raise SystemExit("setup did not write the Claude Code skill.")
        sentence = _stable_sentence(version)
        if sentence not in skill.read_text(encoding="utf-8"):
            raise SystemExit("The installed skill does not match " + version + ".")


def main() -> int:
    only = os.environ.get("UPGRADE_ONLY", "").strip()
    versions = (only,) if only else VERSIONS
    for version in versions:
        if version not in VERSIONS:
            raise SystemExit(version + " is not one of the published releases this job covers.")
        print("upgrade " + version, flush=True)
        _upgrade_and_return(version)
        print("fresh " + version, flush=True)
        _fresh_setup(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
