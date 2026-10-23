"""Write COMPATIBILITY.md from src/agentcoop/data/compat.json."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentcoop.compat import load_rows  # noqa: E402

OUT = ROOT / "COMPATIBILITY.md"
COLUMNS = (
    ("version", "Version"),
    ("date", "Date"),
    ("message_format", "Message format"),
    ("spec", "Spec"),
    ("board_format", "Board format"),
    ("oldest_reader", "Oldest reader"),
    ("board_changes", "Board changes"),
    ("settings_changes", "Settings changes"),
    ("downgrade_loses", "Downgrade loses"),
    ("desktop_skill", "Desktop skill"),
    ("upgrade_from", "Upgrade from"),
)


def _cell(value: object) -> str:
    return " ".join(str(value if value is not None else "").split()).replace("|", "/")


def render(rows: list[dict] | None = None) -> str:
    """Return the markdown page."""
    data = rows if rows is not None else load_rows()
    lines = [
        "# Compatibility",
        "",
        "Generated from src/agentcoop/data/compat.json. Do not edit by hand.",
        "",
        "One row per published version, newest first. The Unreleased row is work that is not in a version yet.",
        "",
        "| " + " | ".join(title for _key, title in COLUMNS) + " |",
        "| " + " | ".join("---" for _key, _title in COLUMNS) + " |",
    ]
    for row in data:
        lines.append("| " + " | ".join(_cell(row.get(key, "")) for key, _title in COLUMNS) + " |")
    lines.extend(
        [
            "",
            "## How to read this",
            "",
            "Oldest reader is the oldest Agent Co-op that can still open a board this version writes. A missing board_format is format 1.",
            "",
            "Downgrade loses is what stops working if you go back one version. When you move down more than one version, `agentcoop update` prints that cell for the version you are leaving and for each version in between.",
            "",
            "Desktop skill is Changed when you need to save the Claude desktop skill again.",
            "",
            "Upgrade from is the oldest version that can move straight to this one. Unknown means that path was not checked.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Write the page, or exit 1 when --check finds a difference."""
    args = list(sys.argv[1:] if argv is None else argv)
    text = render()
    if "--check" in args:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current != text:
            print("COMPATIBILITY.md is out of date. Run: python3 scripts/gen_compat.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
