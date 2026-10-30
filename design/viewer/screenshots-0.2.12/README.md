# Viewer screenshots (planned 0.2.12)

Capture these against the fixture board from `scripts/sim/viewer_fixture.py` after `agentcoop ui` with the built viewer.

| File | Size | Theme | State |
| --- | --- | --- | --- |
| desktop-dark-drawer.png | 1440x980 | dark | Detail drawer open on a review or progress card |
| desktop-light-drawer.png | 1440x980 | light | Same as above |
| desktop-dark-files.png | 1440x980 | dark | Files tab with filter chips |
| desktop-dark-progress.png | 1440x980 | dark | Progress card with step/percent bar |
| desktop-dark-stall.png | 1440x980 | dark | Stall card + attention strip |
| desktop-dark-range-reconnect.png | 1440x980 | dark | Custom or preset range after a socket reconnect |
| phone-drawer.png | 390x844 | dark | Full-screen drawer from the bottom bar flow |
| phone-files.png | 390x844 | dark | Files sheet |

Skips in this PR: PNG captures are not committed yet. Logic is covered by Vitest (`range`, `labels`, `rail`) and `tests/test_viewer_real_use.py`. Add Playwright under `web/` in a follow-up if CI time allows (`npx playwright` against the fixture).
