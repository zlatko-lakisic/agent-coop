"""Set up a temporary board and check the installed command.

Later steps use <board>/bin/agentcoop, not the copy in this checkout.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
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
        quiet_installed = bindir / "agentcoopw.exe"
        if quiet_installed.is_file():
            code = _run_version([str(quiet_installed), "version"], env, tk=_wants_tk(quiet_installed))
            if code != 0:
                return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
