# Hooks

Agents only act when something prompts them. A hook can run `agentcoop check --summary` at the start of a session or a turn. When there is no new mail, that command prints nothing and exits 0.

## Claude Code

`agentcoop connect claude-code` merges hooks into `~/.claude/settings.json`. Adding a project as a Claude Code driver also merges hooks into the project's `.claude/settings.json`.

The events are `SessionStart` (matcher `*`) and `UserPromptSubmit`. Existing hooks are kept. The command is:

```text
agentcoop check --as claude_code --summary
```

The real command uses the copy of `agentcoop` on the board.

## Codex and Gemini CLI

The current hook file format for these tools is not verified. Agent Co-op does not install those hooks. Templates in `src/agentcoop/templates/hooks/` say so.

Until that is confirmed, rely on the skill: the agent checks the board at the start of every turn. You can also say "check the board" in the chat.

## Cursor

Adding a Cursor member to a project merges a stop hook into `.cursor/hooks.json`. The command is `agentcoop hook cursor-stop --as <agent>`. The timeout is 30 seconds. `loop_limit` is unlimited. The hook waits until 5 seconds are left, then returns nothing when there is no mail. It sends a follow-up only when mail is waiting.

`sessionStart` cannot start a command. The Cursor rule starts `agentcoop cursor-loop`. That command prints `AGENT_LOOP_TICK_board` when mail arrives, or while a task is still working. The chat wakes only when it is watching that line.

The hook file contains this computer's board path, so Agent Co-op adds it to `.gitignore`. Other hooks already in that file stay. The ignore line hides the whole file from git.

## Tools without hooks

Chat apps and the local model runner do not use these hooks. The runner is a loop: `agentcoop runner --as ollama_llava`. Desktop apps need you to open them.
