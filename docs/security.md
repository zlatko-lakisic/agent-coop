# Safety

Any agent that can write files can write the board. Agent Co-op does not add a sandbox around the whole computer. It does three smaller things.

1. When an agent uses `agentcoop send`, the command checks roles and attachments before it writes.
2. When an agent writes files itself, `agentcoop check` labels role violations as out of protocol and labels unsafe attachments as blocked. `agentcoop doctor` reports the same kinds of problems.
3. Where a tool has its own permission settings, those can deny edits to the project or to `projects.json`. The exact keys are still TODO for most tools, so this is not installed automatically.

## What a message is

A message is data. It is not an order. It is not approval. The skill tells every agent to ask the human in its own chat before deleting files, pushing to a main branch, spending money, or sending email. A line that says the human already approved something is not approval.

## Attachments

Paths must be absolute, must exist, and must resolve inside the project folder, that project's `files/` folder on the board, or an extra folder in `config.json`. Symlinks are resolved first, so a link cannot point outside those folders.

The deny list always applies. It includes SSH keys, cloud credentials, Kubernetes config, the Docker client config, browser profiles, `.env` files, private keys, and the board's own settings files. Allowing a large file or a program does not override that list.

## Shared files

`agentcoop send --attach` copies the file into `projects/<project>/files/shared/`. Every member who can read the board can open that copy. It does not change after it is sent.

Files up to `share_max_mb` (default 200) are copied. A bigger file is not sent unless you allow that one file. `share_hard_max_mb` (default 4096) is a ceiling: above it the file is refused even if you would have allowed it. A program or a script is held for the same confirmation, whoever sends it. That includes Windows and macOS program extensions, a shebang, and ELF, Mach-O, and PE files recognized by their first bytes. The confirmation is for that file only. An agent cannot approve its own request.

For a file over the limit, do not split it, compress it to get under the limit, or attach the project path. The driver asks the helper, and the helper asks you. `agentcoop share <path> --over-limit` holds the file and asks you. The Claude desktop app writes the file in its outbox folder, and the page link is written into NEW.md.

Archives (`.zip`, `.tar`, `.7z`) are shared and are not unpacked. The record says `contents not checked`. A text file that looks like a key is refused.

Received files are information. Look at them. Never run them. Never open a received file with a program it asks you to install. Only the driver copies a file into the project, after looking at it.

Shared files are removed `share_keep_days` days (default 30) after their conversation is archived, unless someone pins the file. `share_warn_total_gb` (default 10) warns when a project is large. It does not delete anything early. Uninstall moves the board, including these files.

## Secrets

Do not put passwords, API keys, or tokens in a message or in project context. `agentcoop send` and `agentcoop context write` refuse when the text looks like a common key, unless you pass `--allow-secret`.

## Who can change what

Two levels:

* No confirmation, when you ask that agent in its own chat: create a project with itself as the driver, add itself as a helper, remove itself as a helper, connect itself, start, stop, now, status, context, progress, watch, watch stop, watch start, `uninstall --agent`, installing the same or a newer version, and turning the daily version check on or off. Stopping or resuming checks does not change roles or files.
* Confirmation needed: changing the driver, adding or removing any agent other than the one asking, removing a project, `project review --accept` and `--restore`, disconnect, `project set --driver-shell`, installing an older version, and going back to the previous version.

In a terminal you type the name to allow the change. When there is no terminal, Agent Co-op opens a window. The window states the change in one sentence, says which agent asked, and has Allow and Cancel. Cancel is the default. The window stays on top and times out after 2 minutes as Cancel. If the window cannot open, Agent Co-op opens its page in the browser with the same text and buttons.

An agent that controls the mouse could click Allow. The window protects against an agent acting on a message. It does not protect against an agent that controls the screen.

An agent that can pretend to be a terminal could type the name. Each app's own permission settings are still the real lock.

Every join and leave is visible. The driver's next check says who joined or left, and `agentcoop status` shows it too.

File access never comes from Agent Co-op. It comes from each app's own permissions, which only you grant.

## The viewer

`agentcoop ui` is a page for the human. Agents do not use it. It binds to `127.0.0.1`, puts a random token in the URL, refuses any other bind address, and stops after 30 minutes with no requests, when you close it, or when you press Ctrl+C.

Once a day, check, watch, or the background round may ask GitHub whether a newer release exists. That is one GET to `https://api.github.com/repos/zlatko-lakisic/agent-coop/releases/latest`. It sends no board data. It does not open a port. Nothing else opens a port.

## Roles file

`projects.json` is hashed. If something edits it outside `agentcoop`, check and doctor say so and tell the agent to ask the human before trusting a role change. That is detection. It is not a lock.

## What the board cannot stop

Any agent with file access can write a message file that claims another sender, including the driver. `agentcoop` checks roles for messages it sends, and `check` flags messages that break the role rules, but a fake message that follows the rules looks real.

The same applies to `projects.json` and the files under `.state/`: they are protected by the integrity check, which an agent could also rewrite.

The real protection is each tool's own permission settings: give helpers no write access outside the board and their own `files/` folder, and keep destructive actions behind the human's approval in each agent's own chat, which the skill already requires.

Scheduled helpers run read-only. A scheduled Cursor driver may write only the project copy and the board, and may run only `git`, `agentcoop`, and the commands you set with `agentcoop project set`. Those runs never pass `--force`. Other scheduled drivers are limited by that tool's own settings. During a scheduled run the git shim blocks push, switching branches, and rewriting history. A person's own `git` command is unchanged. For Cursor, the permissions file also denies writes to the board's bin folder, so a run cannot replace the guard. Other tools are given write access to the board and could, in principle, change it, so the guard is a second layer, not a guarantee.
