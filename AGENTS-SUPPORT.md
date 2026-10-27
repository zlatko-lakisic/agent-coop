# Supported agents

Generated from src/agentcoop/tools.py. Do not edit by hand.

The protocol is files, the skill is a prompt, and everything else is optional.
Web chat cannot join, because it cannot read files on this computer.

## Cursor

- Agent name: `cursor`
- Can drive: yes
- Joins with: rule
- Project files for: members
- Hooks: supported
- Read-only lock: todo
- Background runs: yes
- Keeps checking: background task
- Status: untested
- Suggested helper note: edits the project and asks helpers
- Notes: Project rule, plus a project stop hook in .cursor/hooks.json. The hook runs agentcoop hook cursor-stop. A beforeSubmitPrompt hook runs agentcoop hook cursor-prompt and records the new prompt. The default timeout is 3600 seconds, from cursor_hook_timeout_seconds, and 3600 is the longest Cursor accepts without a warning. It waits until 5 seconds are left. If this agent has done anything since the hook started, it sends nothing. If mail is waiting and the chat is idle, it sends that mail and tells the driver to continue. If the wait ends with no mail, it sends: Agent Co-op keep-awake. If you are working on anything, ignore this and keep going. Otherwise end your turn. The transcript shape was checked against Cursor 3.27.14. A user line counts as a new turn only when it has a user_query that is not Cursor's shell-task notice. A user line that is only a timestamp does not. If transcript_path is missing, unreadable, or has no line Agent Co-op recognizes, the trace and agentcoop doctor say: Cursor's transcript format is not recognized. A stale hook may interrupt work. loop_limit is unlimited so mail can keep waking the chat. sessionStart cannot start a command, so the rule starts cursor-loop. A tick wakes the chat only when the agent is watching AGENT_LOOP_TICK_board. A finished background command wakes the agent while a turn is still running. After the turn ends the shell can die, and a Cursor restart kills it, so the stop hook is the other wake. One 60 second tick woke a macOS chat after the turn ended. The Start working flow in an existing Cursor window is untested on macOS and Linux. When the chat is not watching, Cursor waits for Start working. Other tools still run in the background. Say Run the driver in the background when the chat is idle to change Cursor. notify_when_idle is off. A notification is not sent unless that setting is true. TODO: detection paths per OS are not confirmed. The command is agent, installed into ~/.local/bin. cursor-agent is the older name. https://cursor.com/docs/cli/installation Flags confirmed at https://cursor.com/docs/cli/reference/parameters : -p, --trust, --workspace, --model, --mode ask. Scheduled runs never pass --force, -f, or --yolo. Before a driver run, Agent Co-op writes <worktree>/.cursor/cli.json so the agent may read broadly, write only the worktree and the board, and run git, agentcoop, agentcoop.exe, agentcoopw.exe, agentcoop.cmd, and the commands in driver_shell_allow. Before a helper run, it writes <board>/projects/<project>/.cursor/cli.json with Read(**) allowed and Write(**) and Shell(*) denied. Permissions: https://cursor.com/docs/cli/reference/permissions A person's ~/.cursor/cli-config.json may also hold permissions. How the two files combine is not documented, so keep broad allow rules out of the global file. Sign in with agent login. https://cursor.com/docs/cli/reference/parameters After Start working, the Cursor driver starts agentcoop cursor-loop in the background and watches for AGENT_LOOP_TICK_board. Helpers still use agentcoop watch. Cursor waits about 30 seconds for a foreground command, then moves it to the background. It does not kill that command. Shell Mode in the CLI kills a command at 30 seconds and that limit cannot be changed: https://cursor.com/docs/cli/shell-mode The default watch wait is 10 minutes, which fits because the command runs in the background. There is no documented cap on how many steps one desktop turn may take. If a turn ends and cursor-loop is not running, the driver starts it again. The person says Start working only when the driver cannot start it. Connecting Cursor writes <project>/.cursor/permissions.json with agentcoop on terminalAllowlist. The app also reads ~/.cursor/permissions.json. https://cursor.com/docs/reference/permissions A full path does not match the short name, so copy the exact command from a prompt into that file if one still appears. Headless agent -p can abort leftover background commands after the turn when you pass --background-shell-timeout. https://cursor.com/docs/cli/changelog Terminal tool: https://cursor.com/docs/agent/tools/terminal

## Claude Code

- Agent name: `claude_code`
- Can drive: yes
- Joins with: skill
- Project files for: driver
- Hooks: supported
- Read-only lock: todo
- Background runs: yes
- Keeps checking: watch loop
- Status: untested
- Suggested helper note: reviews code
- Notes: User skill and user hooks only. SessionStart and UserPromptSubmit run check --summary, which stays quiet outside projects. TODO: exact permission keys for a read-only lock are not confirmed. TODO: detection paths per OS are not confirmed. It keeps checking with agentcoop watch until the person says stop. The Code side of the Claude desktop app uses this entry. Sign in with claude auth login (https://code.claude.com/docs/en/cli-reference). Background flags confirmed at https://code.claude.com/docs/en/cli-reference : -p, --model, --tools, --permission-mode plan and dontAsk, --add-dir, --allowedTools, --disallowedTools. Helper runs are plan mode with Read, Grep, and Glob only. Driver runs use dontAsk and --add-dir for the board.

## Codex CLI

- Agent name: `codex`
- Can drive: yes
- Joins with: skill
- Project files for: driver
- Hooks: todo
- Read-only lock: todo
- Background runs: yes
- Keeps checking: watch loop
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
- Keeps checking: watch loop
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
- Keeps checking: none
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
- Keeps checking: scheduled task
- Status: untested
- Suggested helper note: helps the driver reason
- Notes: Helper in the Claude desktop app. It does not run agentcoop. It writes a join or leave request, then reads NEW.md and writes answers. agentcoop applies requests and answers on the next check or background round. Install from chat with docs/install-claude-desktop.md after Agent Co-op is on the computer. The skill in the repo is skills/agent-coop/SKILL.md. agentcoop desktop prepare writes the upload zip. The Code side of the Claude desktop app is Claude Code, not this entry. After Start working it creates a scheduled task named Agent Co-op: <project> every 5 minutes. Stop or Stop checking pauses that task. Stop the co-op writes a stop_watching request. A scheduled task does not always run on this computer. TODO: detection paths per OS are not confirmed.

## Gemini CLI

- Agent name: `gemini`
- Can drive: yes
- Joins with: gemini_md
- Project files for: members
- Hooks: todo
- Read-only lock: todo
- Background runs: yes
- Keeps checking: watch loop
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
- Keeps checking: none
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
- Keeps checking: none
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
- Keeps checking: none
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
- Keeps checking: none
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
- Keeps checking: none
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
- Keeps checking: none
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
- Keeps checking: none
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
- Keeps checking: none
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
- Keeps checking: none
- Status: planned
- Notes: Not supported. Web chat cannot read files on this computer.

## Open questions

1. Whether ChatGPT Work can write to granted local folders.
2. Whether the ChatGPT desktop app's Codex side reads .codex/skills/.
3. Skill paths for GitHub Copilot, OpenCode, Cline, and Goose.
4. Current hook formats for Codex and Gemini CLI.
5. Read-only permission settings per tool (exact config keys).
6. Detection paths for each app on each OS.
