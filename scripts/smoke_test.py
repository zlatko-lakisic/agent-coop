"""Set up a temporary board and check the installed command.

Later steps use <board>/bin/agentcoop, not the copy in this checkout.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


def _agentcoopw_name(name: str) -> str:
    """Return the no-console sibling name next to a Windows binary."""
    if "agentcoopw" in name.lower():
        return name
    if "agentcoop-" in name:
        return name.replace("agentcoop-", "agentcoopw-", 1)
    return name.replace("agentcoop", "agentcoopw", 1)


def unpack_windows_zip(zip_path: Path, dest: Path) -> tuple[Path, Path]:
    """Unpack agentcoop.exe and agentcoopw.exe from the Windows zip."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest)
    console = dest / "agentcoop.exe"
    quiet = dest / "agentcoopw.exe"
    if not console.is_file() or not quiet.is_file():
        raise SystemExit("The Windows zip must contain agentcoop.exe and agentcoopw.exe side by side.")
    return console, quiet


def _wants_tk(binary: Path) -> bool:
    """macOS and Windows binaries must be able to import tkinter."""
    if sys.platform not in ("darwin", "win32"):
        return False
    name = binary.name.lower()
    if name.endswith(".pyz") or name.startswith("python"):
        return False
    return True


def _run_version(argv: list[str], env: dict, *, tk: bool = False) -> int:
    run_env = dict(env)
    if tk:
        run_env["AGENTCOOP_IMPORT_TK"] = "1"
    completed = subprocess.run(argv, env=run_env, capture_output=True, text=True)
    text = completed.stdout + completed.stderr
    if completed.returncode != 0 or (tk and "tkinter ok" not in completed.stdout):
        sys.stderr.write(text)
        return completed.returncode or 1
    return 0


