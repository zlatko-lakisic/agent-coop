"""Set up a temporary board and check the installed command.

Later steps use <board>/bin/agentcoop, not the copy in this checkout.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _agentcoopw_name(name: str) -> str:
    """Return the no-console sibling name next to a Windows binary."""
    if "agentcoopw" in name.lower():
        return name
    if "agentcoop-" in name:
        return name.replace("agentcoop-", "agentcoopw-", 1)
    return name.replace("agentcoop", "agentcoopw", 1)


def _run_version(argv: list[str], env: dict) -> int:
    completed = subprocess.run(argv, env=env, capture_output=True, text=True)
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout + completed.stderr)
    return completed.returncode


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
        if len(starter) == 1:
            binary = Path(starter[0])
            sibling = binary.with_name(_agentcoopw_name(binary.name))
            if sibling.is_file() and sibling.resolve() != binary.resolve():
                code = _run_version([str(sibling), "version"], env)
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
            code = _run_version([str(quiet_installed), "version"], env)
            if code != 0:
                return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
