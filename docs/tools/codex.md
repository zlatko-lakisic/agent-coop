# Codex CLI

Agent name: `codex`

Can drive: yes

Joins with: skill

Project files for: driver

Hooks: todo

Read-only lock: todo

Background runs: yes

Status: untested

User skill only. TODO: Codex hook format is not verified, so hooks are not installed. TODO: exact sandbox config keys for a read-only lock are not confirmed. TODO: detection paths per OS are not confirmed. Sign in with codex login (https://developers.openai.com/codex/cli/reference). Background flags confirmed at https://developers.openai.com/codex/cli/reference : exec, --sandbox read-only and workspace-write, --cd, --skip-git-repo-check, --model, --ask-for-approval never, --add-dir. workspace-write has network off unless sandbox_workspace_write.network_access is set (https://github.com/openai/codex/blob/13c42a077c88a0d04ae7680a9891d2daf4558577/docs/sandbox.md).

See AGENTS-SUPPORT.md for the full table. Open questions there are not guesses.
