# Handoff: build the viewer app exactly as designed, live over WebSocket, and usable on a phone (planned 0.2.11, not released)

You are the builder for Agent Co-op (github.com/zlatko-lakisic/agent-coop). Work on your own from start to finish. Do not ask Zlatko anything. When something is unclear, pick the safest reasonable option and write it under "Decisions I made" in the PR. Follow the repo's `AGENTS.md`, the compatibility and wiki skills, the Windows, macOS, and Linux rule, and the done report.

**Release:** nothing is released, so no version bump and no tag. Put changes under `Unreleased (planned 0.2.11)` in the changelog and the compatibility row.

**Working rules:** commit and push after each step. If file edits stop working, commit and push right away and say where you stopped. If this chat gets long, finish and merge the current PR, then say which PR is next.

## Where things stand

* **The old page is still what people see.** The earlier viewer handoff asked for three PRs. Only the first (#59, `timeline.py` and the `/api/v2/` endpoints) was built. The app itself (PRs 2 and 3) was never built, and 0.2.10 was released without it. Today `agentcoop ui` still shows the old page: serif text, a project list, and raw progress text.
* **This handoff replaces PRs 2 and 3.** It also changes how the app talks to the server, and adds phone access.

## The design is fixed: build it exactly

The design file is `viewer-design.dc.html`, attached with this handoff. Commit it to `design/viewer/viewer-design.dc.html` as the reference. Zlatko approved this design.

The file is a Design Component:

* the markup inside `<x-dc>` is the layout;
* `{{name}}` fills come from `renderVals()` in the script at the bottom;
* `<sc-for>` repeats;
* `<sc-if>` shows or hides.

Every color, size, radius, spacing, font, icon, label, and piece of copy in it is the spec. Rebuild it in the real app with the same visual result. The sample data in it shows what each element holds. The real app fills those elements from the board.

**Pixel match:**

* Build the same screen from the fixture board (`scripts/sim/viewer_fixture.py` from #59, extended with the same events the design shows).
* Take screenshots at 1440 by 980, dark and light, with the same state open: the timeline, the drawer on the Phase C review, the Agents, Rules, and Files tabs, and the date range dropdown.
* Put them in the PR next to a screenshot of the design rendered the same way. To render the design, convert it to a plain static HTML file with the same markup and styles, committed as `design/viewer/viewer-design.html`.
* List every visible difference under Surprises, and fix each one, or explain it.

**What the design contains, so nothing is missed:**

1. **Top bar, left to right:**
   * the robot mark and "Agent Co-op";
   * the project switcher;
   * the date range button: a calendar icon, the preset name, and the range text;
   * the previous and next range buttons;
   * the Live pill, with a teal dot when on;
   * search with a `/` hint;
   * Filters;
   * the light and dark toggle;
   * Manage.
2. **The date range dropdown:** seven presets, a Local or UTC switch, a month calendar (selected days filled orange, days with activity underlined, future days dimmed), and From and To fields.
3. **The "Needs attention" strip:** pill buttons for Stalled (red), a nudge waiting (amber), and update available (neutral). Clicking one jumps to its event and opens it.
4. **Left panel:** tabs for Agents, Rules, and Files.
   * **Agent cards:** initial mark (square for the driver, round for helpers); name, role, and a state pill; tags; grade line; last seen; a 7-day sparkline. Clicking one filters the timeline, and clicking again clears the filter.
   * **Rules cards:** Roles, What helpers may do, Review, Checking, and Versions.
   * **Files:** a two-column grid with thumbnails.
5. **The timeline:**
   * a count line;
   * a "Showing X only. Show everyone" pill when filtered;
   * day headers that stick to the top while scrolling;
   * each row with a monospace time, a rail line with a dot (square for the driver, round for helpers, red for problems, violet for reviews), and a card.
   * **Each card:** a kind icon in a tinted square, the actor in its color, a kind pill, and the title; the summary; a percent bar for progress; chips. Problem cards have a red border, attention cards amber, and the selected card orange.
6. **The overview rail:** one row per time bucket, with the hour label, bars stacked by agent color, and a marker dot; day-break lines; day labels; and the orange window showing what the timeline shows.
7. **The detail drawer:**
   * the time and title;
   * actor, kind, and id pills;
   * the body text, keeping line breaks;
   * the done-report sections as boxes;
   * the thread list;
   * "Copy id" and "Show raw JSON".
8. **Themes:** dark and light with exactly the palette in `palette()` in the design. Use the system font stack, tabular numbers, and `ui-monospace` for times and ids.

## Change 1: the overview rail stays in view and follows the scroll

In the design the rail sits in the page. In the app it must stay visible while the person scrolls, like Frigate's:

* **It never scrolls away:** the rail is `position: sticky`, with its top at the bottom of the top bar and the attention strip, and a height that fills the rest of the window.
* **It covers the whole selected range:** newest at the top, oldest at the bottom.
* **The window follows the scroll:** the orange window shows exactly which part of the range is on screen. It moves and resizes as the person scrolls, smoothly, updated on animation frames, not on every scroll event.
* **Day labels stay correct:**
  * the day label at the top of the rail shows the day of the topmost visible event;
  * the label at the bottom shows the oldest day in the range;
  * day-break lines and their labels sit at the right places along the rail.
* **Scrubbing:** dragging the window, or clicking or tapping anywhere on the rail, scrolls the timeline to that time. If the events for that time are not loaded yet, load them first, then scroll.
* **Hover:** shows a small tooltip with the bucket's time range and counts per agent.
* **Live mode:** new events add to the top bucket in place, without a jump.
* **Keyboard:** the rail can be focused, and Up and Down move the window by one bucket.

## Change 2: the app talks to the server over one WebSocket

All data and actions between the app and the server go over a single WebSocket. The page updates by itself as the board fills, with no refresh.

### Server

* **No new runtime dependency.** Python's standard library has no WebSocket server, so implement RFC 6455 in a new module, `src/agentcoop/wsserver.py`, on the existing server:
  * the handshake: `Upgrade` and `Sec-WebSocket-Key`, answered with `Sec-WebSocket-Accept` (SHA-1 plus base64);
  * text frames;
  * masked frames from the client;
  * messages split across frames;
  * ping and pong;
  * close with a status code;
  * a maximum message size of 1 MB;
  * one thread per connection, at most 8 connections.

  Test it against a real browser in the end-to-end tests, and against a small test client in unit tests.
* **Origin check:** refuse the upgrade unless `Origin` exactly matches the page's own origin. This stops other web pages from connecting to the board.
* **Sign-in:** the first message must be `{"type":"hello","token":"..."}` within 5 seconds, or the server closes the socket. The token is never put in the WebSocket URL.
* **Messages from the app** are requests with an `id`:
  * `{"id":7,"op":"events","params":{...}}`, answered with `{"id":7,"ok":true,"data":...}` or `{"id":7,"ok":false,"error":"plain sentence"}`;
  * ops: `projects`, `events`, `event`, `histogram`, `agents`, `rules`, `status`, `files`, `subscribe`, `unsubscribe`, `resume`, plus the old page's actions (`driver`, `helper_add`, `helper_remove`, `project_add`, `shutdown`).

  Every action still goes through the existing confirmation. The socket only asks for it, and the result comes back on the socket.
* **Live updates:** after `subscribe` with project, range, and filters, the server pushes changes as they happen:
  * `{"type":"event.added","event":{...}}`;
  * `{"type":"event.updated",...}`, for example when an answer is posted, a review verdict arrives, or a file is shared;
  * `{"type":"histogram.delta",...}`;
  * `{"type":"status",...}`, for the attention items;
  * `{"type":"agents",...}`, for state changes such as watching, stalled, or turn ended;
  * `{"type":"rules",...}`.
* **Change detection:** one watcher thread per server checks the board for changes once a second, using the timeline cache from #59 (path, modified time, size), and sends only what changed. This works the same on Windows, macOS, and Linux, with no extra package.
* **Order:** every pushed message carries a sequence number, in send order, as on the board.
* **Reconnecting:** the app sends `resume` with the last sequence number it saw, and the server replays what was missed. If too much was missed (more than 5,000 messages, or more than 10 minutes), the server says so, and the app reloads the current view.
* **Keep-alive:** a ping every 20 seconds. A socket with no reply for 60 seconds is closed. The viewer's idle shutdown counts an open socket as activity.

### HTTP

HTTP is used only to load the app's own files, and for shared file bytes (images and downloads), because a browser image needs an address.

* **File access:** after `hello`, the server sets a session cookie for file requests: `HttpOnly`, `SameSite=Strict`, `Path=/files/`. File requests need that cookie.
* **The `/api/v2/` HTTP endpoints from #59** stay for scripts and tests, behind the token. The app does not use them.
* **The old POST endpoints** are removed from the app. Keep them working until the end-to-end tests prove the socket versions, then remove them, and say so in the compatibility row.

### In the app

* **Connection state:** show it in the top bar as a small dot next to Live: teal when connected, amber when reconnecting, red when lost.
* **Reconnecting:** try again with backoff (1, 2, 4, 8, up to 30 seconds). When the connection is lost for good, show the `disconnected` empty state, with a Reconnect button.
* **New events:** with Live on and the view at the top, new events slide in at the top. If the person has scrolled away, a "3 new events" pill appears, and clicking it scrolls up. Respect reduced motion.
* **Everything else updates in place,** with no refresh: agent cards, the attention strip, rules, files, and the open drawer, including a new reply in the thread.

## Change 3: open it on a phone

The phone layout must be fully designed, not just squeezed.

### Starting it for a phone

* **Command:** `agentcoop ui --phone`. Chat phrase: "Open the Agent Co-op viewer on my phone".
* **Where it listens:** on the computer's private network address only, as well as 127.0.0.1. Allowed ranges: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, and 100.64.0.0/10 (Tailscale). Refuse a public address. If there are several, use the one on the default route, and list the others.
* **The person approves it** through the existing confirmation, because it opens the board to the local network. Never from a board message.
* **What it prints:** the address and a 6-digit pairing code, valid for 5 minutes and usable once. Also print a QR code of the address in the terminal and in the chat output (text blocks). Use a small bundled QR encoder under an MIT or similar license, with its notice in `THIRD_PARTY_NOTICES`, so there is no new runtime dependency.
* **On the phone:** the person opens the address, types the code, and the server gives that browser a session that expires when the viewer stops or after 12 hours.
* **Read-only from the phone:** the Manage actions are hidden and refused for phone sessions.
* **The desktop viewer** also gets an "Open on phone" button that shows the same QR code and code after the person approves.
* **Warnings, in the docs and in the command output:**
  * Plain HTTP on the local network can be read by others on the same network, so use it only on a trusted home network, or over Tailscale.
  * On Windows, the first time it listens on the network, Windows Firewall may show a window asking to allow it. That window can take focus, so start phone mode when no automation script is running.
  * Away from home, the phone can reach it only over a VPN such as Tailscale.

### Phone layout (under 700 px wide; test at 390 by 844)

* **Top bar, two rows:**
  * row one: the robot mark, the project switcher, the connection dot, and Live;
  * row two: the date range button (preset name only), then search and filter icon buttons that open full-width sheets.
* **The attention strip** scrolls sideways.
* **The timeline** takes the width: times above the card title instead of in a gutter, the card at full width, and the same dots and borders.
* **The overview rail** stays on the right, about 40 px wide, sticky, with the same window that follows the scroll and taps to jump. It shows the hour labels every 3 hours, and day labels as short text ("27").
* **A bottom bar,** fixed, with four 44 px or larger buttons: Timeline, Agents, Rules, and Files. Agents, Rules, and Files open as full-screen sheets with the same cards as the desktop tabs.
* **The detail drawer** becomes a full-screen sheet with a close button at top left. Swiping down closes it.
* **The date range dropdown** becomes a full-screen sheet.
* **Touch:** every touch target is at least 44 px. Text is never smaller than 12 px.
* **Safe areas:** use the iPhone safe areas (`env(safe-area-inset-*)`).
* **Tablet (700 to 1100 px):** the left panel becomes a sheet opened from the top bar, and the rail and timeline stay.

## Tech stack (unchanged from the first viewer handoff)

* **Runtime stays zero-dependency Python.** Node is used only to build the app.
* **Build:** Vite + Preact + TypeScript in `web/`, with exact versions, `package-lock.json`, and `.nvmrc`.
* **Committed output:** the build writes to `src/agentcoop/data/viewer/`, and the built files are committed.
* **CI build check:** a CI job runs only when `web/` or `data/viewer/` changes. It runs `npm ci`, the tests, and the build, then `git diff --exit-code`.
* **Size budget:** JavaScript at most 150 KB gzipped, CSS at most 30 KB gzipped, images at most 600 KB.
* **No external requests at runtime.** The headers from #59 stay. The CSP gains `connect-src 'self' ws://<the same host>:<port>`.
* **Board text is untrusted.** It is always shown as text or as safe Markdown, never as raw HTML.
* **Long lists:** use a virtual list for 20,000 events.
* **Images:** Zlatko will add five images, `empty-range`, `first-run`, `stalled`, `disconnected`, and `waiting-on-you`, as WebP in `web/public/images/`. Use simple SVG placeholders with those names until then.

## Tests

* **Python (all three systems):**
  * the WebSocket handshake;
  * frames, including masked, split, oversized, ping, and close;
  * the origin check refuses a foreign origin;
  * no `hello` within 5 seconds closes the socket;
  * a wrong token closes it;
  * requests and replies;
  * `subscribe` pushes `event.added` within 2 seconds of a message file being written;
  * `resume` replays what was missed;
  * the file cookie works;
  * phone mode: private addresses only, a public one refused, the pairing code is single-use and expires, phone sessions cannot run Manage actions, and starting it needs the person's confirmation.
* **Vitest:**
  * the rail's mapping between scroll position and time, both ways;
  * the window size at different ranges;
  * reconnect backoff;
  * resume handling;
  * merging live events into a loaded list without duplicates or reordering.
* **Playwright with Chromium,** on Ubuntu and Windows at least, against the fixture board:
  * open, and the token is gone from the address bar;
  * the socket connects, and the dot turns teal;
  * write a new message file during the test, and it appears without a reload;
  * the rail stays in view while scrolling, and its window moves;
  * dragging the window scrolls the timeline;
  * a stall appears in the attention strip live;
  * kill and restart the server, and the app reconnects and fills the gap;
  * at 390 by 844: the bottom bar, sheets, the drawer as a full screen, the rail tap, and no horizontal scroll;
  * axe accessibility checks.
* **Screenshots** as CI artifacts, desktop and phone, dark and light, next to the design reference.

## Docs

* **Repo docs:**
  * `docs/viewer.md`: what it shows, live updates, phone mode with its warnings, and keyboard keys;
  * `docs/security.md`: the origin check, the token in the first message, the file cookie, phone pairing, and the private-address rule;
  * the README feature paragraph, with one desktop and one phone screenshot;
  * the changelog;
  * the compatibility row.
* **Chat phrases:** "Open the Agent Co-op viewer" and "Open the Agent Co-op viewer on my phone", in `phrases.json`, the skill, the Cursor rule, AGENTS.md, and GEMINI.md.
* **Wiki:** Board viewer (new or updated), Chat phrases, Command reference, Safety and privacy, and Troubleshooting ("My phone cannot open the viewer").

## Three pull requests, each merged before the next

1. **`feat/viewer-socket`:** the WebSocket server, the protocol, live change detection, `resume`, the file cookie, and the Python tests. The old page keeps working.
2. **`feat/viewer-app`:**
   * the `web/` app built exactly to the design, desktop layout, with the sticky rail that follows the scroll, using only the socket;
   * the build into `data/viewer`;
   * packaging and the zipapp check;
   * the CI viewer job and the size budget;
   * Vitest;
   * the screenshots next to the design;
   * the old page removed, with each of its features moved into Manage and listed in the PR.
3. **`feat/viewer-phone`:** phone mode (listening on a private address, confirmation, pairing code, QR code, read-only sessions), the phone and tablet layouts, Playwright and axe, docs, and wiki.

Each PR has the done report in its body:

* the Asked list covers every numbered design item and every change in this handoff, each marked;
* Claims have evidence: test names, screenshots, and trace or log lines;
* Not verified lists what was not checked, honestly.

Do not mark an item done without a screenshot or test that shows it.

"Needs Zlatko's test" (PR 3):

* On the Windows PC, "Open the Agent Co-op viewer" shows the designed timeline for comstar-game-ai, and new board activity appears without a refresh.
* "Open the Agent Co-op viewer on my phone" shows a QR code. The phone on home Wi-Fi opens it with the code, and the phone layout works, including the rail.
