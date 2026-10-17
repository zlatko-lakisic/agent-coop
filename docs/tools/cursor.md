# Cursor

Agent name: `cursor`

Can drive: yes

Joins with: rule

Project files for: members

Hooks: todo

Read-only lock: todo

Background runs: yes

Status: untested

Project rule only (loads every turn). TODO: hook format for Cursor is not verified, so hooks are not installed. TODO: detection paths per OS are not confirmed. The command is agent, installed into ~/.local/bin. cursor-agent is the older name. https://cursor.com/docs/cli/installation Background helper runs use agent -p --mode ask (read only). Flags confirmed at https://cursor.com/docs/cli/reference/parameters : --print, --model, --workspace, --trust, --force, --mode ask. TODO: driver runs are not scheduled. Print mode without --force was not confirmed to run allowed actions with no prompt. cursor-agent is not installed on the machine that checked this, and the permissions page says to use allow, deny, and --force to control what runs without prompts. https://cursor.com/docs/cli/reference/permissions

See AGENTS-SUPPORT.md for the full table. Open questions there are not guesses.
