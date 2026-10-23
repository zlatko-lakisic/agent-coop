---
name: wiki
description: Keep the Agent Co-op wiki in step with changes a person can see or do. Use when a change touches commands, chat phrases, install, the board, settings, permissions, safety, agents, errors, or troubleshooting.
---

# Wiki

The guide people read is the GitHub wiki, in the `agent-coop.wiki` folder next to this repo. Its branch is `master`. The main repo stays the source for anything an agent follows.

## 1. Decide if the wiki needs an update

Update it when a change touches anything a person sees or does:

* commands, flags, or output
* chat phrases
* skill or rule text
* install, update, or uninstall steps
* board files
* settings
* permissions
* safety rules
* supported agents
* error messages
* the troubleshooting cases

## 2. Find the pages

| Source | Wiki pages |
| --- | --- |
| `src/agentcoop/cli.py` | Command reference, Exit codes |
| `src/agentcoop/data/phrases.json`, `src/agentcoop/templates/` | Chat phrases |
| `src/agentcoop/watch.py` | Keeping agents checking, Exit codes |
| `src/agentcoop/update.py`, `docs/agent-update.md`, `docs/update.md` | Updating, Install a specific version |
| `src/agentcoop/updatecheck.py` | Updating, STATUS.md and the viewer |
| `src/agentcoop/migrate.py`, `COMPATIBILITY.md` | Compatibility, Updating |
| `src/agentcoop/uninstall.py`, `docs/agent-uninstall.md` | Uninstall |
| `src/agentcoop/install.py`, `docs/agent-setup.md` | Install on Windows, Install on macOS, Install on Linux, Your first project |
| `src/agentcoop/confirm.py` | Permissions |
| `src/agentcoop/safety.py`, `docs/security.md` | Safety and privacy |
| `src/agentcoop/projects.py`, `src/agentcoop/schema.py` | Projects, Settings files |
| `src/agentcoop/context.py`, `src/agentcoop/progress.py` | Context and progress |
| `src/agentcoop/rounds.py`, `docs/schedule.md` | Background runs, Keeping agents checking |
| `src/agentcoop/ui.py`, `src/agentcoop/statusfile.py` | STATUS.md and the viewer |
| `src/agentcoop/tools.py`, `AGENTS-SUPPORT.md`, `docs/tools/` | Cursor, Claude Code, Codex and the ChatGPT desktop app, Gemini CLI, Local models, Planned agents, Claude desktop app |
| `src/agentcoop/desktop.py`, `src/agentcoop/outbox.py` | Claude desktop app, Troubleshooting |
| `src/agentcoop/paths.py`, `SPEC.md` | How it works, Board layout, Message format |
| `src/agentcoop/gitshim.py`, `src/agentcoop/worktree.py` | Background runs |
| `src/agentcoop/session.py` | Troubleshooting, Environment variables |
| `src/agentcoop/runner.py`, `docs/runner.md` | Local models |
| `docs/troubleshooting.md` | Troubleshooting |

Add a row when you add a page.

## 3. Edit them

Write for a person. Lead with what to say in chat. Put terminal commands in a details block. Sentence case. No em dashes or en dashes. Start each page with one or two sentences. Mark a new feature with `Added in <version>`, using the version the change will ship in. Do not write that a version is the latest. Link to the releases page.

Check each command, flag, path, phrase, exit code, and message against the source. Run command examples on a temporary board (`AGENTCOOP_BOARD` and `HOME` set to a temp folder) and paste the real output, trimmed.

If something is not confirmed, say that, and say what to check.

## 4. Regenerate

```text
python3 scripts/gen_wiki.py --wiki ../agent-coop.wiki
```

Do not edit `Chat-phrases.md` or `Command-reference.md` by hand.

## 5. Check links

```text
python3 scripts/check_wiki_links.py ../agent-coop.wiki
```

## 6. Commit and push the wiki

Do this only after the main pull request is merged, so the wiki never describes work that did not land. The commit message names that pull request number.

## 7. Say it in the pull request

Add a line `Wiki: <pages changed>`, or `No wiki change: <reason>`.

## 8. Never put on the wiki

Secrets, internal hostnames or IP addresses, personal project details, or a guess presented as a fact.

Every change must work on Windows, macOS, and Linux. Test on all three in CI, and list any skips in the PR.
