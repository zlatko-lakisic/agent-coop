# Supported agents

Generated from src/agentcoop/tools.py. Do not edit by hand.

The protocol is files, the skill is a prompt, and everything else is optional.
Web chat cannot join, because it cannot read files on this computer.

## Cursor

- Agent name: `cursor`
- Can drive: yes
- Joins with: rule
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Status: untested
- Suggested helper note: edits the project and asks helpers
- Notes: Project rule only (loads every turn). TODO: hook format for Cursor is not verified, so hooks are not installed. TODO: detection paths per OS are not confirmed.

## Claude Code

- Agent name: `claude_code`
- Can drive: yes
- Joins with: skill
- Project files for: driver
- Hooks: supported
- Read-only lock: todo
- Status: untested
- Suggested helper note: reviews code
- Notes: User skill and user hooks only. SessionStart and UserPromptSubmit run check --summary, which stays quiet outside projects. TODO: exact permission keys for a read-only lock are not confirmed. TODO: detection paths per OS are not confirmed. The Code side of the Claude desktop app uses this entry.

## Codex CLI

- Agent name: `codex`
- Can drive: yes
- Joins with: skill
- Project files for: driver
- Hooks: todo
- Read-only lock: todo
- Status: untested
- Suggested helper note: answers coding questions
- Notes: User skill only. TODO: Codex hook format is not verified, so hooks are not installed. TODO: exact sandbox config keys for a read-only lock are not confirmed. TODO: detection paths per OS are not confirmed.

## ChatGPT desktop (Codex)

- Agent name: `chatgpt_codex`
- Can drive: yes
- Joins with: agents_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Status: untested
- Suggested helper note: architecture questions
- Notes: The ChatGPT desktop app on macOS and Windows includes Codex. It does not read ~/.codex/config.toml (only the CLI does). TODO: confirm whether it reads .codex/skills/. TODO: detection paths per OS are not confirmed.

## ChatGPT Work

- Agent name: `chatgpt_work`
- Can drive: no
- Joins with: folder_grant
- Project files for: driver
- Hooks: none
- Read-only lock: advisory
- Status: planned
- Suggested helper note: reads the board when folder access is granted
- Notes: Observer, planned. The user grants ~/.agent_board/ and the project folder in the app, then pastes the skill into project instructions. TODO: confirm whether ChatGPT Work can write to granted local folders. Until then it cannot reply or mark done.

## Claude desktop

- Agent name: `claude_desktop`
- Can drive: no
- Joins with: folder_grant
- Project files for: driver
- Hooks: none
- Read-only lock: advisory
- Status: planned
- Suggested helper note: answers from connected folders
- Notes: Connected folders plus a skill. TODO: match the existing Claude desktop setup exactly. Details are not filled in until they are provided. The Code side of the Claude desktop app is Claude Code, not this entry. TODO: detection paths per OS are not confirmed.

## Gemini CLI

- Agent name: `gemini`
- Can drive: yes
- Joins with: gemini_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Status: untested
- Suggested helper note: second opinion on a plan
- Notes: User skill plus a project GEMINI.md section. Runs on this computer and calls a model in Google's cloud. TODO: Gemini CLI hook format is not verified, so hooks are not installed. TODO: detection paths per OS are not confirmed.

## GitHub Copilot

- Agent name: `copilot`
- Can drive: yes
- Joins with: agents_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Status: planned
- Suggested helper note: reviews code in the editor
- Notes: Agent mode in VS Code. TODO: verify the skill path. Until then, install uses an AGENTS.md section.

## OpenCode

- Agent name: `opencode`
- Can drive: yes
- Joins with: agents_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Status: planned
- Suggested helper note: local coding help
- Notes: Can use a local model through Ollama or LM Studio. TODO: verify the skill path. Until then, install uses an AGENTS.md section. TODO: detection paths per OS are not confirmed.

## Cline

- Agent name: `cline`
- Can drive: yes
- Joins with: agents_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Status: planned
- Suggested helper note: editor coding help
- Notes: TODO: verify how Cline loads a skill or rule. Status planned.

## Goose

- Agent name: `goose`
- Can drive: yes
- Joins with: agents_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Status: planned
- Suggested helper note: local coding help
- Notes: A fully local driver can use an Ollama model. TODO: verify the skill path. Status planned. TODO: detection paths per OS are not confirmed.

## Local model runner

- Agent name: `ollama_model`
- Can drive: no
- Joins with: runner
- Project files for: driver
- Hooks: none
- Read-only lock: advisory
- Status: untested
- Suggested helper note: answers from a local model
- Notes: Ollama is the default local runtime. LM Studio uses the same runner with an OpenAI-compatible API. The runner is a helper only.

## Ollama

- Agent name: `ollama_model`
- Can drive: no
- Joins with: runner
- Project files for: driver
- Hooks: none
- Read-only lock: advisory
- Status: untested
- Suggested helper note: answers from a local model
- Notes: Default local model runtime. Registered agents use tool key runner and a name like ollama_llava. TODO: detection paths per OS are not confirmed. Setup can still use a host URL of http://localhost:11434 when you name the model.

## LM Studio

- Agent name: `lmstudio_model`
- Can drive: no
- Joins with: runner
- Project files for: driver
- Hooks: none
- Read-only lock: advisory
- Status: untested
- Suggested helper note: answers from a local model
- Notes: The chat window has no file tools without MCP, so it is not a member. Only the local server is used, through the OpenAI-compatible API. TODO: detection paths per OS are not confirmed.

## ChatGPT on the web

- Agent name: not supported
- Can drive: no
- Joins with: none
- Project files for: driver
- Hooks: none
- Read-only lock: none
- Status: planned
- Notes: Not supported. Web chat cannot read files on this computer.

## Claude on the web

- Agent name: not supported
- Can drive: no
- Joins with: none
- Project files for: driver
- Hooks: none
- Read-only lock: none
- Status: planned
- Notes: Not supported. Web chat cannot read files on this computer.

## Open questions

1. Exact setup of the existing Claude desktop workflow. The Claude desktop entry stays unfinished until those details are provided.
2. Whether ChatGPT Work can write to granted local folders.
3. Whether the ChatGPT desktop app's Codex side reads .codex/skills/.
4. Skill paths for GitHub Copilot, OpenCode, Cline, and Goose.
5. Current hook formats for Codex, Gemini CLI, and Cursor.
6. Read-only permission settings per tool (exact config keys).
7. Detection paths for each app on each OS.
