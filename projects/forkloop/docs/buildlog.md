# Build log

Dated, numeric, mirrors the public posts. One line per event; link the post
once it exists. Newest first.

| Date | What happened | Number | Post |
| --- | --- | --- | --- |
| 2026-09-01 | Repo scaffolded end-to-end: core library, `claims-ops-v1` world (portal + OpenEMR layer), `toy-counter` world, oracle, recorder, exporters, teacher/student policies, best-of-N search, reset benchmark, training scripts, six spike scripts, cookbook example, docs. All offline tests green. | see README "Status" | — |
| 2026-09-01 | Verified from SDK source (solari-sandbox 0.2.0): desktops with `from_snapshot` go through `SandboxClient.create_desktop`; `DesktopClient.create` has no such parameter. Pricing table and plan caps verified on docs.getsolari.com. OpenEMR 8.3.0 (2026-08-18) pinned; installer flags verified against the tagged source. | — | — |

## Planned posts (from the plan, §11)

| When | X | Discord |
| --- | --- | --- |
| Day 1 | revert() p50 across 20 resets + terminal screenshot | intro; ask about record+fromSnapshot and a concurrency bump |
| End Phase 1 | 10 s VNC clip of five resets in a row | golden-snapshot recipe |
| End Phase 2 | Chart 2 | ask for methodology corrections |
| Mid Phase 3 | first verified trajectory + rejected near-misses (reason codes) | — |
| End Phase 3 | rung-1 chart: teacher success vs best-of-N width | — |
| Phase 4 | bake-off table | — |
| End Phase 4 | Chart 1 | post + link |
| Launch | video, final frame, cost table | cookbook PR offer |
| Weekly | "what broke this week" | — |
