# Troubleshooting

`agentcoop doctor` prints a line for each check. Lines that start with `Fix:` include the next command.

## Agent Co-op is not set up

Run `agentcoop setup`. If you are an agent, follow [agent-setup.md](agent-setup.md) and do not use the interactive prompts.

## Claude Code is not connected

Run `agentcoop connect claude-code`. The same pattern works for other tools: `agentcoop connect cursor`.

## A skill file was deleted or edited

Doctor names the file. Run `agentcoop connect TOOL --yes` to write it again. `--yes` is required when a file is already there, so your copy is backed up first as `name.agentcoop-backup-<time>`.

## projects.json was changed outside agentcoop

Roles may not be what you set. Run `agentcoop project show NAME` and set the driver again with `agentcoop project driver NAME AGENT`.

## The folder is already part of another project

Projects cannot sit inside each other. Pick the outer folder, or remove the other project first with `agentcoop project remove NAME`.

## Ollama is not running

Start Ollama, then run `agentcoop doctor`. If the model is missing: `ollama pull llava`.

## macOS says the app cannot be opened

Release binaries are not signed or notarized yet. That is a TODO. After Gatekeeper blocks the binary, open System Settings, then Privacy and Security, and allow the app. You can also run `agentcoop setup` from the zipapp with `python3 agentcoop.pyz` if Python is installed.

## An inbox is huge

Run `agentcoop archive --as NAME`. Handled messages older than 30 days move out of the inbox. `agentcoop log` still shows them.

## An agent is not signed in

`agentcoop status` and `agentcoop doctor` name the command. For Cursor, run `agent login`. Then check the board again.

## There is no scheduler

`agentcoop start` exits if this computer has neither a user scheduler nor cron. Install cron, or run the printed `agentcoop tick` command every 5 minutes some other way.

## Two agents wrote at once

Message files are separate, so two sends do not share one inbox file. If a bare-mode agent rewrites a whole file by hand, it can still clobber a message. Prefer `agentcoop send`.

## My helper's later answers never reached the driver

A second answer to the same message used to be dropped, and `sent/` still looked fine. Each different answer is now posted. Name the file `<message id>.<UTC timestamp>.md` so it does not replace the first file. An exact copy of an answer already posted is not posted again. The receipt next to the file in `sent/` names the posted message.

## Cursor does not pick up messages until I type

In Cursor, say Start working. That starts `agentcoop cursor-loop` in the background and watches for `AGENT_LOOP_TICK_board`. Do not leave a self-written sleep loop running. Stop stops that loop. If the chat is closed, messages wait on STATUS.md until you say Start working. A headless run starts only after you say: Run the driver in the background when the chat is idle.
