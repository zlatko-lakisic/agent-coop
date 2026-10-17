# Background schedule

Agent Co-op can check the board every few minutes and start an agent when it has new mail.

## Cursor

A scheduled Cursor run uses `agent -p --trust --workspace <folder>`. It never passes `--force`, `-f`, or `--yolo`.

Before a driver run, Agent Co-op writes `<worktree>/.cursor/cli.json`. That file allows reading, writing the worktree and the board, and running `git`, `agentcoop`, and any commands set with `agentcoop project set <name> --driver-shell`. It denies writes to `.env` files, key files, and the board's settings. The file is listed in the worktree's git exclude list, so it is not committed.

Before a helper run, Agent Co-op writes `<board>/projects/<project>/.cursor/cli.json` with `Read(**)` allowed and `Write(**)` and `Shell(*)` denied.

Flags are confirmed at https://cursor.com/docs/cli/reference/parameters. Permissions are described at https://cursor.com/docs/cli/reference/permissions.

A person's global `~/.cursor/cli-config.json` may also hold permissions. How that file combines with the project file is not documented, so keep broad allow rules out of the global file.
