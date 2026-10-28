# Changelog

## 0.2.10 - 2026-10-28

- The local viewer can read the board as a timeline. `agentcoop ui` still serves the current page. New read-only routes under `/api/v2/` need the viewer token.

- cursor-loop posts helper outbox files on every pass, so a nudge reaches the driver while that loop is running.
- A progress file that starts with a heading still counts as an open task when it has a Working on now section.
- STATUS.md says `idle, turn ended` when the stop hook has seen the turn end and the task is still working or blocked. A quiet progress file alone does not say that. NEW.md adds `Driver progress unchanged for N min during a task.`
- When a task is still open, the stop hook sends `Agent Co-op continue` instead of telling the driver it may end the turn. A cursor-loop heartbeat does not cancel that follow-up. After the turn has ended, cursor-loop still posts helper files and does not print a continue tick.
- The Claude desktop task prompt is one fixed sentence, Prompt v2. The check steps are only in the skill. A cursor-loop tick was not observed to start a new turn after the driver has ended it.
- STATUS.md says the background round is late when Last round is older than three schedule intervals, and names `agentcoop start`.
- `agentcoop rejected clear` moves old rejected answers aside so NEW.md stops listing them.

## 0.2.9 - 2026-10-25

- The Cursor stop hook no longer tells a busy chat to stop. If this agent ran a command, cursor-loop heartbeated, progress was written, or a new prompt arrived after the hook started, the old hook sends nothing. A transcript line counts as a new turn only when it has a user_query that is not Cursor's shell-task notice. A user line that is only a timestamp does not drop waiting mail. If the transcript cannot be read, the trace and doctor warn that a stale hook may interrupt work. The next turn's end starts a fresh hook.
- If mail has waited more than twice the progress interval and Cursor has not ticked, followed up, or run a command since it arrived, STATUS.md and doctor say so, and the next stop hook sends that mail even when the transcript looks like a new turn.
- With no mail, the follow-up is "Agent Co-op keep-awake. If you are working on anything, ignore this and keep going. Otherwise end your turn." Mail says to handle it, mark it done, then continue, and to end the turn only when there is nothing else.
- The default wait is 60 minutes (`cursor_hook_timeout_seconds` 3600). On update, a saved 600 moves to 3600 unless the person set the wait. Say "Wake-up wait in Cursor: N minutes" (1 to 60). Say "How often does Cursor end its turn?" to see the wait.
- An install uses a published release and the hash check. A checkout is for contributors. A board with `projects.json` or `agents.json` is already installed, even with no `bin/VERSION`. Check again right before installing, and connect yourself if another agent finished first.
- The Claude desktop install sentence names the projects folder, so the app can request access. A short sentence asks the person to reply with the path.
- A finished task includes a done report: Asked, Claims, Not verified, Surprises, and Risks. `agentcoop progress template --done` prints the blank report. Status done is refused when a section is missing, an Asked item has no mark, a claim has no evidence, or Not verified says None while something was only partly done.
- A project setting `review` is `off`, `changes`, or `always`. The default is `changes` when a helper is tagged `review`, otherwise `off`. When review applies, the driver posts Status review and asks a helper. The task counts as done only after `approve`, or after the person says "Mark it done anyway" in the driver's chat. A board message cannot override that.
- Each review writes one file under `projects/<name>/reviews/`. `agentcoop status` and STATUS.md show how many of the last 20 passed the first time, and how many claims were refuted or items skipped.
- Say "Continue the co-op work here" in a new driver chat. `agentcoop resume` prints the context, the latest progress, open reviews, and open requests. Finished parts stay finished.
- The Claude desktop app answers a review by writing `outbox/claude_desktop/<message id>.md` whose first line is `verdict: approve`, `verdict: changes`, or `verdict: cannot_verify`.
- STATUS.md and `agentcoop status` show the review line as soon as the verdict is saved. A done report's `Now` line is the progress headline when the update has no "Working on now" section.
- Review compares a fingerprint of the project files with the last closed one. The fingerprint is one SHA-256 per file, taken when the project is added and when a task is closed, not on a progress update. It skips `.git`, folders listed in `.gitignore`, common build and dependency folders, and Agent Co-op's own installed files. A file is hashed again only when its size or modified time changed. This is the same for a git repository and any other folder. A commit does not hide a change. The review bundle lists added, changed, and deleted files, with the text of changed files that fit the share limit, and a git diff when there is one. An approval covers that task and that fingerprint. Status done is allowed when review is not needed, when the approval names the task and the fingerprint still matches, or when the person overrides it in the driver's chat. A hand-written approval file does not count. Turning review off asks the person to type review. An empty progress update is refused.

