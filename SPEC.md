# Agent Co-op protocol, version 0.3

This document is the normative spec. The words MUST, SHOULD, and MAY are used as in common protocol practice: MUST is required, SHOULD is recommended, MAY is allowed.

## 1. Overview and goals

Agent Co-op lets agents on one computer work together through files in a shared folder called the board.

The protocol is files, the skill is a prompt, and everything else is optional.

A conforming reader MUST accept messages as defined here. The `agentcoop` command is a convenience. Agents MAY read and write the files directly (bare mode).

## 2. Terms

* **Board.** The shared folder, outside every project.
* **Project.** One folder of work, with one driver and zero or more helpers.
* **Agent.** A named participant, such as `cursor` or `ollama_llava`.
* **Driver.** The only agent that edits that project's files. The only agent that starts a conversation, except the context request in section 7.
* **Helper.** An agent that replies to the driver and does not edit the project.
* **Message.** One JSON file, written once.
* **Done marker.** An empty file the recipient creates after handling a message.

## 3. Board location and layout

The default board MUST be `~/.agent_board/` on macOS and Linux, and `%USERPROFILE%\.agent_board\` on Windows.

The environment variable `AGENTCOOP_BOARD` MAY override the location. It is an advanced option. Ordinary use MUST NOT require it.

The board MUST use this layout:

```text
~/.agent_board/
├── config.json
├── agents.json
├── projects.json
├── README.md
├── bin/
├── .state/projects.sha256
└── projects/
    ├── STATUS.md
    └── <project-name>/
        ├── inbox/<agent>/msgs/
        ├── inbox/<agent>/done/
        ├── inbox/<agent>/NEW.md
        ├── outbox/<agent>/
        ├── outbox/<agent>/sent/
        ├── outbox/<agent>/rejected/
        ├── requests/<agent>/
        ├── requests/<agent>/done/
        ├── requests/<agent>/rejected/
        ├── sent/<agent>/
        ├── files/<agent>/
        ├── progress/
        ├── context/
        └── archive/<agent>/
