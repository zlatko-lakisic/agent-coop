# Cursor

Agent name: `cursor`

Can drive: yes

Joins with: rule

Project files for: members

Hooks: todo

Read-only lock: todo

Background runs: yes

Keeps checking: background task

Status: untested

Project rule only (loads every turn). TODO: hook format for Cursor is not verified, so hooks are not installed. TODO: detection paths per OS are not confirmed. The command is agent, installed into ~/.local/bin. cursor-agent is the older name. https://cursor.com/docs/cli/installation Flags confirmed at https://cursor.com/docs/cli/reference/parameters : -p, --trust, --workspace, --model, --mode ask. Scheduled runs never pass --force, -f, or --yolo. Before a driver run, Agent Co-op writes <worktree>/.cursor/cli.json so the agent may read broadly, write only the worktree and the board, and run git, agentcoop, and the commands in driver_shell_allow. Before a helper run, it writes <board>/projects/<project>/.cursor/cli.json with Read(**) allowed and Write(**) and Shell(*) denied. Permissions: https://cursor.com/docs/cli/reference/permissions A person's ~/.cursor/cli-config.json may also hold permissions. How the two files combine is not documented, so keep broad allow rules out of the global file. Sign in with agent login. https://cursor.com/docs/cli/reference/parameters After Start working, Cursor starts agentcoop watch in the background. A finished background command wakes the agent. Cursor waits about 30 seconds for a foreground command, then moves it to the background. It does not kill that command. Shell Mode in the CLI kills a command at 30 seconds and that limit cannot be changed: https://cursor.com/docs/cli/shell-mode The default watch wait is 10 minutes, which fits because the command runs in the background. There is no documented cap on how many steps one desktop turn may take. If a turn ends and watch is not running, the person says Start working. Headless agent -p can abort leftover background commands after the turn when you pass --background-shell-timeout. https://cursor.com/docs/cli/changelog Terminal tool: https://cursor.com/docs/agent/tools/terminal

See AGENTS-SUPPORT.md for the full table. Open questions there are not guesses.
