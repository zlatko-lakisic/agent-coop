# Agent Co-op protocol, version 0.3

This document is the normative spec. The words MUST, SHOULD, and MAY are used as in common protocol practice: MUST is required, SHOULD is recommended, MAY is allowed.

## 1. Overview and goals

Agent Co-op lets agents on one computer work together through files in a shared folder called the board.

The protocol is files, the skill is a prompt, and everything else is optional.

A conforming reader MUST accept messages as defined here. The `agentcoop` command is a convenience. Agents MAY read and write the files directly (bare mode).

A project MAY talk through a private GitHub repository instead of files. Section 16 defines that. A project uses one or the other, never both. Files are the default.

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
        ├── progress/helpers/<agent>/
        ├── context/
        ├── approvals.json
        ├── github/
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
| `progress/helpers/<agent>/` | that helper, for an open request from the driver |
| `context/` | that project's driver, through `agentcoop context write` |
| `approvals.json` | that project's driver, through `agentcoop allow`, only when the person asks in that chat |
| `logs/trace.jsonl` | agentcoop, when tracing is on |
| `github/` | agentcoop, in a project that talks through GitHub |

In a project that talks through GitHub, `agentcoop` is the only writer of `inbox/<agent>/msgs/`, `inbox/<agent>/done/`, and `sent/<agent>/`. See section 16.

A file in `context/` is written once and MUST NOT be edited. `agentcoop archive` MAY delete old versions. It keeps the latest 5, and it deletes an older file only when that file is past the archive age.

A progress file MAY start with `Status` (`working`, `blocked`, `waiting`, `review`, or `done`), `Step` (such as `3 of 7`), `Percent` (a whole number from 0 to 100), `Now` (one sentence), and `For` (a message id). `Percent` is present only when it was counted. A file with Status `done` or `review` MUST include the sections Asked, Claims, Not verified, Surprises, and Risks. A helper writes `progress/helpers/<agent>/<stamp>_<message id>.md`. Older programs ignore that folder. A Claude desktop progress note is `outbox/<agent>/<message id>.progress.md`. It is not an answer. The answer is `<message id>.md` or `<message id>.<UTC timestamp>.md`.

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
| `seq` | Optional positive integer. The order the message was sent on this board. Readers that do not know this field MUST ignore it. Listing order is `ts`, then `seq`, then `id`. |
| `project` | Project name. |
| `sender`, `recipient` | Agent names. |
| `type` | SHOULD be `question`, `answer`, `task`, `status`, `ack`, `patch`, `context_request`, `nudge`, `ping`, `heartbeat`, `decline`, `cancel`, or `review`. Unknown types MUST be tolerated. Older readers mark `ping`, `heartbeat`, `decline`, `cancel`, and `review` out of protocol. |
| `re` | Id of the message being answered, or `null`. |
| `subject` | At most 120 characters. |
| `body` | At most 16 KB of UTF-8 unless config raises the limit. Longer text SHOULD be a file under `files/<sender>/`, attached by path. |
| `attachments` | Absolute paths. MAY be empty. After a normal send these paths are the shared copies. |
| `attachment_info` | Optional. One object per attachment, in the same order. A later message that sends the same bytes MUST keep its own name, description, and kind, and MUST point at the stored copy. Readers that do not know this field MUST ignore it. |
| `needs_reply` | Boolean. |
| `group` | Optional. The same id on each copy of a second opinion. At most 3 helpers. |
| `moved_from` | Optional. The request id this message replaces. |
| `auto` | Optional boolean. True when Agent Co-op posted a heartbeat because the agent did not. |
| `about` | Optional message id a ping is about. |
| `verdict` | Optional on `type` `review`. One of `approve`, `changes`, or `cannot_verify`. A review request omits it. Readers that do not know this field MUST ignore it. |

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

