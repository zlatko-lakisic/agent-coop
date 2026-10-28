# Stop harness

`scripts/sim/` builds a throwaway board and plays a helper without Cursor. Use it when a Cursor driver stops and you need the agentcoop side on its own.

Never point it at the real board. Set `AGENTCOOP_BOARD` to a new folder, or pass `--board`.

The helper script only writes outbox files. It does not run `agentcoop`. The game script only prints `epoch N of M`.

Fast runs use a fake driver, so they do not spend a Cursor model. They cover the board: helper files, a check, a standing approval, a missing Status line, and an update that makes `cursor-loop` exit 5.

```text
python scripts/sim/run.py --scenario s3 --driver fake --board C:\temp\coop-board --project C:\temp\coop-project --report C:\temp\s3.json
python scripts/sim/run.py --scenario s4 --driver fake --board C:\temp\coop-board --project C:\temp\coop-project --report C:\temp\s4.json
python scripts/sim/run.py --scenario s12 --driver fake --board C:\temp\coop-board --project C:\temp\coop-project --report C:\temp\s12.json
python scripts/sim/run.py --scenario s10 --driver fake --board C:\temp\coop-board --project C:\temp\coop-project --report C:\temp\s10.json
python scripts/sim/run.py --scenario s13 --driver fake --board C:\temp\coop-board --project C:\temp\coop-project --report C:\temp\s13.json
```

`s3` writes three answers in one second. `s4` writes a restart suggestion after a standing approval. `s12` ticks when Now is set and Status is missing. `s10` exits 5 after `bin/VERSION` changes. `s13` starts a stop hook, records a cursor-loop heartbeat during the wait, and checks that the hook still sends the keep-awake line. A second wait with no activity sends the keep-awake line too.

A real Cursor run is manual. Save `agent --print --output-format stream-json` next to the board's `logs/trace.jsonl`. Say which run was the CLI and which was the app. See [cursor-stops.md](cursor-stops.md).
