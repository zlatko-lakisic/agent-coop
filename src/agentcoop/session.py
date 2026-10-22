"""Double-check the tool an agent says it is.

Only environment variables documented by that tool count. Cursor does not
document one, so a session with no confirmed clue is accepted as whatever
tool the agent named.
"""

from __future__ import annotations

import os
from typing import Mapping

from agentcoop.errors import CoopError
from agentcoop.tools import find_tool, tool_choices_text

# Claude Code: https://code.claude.com/docs/en/env-vars
# Gemini CLI: https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/shell.md
# Codex: https://github.com/openai/codex/blob/main/codex-rs/core/src/spawn.rs
#        https://github.com/openai/codex/blob/main/codex-rs/core/src/exec_env.rs


def _set(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key) or "").strip())


def confirmed_tool(env: Mapping[str, str] | None = None) -> str | None:
    """Return claude-code, gemini-cli, codex, mixed, or None."""
    values: Mapping[str, str] = os.environ if env is None else env
    found = []
    if _set(values, "CLAUDECODE"):
        found.append("claude-code")
    if _set(values, "GEMINI_CLI"):
        found.append("gemini-cli")
    if _set(values, "CODEX_SANDBOX") or _set(values, "CODEX_THREAD_ID"):
        found.append("codex")
    if not found:
        return None
    if len(found) > 1:
        return "mixed"
    return found[0]


def tool_matches(stated: str, seen: str | None) -> bool:
    """Return True when the stated tool is not contradicted."""
    if seen is None:
        return True
    if seen == "mixed":
        return False
    if seen == "codex" and stated in ("codex", "chatgpt-codex"):
        return True
    return stated == seen


def mismatch_message(stated_display: str, seen: str) -> str:
    """Return the refusal. Nothing was changed."""
    if seen == "mixed":
        return "This session looks like more than one tool. Nothing was changed."
    names = {
        "claude-code": "Claude Code",
        "gemini-cli": "Gemini CLI",
        "codex": "Codex",
    }
    return (
        "This session looks like "
        + names.get(seen, seen)
        + ", not "
        + stated_display
        + ". Nothing was changed."
    )


def connect_self(
    tool_key: str,
    *,
    agent_name: str | None = None,
    model: str | None = None,
    yes: bool = False,
) -> str:
    """Install this agent's own files and register it. Safe to run again."""
    tool = find_tool(tool_key)
    if tool is None:
        raise CoopError("Unknown agent " + tool_key + ". You can use: " + tool_choices_text())
    seen = confirmed_tool()
    if not tool_matches(tool.key, seen):
        raise CoopError(mismatch_message(tool.display, seen or "mixed"))
    from agentcoop.wizard import connect_one

    return connect_one(
        tool.key,
        agent_name=agent_name,
        yes=yes,
        model=model,
    )
