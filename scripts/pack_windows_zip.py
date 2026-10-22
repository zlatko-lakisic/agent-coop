"""Pack the two Windows programs into one zip under plain names."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def pack_windows_zip(console: Path, quiet: Path, dest: Path) -> None:
    """Write agentcoop.exe and agentcoopw.exe side by side."""
    if not console.is_file() or not quiet.is_file():
        raise SystemExit("Both Windows programs are required.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(console, "agentcoop.exe")
        archive.write(quiet, "agentcoopw.exe")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print("Usage: pack_windows_zip.py CONSOLE QUIET DEST", file=sys.stderr)
        return 2
    pack_windows_zip(Path(args[0]), Path(args[1]), Path(args[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