def _watch_smoke(command: Path, env: dict, root: Path) -> int:
    """Prove watch returns when a message arrives during a 1 minute wait."""
    connected = subprocess.run(
        [str(command), "connect", "claude-code", "--yes"],
        env=env,
        capture_output=True,
        text=True,
    )
    if connected.returncode != 0:
        sys.stderr.write(connected.stdout + connected.stderr)
        return connected.returncode or 1
    demo = root / "demo"
    demo.mkdir()
    added = subprocess.run(
        [
            str(command),
            "project",
            "add",
            str(demo),
            "--name",
            "smoke",
            "--driver",
            "cursor",
            "--helper",
            "claude_code=reviews",
            "--gitignore",
            "no",
            "--yes",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if added.returncode != 0:
        sys.stderr.write(added.stdout + added.stderr)
        return added.returncode or 1
    watch = subprocess.Popen(
        [str(command), "watch", "--as", "claude_code", "--project", "smoke", "--timeout", "1"],
        env=env,
        cwd=str(demo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(2)
    sent = subprocess.run(
        [
            str(command),
            "send",
            "--as",
            "cursor",
            "--project",
            "smoke",
            "--to",
            "claude_code",
            "--subject",
            "Ping",
            "--body",
            "Hello from smoke.",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if sent.returncode != 0:
        watch.kill()
        sys.stderr.write(sent.stdout + sent.stderr)
        return sent.returncode or 1
    try:
        output, errors = watch.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        watch.kill()
        sys.stderr.write("watch did not finish\n")
        return 1
    if watch.returncode != 0 or "Handle these, then run agentcoop watch again." not in output:
        sys.stderr.write(output + errors)
        return watch.returncode or 1
    if "Read from cursor: Ping. Hello from smoke." not in output:
        sys.stderr.write(output + errors)
        return 1
    return 0


def _payload_from(starter: list[str]) -> Path | None:
    """Return the built file smoke was given, when it is a real file."""
    for item in starter:
        path = Path(item)
        if path.is_file() and path.suffix.lower() in (".pyz", ".exe", ".zip"):
            return path
        if path.is_file() and path.name.startswith("agentcoop"):
            return path
    return None


def _update_smoke(command: Path, starter: list[str], env: dict, root: Path) -> int:
    """Check the daily lookup, then install the built file that is already on disk."""
    checked = subprocess.run(
        [str(command), "update", "--check"],
        env=env,
        capture_output=True,
        text=True,
    )
    text = checked.stdout + checked.stderr
    if checked.returncode != 0:
        sys.stderr.write(text)
        return checked.returncode or 1
    payload = _payload_from(starter)
    if payload is None:
        return 0
    folder = root / "from-file"
    folder.mkdir()
    dest = folder / payload.name
    shutil.copy2(payload, dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    (folder / "SHA256SUMS").write_text(digest + "  " + dest.name + "\n", encoding="utf-8")
    installed = subprocess.run(
        [str(command), "update", "--from-file", str(dest)],
        env=env,
        capture_output=True,
        text=True,
    )
    if installed.returncode != 0:
        sys.stderr.write(installed.stdout + installed.stderr)
        return installed.returncode or 1
    return 0


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    with tempfile.TemporaryDirectory(prefix="agentcoop-smoke-") as raw:
        home = Path(raw) / "home"
        home.mkdir()
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["PYTHONPATH"] = str(src)
        starter = sys.argv[1:] or [sys.executable, "-m", "agentcoop"]
        if len(starter) == 1 and str(starter[0]).lower().endswith(".zip"):
            console, quiet = unpack_windows_zip(Path(starter[0]), Path(raw) / "windows")
            code = _run_version([str(quiet), "version"], env, tk=_wants_tk(quiet))
            if code != 0:
                return code
            starter = [str(console)]
        if len(starter) == 1:
            binary = Path(starter[0])
            if _wants_tk(binary):
                code = _run_version([str(binary), "version"], env, tk=True)
                if code != 0:
                    return code
            sibling = binary.with_name(_agentcoopw_name(binary.name))
            if sibling.is_file() and sibling.resolve() != binary.resolve():
                code = _run_version([str(sibling), "version"], env, tk=_wants_tk(sibling))
                if code != 0:
                    return code
        setup = subprocess.run(
            [
                *starter,
                "setup",
                "--connect",
                "cursor",
                "--schedule",
                "no",
                "--yes",
            ],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
        )
        if setup.returncode != 0:
            sys.stderr.write(setup.stdout + setup.stderr)
            return setup.returncode
        board = home / ".agent_board"
        bindir = board / "bin"
        if os.name == "nt":
            candidates = [bindir / "agentcoop.exe", bindir / "agentcoop.cmd"]
        else:
            candidates = [bindir / "agentcoop"]
        command = next((path for path in candidates if path.is_file()), candidates[0])
        argv = [str(command), "doctor"]
        if not command.is_file():
            sys.stderr.write("The installed command is missing: " + str(command) + "\n")
            return 1
        doctor = subprocess.run(argv, env=env, capture_output=True, text=True)
        text = doctor.stdout + doctor.stderr
        if doctor.returncode != 0:
            sys.stderr.write(text)
            return doctor.returncode
        for line in text.splitlines():
            if line.startswith("Fix:") and "command" in line.lower():
                sys.stderr.write(text)
                return 1
        sys.stdout.write(text)
        if _watch_smoke(command, env, Path(raw)) != 0:
            return 1
        if _update_smoke(command, starter, env, Path(raw)) != 0:
            return 1
        prepare = subprocess.run(
            [*starter, "desktop", "prepare"],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
        )
        if prepare.returncode != 0:
            sys.stderr.write(prepare.stdout + prepare.stderr)
            return prepare.returncode
        quiet_installed = bindir / "agentcoopw.exe"
        if quiet_installed.is_file():
            code = _run_version([str(quiet_installed), "version"], env, tk=_wants_tk(quiet_installed))
            if code != 0:
                return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
