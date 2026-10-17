"""Set up a temporary board and check the installed command.

Later steps use <board>/bin/agentcoop, not the copy in this checkout.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


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
        if os.name == "nt":
            command = board / "bin" / "agentcoop.cmd"
            argv = ["cmd", "/c", str(command), "doctor"]
        else:
            command = board / "bin" / "agentcoop"
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
