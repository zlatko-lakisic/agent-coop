"""Build dist/agentcoop.pyz from the package."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentcoop.install import build_zipapp  # noqa: E402


def main() -> None:
    """Write the zipapp and print its path."""
    dest = ROOT / "dist" / "agentcoop.pyz"
    build_zipapp(dest)
    print(dest)


if __name__ == "__main__":
    main()