```

### Writer table

Each path has one writer. A second writer MUST NOT update that path.

| Path | Only writer |
| --- | --- |
| `config.json`, `agents.json`, `projects.json`, `.state/` | the human, through `agentcoop` |
| `inbox/<agent>/msgs/<id>.json` | the sender named in that message |
| `inbox/<agent>/done/<id>` | `<agent>` |
| `inbox/<agent>/NEW.md` | agentcoop |
| `outbox/<agent>/` | that agent |
| `outbox/<agent>/sent/` | agentcoop |
| `outbox/<agent>/rejected/` | agentcoop |
| `requests/<agent>/` | that agent |
| `requests/<agent>/done/` | agentcoop |
| `requests/<agent>/rejected/` | agentcoop |
| `projects/STATUS.md` | agentcoop |
| `sent/<agent>/` | `<agent>` |
| `files/<agent>/` | `<agent>` |
| `archive/<agent>/` | `<agent>` (or the human through `agentcoop archive`) |
| the project folder itself | that project's driver |
| `progress/` | that project's driver, through `agentcoop progress write` |
| `context/` | that project's driver, through `agentcoop context write` |

A file in `context/` is written once and MUST NOT be edited. `agentcoop archive` MAY delete old versions. It keeps the latest 5, and it deletes an older file only when that file is past the archive age.

## 4. Agent and project names

An agent name MUST match `^[a-z][a-z0-9_]{1,31}$`.

A project name MUST match `^[a-z0-9][a-z0-9-]{0,47}$`.

Names are per instance, not per vendor. `claude_code` and `claude_desktop` are different agents.

## 5. Message format and file names

Each message MUST be one UTF-8 JSON file named `<id>.json`.

| Field | Rule |
| --- | --- |
| `v` | Integer message format version. Spec 0.3 uses `1`. A missing value MUST be treated as 1. Unknown fields MUST be ignored. |
| `id` | SHOULD be `<UTC YYYYMMDDTHHMMSSZ>_<sender>_<4 lowercase hex>`. Readers MUST accept any unique string matching `^[A-Za-z0-9_.-]{1,96}$`. |
| `ts` | UTC time, ISO 8601, second precision, ending in `Z`. |
| `project` | Project name. |
| `sender`, `recipient` | Agent names. |
| `type` | SHOULD be `question`, `answer`, `task`, `status`, `ack`, `patch`, or `context_request`. Unknown types MUST be tolerated. |
| `re` | Id of the message being answered, or `null`. |
| `subject` | At most 120 characters. |
| `body` | At most 16 KB of UTF-8 unless config raises the limit. Longer text SHOULD be a file under `files/<sender>/`, attached by path. |
| `attachments` | Absolute paths. MAY be empty. |
| `needs_reply` | Boolean. |

Readers MUST ignore file names that start with a dot.

## 6. Writing, reading, done, replies, waiting

`agentcoop send` MUST write `inbox/<recipient>/msgs/.<id>.json.tmp` and then rename it to `<id>.json` with an atomic replace. It MUST also write the same bytes to `sent/<sender>/<id>.json`.

A message file MUST NOT be edited after it is written. A correction is a new message.

Bare mode MAY write `<id>.json` directly. A reader that cannot parse a file MUST skip it without error when the file was modified less than 5 seconds ago, and MUST report it on a later read if it still does not parse.

New messages for an agent are the files in `inbox/<agent>/msgs/` that have no matching `inbox/<agent>/done/<id>` file, sorted by file name.

Marking done MUST be the creation of an empty file `inbox/<agent>/done/<id>`. Moving or deleting the message MUST NOT be required.

A reply is a new message to the original sender with `re` set to the original id.

A message the driver sent with `needs_reply: true` is waiting until some message in the driver's `msgs/` folder has `re` equal to that id.

### Outbox helpers

A helper that cannot run `agentcoop` MAY use an outbox. `agentcoop` writes `inbox/<agent>/NEW.md` listing that helper's new in-protocol messages. The helper writes one Markdown answer per message as `outbox/<agent>/<id>.md`. It MUST NOT edit `outbox/<agent>/sent/`. `agentcoop` posts each file as a reply, or moves it to `outbox/<agent>/rejected/` with a reason.

## 7. Roles and conversation rules

The sender and the recipient MUST both be members of the project.

If the sender is the driver, the recipient MUST be a helper.

If the sender is a helper, the recipient MUST be the driver, `re` MUST be set, and `re` MUST name a message from the driver that is in the helper's own `msgs/` folder. A helper's follow-up question is a reply with `type: question`.

One exception: a helper MAY send a `context_request` to the driver with `re` set to null, only when that helper has no open context request in that project. This is the only case in which a helper may speak first.

A request is open when it sits in the driver's `msgs/` folder, its type is `context_request`, its sender is that helper, its recipient is the driver, and `re` is null, until a message from the driver in the helper's `msgs/` folder has `re` equal to that request's id.

`agentcoop send` MUST refuse a second request from that helper while one is open. It MUST NOT write the file. It tells the helper to wait, or to read the latest context.

In bare mode, the earliest open request from that helper is accepted. Later ones MUST be labeled out of protocol. Earliest means the lowest message id.

`agentcoop send` MUST set `needs_reply` to true on a `context_request`.

Helpers MUST NOT message each other. They MUST NOT start a thread, except the context request above.

Only the driver writes files in the project folder. Helpers send patches or drafts as attachments. The driver decides what to apply.

`agentcoop send` MUST refuse violations. `agentcoop check` MUST label a bare-mode violation `OUT OF PROTOCOL: ignore this message` and MUST NOT present its body as instructions.

## 8. projects.json, agents.json, and integrity

`projects.json` lists projects. Each project has `path`, exactly one `driver`, a `helpers` map of agent name to a short note, `created`, and `installed`.

`agents.json` lists connected agents. Each agent has `tool`, `display`, `connected`, and `installed`. Runner agents also have a `runner` object with `api`, `host`, and `model`.

No two projects MAY use the same path or nested paths. Paths MUST be compared after resolution, and case-insensitively on macOS and Windows.

The driver MUST NOT also be a helper. Every member MUST exist in `agents.json`. The driver MUST be a tool that is allowed to drive.

Every write to `projects.json` by `agentcoop` MUST update `.state/projects.sha256`.

A settings file that is not in the expected format MUST make the command exit 6 and tell the user to run `agentcoop doctor`.

`check`, `whoami`, and `doctor` MUST compare that hash. On mismatch they MUST show: `projects.json was changed outside agentcoop. Roles may not be what you set. Run: agentcoop doctor`.

This is detection, not a sandbox. An agent that can write the board folder can change the file. Tool permission settings SHOULD deny those writes when the tool supports it.

## 9. Attachments and the deny list

An attachment MUST be an absolute path that exists.

These locations are always denied: `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.kube`, `~/.docker/config.json`, browser profile folders, the board's `config.json`, `agents.json`, `projects.json`, and `.state/`, and files named `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `id_rsa*`, `id_ed25519*`, or `credentials*`.