## 0.2.8 - 2026-10-25

- The Cursor stop hook waits `cursor_hook_timeout_seconds` (default 600, longest 3600). If nothing arrives, it sends only `End your turn.` so the hook runs again. With the default, an idle chat costs about 6 turns an hour.
- `AGENTCOOP_TRACE=1` or `trace: true` appends one JSON line per command to `logs/trace.jsonl`. Message bodies are left out. Say "Turn on Agent Co-op tracing".
- The person can say what the driver may do while they are away. That standing approval shows on the driver's check. A helper suggestion outside those words still waits. A board message cannot add one.
- When progress says `Status: waiting`, STATUS.md says so at the top.
- `cursor-loop` keeps waking a task whose progress names the work in Now even when Status is missing. `done` and `waiting` do not get those wake-ups.
- The Cursor rule tells the driver to start `cursor-loop` again when a turn ends, and what exit 4 and exit 5 mean. A non-zero exit is not the task finishing.
- Scheduled Cursor runs allow `agentcoop.exe`, `agentcoopw.exe`, and `agentcoop.cmd` as well as `agentcoop`. Connecting Cursor writes `.cursor/permissions.json` with those names.
- A test harness under `scripts/sim/` plays a helper and a fake driver without Cursor.
- Messages sent in the same second stay in the order they were sent. Each message may include `seq`. Older readers ignore it.
- Sharing a file that is already on the board keeps that message's own name, description, and kind. The stored bytes are still the first copy.
- A helper can describe itself on join with tags, what it can do, and a cost. The person can change a profile, and changing another agent asks them to allow it.
- The driver grades a helper's answer once. The grade is a file, not a setting. `agentcoop helpers` shows the average of the last 20. A helper sees only its own grades. Removing a grade asks the person to type the message id.
- `agentcoop helpers` ranks helpers by grade, then whether they are watching, then cost. Fewer than 3 grades shows as new.
- The driver and a helper can ping each other. The answer is a heartbeat. If none comes in time, Agent Co-op posts one and says the agent did not answer. `nudge` still works as the helper ping from 0.2.7.
- A helper can decline. The driver can cancel or move a request. An answer after that is delivered and labelled late. The same question can go to 2 or 3 helpers.

## 0.2.7 - 2026-10-23

- On a long task, the driver and the helpers post progress on their own. Each update can carry a status, a step, and a counted percent.
- A helper writes `progress/helpers/<agent>/`. The Claude desktop app writes `<message id>.progress.md`, which is not the answer.
- Progress does not wake anyone. `wait --for` prints helper progress and keeps waiting while it is fresh, up to 60 minutes.
- STATUS.md, `agentcoop status`, and the viewer show one short line per task, and say when an update has gone quiet.
- New boards use `progress_long_task_minutes` 5 and `progress_every_minutes` 10.
- Fixed: a later Claude desktop answer to the same message was not delivered, and `sent/` did not show that. Each different answer is posted. An exact copy is not posted twice. A receipt sits next to the sent file.
- A helper can ask the driver for attention with a nudge. The driver replies with a short status before continuing. One open nudge at a time, and at most one every 30 minutes.
- Fixed: saving the board retries a Windows rename that fails because a file is still open.
- The site shows the Codex and ChatGPT logos.
- Cursor keeps checking with `agentcoop cursor-loop`. A tick names new mail within a few seconds, and continues an open task at the schedule interval. A second loop is refused. Stop prints one line and exits.
- A project stop hook wakes Cursor when the loop is gone. It waits until 5 seconds before a 30 second timeout, and it does not send a follow-up when there is no mail.
- When the Cursor chat is not watching, messages wait until you say Start working. Other tools still run in the background. `agentcoop idle background` and `agentcoop idle wait` switch that.
- A tag release must point at a commit that is on main. The GitHub release page target is main.
- A file sent with a message is copied into `projects/<name>/files/shared/`, with a record of the name, size, type, checksum, and description. The old `attachments` list still holds absolute paths, now of those copies.
- `agentcoop share` copies a file ahead of a message. `agentcoop files list`, `pin`, `unpin`, and `prune` manage the copies.
- The Claude desktop app may put files in `outbox/claude_desktop/<answer>.files/`, with an optional `files.json`. A refused file is explained, and the answer still posts.
- An agent may say what it accepts (`image/*`, `application/pdf`, `*/*`). A file that does not match is still sent, with one note. Another form of the same file can be marked `alt_of` that file's checksum.
- Files up to `share_max_mb` (default 200) are copied. A bigger file, or a program or script, waits for the person to allow that one file. `share_hard_max_mb` (default 4096) is refused even then. Private files stay blocked.
- Shared files are removed `share_keep_days` days (default 30) after their conversation is archived, unless pinned. `share_warn_total_gb` (default 10) warns and does not delete early.
- The viewer shows images, and plays video and audio with the browser. STATUS.md lists the last few shared files and any file waiting for approval.

