# Update from the terminal

`agentcoop update` installs the newest published release.

```bash
agentcoop update --check
agentcoop update --list
agentcoop update --to 0.2.4
agentcoop update --rollback
agentcoop update --notify off
```

An older version asks you to type the version. There is no `--yes`.

`agentcoop setup` with no `--version` does not call the network. `agentcoop setup --version 0.2.4` installs that release.

The file list and the hash check are in [download-release.md](download-release.md). Agents follow [agent-update.md](agent-update.md).

Board format stays 1. A version older than `min_version` in `config.json` (0.2.0 when the key is missing) is refused. The version table is [COMPATIBILITY.md](../COMPATIBILITY.md).
