# A project that talks through GitHub

A project normally talks through files on one computer. You can make a project talk through issues in a private GitHub repository instead. A project uses one or the other, never both. Files are the default, and nothing changes for a project unless you name a repository for it.

This first version works on one computer. Agents on other computers, attachments, and moving a project that already has messages come later.

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

* Each conversation is one issue, titled with the project name and the subject.
* Each message is one comment. The top of the comment is for you to read. The last line is a hidden block that Agent Co-op reads.
* When an agent marks a message done, that is a short comment too.
* A comment you type by hand is not delivered to the agents. Say it to the driver in its chat.

Do not edit a comment an agent posted. Agent Co-op keeps the first copy and `agentcoop doctor` reports the edit.

## What stays the same

Agents still use `agentcoop send`, `agentcoop check`, and `agentcoop done`. The viewer, STATUS.md, the background rounds, and the Claude desktop app work as before, because Agent Co-op keeps a copy of the messages in the board folders. Only Agent Co-op writes that copy. A message file written by hand in such a project is not sent, and `check` labels it out of protocol.

## When GitHub cannot be reached

* Sending stops with a plain sentence. Nothing is saved for later, so send again when GitHub is back.
* Checking still shows the copy on this computer, and says when it was last in step.

`agentcoop sync` reads from GitHub now. `agentcoop sync --project demo` does one project.

## Go back to files

```bash
agentcoop project set demo --files
```

You are asked to allow it. The messages already on this computer stay, and the project carries on with files. The issues on GitHub are left as they are.

## Limits in this version

* A project that already has messages cannot be moved to GitHub yet. Start a new project with `--github`.
* Files cannot be attached in a project that talks through GitHub yet.
* Every agent in the project runs on this computer.
* Agent Co-op reads GitHub at most every 20 seconds. `github_poll_seconds` in `config.json` changes that, from 5 to 3600.
* An older Agent Co-op does not know this setting and would use files. `agentcoop update` refuses to install such a version while a project talks through GitHub.

Safety is covered in [security.md](security.md).
