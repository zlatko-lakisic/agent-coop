# Install Agent Co-op for the Claude desktop app

This page is for Claude in the desktop app. The person said: Install Agent Co-op from github.com/zlatko-lakisic/agent-coop.

You do not run `agentcoop`. Follow these steps, then stop.

1. Check that Agent Co-op is installed on this computer. The board folder is `%USERPROFILE%\.agent_board` on Windows, and `~/.agent_board` on macOS and Linux. If that folder does not exist, tell the person to first ask an agent that can run commands, such as Cursor, to install it. Then stop.
2. Offer to save the skill. The text is [skills/agent-coop/SKILL.md](../skills/agent-coop/SKILL.md). Where the app lets you propose a skill for one-click saving, do that with this file. Otherwise tell the person how to add it by hand:
   * Download the file from https://github.com/zlatko-lakisic/agent-coop/blob/main/skills/agent-coop/SKILL.md
   * Put it in a folder named `agent-coop`, and zip that folder so the zip contains `agent-coop/SKILL.md`
   * In the app: Customize > Skills, then +, then Create skill, then Upload a skill
   * How to create a skill: https://support.claude.com/en/articles/12512198-how-to-create-custom-skills
   * How to turn a skill on: https://support.claude.com/en/articles/12512180-use-skills-in-claude
3. Ask for access to `<board>/projects` (read and write). Where the app lets you request folder access, request it. Otherwise give the person the exact path. The folder is hidden, so they should type it. Local files in the desktop app: https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork
4. Tell the person what to say next: Join the <project> co-op and start working.

An agent that can run commands can also write the zip with `agentcoop desktop prepare`.

If that agent must ask you to allow a change, it prints one of these before it waits:

* `Agent Co-op is showing a window on your screen. Click Allow to confirm.`
* `Agent Co-op needs you to allow this change: <the change in one sentence>`
* `Open this page to allow or cancel it: http://127.0.0.1:<port>/confirm?...`
* `It waits for 2 minutes.`

If nobody allows it in time, it prints `Nobody allowed the change in time, so nothing changed.`
