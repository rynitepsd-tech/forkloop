# Build log

Dated, numeric, mirrors the public posts. One line per event; link the post
once it exists. Newest first.

| Date | What happened | Number | Post |
| --- | --- | --- | --- |
| 2026-09-02 | Desktop reset benchmark (fork, n=10): p50 25.0 s, one failure from a full disk → golden v5 rebuilt lean (≈550 MB free). Spike 2: two forks in parallel from one snapshot. Spike 3: recording works on forked desktops, `recordingUrl` never populates. Family 1 blocked on the calendar provider list (documented). | p50 25.0 s | — |
| 2026-09-02 | Starter plan: golden **desktop** world built (`snap_dl4e05ciyt1p`); scripted GUI episode through the real agent channel → reward 1.0, decoy control → `WRONG_VALUE`. Frames in `docs/demo_episode/`. | reward 1.0 | — |
| 2026-09-02 | Spike 5: screenshot 0.13 s, click 0.19 s, observe-act-observe loop 0.45 s p50 (≈2.2 steps/s). Fork restores RAM + windows (uptime continues). `revert()` 409 on desktops as well. | 0.45 s | — |
| 2026-09-01 | Fork-mode reset benchmark on Solari sandboxes: 10/10 ok, p50 19.1 s (restore 17.4 s + seed/health/baseline 1.7 s). Chart 2 fork bar rendered. | p50 19.1 s | — |
| 2026-09-01 | Golden `claims-ops-v1` world built on a real Solari sandbox (OpenEMR 8.3.0 + portal); headless controller loop verified: oracle rewarded 2 correct UI-path appeals, rejected a decoy number (`WRONG_VALUE`). | snap_dl4cngznmvr7 | — |
| 2026-09-01 | First real measurements (Free plan): desktops 402; `revert()` 409 "Not revertable" and destructive on a running machine; snapshot 14–20 s; from_snapshot ≈ 18 s. | 409 | — |
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
