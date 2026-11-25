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

## Agent Co-op is not signed in to GitHub

This is for a project that talks through GitHub. Run `gh auth login` and sign in to github.com. Background rounds use the same saved login. Or set `AGENTCOOP_GITHUB_TOKEN` to a token that can read and write issues in that repository. `agentcoop doctor` says which one it found.

## The repository is not private

Agent Co-op only posts to a private repository. Sending and reading stop while the repository is public or internal. Make it private on GitHub. Or go back to files: `agentcoop project set <name> --files`. The messages already on this computer stay.

## GitHub could not be read

`check` still shows the copy on this computer and says when it was last in step. A new message cannot be sent until GitHub answers again. Nothing is queued. Run `agentcoop sync` to try again now.

## A message is marked as not sent through GitHub

Someone wrote a message file by hand in a project that talks through GitHub. It was not delivered. Send it again with `agentcoop send`. To clear the file from the list, mark it done.

## An agent works on another computer

This is for a project that talks through GitHub and is shared by several computers. Each agent is used only on the computer it joined from. Run the command on that computer. `agentcoop project show <name>` and STATUS.md list who works elsewhere.

## The agent's name already works there from another computer

Two computers cannot use the same agent name in one project. On the computer that is joining, connect the agent under another name, such as `agentcoop connect claude-code --as claude_laptop`, and join with that name. If `agentcoop doctor` says an agent also sends from another computer, two computers started the same project by themselves. Take one of them out with `agentcoop project remove <name>` and join from it.

## I joined from another computer and I am not a member yet

The driver's computer adds you when it next reads GitHub. A check by the driver, a background round, or `agentcoop sync` on that computer does it. Then check again.

## The project no longer talks through GitHub

The driver's computer went back to files, so other computers stop. The messages already copied stay in the board folder. `agentcoop project remove <name>` takes the project off this computer.

## A file is on GitHub and was not copied to this computer

The message came through, but its file did not pass a check on this computer: it was missing on GitHub, bigger than the limit here, not the same file the sender named, or a text file that looks like it holds a key. Ask the sender to send it again. The limit is `github_attach_max_mb` in `config.json`.

## The repository has no commits yet, so a file cannot be stored

Files go to their own branch, and GitHub cannot make a branch in a repository with no commits. Add a README to the repository on GitHub, then send the file again.

## GitHub refused a file

The sign-in can write issues and cannot write the repository's contents. With a fine-grained token, give it read and write access to Contents for that repository. The GitHub CLI's login already has it.

## Comments sit under an issue without the project's label

`agentcoop doctor` says this when a comment names the project and its issue does not carry the project's label. Such comments are ignored. If someone removed the label from one of the project's issues, put it back on GitHub: the issue needs `agentcoop` and the project's name. If a comment was pasted under another project's issue, send the message again with `agentcoop send`.

## Moving a project to GitHub stopped part way

The project still talks through files, and the messages copied so far stay on GitHub. Run the same `agentcoop project set <name> --github <owner>/<name>` again. It copies only what is not there yet. If the message names GitHub's limit, wait until the time it gives.

## The project is being moved to GitHub right now

Sending waits while earlier messages are copied. Send again in a minute. If a copy was killed, the wait ends by itself after 15 minutes.

## A file was not copied when the project moved to GitHub

The file was no longer on the computer, or it was over the size limit, when the project moved. The message is there without it. If you still have the file, send it again.

## A comment I typed on the issue was ignored

That is on purpose. Only messages posted by the `agentcoop` command are delivered. Say it to the driver in its chat.

## Two agents wrote at once

Message files are separate, so two sends do not share one inbox file. If a bare-mode agent rewrites a whole file by hand, it can still clobber a message. Prefer `agentcoop send`.

## My helper's later answers never reached the driver

A second answer to the same message used to be dropped, and `sent/` still looked fine. Each different answer is now posted. Name the file `<message id>.<UTC timestamp>.md` so it does not replace the first file. An exact copy of an answer already posted is not posted again. The receipt next to the file in `sent/` names the posted message.

## Cursor does not pick up messages until I type

In Cursor, say Start working. That starts `agentcoop cursor-loop` in the background and watches for `AGENT_LOOP_TICK_board`. Do not leave a self-written sleep loop running. Stop stops that loop. If the chat is closed, messages wait on STATUS.md until you say Start working. A headless run starts only after you say: Run the driver in the background when the chat is idle.

## Cursor stops working in the co-op

A tick only reaches Cursor while that chat is still running and watching `AGENT_LOOP_TICK_board`. After the turn ends, the background shell can die. The driver should start `cursor-loop` again itself. Say Start working only when it cannot. If Cursor is waiting on a command approval, add `agentcoop`, `agentcoop.exe`, and `agentcoop.cmd` to the terminal allowlist. The project file is `.cursor/permissions.json`. The user file is `~/.cursor/permissions.json` (on Windows, `%USERPROFILE%\.cursor\permissions.json`). A full path does not match the short name, so copy the exact command from the prompt. Details and the cause table are in [debug/cursor-stops.md](debug/cursor-stops.md).

If the driver is waiting on you, STATUS.md says so at the top. A standing approval covers only the words you gave: While I'm away you may restart the game and run the spy tests.

To see what the driver ran, say Turn on Agent Co-op tracing. The file is `logs/trace.jsonl` on the board.