A helper that cannot run `agentcoop` MAY use an outbox. `agentcoop` writes `inbox/<agent>/NEW.md` listing that helper's new in-protocol messages. The helper writes one Markdown answer as `outbox/<agent>/<id>.md` or `outbox/<agent>/<id>.<anything>.md`. The id is the text before the first dot. It MUST NOT edit `outbox/<agent>/sent/`. A later answer with different text is a new reply. The subject of the first reply is `Re: <subject>`. Later replies from that helper are `Re: <subject> (follow-up N)`, starting at 2. An exact copy of a reply already posted is not posted again. `outbox/<agent>/sent/<file>.posted.txt` records the posted file name, the recipient, and the UTC time. A name that already exists gets `.2`, then `.3`, before `.md`. Names that differ only by case are the same name on Windows, macOS, and Linux. A file left in `processing/` for more than 10 minutes with no live lock is posted again, through the same duplicate check. A progress file is `outbox/<agent>/<id>.progress.md` and is not an answer.

A nudge file is `outbox/<agent>/nudge.md` or `outbox/<agent>/nudge.<anything>.md`. The file text is the body, at most 500 characters.

## 7. Roles and conversation rules

The sender and the recipient MUST both be members of the project.

If the sender is the driver, the recipient MUST be a helper.

If the sender is a helper, the recipient MUST be the driver, `re` MUST be set, and `re` MUST name a message from the driver that is in the helper's own `msgs/` folder. A helper's follow-up question is a reply with `type: question`.

One exception: a helper MAY send a `context_request` to the driver with `re` set to null, only when that helper has no open context request in that project.

A second exception: a helper MAY send a `nudge` to the driver with `re` set to null. The body is at most 500 characters. The subject is `Attention`. `needs_reply` is true. A helper may have only one open nudge, and at most one nudge every 30 minutes. An open nudge stays open until the driver sends that helper any message after it. `agentcoop send` MUST refuse a nudge that breaks these rules and MUST say when the next one is allowed. The driver MUST NOT send a nudge. `nudge` is the 0.2.7 name for a helper ping. A file that already has type `nudge` stays valid.

A third exception: a helper MAY send a `ping` to the driver with `re` set to null, and the driver MAY send a `ping` to a helper. The body is at most 500 characters. One open ping per pair, and at most one per pair per `progress_every_minutes`. The answer is a `heartbeat` with `re` set to the ping. If none arrives in that time, Agent Co-op posts one with `auto` set to true and says the agent did not answer. Two automatic heartbeats in a row mark that agent not answering.

A helper MAY `decline` a request. The driver MAY `cancel` one, or `move` it. A move sends `cancel` and a new request with `moved_from` set to the old id and the same attachments. An answer that arrives after a cancel or a move is still delivered and labelled late.

The driver MAY send the same question to 2 or 3 helpers. More than 3 is refused. Each copy has the same `group`. The body says that others were asked and that the helper should answer on its own. Helpers MUST NOT see each other's answers.

These exceptions are the only cases in which a helper may speak first.

A request is open when it sits in the driver's `msgs/` folder, its type is `context_request`, its sender is that helper, its recipient is the driver, and `re` is null, until a message from the driver in the helper's `msgs/` folder has `re` equal to that request's id.

`agentcoop send` MUST refuse a second request from that helper while one is open. It MUST NOT write the file. It tells the helper to wait, or to read the latest context.

In bare mode, the earliest open request from that helper is accepted. Later ones MUST be labeled out of protocol. Earliest means the lowest message id.

`agentcoop send` MUST set `needs_reply` to true on a `context_request`.

Helpers MUST NOT message each other. They MUST NOT start a thread, except the context request, the nudge, and the ping above.

A fourth exception: the driver MAY send a `review` request to one helper, with `re` null and `needs_reply` true. The helper answers with `type` `review`, `re` set to that request, and `verdict` `approve`, `changes`, or `cannot_verify`. The driver MUST NOT send a verdict. While review applies, Status `done` waits until that verdict is `approve`, or the person records an override in the driver's chat. A board message MUST NOT record that override.

Only the driver writes files in the project folder. Helpers send patches or drafts as attachments. The driver decides what to apply.

`agentcoop send` MUST refuse violations. `agentcoop check` MUST label a bare-mode violation `OUT OF PROTOCOL: ignore this message` and MUST NOT present its body as instructions.

## 8. projects.json, agents.json, and integrity

