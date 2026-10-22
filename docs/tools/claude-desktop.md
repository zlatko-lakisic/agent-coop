# Claude desktop

Agent name: `claude_desktop`

Can drive: no

Claude desktop is a helper. It does not run `agentcoop`. It writes files, and `agentcoop` applies them at the next check by any agent in the project, or at the next background round.

The Code side of the Claude desktop app is Claude Code, not this helper.

## Setup from chat

In the Claude desktop app, say: Install Agent Co-op from github.com/zlatko-lakisic/agent-coop.

Claude follows [install-claude-desktop.md](../install-claude-desktop.md). Agent Co-op must already be on the computer. The first install is done by an agent that can run commands, such as Cursor.

The skill in the repo is [skills/agent-coop/SKILL.md](../../skills/agent-coop/SKILL.md). `agentcoop desktop prepare` writes the same skill as `agent-coop.zip` for people who prefer to upload a file.

## Joining

In that Claude project, say: Join the <project> co-op and start working.

The skill writes `<board>/projects/<project>/requests/claude_desktop/<UTC timestamp>.json`:

```json
{"action": "join"}
```

Leave uses `{"action": "leave"}`. A note is optional.

`agentcoop` applies the request on the next `check` by any member, and on every `tick`. A file younger than 5 seconds is left for the next round. Any other action is moved to `requests/claude_desktop/rejected/` because it needs a person.

The driver's next check says `claude_desktop joined as a helper.`

## STATUS.md

`agentcoop` writes `<board>/projects/STATUS.md` on every tick, check, send, and membership change. It lists each project, whether the schedule is on, the last round, and the latest progress headline. The skill reads that file and summarizes it.

## Working

The skill reads `NEW.md`, the context, and the progress. It writes answers in `outbox/claude_desktop/`. It uses `NEED CONTEXT:` when background is missing. It checks `rejected/`.

If an answer needs the project's own files, the skill asks the person to grant that folder, read only.

## Limit

A request does not take effect when it is written. It takes effect at the next check by any agent, or the next background round.

`agentcoop desktop prompt --project frontend` prints a prompt you can paste into a scheduled task.

## Scheduled tasks

Scheduled tasks in the Claude app: the shortest documented schedule is hourly, and new tasks may run in the cloud, where they cannot see files on your computer. If your tasks run in the cloud, tell the app "check the co-op" yourself, from your computer or your phone, when you want it to answer.

Help: https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork

## Confirmation

When an agent that can run Agent Co-op needs you to allow a change, and it has no terminal, it prints before it waits:

* `Agent Co-op is showing a window on your screen. Click Allow to confirm.`
* Or `Agent Co-op needs you to allow this change: <the change in one sentence>`, then `Open this page to allow or cancel it: http://127.0.0.1:<port>/confirm?...`, then `It waits for 2 minutes.`

If nobody allows it in time, it prints `Nobody allowed the change in time, so nothing changed.`

## Terminal

`agentcoop desktop add frontend` still asks you to confirm, then prepares the skill zip and joins `claude_desktop`. `agentcoop desktop remove frontend` removes the helper. Posted answers in `outbox/claude_desktop/sent/` stay.

## Remove it from chat

Say: Leave this project.

That writes a leave request. Files you edited stay in place.
