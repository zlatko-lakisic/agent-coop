# Why the Cursor driver stops

This page is the record of the 0.2.8 investigation. It uses a separate test board. It does not use a real project.

## What was tested

The scripted CLI scenarios were not run. `agent` and `cursor-agent` are not installed on this PC, and installing them was not allowed. The fake driver still covers s3, s4, s10, and s12 in CI.

A real Cursor app chat on this project ran a temporary stop hook. The command was Python, timeout 600, `loop_limit` null. It did not use the Comstar project or the real board.

## Stop hook limits

Measured in this app chat on 2026-10-25, and checked against the installed Cursor app.

| Question | Result |
| --- | --- |
| Largest timeout Cursor accepts | Any positive number of seconds. The app warns above 3600 and does not reject it. If the field is omitted, the app uses 60. The kill timer is that many seconds. Docs: https://cursor.com/docs/hooks |
| Did a 600 second timeout survive a 4 minute wait? | Yes. Mail written at 240 seconds was seen at 240.1 seconds, and the chat continued by itself. |
| Is the chat or the input blocked while the hook waits? | The turn had already ended. The next line arrived as a new message after the wait. The installed app does not wait for the hook before it marks the turn finished, and the follow-up does not clear text already in the box. A person was not asked to type during the wait. |
| Does `loop_limit: null` allow more than 5 follow-ups? | Yes. The hook sent 10 follow-ups (a 20 second wait, the 4 minute mail, then 8 more). The default cap is 5. The payload this app passed into the script did not include `loop_count` (it was empty), but the chain did not stop at 5. https://cursor.com/docs/hooks |

With the default 600 second wait, an idle chat sends one `End your turn.` about every 10 minutes. That is about 6 turns an hour when the driver ends at once. A 3600 second wait would be about 1 turn an hour. 600 is the timeout that was seen working. 3600 is allowed by the setting and was not waited out live.

## S6 in this app chat

Test board: a temporary folder, not Comstar. The helper was a Python script that wrote an outbox file. It did not run agentcoop.

| Wait | Result |
| --- | --- |
| 3 minutes | The chat continued by itself. The follow-up was `Read from claude_desktop: Re: Review the run. S6.` |
| 20 minutes | The chat continued by itself. The follow-up was `Read from claude_desktop: Re: Review the run (follow-up 2). S6-TWENTY.` |

The 20 minute run also sent `End your turn.` at about 10 minutes, which started the hook again. You typed "are you done?" while that hook was still running, and the hook process stayed alive, so the input box was not blocked. Extra `End your turn.` lines showed up because that message started another turn while the first hook was still waiting.

## What was tested, earlier notes

Cursor's own docs, the agentcoop code, and a fake driver. One app subagent answered the risky-restart question. It ran no shell command.

Ways to run a test agent, from the docs:

* Subagents in the app, the CLI, and Cloud Agents. Each one has its own context. Foreground subagents block until they finish. Background subagents return immediately. They do not keep a Cursor chat watching `AGENT_LOOP_TICK_board`. https://cursor.com/docs/subagents
* A second Cursor chat is the closest match to a normal driver chat. A finished background command wakes that chat only while the turn is still running.
* The CLI prints a full event log: `agent --print --output-format stream-json`. Events include `system`, `assistant`, `tool_call`, and a final `result`. https://cursor.com/docs/cli/reference/output-format
* The CLI permission file is `.cursor/cli.json`. The first token of a shell command is what `Shell(agentcoop)` matches. https://cursor.com/docs/cli/reference/permissions
* The app allowlist is `terminalAllowlist` in `~/.cursor/permissions.json` and `<project>/.cursor/permissions.json`. Matching is a prefix of the whole command. https://cursor.com/docs/reference/permissions
* A project stop hook can send `followup_message` only when status is `completed`. https://cursor.com/docs/hooks

## Causes