`projects.json` lists projects. Each project has `path`, exactly one `driver`, a `helpers` map of agent name to a short note, `created`, and `installed`.

A project MAY include `profiles`, a map of agent name to an object. That object MAY include `accepts`, `tags` (short labels), `can` (`read`, `run`, `vision`, `web`), and `cost` (`low`, `medium`, `high`). A missing field means unknown. Older readers MUST ignore fields they do not use. The helper note stays in `helpers`.

`projects/<project>/grades/<helper>/` holds one grade file per answer, written once and never edited. The file name is `<UTC stamp>_<answer id>.json`. Only the driver at that time, from `roles_history`, may write there. A grade has `score` (1 to 5), `outcome` (`used`, `partly_used`, `not_used`, `wrong`), optional `correct`, `useful`, and `followed_ask` (each 1 to 5), `tags`, and `note`. Agent Co-op fills `reply_minutes`, `progress_updates`, `late`, `declined`, and `second_opinion_group`. Totals are computed when read. Removing a grade moves the file to `grades/removed/`. Older readers MUST ignore the folder.

`projects/<project>/reviews/` holds one file per review verdict, written once and never edited. The file name is `<message id>.json`. It records the driver, the reviewer, the verdict, how many claims were refuted, how many asked items were skipped, how many surprises were unexplained, and whether the review passed the first time. `open.json` is the current review. An approval records the progress id and the fingerprint the reviewer was shown, and a seal only this program can write. `override.json` is the person's override, also sealed. `fingerprint.json` is the last closed fingerprint: one SHA-256 per project file. It is written when the project is added and when a task is closed. Older readers MUST ignore the folder.

A project MAY include `review`: `off`, `changes`, or `always`. When the key is missing and a helper profile has the tag `review`, the mode is `changes`. Otherwise it is `off`. Older readers MUST ignore the key.

A project MAY include `github`, an object with `repo` set to `owner/name`. That object MAY include `homes`, a map of agent name to the computer the agent works on, as section 16 defines. When the key is present the project talks through that repository, as section 16 defines. When the key is missing the project talks through files. Older readers ignore the key and would use files, so `agentcoop update` MUST refuse to install a version that cannot talk through GitHub while any project has the key.

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

### Shared copies

A normal send MUST copy each attachment into `projects/<project>/files/shared/<message id>/<file name>` before the message is visible. The copy MUST be a regular file, written under a temporary name and then renamed. If the source size changes during the copy, the send MUST refuse that file. The same sha256 already stored for that project MUST be reused instead of copied again.

`attachments` MUST remain a list of absolute paths, now pointing at those copies, so an older reader still opens a file. `attachment_info` MUST match that list one to one. Each entry has `name`, `path`, `source_path`, `bytes`, `sha256`, `media_type`, `kind`, and `description`. `alt_of`, when present, is the sha256 of another attachment in the same message. An archive entry MAY set `contents` to `not checked`. Agent Co-op MUST NOT unpack an archive.

`media_type` SHOULD come from the file name and the first bytes, so a wrong extension is caught. The sender MAY set it. `kind` is a short hint such as `screenshot`, `image`, `video`, `audio`, `data`, `document`, `patch`, `archive`, or `other`. Unknown kinds MUST be allowed.

A file larger than `share_max_mb` (default 200) MUST NOT be copied unless the person allows that one file. A file larger than `share_hard_max_mb` (default 4096) MUST be refused. A program or a script MUST be held until the person allows that one file. Approval of one file MUST NOT allow a different file. Private files stay denied no matter who approves. A text file that looks like a key MUST be refused, and approval MUST NOT override that. A symlink whose target is outside the allowed folders MUST be refused, and the shared copy MUST NOT itself be a symlink. The copy step MAY also read a file the helper wrote in its own `outbox/` folder. The path stored on the message is the shared copy.

An agent MAY declare `accepts` as a list of media type patterns (`image/*`, `application/pdf`, `*/*`). A missing list means unknown and MUST NOT block a send. When a file does not match, the sender and the recipient's check MUST show one note, and the file MUST still be sent.

