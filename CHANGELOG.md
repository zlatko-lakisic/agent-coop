# Changelog

## Unreleased (planned 0.2.7)

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
