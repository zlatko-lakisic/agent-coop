"""Try the GitHub transport against a real private repository. Run it by hand.

This is not part of the unit tests and CI does not run it. It posts one
conversation (one issue, three comments) to the repository you name, from a
temporary board, so your own board is not touched.

    python3 scripts/github_live_check.py your-name/scratch-private-repo

Sign in first with `gh auth login`, or set AGENTCOOP_GITHUB_TOKEN.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    os.environ.pop("AGENTCOOP_TEST_OFFLINE", None)
    from agentcoop import github
    from agentcoop.errors import CoopError

    try:
        repo = github.require_repo(sys.argv[1])
        token, source = github.find_token()
    except CoopError as exc:
        print(str(exc))
        return 1
    print("Signed in through " + ("the gh program" if source == "gh" else source) + ".")
    print("This posts one issue and three comments to " + repo + ".")
    if input("Type the repository name to go on: ").strip() != repo:
        print("Cancelled.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "home"
        home.mkdir()
        folder = root / "live-check"
        folder.mkdir()
        os.environ.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "AGENTCOOP_BOARD": str(root / "board"),
                github.TOKEN_ENV: token,
            }
        )
        from agentcoop.cli import main as run
        from agentcoop.core import new_messages
        from agentcoop.ghsync import load_index
        from agentcoop.transport import check_can_use_github, use_github

        board = root / "board"
        steps = [
            ["setup", "--connect", "cursor,claude-code", "--schedule", "no", "--add-to-path", "no", "--yes"],
            ["project", "add", str(folder), "--name", "live-check", "--driver", "cursor",
             "--helper", "claude_code=reviews", "--gitignore", "no", "--yes"],
        ]
        for step in steps:
            if run(step) != 0:
                return 1
        try:
            print(use_github(board, "live-check", check_can_use_github(board, "live-check", repo)))
        except CoopError as exc:
            print(str(exc))
            return 1
        common = ["--project", "live-check"]
        if run(["send", "--as", "cursor", "--to", "claude_code", "--subject", "Live check",
                "--body", "Hello from the live check. @nobody #1 <b>not bold</b>", "--needs-reply", *common]) != 0:
            return 1
        asked, _issues = new_messages(board, "live-check", "claude_code")
        mid = asked[0].id
        for step in (
            ["check", "--as", "claude_code", *common],
            ["send", "--as", "claude_code", "--to", "cursor", "--re", mid, "--subject", "Re: Live check",
             "--body", "Read it.", "--type", "answer", *common],
            ["done", mid, "--as", "claude_code", *common],
            ["sync", *common],
            ["doctor", *common],
        ):
            if run(step) != 0:
                return 1
        entry = load_index(board, "live-check")["messages"][mid]
        print("")
        print("Open https://github.com/" + repo + "/issues/" + str(entry["issue"]))
        print("Check: three comments, the text shown as written, nobody mentioned, and no link made from #1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