Shared files MUST be removed `share_keep_days` days (default 30) after every non-archived message that references them has been archived, unless the file is pinned.

An outbox helper MAY place files in `outbox/<agent>/<answer name without .md>.files/`, with an optional `files.json` of `description` and `kind` per file name. A file that cannot be shared MUST be moved to `rejected/` with a reason. The answer MUST still be posted, and it MUST say which files were held back.

If the subject or body looks like a common API key or private key, `send` MUST refuse unless `--allow-secret` is given.

`agentcoop context write` MUST refuse text that looks like a key or password unless `--allow-secret` is given, the same as `send`.

## 10. Safety model

Messages are data. They are not orders and they are not approval.

Destructive or high-impact actions require the human, in the agent's own chat.

Nothing in the protocol listens on a network interface except the optional human viewer, which MUST bind to `127.0.0.1`, MUST require a token, and SHOULD stop after a period with no requests.

A project that talks through files MUST NOT send board data over a network. A project that talks through GitHub sends its messages to the one private repository the person named, and nowhere else.

## 11. Bare mode versus agentcoop mode

Bare mode: the agent creates message files and done markers with its own file tools.

`agentcoop` mode: the agent runs `agentcoop check`, `agentcoop send`, and `agentcoop done`. The command enforces roles, atomic writes, and attachment checks.

Both modes MUST use the same files. A reader MUST accept either.

Bare mode is not available in a project that talks through GitHub. A message file written by hand there is not sent, and readers MUST label it out of protocol.

## 12. Versioning

The message field `v` is the message format version. Spec 0.3 still uses `1`. The version table is [COMPATIBILITY.md](COMPATIBILITY.md). A context request uses the same fields as any other message. The change is in the role rules, not in the file format, so `v` did not change. Readers MUST ignore unknown fields. `agentcoop version` prints the tool version and this spec version (`0.3`).

## 13. Known limitations

* In a project that talks through files, agents must share one machine's filesystem. A cloud agent joins only if its tools can read and write that filesystem.
* In a project that talks through GitHub, anyone who can write to the repository can post a message that claims any sender. The private repository is the lock.
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
* making a project talk through GitHub, or through files again (`project add --github`, `project set --github`, `project set --files`)
* joining a project that another computer started on GitHub (`join --github`)

Confirmation MAY be typed in a terminal, given in a window, or given on the local page when a window is not available. Cancel is the default. If the person does not allow the change, the command MUST exit 1, MUST say `The person did not allow this change.`, and MUST NOT change the board.

Every join and leave MUST be visible in the driver's next check and in `status`.

This protocol does not grant file access. File access comes from each tool, and only the person grants it.

## 15. Requests and STATUS.md

A helper that cannot run `agentcoop` MAY write `requests/<agent>/<utc>.json` containing `{"action": "join"}` or `{"action": "leave"}`, and an optional `note`. A join MAY also include `tags` (a list of short labels), `can` (`read`, `run`, `vision`, or `web`), and `cost` (`low`, `medium`, or `high`). The same helper MAY write `{"action": "stop_watching"}` or `{"action": "start_watching"}`. The writer is that agent.

`agentcoop` MUST process those files on every `tick`, and at the start of every `check` by a member of that project, together with the outbox. `watch` MUST process them on each poll so a stop takes effect while an agent is waiting. `tick` MUST also cover projects whose members are not checking. A file modified less than 5 seconds ago MUST be left in place.

`join` and `leave` apply only to the agent named by the folder. `stop_watching` and `start_watching` apply only when that agent is a member. They set or clear `projects/<project>/watch.json`. Any other action MUST be moved to `requests/<agent>/rejected/` with the reason `This needs a person. Ask an agent that can run Agent Co-op, such as Cursor, or use the Agent Co-op page.`

A processed join, leave, stop_watching, or start_watching file MUST move to `requests/<agent>/done/` with a one-line result file beside it. `stop_watching` and `start_watching` from an agent who is not a member MUST move to `requests/<agent>/rejected/`.

