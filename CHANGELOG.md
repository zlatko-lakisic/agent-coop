# Changelog

## 0.2.6 - 2026-10-22

- A downgrade prints what you lose, from the compatibility table, before you type the version.

## 0.2.5 - 2026-10-22

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
