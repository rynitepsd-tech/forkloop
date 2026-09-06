# Build log

Dated, numeric, mirrors the public posts. One line per event; link the post
once it exists. Newest first.

| Date | What happened | Number | Post |
| --- | --- | --- | --- |
| 2026-09-06 | **Second LoRA rung, recipe v2-reasoning, on a Lambda H100 80 GB** (3.67 h ≈ $12.1 at $3.29/h): `f3-ckpt-025-v2` = same 25 episodes / 1,758 records / hyperparameters as v1 but the assistant turn is the teacher's reasoning line then the tool call (target 35 → 60 tokens; 483 records carry the AUTH number in the reasoning); batch 4 × accum 2, 440 steps, 2.28 h, loss 1.26 → 0.19, peak 59 GB; sdpa on both towers (3.0 s/example, eager 4.7 s). Held-out seeds 200–229, same vLLM stack: **0/30 [0, 11.3]**, `auth_typed` 0/30 → gate failed, ckpt-50 not run. Staircase base → v1 → v2: login 30 → 0 → 13, chart 2 → 0 → 12, document 0 → 0 → 9, appeal submitted 0 → 19 → 13, invented number submitted 0 → 19 → 13 (12/13 are training-set numbers; the reasoning narrates "The authorization number is …" before any lookup). Of the 9 document episodes none typed a number (all looped to the 120-step budget); seed 211 read the right number into its reasoning and never left OpenEMR. Parser: 0 invalid actions on 2,432 reasoning-then-tool-call replies. Box: new empty filesystem `Forkloop-with-H100` was attached (not last night's), model re-downloaded anonymously; `train/box_setup.sh` (one command) added; `--png` staircase chart in `docs/images/staircase-f3-ladder.png`. Solari $0.17. | 0/30; auth_typed 0/30; document 0 → 9/30 | — |
| 2026-09-06 | **First LoRA rung on a rented GPU** (Lambda 1× RTX A6000, ≈9.1 h ≈ $9.9 at $1.09/h): `f3-ckpt-025` = Fara1.5-4B + LoRA r16 on the first 25 verified family-3 episodes (1,758 records, seeds 20–45, 0 held-out leakage, instructions re-rendered: 0 changed), 2 epochs, batch 2 × accum 4, 440 steps, loss 1.10 → 0.065, 5.53 h, peak 34 GB; training chat = fair-eval chat (no-user prompt + credentials note + nav-macro schema). Held-out seeds 200–229 through one vLLM stack: **base 0/30 [0, 11.3]** (30/30 logins, 2/30 charts, 24/30 looping, 233 `visit_url`), **ckpt-25 0/30 [0, 11.3]** but a different staircase: 0/30 logins (skips OpenEMR), portal claim 26/30, appeal form 23/30, appeal submitted 19/30, every submitted number invented (`AUTH-55S24141` ×14; none a decoy, none in the training data), 11/30 looping; 0 `visit_url`, 0 invalid. Box gotchas: python3.11 from apt, `torchvision` missing from the `train` extra, vLLM needs `ninja` on PATH. Solari VM $0.45 for both evals; adapter backed up to `checkpoints/f3-ckpt-025/`, merged model on the NFS mount. Not run: rungs 50/94. | 0/30 vs 0/30; appeal_submitted 0 → 19/30 | — |
| 2026-09-02 | **First verified teacher trajectories**: `claude-opus-5` scores 1.0 on 2/2 family-3 episodes through the real desktop world (`runs/teacher-pilot4`). On the way: teacher screenshot-queue fix, waits exempt from `max_steps`, app URLs in instructions, `oracle.ignore_columns` for OpenEMR's uuid backfill, per-episode Chrome/kernel diagnostics, pool create timeout. | 2/2 | — |
| 2026-09-02 | Family-1 "blocker" diagnosed: Chrome renderer crash without `--disable-gpu` + OpenEMR's per-user provider filter ("All Users" fixes it). `snapshot()` refused on forks → golden v6 rebuilt from scratch with the Chrome fix. Teacher wired for identity-linked keys (`ANTHROPIC_WORKSPACE_ID`); blocked on the workspace id. Solari message drafted (`docs/solari-message.md`). | calendar renders | — |
| 2026-09-02 | Pool now falls back to fork mode when `revert()` is refused (verified live on the account); reset reports label the method actually used. Handoff doc written. | — | — |
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