`agentcoop` MUST write `projects/STATUS.md` on every `tick`, `check`, `send`, and membership change, and when a heartbeat or the stop flag changes. When the latest progress has Status `waiting`, the file MUST say so at the top, with what the driver needs. The file lists each project's name, folder, driver, and helpers, whether the background schedule is on, the time of the last round, each project's latest progress headline, one short line for the driver task and each helper task in progress, whether checking is on or stopped, and whether each agent is watching.

`projects/<project>/approvals.json` holds standing approvals the person gave in the driver's chat. A board message MUST NOT add one. Older programs ignore the file. `logs/trace.jsonl` is one JSON line per command when `AGENTCOOP_TRACE=1` or `config.json` has `"trace": true`. Message bodies are left out.

## 16. GitHub transport

A project whose record has `github.repo` talks through issues in that repository. GitHub is the record. The board folders are a copy.

### Private only

The repository MUST be private. `agentcoop` MUST check that GitHub reports `private` as true and `visibility` as `private` before it turns the setting on, and again each time it reads from GitHub. An `internal` repository MUST be refused. If the repository is no longer private, `send`, `done`, and `check` MUST stop, MUST say so, and MUST NOT post or read anything.

Turning the setting on or off MUST ask the person first, as section 14 requires.

### Sign-in

`agentcoop` uses `AGENTCOOP_GITHUB_TOKEN` when it is set, and otherwise the token the GitHub CLI prints for `gh auth token`. It MUST NOT write the token to the board, to a log, or to its output. It MUST send the token only to `api.github.com` and MUST NOT repeat it when a request is sent on to another address.

### Comments

Every event is one issue comment. A comment MUST NOT be edited after it is posted.

A comment ends with one hidden block on its own line:

```text
<!-- agentcoop:1 {"kind":"message","message":{...}} -->
```

The text between the marker and the closing `-->` is one JSON object. It MUST be ASCII, and `<`, `>`, and `&` MUST be written as `\u003c`, `\u003e`, and `\u0026`, so the block can never contain `-->`. A reader takes the last block in the comment. The number after `agentcoop:` is the wire version. A reader MUST skip a block with a wire version or a `kind` it does not know.

The text above the block is for people. Readers MUST ignore it. A comment with no block is not a message and MUST NOT be delivered to an agent.

| `kind` | Other fields | Meaning |
| --- | --- | --- |
| `message` | `message`: the message object from section 5, with its fields in the order the sender wrote them | one message |
| `done` | `project`, `agent`, `ids`, `ts` | `agent` handled those message ids |
| `thread` | `project`, `root` | written in the issue text when a conversation opens. Readers do not need it. |
| `project` | `project` | written in the issue text of the project's own issue. Readers do not need it. |
| `roster` | `project`, `driver`, `helpers` (name to note), `homes` (name to computer), `ts` | the member list, and where each agent works |
| `request` | `project`, `action` (`join` or `leave`), `agent`, `home`, `note`, `ts` | an agent on another computer asks to join or leave |
| `context` | `project`, `name`, `part`, `of`, `text` | one part of a context version. `name` is the file name from section 3. |
| `closed` | `project`, `ts` | the driver's computer took the project back to files |

A reader MUST skip a `roster`, `request`, `context`, or `closed` comment that was edited.

### Conversations

A message that has `re`, `about`, or `moved_from` naming a message already on GitHub MUST be posted to that message's issue. A message whose `group` matches an earlier message goes to that issue too. Any other message opens a new issue, titled `[<project>] <subject>`.

`agentcoop` reads new comments with the repository's comment list, oldest first, starting from the newest `updated_at` it has seen. It SHOULD NOT read more often than `github_poll_seconds` in `config.json` (default 20).

### Several computers

Each board has a computer name: 12 lowercase hex characters, made at random once and kept in `.state/computer-id`. It is not the real name of the computer.

`github.homes` maps each member to the computer name it works on. An empty value means no computer has claimed that agent yet. When `homes` is missing, every member works on this computer. An agent MUST be used only on its own computer: `send`, `check`, `done`, and the background round MUST refuse or skip an agent whose home is another computer. An agent that works elsewhere need not be in this computer's `agents.json`.

