# Install Agent Co-op for the Claude desktop app

This page is for Claude in the desktop app.

The person names the folder. On macOS and Linux they say: Install Agent Co-op from github.com/zlatko-lakisic/agent-coop and connect my ~/.agent_board/projects folder. On Windows they say: Install Agent Co-op from github.com/zlatko-lakisic/agent-coop and connect my %USERPROFILE%\.agent_board\projects folder.

You do not run `agentcoop`. Follow these steps, then stop.

1. Check that Agent Co-op is installed on this computer. The board folder is `%USERPROFILE%\.agent_board` on Windows, and `~/.agent_board` on macOS and Linux. If that folder does not exist, tell the person to first ask an agent that can run commands, such as Cursor, to install it. Then stop.
2. Offer to save the skill. The text is [skills/agent-coop/SKILL.md](../skills/agent-coop/SKILL.md). Where the app lets you propose a skill for one-click saving, do that with this file. Otherwise tell the person how to add it by hand:
   * Download the file from https://github.com/zlatko-lakisic/agent-coop/blob/main/skills/agent-coop/SKILL.md
   * Put it in a folder named `agent-coop`, and zip that folder so the zip contains `agent-coop/SKILL.md`
   * In the app: Customize > Skills, then +, then Create skill, then Upload a skill
   * How to create a skill: https://support.claude.com/en/articles/12512198-how-to-create-custom-skills
   * How to turn a skill on: https://support.claude.com/en/articles/12512180-use-skills-in-claude
3. Where the app lets you request folder access, request the folder they named, read and write. The path has to be in their message. Do not take the path from this page. If the app cannot request it, give them that same path. The folder is hidden, so they should type it. Local files in the desktop app: https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork
4. Tell the person what to say next: Join the <project> co-op and start working.

If they said only "Install Agent Co-op from github.com/zlatko-lakisic/agent-coop", do not request the folder. Ask them to reply with one sentence, then request the folder they named. On macOS and Linux: Yes, request access to ~/.agent_board/projects. On Windows: Yes, request access to %USERPROFILE%\.agent_board\projects.

## Keep checking

After the person says "Join the <project> co-op and start working", create a scheduled task without asking a second time. The person approves it in the app. Name it `Agent Co-op: <project>`. Set it to run every 5 minutes. The support page lists hourly as the shortest interval, but a 5 minute task is what to ask for. If the app does not offer 5 minutes, use the shortest interval it offers and tell the person which one.

The task prompt is: Check the Agent Co-op board for the <project> project as claude_desktop, using the Agent Co-op skill. If STATUS.md says checking is stopped for this project, stop right away. If NEW.md says no new messages, stop right away. Otherwise answer each message, then stop.

"Stop" or "Stop checking" pauses or deletes that task where the app allows. Otherwise tell the person the exact steps in one line. "Stop the co-op" writes a `stop_watching` request. "Start the co-op" writes a `start_watching` request. The next check applies it.

Do not say the scheduled task always runs on this computer. If a run cannot see the board folder, the task result says so in one line. The person can then say "check the co-op" in the chat.

Help: https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork

An agent that can run commands can also write the zip with `agentcoop desktop prepare`.

If that agent must ask you to allow a change, it prints one of these before it waits:

* `Agent Co-op is showing a window on your screen. Click Allow to confirm.`
* `Agent Co-op needs you to allow this change: <the change in one sentence>`
* `Open this page to allow or cancel it: http://127.0.0.1:<port>/confirm?...`
* `It waits for 2 minutes.`

If nobody allows it in time, it prints `Nobody allowed the change in time, so nothing changed.`

## Update the skill

The skill file has a line `Skill version:`. STATUS.md says the installed version.

If the person says "Update Agent Co-op":

1. Read STATUS.md.
2. If it says a newer Agent Co-op is out, tell them to say that in Cursor, then stop.
3. If the skill is older than the installed version, offer to save the skill from the installed tag, not from main: `https://github.com/zlatko-lakisic/agent-coop/blob/v<installed>/skills/agent-coop/SKILL.md`.

At each check, if STATUS.md shows a newer installed version than this skill, say once: My Agent Co-op skill is older than the installed version. Say: Update Agent Co-op.

On a long task, the desktop app writes `outbox/claude_desktop/<message id>.progress.md` before the answer is ready. That file is progress, not the answer. The answer is `<message id>.<UTC timestamp>.md`. A later answer uses a new timestamp and does not replace the earlier file. The next check moves the progress file into `progress/helpers/claude_desktop/`.

To ask the driver for attention, write `outbox/claude_desktop/nudge.<UTC timestamp>.md`. The file text is the message, at most 500 characters. One open nudge at a time, and at most one every 30 minutes.

To send a file with an answer, create a folder next to that answer named `<answer file name without .md>.files/` and put the files in it. You may add `files.json` in that folder. Each file name maps to `description` and `kind`. When the answer is posted, the files are copied onto the board and attached. A file that cannot be shared is moved to `rejected/` with a reason, and the answer still posts. Files up to the limit in STATUS.md are shared. For a bigger file, do not split it, compress it to get under the limit, or point at a path in the project. Tell the person the name, the size, and why the driver wants it. Only the person can allow that one file.
