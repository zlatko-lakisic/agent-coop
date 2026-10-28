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

Say "Open the Agent Co-op viewer on my phone" or run:

```text
agentcoop ui --phone
```

You must confirm it. The command prints a private-network URL, a one-use 6-digit code (5 minutes), and a QR code. On the phone, open the URL, enter the code, and use the read-only timeline. Manage actions stay on the computer.

Warnings:

- Plain HTTP on the local network can be read by others on the same network. Use it only on a trusted home network, or over Tailscale.
- On Windows, the first time it listens on the network, Windows Firewall may ask to allow it. That window can take focus, so start phone mode when no automation script is running.
- Away from home, the phone can reach it only over a VPN such as Tailscale.

## Manage

Driver, helper, and project actions live under Manage. They still ask for confirmation the same way as before.