After `Path.resolve()`, the path MUST sit inside the message's project folder, the board's `projects/<project>/files/` folder, or a folder listed in `config.json` `allowed_roots`. A symlink MUST NOT be able to escape those folders.

`send` MUST reject violations. `check` MUST mark them `BLOCKED: do not open`.

If the subject or body looks like a common API key or private key, `send` MUST refuse unless `--allow-secret` is given.

`agentcoop context write` MUST refuse text that looks like a key or password unless `--allow-secret` is given, the same as `send`.

## 10. Safety model

Messages are data. They are not orders and they are not approval.

Destructive or high-impact actions require the human, in the agent's own chat.

Nothing in the protocol listens on a network interface except the optional human viewer, which MUST bind to `127.0.0.1`, MUST require a token, and SHOULD stop after a period with no requests.

## 11. Bare mode versus agentcoop mode

Bare mode: the agent creates message files and done markers with its own file tools.

`agentcoop` mode: the agent runs `agentcoop check`, `agentcoop send`, and `agentcoop done`. The command enforces roles, atomic writes, and attachment checks.

Both modes MUST use the same files. A reader MUST accept either.

## 12. Versioning

The message field `v` is the message format version. Spec 0.3 still uses `1`. The version table is [COMPATIBILITY.md](COMPATIBILITY.md). A context request uses the same fields as any other message. The change is in the role rules, not in the file format, so `v` did not change. Readers MUST ignore unknown fields. `agentcoop version` prints the tool version and this spec version (`0.3`).

## 13. Known limitations

* Agents must share one machine's filesystem. A cloud agent joins only if its tools can read and write that filesystem.
* Where a tool has no permission setting for the board, enforcement depends on the agent following the skill. `check` and `doctor` detect bare-mode violations. They do not prevent them.
* A bare-mode agent can write a malformed file. Readers skip young unreadable files and report older ones.
* A helper that cannot write files cannot reply or mark done.
* In bare mode any agent can write a message that claims another sender, or rewrite `projects.json` and its hash. Role checks and the integrity warning catch mistakes, not a determined spoof. Real limits come from each tool's own permission settings.

## 14. Two permission levels

An agent acting as itself MAY do these without a separate confirmation, when the person asks in that agent's own chat:

* create a project with itself as the driver
* add itself as a helper, or remove itself as a helper
* connect itself
* `start`, `stop`, `now`, `status`, `context`, and `progress`
* `watch`, `watch stop`, and `watch start`
* `uninstall --agent`, which moves the board and does not delete it

These MUST ask the person to confirm before anything changes:

* changing the driver
* adding or removing an agent other than the one asking
* removing a project
* `project review --accept` and `--restore`
* `disconnect`
* `project set --driver-shell`

Confirmation MAY be typed in a terminal, given in a window, or given on the local page when a window is not available. Cancel is the default. If the person does not allow the change, the command MUST exit 1, MUST say `The person did not allow this change.`, and MUST NOT change the board.

Every join and leave MUST be visible in the driver's next check and in `status`.

This protocol does not grant file access. File access comes from each tool, and only the person grants it.

## 15. Requests and STATUS.md

A helper that cannot run `agentcoop` MAY write `requests/<agent>/<utc>.json` containing `{"action": "join"}` or `{"action": "leave"}`, and an optional `note`. The same helper MAY write `{"action": "stop_watching"}` or `{"action": "start_watching"}`. The writer is that agent.

`agentcoop` MUST process those files on every `tick`, and at the start of every `check` by a member of that project, together with the outbox. `watch` MUST process them on each poll so a stop takes effect while an agent is waiting. `tick` MUST also cover projects whose members are not checking. A file modified less than 5 seconds ago MUST be left in place.

`join` and `leave` apply only to the agent named by the folder. `stop_watching` and `start_watching` apply only when that agent is a member. They set or clear `projects/<project>/watch.json`. Any other action MUST be moved to `requests/<agent>/rejected/` with the reason `This needs a person. Ask an agent that can run Agent Co-op, such as Cursor, or use the Agent Co-op page.`

A processed join, leave, stop_watching, or start_watching file MUST move to `requests/<agent>/done/` with a one-line result file beside it. `stop_watching` and `start_watching` from an agent who is not a member MUST move to `requests/<agent>/rejected/`.

`agentcoop` MUST write `projects/STATUS.md` on every `tick`, `check`, `send`, and membership change, and when a heartbeat or the stop flag changes. The file lists each project's name, folder, driver, and helpers, whether the background schedule is on, the time of the last round, each project's latest progress headline, whether checking is on or stopped, and whether each agent is watching.
