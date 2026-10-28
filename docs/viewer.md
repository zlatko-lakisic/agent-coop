# Board viewer

`agentcoop ui` opens a local page that shows the board as a timeline. Agents do not connect to it.

## What it shows

- A timeline of messages, progress, reviews, files, stalls, and settings, newest first
- Agent cards, rules in force, and shared files
- A sticky overview rail on the right. Drag or click it to jump in time
- Live updates over a WebSocket while the page stays open

## Open it

Say "Open the Agent Co-op viewer" or run:

```text
agentcoop ui
```

The link includes a one-time token. The page removes the token from the address bar and keeps it in the tab session.

## Keyboard

- `/` focuses search
- Arrow keys on the overview rail move the window by one bucket

## Phone

Phone mode (`agentcoop ui --phone`) lands in a follow-up change. Use it only on a trusted home network or over Tailscale. Plain HTTP on the local network can be read by others on the same network.

## Manage

Driver, helper, and project actions live under Manage. They still ask for confirmation the same way as before.
