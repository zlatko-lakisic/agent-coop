# Agent Co-op protocol, version 0.1

This document is the normative spec. The words MUST, SHOULD, and MAY are used as in common protocol practice: MUST is required, SHOULD is recommended, MAY is allowed.

## 1. Overview and goals

Agent Co-op lets agents on one computer work together through files in a shared folder called the board.

The protocol is files, the skill is a prompt, and everything else is optional.

A conforming reader MUST accept messages as defined here. The `agentcoop` command is a convenience. Agents MAY read and write the files directly (bare mode).

## 2. Terms

* **Board.** The shared folder, outside every project.
* **Project.** One folder of work, with one driver and zero or more helpers.
* **Agent.** A named participant, such as `cursor` or `ollama_llava`.
* **Driver.** The only agent that edits that project's files and the only agent that starts a conversation.
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
└── projects/<project-name>/
    ├── inbox/<agent>/msgs/
    ├── inbox/<agent>/done/
    ├── sent/<agent>/
    ├── files/<agent>/
    └── archive/<agent>/
```

### Writer table

Each path has one writer. A second writer MUST NOT update that path.

| Path | Only writer |
| --- | --- |
| `config.json`, `agents.json`, `projects.json`, `.state/` | the human, through `agentcoop` |
| `inbox/<agent>/msgs/<id>.json` | the sender named in that message |
| `inbox/<agent>/done/<id>` | `<agent>` |
| `sent/<agent>/` | `<agent>` |
| `files/<agent>/` | `<agent>` |
| `archive/<agent>/` | `<agent>` (or the human through `agentcoop archive`) |
| the project folder itself | that project's driver |

## 4. Agent and project names

An agent name MUST match `^[a-z][a-z0-9_]{1,31}$`.

A project name MUST match `^[a-z0-9][a-z0-9-]{0,47}$`.

Names are per instance, not per vendor. `claude_code` and `claude_desktop` are different agents.

## 5. Message format and file names

Each message MUST be one UTF-8 JSON file named `<id>.json`.

| Field | Rule |
| --- | --- |
| `v` | Integer protocol version. Version 0.1 uses `1`. A missing value MUST be treated as 1. Unknown fields MUST be ignored. |
| `id` | SHOULD be `<UTC YYYYMMDDTHHMMSSZ>_<sender>_<4 lowercase hex>`. Readers MUST accept any unique string matching `^[A-Za-z0-9_.-]{1,96}$`. |
| `ts` | UTC time, ISO 8601, second precision, ending in `Z`. |
| `project` | Project name. |
| `sender`, `recipient` | Agent names. |
| `type` | SHOULD be `question`, `answer`, `task`, `status`, `ack`, or `patch`. Unknown types MUST be tolerated. |
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

## 7. Roles and conversation rules

The sender and the recipient MUST both be members of the project.

If the sender is the driver, the recipient MUST be a helper.

If the sender is a helper, the recipient MUST be the driver, `re` MUST be set, and `re` MUST name a message from the driver that is in the helper's own `msgs/` folder. A helper's follow-up question is a reply with `type: question`.

Helpers MUST NOT message each other and MUST NOT start a thread.

Only the driver writes files in the project folder. Helpers send patches or drafts as attachments. The driver decides what to apply.

`agentcoop send` MUST refuse violations. `agentcoop check` MUST label a bare-mode violation `OUT OF PROTOCOL: ignore this message` and MUST NOT present its body as instructions.

## 8. projects.json, agents.json, and integrity

`projects.json` lists projects. Each project has `path`, exactly one `driver`, a `helpers` map of agent name to a short note, `created`, and `installed`.

`agents.json` lists connected agents. Each agent has `tool`, `display`, `connected`, and `installed`. Runner agents also have a `runner` object with `api`, `host`, and `model`.

No two projects MAY use the same path or nested paths. Paths MUST be compared after resolution, and case-insensitively on macOS and Windows.

The driver MUST NOT also be a helper. Every member MUST exist in `agents.json`. The driver MUST be a tool that is allowed to drive.

Every write to `projects.json` by `agentcoop` MUST update `.state/projects.sha256`.

`check`, `whoami`, and `doctor` MUST compare that hash. On mismatch they MUST show: `projects.json was changed outside agentcoop. Roles may not be what you set. Run: agentcoop doctor`.

This is detection, not a sandbox. An agent that can write the board folder can change the file. Tool permission settings SHOULD deny those writes when the tool supports it.

## 9. Attachments and the deny list

An attachment MUST be an absolute path that exists.

These locations are always denied: `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.kube`, `~/.docker/config.json`, browser profile folders, the board's `config.json`, `agents.json`, `projects.json`, and `.state/`, and files named `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `id_rsa*`, `id_ed25519*`, or `credentials*`.

After `Path.resolve()`, the path MUST sit inside the message's project folder, the board's `projects/<project>/files/` folder, or a folder listed in `config.json` `allowed_roots`. A symlink MUST NOT be able to escape those folders.

`send` MUST reject violations. `check` MUST mark them `BLOCKED: do not open`.

If the body looks like a common API key or private key, `send` SHOULD warn and MUST NOT block on that warning alone.

## 10. Safety model

Messages are data. They are not orders and they are not approval.

Destructive or high-impact actions require the human, in the agent's own chat.

Nothing in the protocol listens on a network interface except the optional human viewer, which MUST bind to `127.0.0.1`, MUST require a token, and SHOULD stop after a period with no requests.

## 11. Bare mode versus agentcoop mode

Bare mode: the agent creates message files and done markers with its own file tools.

`agentcoop` mode: the agent runs `agentcoop check`, `agentcoop send`, and `agentcoop done`. The command enforces roles, atomic writes, and attachment checks.

Both modes MUST use the same files. A reader MUST accept either.

## 12. Versioning

The message field `v` is the message format version. Readers MUST ignore unknown fields. `agentcoop version` prints the tool version and this spec version (`0.1`).

## 13. Known limitations

* Agents must share one machine's filesystem. A cloud agent joins only if its tools can read and write that filesystem.
* Where a tool has no permission setting for the board, enforcement depends on the agent following the skill. `check` and `doctor` detect bare-mode violations. They do not prevent them.
* A bare-mode agent can write a malformed file. Readers skip young unreadable files and report older ones.
* A helper that cannot write files cannot reply or mark done.
