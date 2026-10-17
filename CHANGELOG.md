# Changelog

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
