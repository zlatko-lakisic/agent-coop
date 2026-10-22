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
- Background runs: yes
- Status: untested
- Suggested helper note: edits the project and asks helpers
- Notes: Project rule only (loads every turn). TODO: hook format for Cursor is not verified, so hooks are not installed. TODO: detection paths per OS are not confirmed. The command is agent, installed into ~/.local/bin. cursor-agent is the older name. https://cursor.com/docs/cli/installation Flags confirmed at https://cursor.com/docs/cli/reference/parameters : -p, --trust, --workspace, --model, --mode ask. Scheduled runs never pass --force, -f, or --yolo. Before a driver run, Agent Co-op writes <worktree>/.cursor/cli.json so the agent may read broadly, write only the worktree and the board, and run git, agentcoop, and the commands in driver_shell_allow. Before a helper run, it writes <board>/projects/<project>/.cursor/cli.json with Read(**) allowed and Write(**) and Shell(*) denied. Permissions: https://cursor.com/docs/cli/reference/permissions A person's ~/.cursor/cli-config.json may also hold permissions. How the two files combine is not documented, so keep broad allow rules out of the global file. Sign in with agent login. https://cursor.com/docs/cli/reference/parameters

## Claude Code

- Agent name: `claude_code`
- Can drive: yes
- Joins with: skill
- Project files for: driver
- Hooks: supported
- Read-only lock: todo
- Background runs: yes
- Status: untested
- Suggested helper note: reviews code
- Notes: User skill and user hooks only. SessionStart and UserPromptSubmit run check --summary, which stays quiet outside projects. TODO: exact permission keys for a read-only lock are not confirmed. TODO: detection paths per OS are not confirmed. The Code side of the Claude desktop app uses this entry. Sign in with claude auth login (https://code.claude.com/docs/en/cli-reference). Background flags confirmed at https://code.claude.com/docs/en/cli-reference : -p, --model, --tools, --permission-mode plan and dontAsk, --add-dir, --allowedTools, --disallowedTools. Helper runs are plan mode with Read, Grep, and Glob only. Driver runs use dontAsk and --add-dir for the board.

## Codex CLI

- Agent name: `codex`
- Can drive: yes
- Joins with: skill
- Project files for: driver
- Hooks: todo
- Read-only lock: todo
- Background runs: yes
- Status: untested
- Suggested helper note: answers coding questions
- Notes: User skill only. TODO: Codex hook format is not verified, so hooks are not installed. TODO: exact sandbox config keys for a read-only lock are not confirmed. TODO: detection paths per OS are not confirmed. Sign in with codex login (https://developers.openai.com/codex/cli/reference). Background flags confirmed at https://developers.openai.com/codex/cli/reference : exec, --sandbox read-only and workspace-write, --cd, --skip-git-repo-check, --model, --ask-for-approval never, --add-dir. workspace-write has network off unless sandbox_workspace_write.network_access is set (https://github.com/openai/codex/blob/13c42a077c88a0d04ae7680a9891d2daf4558577/docs/sandbox.md).

## ChatGPT desktop (Codex)

- Agent name: `chatgpt_codex`
- Can drive: yes
- Joins with: agents_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Background runs: yes
- Status: untested
- Suggested helper note: architecture questions
- Notes: The ChatGPT desktop app on macOS and Windows includes Codex. It does not read ~/.codex/config.toml (only the CLI does). TODO: confirm whether it reads .codex/skills/. TODO: detection paths per OS are not confirmed. Background runs only when the codex command is installed. Otherwise it answers when you open the ChatGPT desktop app.

## ChatGPT Work

- Agent name: `chatgpt_work`
- Can drive: no
- Joins with: folder_grant
- Project files for: driver
- Hooks: none
- Read-only lock: advisory
- Background runs: no
- Status: planned
- Suggested helper note: reads the board when folder access is granted
- Notes: Observer, planned. The user grants ~/.agent_board/ and the project folder in the app, then pastes the skill into project instructions. TODO: confirm whether ChatGPT Work can write to granted local folders. Until then it cannot reply or mark done. Answers when you open the app.

## Claude desktop

- Agent name: `claude_desktop`
- Can drive: no
- Joins with: outbox
- Project files for: driver
- Hooks: none
- Read-only lock: advisory
- Background runs: no
- Status: untested
- Suggested helper note: helps the driver reason
- Notes: Helper in the Claude desktop app. It does not run agentcoop. It writes a join or leave request, then reads NEW.md and writes answers. agentcoop applies requests and answers on the next check or background round. Install from chat with docs/install-claude-desktop.md after Agent Co-op is on the computer. The skill in the repo is skills/agent-coop/SKILL.md. agentcoop desktop prepare writes the upload zip. The Code side of the Claude desktop app is Claude Code, not this entry. TODO: detection paths per OS are not confirmed.

## Gemini CLI

- Agent name: `gemini`
- Can drive: yes
- Joins with: gemini_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Background runs: yes
- Status: untested
- Suggested helper note: second opinion on a plan
- Notes: User skill plus a project GEMINI.md section. Runs on this computer and calls a model in Google's cloud. TODO: Gemini CLI hook format is not verified, so hooks are not installed. TODO: detection paths per OS are not confirmed. Sign in by running gemini and choosing Login with Google (https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html). Helper flag confirmed at https://google-gemini.github.io/gemini-cli/docs/cli/headless.html : -p, --model, --approval-mode plan (read only). TODO: driver runs are not scheduled. --approval-mode auto_edit still asks before shell commands, and no confirmed flag adds the board as a writable folder.

## GitHub Copilot

- Agent name: `copilot`
- Can drive: yes
- Joins with: agents_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Background runs: no
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
- Background runs: todo
- Status: planned
- Suggested helper note: local coding help
- Notes: Can use a local model through Ollama or LM Studio. TODO: verify the skill path. Until then, install uses an AGENTS.md section. TODO: detection paths per OS are not confirmed. TODO: opencode run and --auto exist, but docs say permissions are not a sandbox and shell still runs with host authority. Not scheduled until a confirmed way exists to refuse writes outside the working folder.

## Cline

- Agent name: `cline`
- Can drive: yes
- Joins with: agents_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Background runs: no
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
- Background runs: todo
- Status: planned
- Suggested helper note: local coding help
- Notes: A fully local driver can use an Ollama model. TODO: verify the skill path. Status planned. TODO: detection paths per OS are not confirmed. TODO: goose run exists, but a one-shot mode with permission controls that refuse writes outside the working folder is not confirmed.

## Local model runner

- Agent name: `ollama_model`
- Can drive: no
- Joins with: runner
- Project files for: driver
- Hooks: none
- Read-only lock: advisory
- Background runs: yes
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
- Background runs: yes
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
- Background runs: yes
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
- Background runs: no
- Status: planned
- Notes: Not supported. Web chat cannot read files on this computer.

## Claude on the web

- Agent name: not supported
- Can drive: no
- Joins with: none
- Project files for: driver
- Hooks: none
- Read-only lock: none
- Background runs: no
- Status: planned
- Notes: Not supported. Web chat cannot read files on this computer.

## Open questions

1. Whether ChatGPT Work can write to granted local folders.
2. Whether the ChatGPT desktop app's Codex side reads .codex/skills/.
3. Skill paths for GitHub Copilot, OpenCode, Cline, and Goose.
4. Current hook formats for Codex, Gemini CLI, and Cursor.
5. Read-only permission settings per tool (exact config keys).
6. Detection paths for each app on each OS.
