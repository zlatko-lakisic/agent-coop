# Compatibility

Generated from src/agentcoop/data/compat.json. Do not edit by hand.

One row per published version, newest first. The Unreleased row is work that is not in a version yet.

| Version | Date | Message format | Spec | Board format | Oldest reader | Board changes | Settings changes | Downgrade loses | Desktop skill | Upgrade from |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Unreleased |  | 1 | 0.3 | 1 | 0.2.0 | None | None | Nothing | Same | 0.2.0 |
| 0.2.5 | 2026-10-22 | 1 | 0.3 | 1 | 0.2.0 | Optional files update-check.json and bin/previous. Older programs ignore them. | config.json may contain board_format, min_version, and update_check. All three are optional. | You lose update from chat, the daily new-version notice, and the watch exit when the program itself changes. The board still opens. | Changed | 0.2.0 |
| 0.2.4 | 2026-10-22 | 1 | 0.3 | 1 | 0.2.0 | projects/<name>/watch.json and projects/<name>/watchers/. STATUS.md says whether checking is on. | None | Stop the co-op no longer stops agents. Watch no longer waits without a model. | Changed | Unknown |
| 0.2.3 | 2026-10-22 | 1 | 0.3 | 1 | 0.2.0 | None | None | The desktop skill name goes back to claude-desktop. A confirmation without a terminal no longer prints the page link. | Changed | Unknown |
| 0.2.2 | 2026-10-22 | 1 | 0.3 | 1 | 0.2.0 | projects/<name>/requests/ and projects/STATUS.md. | A project may contain a notices list. | Chat install, join, and leave stop working. 0.2.1 ignores STATUS.md and request files. | Same | Unknown |
| 0.2.1 | 2026-10-22 | 1 | 0.2 | 1 | 0.2.0 | Outbox folders for a helper that cannot write the inbox. | None | Claude desktop can no longer join. Windows scheduled tasks lose the no-console program. | Changed | Unknown |
| 0.2.0 | 2026-10-17 | 1 | 0.1 | 1 | 0.2.0 | First board: inbox, sent, files, archive, context, and progress. | First copies of projects.json, agents.json, and config.json. | Nothing. This is the first release. | Same | Unknown |

## How to read this

Oldest reader is the oldest Agent Co-op that can still open a board this version writes. A missing board_format is format 1.

Downgrade loses is what stops working if you go back one version. When you move down more than one version, `agentcoop update` prints that cell for the version you are leaving and for each version in between.

Desktop skill is Changed when you need to save the Claude desktop skill again.

Upgrade from is the oldest version that can move straight to this one. Unknown means that path was not checked.
