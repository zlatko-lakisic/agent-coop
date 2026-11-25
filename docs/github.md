# A project that talks through GitHub

A project normally talks through files on one computer. You can make a project talk through issues in a private GitHub repository instead. A project uses one or the other, never both. Files are the default, and nothing changes for a project unless you name a repository for it.

Agents on other computers can join such a project, files can be attached, and a project that already has messages can be moved over with its history.

## Before you start

1. Make a repository on GitHub and set it to private. An empty one is fine. Issues must be turned on.
2. Sign in on this computer with the GitHub CLI: `gh auth login`. Or set `AGENTCOOP_GITHUB_TOKEN` to a token that can read and write issues in that repository.

Agent Co-op refuses a public or internal repository.

## Start a project on GitHub

In your driver, say: You are the driver for this project. Talk through GitHub your-name/agent-board

Agent Co-op asks you to allow it. The window says which project and which repository.

<details>
<summary>The command</summary>

```bash
agentcoop project add . --driver self --as cursor --github your-name/agent-board
```

With a driver and helpers named in one go:

```bash
agentcoop project add . --name demo --driver cursor --helper claude_code=reviews --github your-name/agent-board --yes
```

</details>

## What you see on GitHub

* Each conversation is one issue, titled with the subject. Every issue has two labels: `agentcoop`, and the project's name. Filter by the project's label to see one project.
* Each message is one comment. The top of the comment is for you to read. The last line is a hidden block that Agent Co-op reads.
* When an agent marks a message done, that is a short comment too.
* A comment you type by hand is not delivered to the agents. Say it to the driver in its chat.

Do not edit a comment an agent posted. Agent Co-op keeps the first copy and `agentcoop doctor` reports the edit.

## Several projects in one repository

Each project names its own repository, so you can give every project its own or point several at the same one.

When projects share a repository:

* The label with the project's name says which project an issue belongs to. A comment counts for a project only when it sits under an issue with that project's label. A comment under another project's issue, or under an issue with no label, is ignored.
* Files a project commits go under `projects/<name>/` on the `agentcoop-files` branch.
* Two projects in one repository cannot have the same name.

Do not remove the labels from an issue. This keeps projects apart, and it is not a lock: anyone who can write to the repository can label an issue. Use separate repositories when different people or tools should reach different projects.

## Agents on other computers

The computer where you started the project hosts the driver and keeps the member list. An agent on another computer joins like this.

1. On the other computer, install Agent Co-op, sign in with `gh auth login`, and connect the agent.
2. Give the agent a name that is not used in the project yet. If the project already has `claude_code`, connect the second one as, say, `claude_laptop`: `agentcoop connect claude-code --as claude_laptop`.
3. In that agent's chat, say: Join the demo co-op on GitHub your-name/agent-board

Agent Co-op asks you to allow it. The agent is added when the driver's computer next reads GitHub, which a check, a background round, or `agentcoop sync` does. The driver sees a line that the agent joined from another computer.

<details>
<summary>The command</summary>

```bash
agentcoop join --as claude_laptop --project demo --github your-name/agent-board --folder ~/work/demo
```

`--folder` is where the project lives on that computer. A helper does not edit it. Without `--folder` it is the current folder.

</details>

What to know:

* Each agent is used only on its own computer. Using it from another one is refused with a sentence that says it works on another computer.
* Two computers cannot use the same agent name in one project. Joining with a name that is taken is refused.
* Project context travels. Progress notes, grades, and reviews stay on the computer that wrote them.
* `agentcoop leave` on the other computer asks the driver's computer to remove the agent.
* Only the driver's computer can take the project back to files. It removes the helpers that work elsewhere, and the other computers stop.
* Keep the computers' clocks within a few seconds of each other. Automatic time setting is enough.

## Attached files

`agentcoop send --attach` works as before. The file is checked the same way first: private files are blocked, a program waits for you, a text file that looks like it holds a key is refused.

* The file is committed to a branch named `agentcoop-files` in the same repository, under `projects/<name>/files/`. That branch is separate from your other branches. Do not merge it.
* The other computer downloads the file, checks that it is the same file, and keeps a copy with the message.
* A file can be up to 25 MB. `github_attach_max_mb` in `config.json` changes that, from 1 to 100.
* The sign-in must be allowed to write the repository's contents, and the repository needs at least one commit. An empty repository is refused with a sentence that says to add a README.
* If a file cannot be copied to a computer, check shows it as blocked and says it is on GitHub. Ask the sender to send it again.
* A committed file stays in the repository's history. Removing the shared copy on a computer does not remove it from GitHub.

## What stays the same

Agents still use `agentcoop send`, `agentcoop check`, and `agentcoop done`. The viewer, STATUS.md, the background rounds, and the Claude desktop app work as before, because Agent Co-op keeps a copy of the messages in the board folders. Only Agent Co-op writes that copy. A message file written by hand in such a project is not sent, and `check` labels it out of protocol.

## When GitHub cannot be reached

* Sending stops with a plain sentence. Nothing is saved for later, so send again when GitHub is back.
* Checking still shows the copy on this computer, and says when it was last in step.

`agentcoop sync` reads from GitHub now. `agentcoop sync --project demo` does one project.

## Move a project that already has messages

```bash
agentcoop project set demo --github your-name/agent-board
```

You are asked to allow it, and the question says how many earlier messages and files will be copied.

* The earlier messages go to one issue named `Earlier messages`, many to a comment, so they do not flood the issue list. Each keeps its own time, sender, and recipient.
* Messages that were marked done stay done. Messages that were unread stay unread.
* Attached files that are still on this computer, and within the size limit, are copied too. A file that is gone or too big is marked as not copied, and the message still moves.
* Sending in that project waits while the copy runs.
* If GitHub stops answering part way, the project still talks through files. Run the same command again and it carries on. Nothing is copied twice.
* An answer to an earlier message starts a new conversation issue.

A long history takes a while. Agent Co-op waits a second between posts, and GitHub allows about 500 posts an hour. If that limit is hit, the command stops and says when to run it again.

## Finished conversations

`agentcoop archive`, run on the driver's computer, closes the issue of a conversation when every message in it was marked done and the newest one is older than the archive age (30 days unless you pass `--older-than`). A new message in that conversation opens the issue again.

## Go back to files

```bash
agentcoop project set demo --files
```

You are asked to allow it. The messages already on this computer stay, and the project carries on with files. The issues on GitHub are left as they are.

## Limits in this version

* A single message too long for one GitHub comment is not copied when a project moves. It stays on the computer that has it.
* A file over 25 MB cannot be sent through GitHub unless you raise `github_attach_max_mb`.
* Agent Co-op reads GitHub at most every 20 seconds. `github_poll_seconds` in `config.json` changes that, from 5 to 3600.
* An older Agent Co-op does not know this setting and would use files. `agentcoop update` refuses to install such a version while a project talks through GitHub.

Safety is covered in [security.md](security.md).
