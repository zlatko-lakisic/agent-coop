# Decisions for viewer real-use fixes (planned 0.2.12)

Recorded while implementing `fix/viewer-real-use` without blocking on questions.

1. **Rolling presets:** `1h` / `6h` / `24h` / `today` / `7d` / `30d` / `all` recompute `end = now` on every load and live tick. Custom ranges stay fixed. Prev/next on a preset become a custom window with a human label.
2. **URL range:** Persist preset or custom start/end, project, query, live, and timezone mode in the query string. Never put the token in the URL.
3. **Stall snapshot:** Write `projects/<name>/progress/.stall.json` once when Status is `working` and quiet. Minutes freeze at detection. Clear on any progress write. `waiting` / `blocked` are waiting, not stalled.
4. **Severity:** Single `SEVERITY` map in `timeline.py`; documented in `SEVERITY.md`. `update.installed` is info.
5. **System actor:** Board-written events use actor `Agent Co-op` with the system palette color.
6. **Drawer:** Fixed panel below the top bar, 440px on desktop, full width on phone; covers the rail, not the timeline center; Esc restores focus to the opened card.
7. **Desktop scroll:** App uses `100dvh` CSS grid; only the left panel and timeline scroll. Phone keeps page scroll with a pinned rail.
8. **Files tab:** Aggregate attachments, shared index, helper drafts, outbox `.files`, body absolute paths, held, and outside. Deduplicate by name/key and list every linked message.
9. **Playwright:** Prefer Vitest + Python coverage for CI time; add a lightweight Playwright script under `web/` when Chromium install is practical, otherwise screenshots from a fixture HTTP page.
10. **Day headers:** Day key includes the year so the same calendar day in different years never merges.
11. **Compat:** Stall snapshot file and richer `query_events` / `files` payloads are additive; older readers ignore them.
