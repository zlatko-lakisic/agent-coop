# Cursor

Agent name: `cursor`

Can drive: yes

Joins with: rule

Project files for: members

Hooks: supported

Read-only lock: todo

Background runs: yes

Keeps checking: background task

Status: untested

Project rule, plus a project stop hook in .cursor/hooks.json. The hook runs agentcoop hook cursor-stop. The default timeout is 600 seconds, from cursor_hook_timeout_seconds, and 3600 is the longest Cursor accepts without a warning. It waits until 5 seconds are left. If mail is waiting, it sends that mail. If the wait ends with no mail, it sends only: End your turn. loop_limit is unlimited so mail can keep waking the chat. sessionStart cannot start a command, so the rule starts cursor-loop. A tick wakes the chat only when the agent is watching AGENT_LOOP_TICK_board. A finished background command wakes the agent while a turn is still running. After the turn ends the shell can die, and a Cursor restart kills it, so the stop hook is the other wake. One 60 second tick woke a macOS chat after the turn ended. The Start working flow in an existing Cursor window is untested on macOS and Linux. When the chat is not watching, Cursor waits for Start working. Other tools still run in the background. Say Run the driver in the background when the chat is idle to change Cursor. notify_when_idle is off. A notification is not sent unless that setting is true. TODO: detection paths per OS are not confirmed. The command is agent, installed into ~/.local/bin. cursor-agent is the older name. https://cursor.com/docs/cli/installation Flags confirmed at https://cursor.com/docs/cli/reference/parameters : -p, --trust, --workspace, --model, --mode ask. Scheduled runs never pass --force, -f, or --yolo. Before a driver run, Agent Co-op writes <worktree>/.cursor/cli.json so the agent may read broadly, write only the worktree and the board, and run git, agentcoop, agentcoop.exe, agentcoopw.exe, agentcoop.cmd, and the commands in driver_shell_allow. Before a helper run, it writes <board>/projects/<project>/.cursor/cli.json with Read(**) allowed and Write(**) and Shell(*) denied. Permissions: https://cursor.com/docs/cli/reference/permissions A person's ~/.cursor/cli-config.json may also hold permissions. How the two files combine is not documented, so keep broad allow rules out of the global file. Sign in with agent login. https://cursor.com/docs/cli/reference/parameters After Start working, the Cursor driver starts agentcoop cursor-loop in the background and watches for AGENT_LOOP_TICK_board. Helpers still use agentcoop watch. Cursor waits about 30 seconds for a foreground command, then moves it to the background. It does not kill that command. Shell Mode in the CLI kills a command at 30 seconds and that limit cannot be changed: https://cursor.com/docs/cli/shell-mode The default watch wait is 10 minutes, which fits because the command runs in the background. There is no documented cap on how many steps one desktop turn may take. If a turn ends and cursor-loop is not running, the driver starts it again. The person says Start working only when the driver cannot start it. Connecting Cursor writes <project>/.cursor/permissions.json with agentcoop on terminalAllowlist. The app also reads ~/.cursor/permissions.json. https://cursor.com/docs/reference/permissions A full path does not match the short name, so copy the exact command from a prompt into that file if one still appears. Headless agent -p can abort leftover background commands after the turn when you pass --background-shell-timeout. https://cursor.com/docs/cli/changelog Terminal tool: https://cursor.com/docs/agent/tools/terminal

See AGENTS-SUPPORT.md for the full table. Open questions there are not guesses.
