# Agent Co-op contributors

Before you finish a change that could affect an older version, follow `.cursor/skills/compatibility/SKILL.md`.

Before you finish a change a person would see or do, follow `.cursor/skills/wiki/SKILL.md`.

Every change must work on Windows, macOS, and Linux. Test on all three in CI, and list any skips in the PR.

A tag release must point at a commit that is on main. On the GitHub release page, the target is main.

Pull request checklist includes: Works on Windows, macOS, and Linux.

## From a checkout

From a checkout, run commands as `PYTHONPATH=src python3 -m agentcoop`.

Use that only when you are changing this repo. Installing Agent Co-op uses a published release file and the hash check. Never clone, build, or run from source to install.
