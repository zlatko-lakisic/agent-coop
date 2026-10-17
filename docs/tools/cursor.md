# Cursor

Agent name: `cursor`

Can drive: yes

Joins with: rule

Project files for: members

Hooks: todo

Read-only lock: todo

Background runs: yes

Status: untested

Project rule only (loads every turn). TODO: hook format for Cursor is not verified, so hooks are not installed. TODO: detection paths per OS are not confirmed. Background helper runs use cursor-agent -p --mode ask (read only). Flags confirmed at https://cursor.com/docs/cli/reference/parameters : --print, --model, --workspace, --trust, --force, --mode ask. TODO: driver runs are not scheduled. No confirmed flag adds the board as a writable folder beside the workspace. --force allows writes everywhere, which is broader than the worktree plus the board.

See AGENTS-SUPPORT.md for the full table. Open questions there are not guesses.
