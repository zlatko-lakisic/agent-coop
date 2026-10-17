# Project context

Each project can have one context document. It tells a new helper what the project is, what is in scope, and what to leave alone.

The driver writes it:

```bash
agentcoop context template
agentcoop context write --as <driver> --body -
```

A helper reads it before other work:

```bash
agentcoop context show --as <helper>
```

If there is no context yet, the helper asks the driver once with a `context_request`. It does not ask again while that request is still open. The driver answers by writing the context document.

Versions stay in the project's `context/` folder on the board. `agentcoop context show` prints the latest one.
