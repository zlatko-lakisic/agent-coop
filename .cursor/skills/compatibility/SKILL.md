---
name: compatibility
description: Update the Agent Co-op compatibility table when a change can affect an older version. Use when editing messages, the board, settings, roles, migrations, skills, setup, update, or the schedule.
---

# Compatibility

The table is `src/agentcoop/data/compat.json`. `COMPATIBILITY.md` is generated from it. Cursor reads this skill from `.cursor/skills/` ([project skills](https://cursor.com/docs/skills)).

## 1. Check the change

Before you finish, see if the change touches any of these:

* message fields, message types, or the `v` value (`core.py`, `schema.py`, SPEC.md)
* board folders: inbox, done, sent, files, archive, context, progress, outbox, requests, watchers, STATUS.md, or any new board file
* keys in projects.json, agents.json, config.json, or the integrity hash
* role rules
* `board_format`, `min_version`, or migrations
* skill, rule, AGENTS.md, GEMINI.md, or Claude desktop skill text
* setup or update flags that older versions call, or that `docs/agent-setup.md` and `docs/agent-update.md` tell an agent to run
* the background schedule entry or the PATH wrapper

## 2. Update the Unreleased row

If something in that list changed, edit the Unreleased row in `src/agentcoop/data/compat.json`. Then run:

```text
python3 scripts/gen_compat.py
```

## 3. Write what a downgrade loses

Say what the person loses, in plain words. Write `Nothing` only when that is true.

## 4. Keep old boards readable

Additive is the default. An older version should ignore the new key or file.

If an older version would break, add a migration with a down step, raise `board_format`, and set `min_version`. Explain in the pull request why that was needed.

## 5. When nothing in the list changed

Leave the table alone. In the pull request body, add a line `No compatibility change:` and the reason.

## 6. Version bump

In the version bump pull request, turn the Unreleased row into the new version and the release date. Add a new Unreleased row with `Nothing` and `None`.