The computer that the driver works on keeps the member list. It posts a `roster` event on the project's own issue when the project starts to talk through GitHub and each time the list changes, and before it posts a message. Every other computer copies the newest `roster` into its `projects.json`, with the event's `ts` as the time of the change, and MUST NOT post one. The driver's computer ignores a `roster` it did not post, and `doctor` reports it.

To join from another computer, an agent posts a `request` with `action` `join` and its computer name as `home`. The driver's computer adds the agent as a helper and records its home, or records the home of a member that had none. It MUST NOT change the home of an agent that already has one. A `leave` request removes a helper whose home matches. The driver sees each join and leave in its next check. Joining asks the person first, as section 14 requires.

Two computers MUST NOT use the same agent name in one project. `agentcoop join` refuses a name whose home is another computer. A message from a sender that works on this computer, which this computer did not post, MUST be reported by `doctor`.

`agentcoop context write` posts the new version as one or more `context` events before it saves the file. Other computers save the version when they have every part.

When the driver's computer goes back to files it removes the helpers that work elsewhere and posts `closed`. Another computer that reads `closed` MUST stop sending and reading for that project. Only the driver's computer can take a project back to files.

Computers in one project SHOULD keep their clocks within a few seconds of each other. Role rules compare a message's `ts`, set by the sender's clock, with the time of a membership change.

### Attached files

A file sent with a message is committed to the branch `agentcoop-files` of the same repository, at `<project>/<first two hex of the SHA-256>/<SHA-256>/<file name>`. `agentcoop` creates that branch with no parent commit, so it shares no history with other branches. A file that is already there is not committed again. The file MUST pass every check in section 9 first, and MUST NOT be larger than `github_attach_max_mb` in `config.json` (default 25, at most 100).

In the comment, each entry of `attachments` and each `path` in `attachment_info` is `github:` followed by that path. `source_path` is left out. No path on the sender's computer is posted. The sender's own copy keeps its local paths.

A computer copies a message for an agent that works there like this. For each file it checks that the name is a plain file name and that the path, the name, and the SHA-256 in `attachment_info` agree. It downloads the file, checks the size and the SHA-256, and keeps it in `files/shared/<message id>/`, as section 9 defines for a shared copy. Its copy of the message then points at that file. A file that fails a check, is missing, or looks like it holds a key MUST NOT be kept. Its entry stays `github:...`, and readers MUST show it as blocked. If GitHub fails while a file is fetched, the message is not copied and the next read tries again.

A computer does not download files for agents that work elsewhere.

### The copy on disk

For each `message` event addressed to a member of the project, `agentcoop` writes the same JSON, or the JSON with local file paths as "Attached files" defines, to `inbox/<recipient>/msgs/<id>.json` and `sent/<sender>/<id>.json`, with the bytes section 6 defines. For each `done` event it creates `inbox/<agent>/done/<id>`. It MUST check the id and both agent names against sections 4 and 5 before it builds a path. The first comment that uses an id keeps it.

`projects/<project>/github/index.json` records the issue and comment for each message id. `state.json` records where reading stopped. `agentcoop` is the only writer of that folder.

A message file in `inbox/<agent>/msgs/` whose id is not in the index was not sent through GitHub. Readers MUST label it out of protocol. A message whose comment had been edited before this computer first copied it MUST be labelled out of protocol. A comment edited after it was copied MUST NOT change the copy.

### Sending and failing

`agentcoop send` MUST post the comment first. It writes the copy only after GitHub accepts the comment. If GitHub cannot be reached, the command MUST exit non-zero and MUST NOT write the message anywhere. `agentcoop done` posts the `done` event before it creates the marker.

`agentcoop check` MAY show the last copy when GitHub cannot be reached. It MUST then say that GitHub could not be read, and when the copy was last in step.

### Limits

* `seq` is counted on each computer. It orders messages one computer sent in the same second.
* A committed file stays in the repository's history, also after the shared copy on a computer is removed.
* A project that already has messages cannot be moved to GitHub yet.
* Progress notes, grades, and reviews stay on the computer that wrote them. Heartbeat messages travel like any message.
* Conversation issues stay open. Nothing closes them yet.