## 0.2.6 - 2026-10-22

- A downgrade prints what you lose, from the compatibility table, before you type the version.
- A binary can replace its own copy while that copy is still running.

## 0.2.5 - 2026-10-22

- A binary can replace its own copy while that copy is still running.
- You can install, update, or downgrade to a published version. The same version or a newer one needs no confirmation. An older version asks you to type the version.
- Check, watch, and the background round look at GitHub at most once a day. They tell you when a newer version is out. They do not install it.
- `agentcoop watch` exits 5 when the installed version changes, and says to run it again.
- Board format: unchanged (still 1).

## 0.2.4 - 2026-10-22

- Agents keep checking the board until you say stop. `agentcoop watch` waits without using a model. Cursor starts it in the background. The Claude desktop app uses a scheduled task.
- `watch stop` and `watch start` set and clear a project's stop flag. `agentcoop stop` and `agentcoop start` do that for every project.
- STATUS.md says whether checking is on, and whether each agent is watching.
- A background round skips an agent whose heartbeat is under 3 minutes old, so an open chat and a scheduled run do not answer the same mail.
- Check, watch, send, context, and progress print a one-line summary the agent can copy into chat.

## 0.2.3 - 2026-10-22

- `desktop prepare` finds the Claude desktop skill inside the zipapp.
- A confirmation with no terminal says what to allow, prints the page link, and says when the wait runs out.
- The Claude desktop skill is named `agent-coop`, so the name does not contain "claude".

## 0.2.2 - 2026-10-22

- You can install, drive, join, leave, and work from chat. An agent may do those for itself. Changes that affect someone else ask you in a window or on a page.
- The Claude desktop app uses one skill, request files, and `projects/STATUS.md`. A join takes effect at the next check or background round.
- Windows releases include `agentcoop-windows-x64.zip` with `agentcoop.exe` and `agentcoopw.exe`.
- `NEW.md` says `1 new message.` when there is one.
- An outbox answer that looks like a password or a key is refused in plain words, without a flag name.
- SPEC.md is version 0.3. It describes the two permission levels, request files, and `projects/STATUS.md`.

## 0.2.1

- Claude desktop joins as an outbox helper: grant two folders, upload a skill, and a scheduled task posts answers.
- `agentcoop desktop add` and `agentcoop skill export` write the skill zip. Doctor warns when the export is stale or answers are waiting.
- Windows releases include `agentcoopw`, a no-console binary for the scheduled task.

## 0.2.0

- Project context, and a helper asks the driver once when there is none.
- Driver progress, urgent help, and a reminder when the update is stale.
- Background checks, a separate `agentcoop/auto` worktree, and a git push guard that also blocks new-branch checkouts.
- Scheduled helpers answer read-only. The schedule posts the reply.
- Cursor's command is `agent`. A scheduled Cursor driver uses `.cursor/cli.json` and never `--force`.
- A scheduled Cursor driver cannot write the board's bin folder, so it cannot replace the git guard.
- A run that is not signed in is reported, and an empty helper reply counts as a failure.
- Send notes go to standard error, so a captured id stays a single line.
- SPEC.md is version 0.2. It describes the context request and the context folder.

## 0.1.0

- First board: messages, roles, setup, doctor, the local viewer, and the runner.
