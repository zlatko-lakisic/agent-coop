"""Write AGENTS-SUPPORT.md from the capability table."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentcoop.tools import all_tools  # noqa: E402

def _background_label(headless: object) -> str:
    """Return the support-page label for a tool's headless field."""
    if headless == "todo":
        return "todo"
    if headless == "none" or not headless:
        return "no"
    if isinstance(headless, dict):
        return "yes"
    return "no"


HANDWRITTEN = {"claude-desktop"}

OPEN_QUESTIONS = (
    "Whether ChatGPT Work can write to granted local folders.",
    "Whether the ChatGPT desktop app's Codex side reads .codex/skills/.",
    "Skill paths for GitHub Copilot, OpenCode, Cline, and Goose.",
    "Current hook formats for Codex, Gemini CLI, and Cursor.",
    "Read-only permission settings per tool (exact config keys).",
    "Detection paths for each app on each OS.",
)


def render() -> str:
    """Return the support page that matches the capability table."""
    lines = [
        "# Supported agents",
        "",
        "Generated from src/agentcoop/tools.py. Do not edit by hand.",
        "",
        "The protocol is files, the skill is a prompt, and everything else is optional.",
        "Web chat cannot join, because it cannot read files on this computer.",
        "",
    ]
    for tool in all_tools():
        lines.append("## " + tool.display)
        lines.append("")
        if tool.default_agent_name:
            lines.append("- Agent name: `" + tool.default_agent_name + "`")
        else:
            lines.append("- Agent name: not supported")
        lines.append("- Can drive: " + ("yes" if tool.can_drive else "no"))
        lines.append("- Joins with: " + tool.join_method)
        lines.append("- Project files for: " + tool.project_files_for)
        lines.append("- Hooks: " + tool.hooks)
        lines.append("- Read-only lock: " + tool.read_only_lock)
        lines.append("- Background runs: " + _background_label(tool.headless))
        lines.append("- Keeps checking: " + tool.keeps_checking)
        lines.append("- Status: " + tool.status)
        if tool.helper_note:
            lines.append("- Suggested helper note: " + tool.helper_note)
        lines.append("- Notes: " + tool.notes)
        lines.append("")
    lines.append("## Open questions")
    lines.append("")
    for index, question in enumerate(OPEN_QUESTIONS, start=1):
        lines.append(str(index) + ". " + question)
    lines.append("")
    return "\n".join(lines)


def render_tool_page(tool: object) -> str:
    """Return one docs/tools page."""
    lines = ["# " + tool.display, ""]  # type: ignore[attr-defined]
    if tool.default_agent_name:  # type: ignore[attr-defined]
        lines.append("Agent name: `" + tool.default_agent_name + "`")  # type: ignore[attr-defined]
    else:
        lines.append("Agent name: not supported")
    lines.append("")
    lines.append("Can drive: " + ("yes" if tool.can_drive else "no"))  # type: ignore[attr-defined]
    lines.append("")
    lines.append("Joins with: " + tool.join_method)  # type: ignore[attr-defined]
    lines.append("")
    lines.append("Project files for: " + tool.project_files_for)  # type: ignore[attr-defined]
    lines.append("")
    lines.append("Hooks: " + tool.hooks)  # type: ignore[attr-defined]
    lines.append("")
    lines.append("Read-only lock: " + tool.read_only_lock)  # type: ignore[attr-defined]
    lines.append("")
    lines.append("Background runs: " + _background_label(tool.headless))  # type: ignore[attr-defined]
    lines.append("")
    lines.append("Keeps checking: " + tool.keeps_checking)  # type: ignore[attr-defined]
    lines.append("")
    lines.append("Status: " + tool.status)  # type: ignore[attr-defined]
    lines.append("")
    lines.append(tool.notes)  # type: ignore[attr-defined]
    lines.append("")
    lines.append("See AGENTS-SUPPORT.md for the full table. Open questions there are not guesses.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Write the pages, or fail when --check finds a difference."""
    args = list(sys.argv[1:] if argv is None else argv)
    target = ROOT / "AGENTS-SUPPORT.md"
    text = render()
    pages = {}
    for tool in all_tools():
        if tool.key in HANDWRITTEN:
            continue
        pages[ROOT / "docs" / "tools" / (tool.key + ".md")] = render_tool_page(tool)
    if "--check" in args:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        stale = current != text
        for path, page in pages.items():
            if not path.is_file() or path.read_text(encoding="utf-8") != page:
                stale = True
        if stale:
            print("AGENTS-SUPPORT.md is out of date. Run: python3 scripts/gen_agents_support.py", file=sys.stderr)
            return 1
        return 0
    target.write_text(text, encoding="utf-8")
    for path, page in pages.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