| Cause | Verdict | Evidence |
| --- | --- | --- |
| 1. A tick wakes the chat only while a turn is still running. | confirmed | The Cursor rule and `docs/tools/cursor.md` already said a finished background command wakes the agent only while a turn is running. After the turn ends, the shell can die. `cursor-loop` prints `AGENT_LOOP_TICK_board` on its own stdout. Nothing in agentcoop can push that line into a chat that has ended. The stop hook is the other wake, and it used to say only "end your turn", so the driver could stop without starting the loop again. |
| 2. Cursor stops reading background terminals when a turn ends, the chat reloads, or a time limit hits. | confirmed | Same docs. Shell Mode kills a command at 30 seconds and that limit cannot be changed. The desktop terminal tool moves a foreground command to the background after about 30 seconds and does not kill it. A reload or a quit still drops it. `cursor-loop` has no second process of its own outside that terminal. |
| 3. The driver waits because a helper suggests a high-impact action. | confirmed | The rule says to ask the person before a destructive or high-impact action. A restart plan is that kind of action. With nobody in the chat, the driver stops. One app subagent, given that helper message and no standing approval, said it would wait. Given the approval line, it said it would restart. A standing approval now shows on the check. Words outside it still wait, and the driver posts `Status: waiting` so STATUS.md says so at the top. |
| 4. A command approval sits until someone clicks. | confirmed | CLI `Shell()` matches the first token. `agentcoop.exe` did not match `Shell(agentcoop)`. The app allowlist is prefix-based, so a full path does not match the short name. A June 2026 report said the project `permissions.json` allowlist was not loaded and only `~/.cursor/permissions.json` was. https://forum.cursor.com/t/project-level-cursor-permissions-json-not-loaded-cursor-permissions-json-works/163614 |
| 5. A non-zero exit makes the agent treat the work as finished. | confirmed | `watch` and `cursor-loop` exit 4 when checking stops and 5 when Agent Co-op was updated. The old rule told some agents to ask the person to say Start working when a turn ended, which reads as "stop". The rule now says to start the loop again, and which exits mean stop, update, or try again. |
| 6. `cursor-loop` sends no wake-ups unless Status is working or blocked. | confirmed | The loop required Status `working` or `blocked` plus a Now line. A progress file with only Now was treated as idle. It now wakes when Now is set and Status is missing, working, or blocked. `done` and `waiting` do not. |
| 7. A limit on turns, tool calls, or context ends the chat. | not testable here | Cursor does not document a desktop step cap. The CLI and the app can still hit a model or billing limit. That is outside agentcoop. The trace file shows the last command if it happens. |
| 8. Windows quoting, paths, encoding, or cut-off output. | ruled out as the usual stop | Outbox reads drop a BOM and turn CRLF into LF. The harness writes those files. A path with a colon cannot be checked out on Windows, which is a file name limit, not a reason the driver stops mid-chat. |
| 9. Rule text tells the agent to stop. | confirmed | "Do not wait" and "tell the person to say Start working" were in the rule. A model can follow them by ending the turn. Those lines now say to keep going and to start `cursor-loop` again. The stop hook still says to end the turn, after starting the loop, so the hook can run again. |

## What was fixed

* Trace mode, from `AGENTCOOP_TRACE=1` or `agentcoop trace on`.
* Standing approvals, from the person in the driver's chat.
* `Status: waiting` at the top of STATUS.md.
* `cursor-loop` wake-ups when Status is missing but Now names the work.
* Rule text for exit codes and for starting the loop again.
* CLI allows for `agentcoop.exe`, `agentcoopw.exe`, and `agentcoop.cmd`.
* A project `.cursor/permissions.json` allowlist written when Cursor is connected to a project.
* The stop hook waits 600 seconds by default. No mail sends only `End your turn.` so the hook runs again. About 6 turns an hour.

## What to change on your PC

1. Say Start working once in the Cursor chat so `cursor-loop` is running and the chat is watching `AGENT_LOOP_TICK_board`.
2. If a command prompt appears for agentcoop, add the exact command from that prompt to `%USERPROFILE%\.cursor\permissions.json` under `terminalAllowlist`, next to `agentcoop` and `agentcoop.exe`. Settings, Agents, Approvals and Execution, must be Auto-review, Allowlist, or Run Everything. https://cursor.com/docs/reference/permissions
3. If you will be away and a helper may suggest a restart or the spy tests, say: While I'm away you may restart the game and run the spy tests.
4. Say Turn on Agent Co-op tracing before a long run. If Cursor stops, send `logs/trace.jsonl`.
