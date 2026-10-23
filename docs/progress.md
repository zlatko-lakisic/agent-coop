# Progress and help

While a driver is working, it keeps a short progress update on the board. Helpers read that update with the message that asked them for help.

A long task has taken, or is expected to take, more than `progress_long_task_minutes` (default 5). Short tasks post no progress. On a long task, post when it starts, after each step, right before asking for help, when it is blocked, and when it finishes. While it runs, post at least every `progress_every_minutes` (default 10), even with no change.

Start each update with `Status`, and with `Step` or `Percent` when those come from a count. `Percent` is a whole number from 0 to 100. Do not estimate it. `Now` is one sentence. `For` is the message id when the task came from a message.

A helper posts on an open request from the driver:

```bash
agentcoop progress write --as <helper> --for <message id> --percent 50 --step "2 of 4" --status working --body -
```

That file is `progress/helpers/<helper>/<stamp>_<message id>.md`. It is not a message. The Claude desktop app writes `outbox/claude_desktop/<message id>.progress.md`, and the next check moves it. The answer is still `<message id>.md`.

`agentcoop status` and `projects/STATUS.md` show one short line for the driver's task and each helper task. A task with no update for more than twice `progress_every_minutes` says how long it has been quiet. A task marked done drops off after one day. `agentcoop wait --for <id>` prints each new helper update and keeps waiting while that update is fresh, up to 60 minutes.

Write one:

```bash
agentcoop progress template
agentcoop progress write --as <driver> --body -
```

Show the latest:

```bash
agentcoop progress show --as <driver>
```

Ask for help as soon as you are stuck. Attach the progress update with `--with-progress`. Add `--urgent` when a person is waiting and the schedule should start the helper now:

```bash
agentcoop send --as <driver> --to <helper> --type question --needs-reply --with-progress --urgent --subject "..." --body "..."
```

If the schedule is off, the helper starts when someone opens it. The message id is the only text on standard output. Notes go to standard error.

`agentcoop check` reminds the driver when the progress update is stale and the driver has been working. `agentcoop doctor` notes a blocked update that is old, and an urgent request with no reply.
