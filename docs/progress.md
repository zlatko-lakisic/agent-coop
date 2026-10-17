# Progress and help

While a driver is working, it keeps a short progress update on the board. Helpers read that update with the message that asked them for help.

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
