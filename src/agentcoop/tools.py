"""Capability table for tools that can join the board.

Detection paths and unverified hook formats stay empty on purpose.
Open questions are noted on each entry instead of filled in by guesswork.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    """One product an agent can be connected as."""

    key: str
    display: str
    default_agent_name: str
    can_drive: bool
    can_run_commands: bool
    can_write_files: bool
    join_method: str
    hooks: str
    read_only_lock: str
    detect: tuple[str, ...]
    install_paths: tuple[dict[str, str], ...]
    status: str
    notes: str
    helper_note: str = ""
    supported: bool = True


def _skill(scope: str, path: str) -> dict[str, str]:
    return {"scope": scope, "kind": "skill", "path": path}


def _rule(path: str) -> dict[str, str]:
    return {"scope": "project", "kind": "rule", "path": path}


def _section(kind: str, path: str) -> dict[str, str]:
    return {"scope": "project", "kind": kind, "path": path}


def _hook(scope: str, path: str) -> dict[str, str]:
    return {"scope": scope, "kind": "hook", "path": path}


TOOLS: tuple[Tool, ...] = (
    Tool(
        key="cursor",
        display="Cursor",
        default_agent_name="cursor",
        can_drive=True,
        can_run_commands=True,
        can_write_files=True,
        join_method="rule",
        hooks="todo",
        read_only_lock="todo",
        detect=(),
        install_paths=(_rule(".cursor/rules/agent-coop.mdc"),),
        status="untested",
        notes=(
            "Project rule only (loads every turn). "
            "TODO: hook format for Cursor is not verified, so hooks are not installed. "
            "TODO: detection paths per OS are not confirmed."
        ),
        helper_note="edits the project and asks helpers",
    ),
    Tool(
        key="claude-code",
        display="Claude Code",
        default_agent_name="claude_code",
        can_drive=True,
        can_run_commands=True,
        can_write_files=True,
        join_method="skill",
        hooks="supported",
        read_only_lock="todo",
        detect=(),
        install_paths=(
            _skill("user", ".claude/skills/agent-coop/SKILL.md"),
            _hook("user", ".claude/settings.json"),
        ),
        status="untested",
        notes=(
            "User skill and user hooks only. SessionStart and UserPromptSubmit run "
            "check --summary, which stays quiet outside projects. "
            "TODO: exact permission keys for a read-only lock are not confirmed. "
            "TODO: detection paths per OS are not confirmed. "
            "The Code side of the Claude desktop app uses this entry."
        ),
        helper_note="reviews code",
    ),
    Tool(
        key="codex",
        display="Codex CLI",
        default_agent_name="codex",
        can_drive=True,
        can_run_commands=True,
        can_write_files=True,
        join_method="skill",
        hooks="todo",
        read_only_lock="todo",
        detect=(),
        install_paths=(_skill("user", ".codex/skills/agent-coop/SKILL.md"),),
        status="untested",
        notes=(
            "User skill only. "
            "TODO: Codex hook format is not verified, so hooks are not installed. "
            "TODO: exact sandbox config keys for a read-only lock are not confirmed. "
            "TODO: detection paths per OS are not confirmed."
        ),
        helper_note="answers coding questions",
    ),
    Tool(
        key="chatgpt-codex",
        display="ChatGPT desktop (Codex)",
        default_agent_name="chatgpt_codex",
        can_drive=True,
        can_run_commands=True,
        can_write_files=True,
        join_method="agents_md",
        hooks="todo",
        read_only_lock="todo",
        detect=(),
        install_paths=(
            _section("agents_md", "AGENTS.md"),
            _skill("project", ".codex/skills/agent-coop/SKILL.md"),
        ),
        status="untested",
        notes=(
            "The ChatGPT desktop app on macOS and Windows includes Codex. "
            "It does not read ~/.codex/config.toml (only the CLI does). "
            "TODO: confirm whether it reads .codex/skills/. "
            "TODO: detection paths per OS are not confirmed."
        ),
        helper_note="architecture questions",
    ),
    Tool(
        key="chatgpt-work",
        display="ChatGPT Work",
        default_agent_name="chatgpt_work",
        can_drive=False,
        can_run_commands=False,
        can_write_files=False,
        join_method="folder_grant",
        hooks="none",
        read_only_lock="advisory",
        detect=(),
        install_paths=(),
        status="planned",
        notes=(
            "Observer, planned. The user grants ~/.agent_board/ and the project "
            "folder in the app, then pastes the skill into project instructions. "
            "TODO: confirm whether ChatGPT Work can write to granted local folders. "
            "Until then it cannot reply or mark done."
        ),
        helper_note="reads the board when folder access is granted",
    ),
    Tool(
        key="claude-desktop",
        display="Claude desktop",
        default_agent_name="claude_desktop",
        can_drive=False,
        can_run_commands=False,
        can_write_files=True,
        join_method="folder_grant",
        hooks="none",
        read_only_lock="advisory",
        detect=(),
        install_paths=(),
        status="planned",
        notes=(
            "Connected folders plus a skill. "
            "TODO: match the existing Claude desktop setup exactly. "
            "Details are not filled in until they are provided. "
            "The Code side of the Claude desktop app is Claude Code, not this entry."
        ),
        helper_note="answers from connected folders",
    ),
    Tool(
        key="gemini-cli",
        display="Gemini CLI",
        default_agent_name="gemini",
        can_drive=True,
        can_run_commands=True,
        can_write_files=True,
        join_method="gemini_md",
        hooks="todo",
        read_only_lock="todo",
        detect=(),
        install_paths=(
            _skill("user", ".gemini/skills/agent-coop/SKILL.md"),
            _section("gemini_md", "GEMINI.md"),
        ),
        status="untested",
        notes=(
            "User skill plus a project GEMINI.md section. "
            "Runs on this computer and calls a model in Google's cloud. "
            "TODO: Gemini CLI hook format is not verified, so hooks are not installed. "
            "TODO: detection paths per OS are not confirmed."
        ),
        helper_note="second opinion on a plan",
    ),
    Tool(
        key="copilot",
        display="GitHub Copilot",
        default_agent_name="copilot",
        can_drive=True,
        can_run_commands=True,
        can_write_files=True,
        join_method="agents_md",
        hooks="todo",
        read_only_lock="todo",
        detect=(),
        install_paths=(_section("agents_md", "AGENTS.md"),),
        status="planned",
        notes=(
            "Agent mode in VS Code. "
            "TODO: verify the skill path. Until then, install uses an AGENTS.md section."
        ),
        helper_note="reviews code in the editor",
    ),
    Tool(
        key="opencode",
        display="OpenCode",
        default_agent_name="opencode",
        can_drive=True,
        can_run_commands=True,
        can_write_files=True,
        join_method="agents_md",
        hooks="todo",
        read_only_lock="todo",
        detect=(),
        install_paths=(_section("agents_md", "AGENTS.md"),),
        status="planned",
        notes=(
            "Can use a local model through Ollama or LM Studio. "
            "TODO: verify the skill path. Until then, install uses an AGENTS.md section."
        ),
        helper_note="local coding help",
    ),
    Tool(
        key="cline",
        display="Cline",
        default_agent_name="cline",
        can_drive=True,
        can_run_commands=True,
        can_write_files=True,
        join_method="agents_md",
        hooks="todo",
        read_only_lock="todo",
        detect=(),
        install_paths=(_section("agents_md", "AGENTS.md"),),
        status="planned",
        notes="TODO: verify how Cline loads a skill or rule. Status planned.",
        helper_note="editor coding help",
    ),
    Tool(
        key="goose",
        display="Goose",
        default_agent_name="goose",
        can_drive=True,
        can_run_commands=True,
        can_write_files=True,
        join_method="agents_md",
        hooks="todo",
        read_only_lock="todo",
        detect=(),
        install_paths=(_section("agents_md", "AGENTS.md"),),
        status="planned",
        notes=(
            "A fully local driver can use an Ollama model. "
            "TODO: verify the skill path. Status planned."
        ),
        helper_note="local coding help",
    ),
    Tool(
        key="runner",
        display="Local model runner",
        default_agent_name="ollama_model",
        can_drive=False,
        can_run_commands=True,
        can_write_files=True,
        join_method="runner",
        hooks="none",
        read_only_lock="advisory",
        detect=(),
        install_paths=(),
        status="untested",
        notes=(
            "Ollama is the default local runtime. LM Studio uses the same runner "
            "with an OpenAI-compatible API. The runner is a helper only."
        ),
        helper_note="answers from a local model",
    ),
    Tool(
        key="ollama",
        display="Ollama",
        default_agent_name="ollama_model",
        can_drive=False,
        can_run_commands=True,
        can_write_files=True,
        join_method="runner",
        hooks="none",
        read_only_lock="advisory",
        detect=(),
        install_paths=(),
        status="untested",
        notes=(
            "Default local model runtime. Registered agents use tool key runner "
            "and a name like ollama_llava. "
            "TODO: detection paths per OS are not confirmed. Setup can still use "
            "a host URL of http://localhost:11434 when you name the model."
        ),
        helper_note="answers from a local model",
    ),
    Tool(
        key="lmstudio",
        display="LM Studio",
        default_agent_name="lmstudio_model",
        can_drive=False,
        can_run_commands=True,
        can_write_files=True,
        join_method="runner",
        hooks="none",
        read_only_lock="advisory",
        detect=(),
        install_paths=(),
        status="untested",
        notes=(
            "The chat window has no file tools without MCP, so it is not a member. "
            "Only the local server is used, through the OpenAI-compatible API. "
            "TODO: detection paths per OS are not confirmed."
        ),
        helper_note="answers from a local model",
    ),
    Tool(
        key="chatgpt-web",
        display="ChatGPT on the web",
        default_agent_name="",
        can_drive=False,
        can_run_commands=False,
        can_write_files=False,
        join_method="none",
        hooks="none",
        read_only_lock="none",
        detect=(),
        install_paths=(),
        status="planned",
        notes="Not supported. Web chat cannot read files on this computer.",
        supported=False,
    ),
    Tool(
        key="claude-web",
        display="Claude on the web",
        default_agent_name="",
        can_drive=False,
        can_run_commands=False,
        can_write_files=False,
        join_method="none",
        hooks="none",
        read_only_lock="none",
        detect=(),
        install_paths=(),
        status="planned",
        notes="Not supported. Web chat cannot read files on this computer.",
        supported=False,
    ),
)

_BY_KEY = {tool.key: tool for tool in TOOLS}

_NAME_SAFE = re.compile(r"[^a-z0-9_]+")


def all_tools() -> tuple[Tool, ...]:
    """Return every tool entry, including ones that cannot join."""
    return TOOLS


def supported_tools() -> tuple[Tool, ...]:
    """Return tools a person can connect."""
    return tuple(tool for tool in TOOLS if tool.supported and tool.join_method != "none")


def get_tool(key: str) -> Tool:
    """Return one tool entry or raise KeyError."""
    return _BY_KEY[key]


def tool_can_drive(key: str) -> bool:
    """Return True when this tool may be a project driver."""
    tool = _BY_KEY.get(key)
    if tool is None:
        return False
    return tool.can_drive


def sanitize_agent_name(prefix: str, model: str) -> str:
    """Build an agent name from a runtime prefix and a model id."""
    raw = (prefix + "_" + model).lower().replace("-", "_").replace(".", "_")
    raw = _NAME_SAFE.sub("_", raw).strip("_")
    if not raw or not raw[0].isalpha():
        raw = "model_" + raw
    raw = raw[:32].strip("_")
    if len(raw) < 2:
        raw = (raw + "_agent")[:32]
    return raw
