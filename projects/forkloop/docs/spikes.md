# Day-1 spikes — results template

**Status: no numbers have been measured yet.** Every cell that says
*not yet measured* is exactly that. The scripts in `spikes/` are written
against the verified SDK surface (see "Verified so far" at the bottom) but
have not been run: they need a `SOLARI_API_KEY` on a paid plan (desktops
return `402 FeatureRequiresPlan` on Free).

Run everything: `SOLARI_API_KEY=... ./spikes/run_all.sh`. Each spike prints a
table and appends `{"spike", "ts", "metric", "value", "unit", "notes"}` lines
to `spikes/results.jsonl`. Fill this file from that log. Leftover VMs from a
crashed run: `python spikes/_common.py --reap` (kills everything tagged
`metadata.forkloop=spike`).

---

## Measured on 2026-09-02 (later) — Chrome crash, calendar providers, fork snapshots

| Probe | Result |
| --- | --- |
| OpenEMR calendar provider list (SQL + PHP) | `users_facility` is empty for everyone including the visible admin; `UserService::searchUsersForCalendar` (8.3 replaced `getProviderInfo` here) has no filter beyond `authorized=1 AND calendar=1`; an authenticated `curl` session renders all seven providers in `#pc_username` and in the edit dialog. The narrowing is OpenEMR's per-session `pc_username` (= the logged-in user after login); "All Users" widens it. Not a data bug. |
| Chrome on the `default` desktop | Every authenticated OpenEMR page → "Aw, Snap! Error code: 5"; `chrome.log`: `ContextResult::kTransientFailure: Failed to send GpuControl.CreateCommandBuffer`. `/dev/shm` is 992 MB and RAM is fine (≈ 2.9 GB available). Relaunch with `--disable-gpu` → tabs UI, calendar day/week views render; `--no-sandbox` is not needed. The portal cookie survives the relaunch. |
| `snapshot()` on a fork (n=3, 20 s apart) | 409 `Not snapshottable` on a `from_snapshot` desktop in state `running`; a fresh `default` desktop snapshots in seconds (`snap_dl4s2dh2au79`, deleted). |
| Fork ready time today (n=9) | 23.8–28.1 s in seven runs, 92.1 s and 115.4 s in two (host contention; `dmesg` shows RCU-stall warnings after restore). |
| Fork capacity (≈ 11:00–11:20 UTC) | Three consecutive `from_snapshot` desktops never became ready (control channel closed with code 1000 during restore; one build machine lost its channel mid-upload), then `create(from_snapshot)` returned **503 `No sandbox host available`** after 52 s while a fresh `default` desktop was ready in 0.7 s and stayed healthy for 60 s. Fork restores depend on warm snapshot hosts; the pool's retry budget (`max_retries=6`) is what absorbs this. |
| Concurrency cap with zero sessions (≈ 13:50–14:30 UTC) | After the channel drops, every create (fork or fresh) → 429 `Too many concurrent sessions` while both `list_all` kinds return 0; one earlier create hung > 5 min with no response. The teacher pilot (`runs/teacher-pilot`) therefore never got a machine and was stopped. Platform-side stuck sessions; nothing to fix client-side. |
| Teacher pilot 1 (`runs/teacher-pilot`, `claude-opus-5`, effort high) | Seed 0: reset lost the control channel during `before_episode` (1006). Seed 1: 60 steps, reward 0 `NOT_DONE`, 99.9k input / 2.8k output tokens (≈ $0.57). The teacher logged in, searched the right patient and recovered sensibly three times, but the OpenEMR chart tab crashed ("Aw, Snap! Error code: 5", i.e. renderer SIGTRAP) every time — with `--disable-gpu` already relaunched by the hook. Two probes on other forks opened the same seeded patient's chart fine. Every batch that ended with a `screenshot` call also burned an invalid step (fixed: `LOCAL_MEMBERS` in `teacher.py`). Episodes now save `diagnostics/chrome.log`, `dmesg.log`, `chrome_ps.txt`. |
| Teacher pilot 2 (`runs/teacher-pilot2`, after the screenshot fix) | Seed 0: 60 steps, reward 0, 267k input tokens; one chart-tab crash then recovery, then the budget went on scrolling the Documents tree. Seed 1: 60 steps, reward 0, **303k input tokens (≈ $1.55)**, no crash — the teacher found the correct authorization number `AUTH-73Z20943`, downloaded the letter and was back in the portal heading for the appeal form when `max_steps` hit; 19 of its 60 steps were `wait()` and 8 were spent guessing OpenEMR's URL. Changes made: `wait()` no longer counts toward `max_steps`; instructions carry both app URLs; Chrome runs with `--enable-logging=stderr` so `diagnostics/chrome.log` explains the next renderer crash. |
| Teacher pilot 3 (`runs/teacher-pilot3`, waits free, URLs in the instruction) | **Seed 1: the teacher completed the task end to end** — right authorization number from page 2 of the letter, PDF downloaded, appeal filed with reason + number + attachment through the GTK file chooser; all six effect checks passed (milestones 1.0), 87 steps, 498k input / 5.7k output tokens (≈ $2.6). Reward stayed 0 with `COLLATERAL_EDIT`: OpenEMR rewrote `patient_data`/`insurance_data` rows 501000–501001 and `documents` 501000 just from viewing them (uuid backfill on first access), which the checksum oracle counts as edits — an oracle calibration bug, see the fix below. One chart-tab crash again; `chrome.log` shows `Network service crashed or was terminated, restarting service` at those moments and no renderer FATAL. Seed 2 was reading decoy documents (step 63, 246k tokens) when the control channel dropped (`Not connected`). |
| Collateral columns (GUI path, seed 1, before/after row dump) | Viewing the patient through the tabs UI sets `uuid` on `patient_data` (both search hits), `insurance_data` and `documents`, and the write bumps `patient_data.last_updated` and `documents.revision`; nothing else changes. `oracle.ignore_columns: {openemr: [uuid, last_updated, revision]}` makes the same path hash-clean. The curl path (`demographics.php`) does not trigger the backfill — the tabs UI does. |
| **Teacher pilot 4 (`runs/teacher-pilot4`, calibrated oracle) — first verified trajectories** | **2/2 episodes reward 1.0** (`OK`, every effect and invariant check passed). Seed 1: 95 steps (35 waits), 37 model calls, 526k input / 6.7k output tokens (≈ $2.8), one chart-tab crash recovered by the teacher. Seed 3: 53 steps (18 waits), 21 model calls, 253k input / 3.9k output tokens (≈ $1.4), no crash. Reset 54 s on the new host. Mean model latency ≈ 6 s per call. This is the rung-1 result the plan asked for: the world, the reset, the agent channel, the teacher and the oracle agree end to end. |
| **Family-3 volume run, 2026-09-02 evening (`runs/teacher-f3-s0-9`, `runs/teacher-f3-s1-9`, seeds 0–9, fork mode, concurrency 2)** | **6/10 verified; 8/12 over every teacher episode = 66.7 % [39.1, 86.2] Wilson 95 %.** All four failures are `NOT_DONE` by the 60-action budget (90–94 steps with waits): three of them lost 2–3 Chrome tab crashes ("page crashed", `Network service crashed` in `chrome.log`) at ≈ 15 actions each, the fourth spent its last 100 s re-opening PDFs whose viewer showed stale renders. Verified episodes average 0.9 crash mentions, failed ones 2.0 (`scripts/episode_table.py`). No wrong-record, duplicate or collateral edits in 12 episodes; invalid-action rate 0/703. Cost (priced by `forkloop metrics`): $2.22 per episode, $4.00 per verified episode, of which the desktop is $0.013 per episode; $26.70 of Opus across all 12 episodes (3.6 M input tokens, 1.3 M of them served from cache without any `cache_control` on our side). Median reset 35.7 s in fork mode including the 8 s Chrome relaunch. The guest kernel logged RCU stalls (`rcu_preempt self-detected stall`, ≈ 90 s at 250 Hz) mid-episode in seeds 3 and 4; `dmesg` was captured as a 60-line tail so the count is incomplete — the capture now keeps stall and OOM lines too, plus `uptime`/`free -m` (`sys.txt`), so the next run can place stalls on the episode timeline. Ops: the first attempt burned four seeds because a fresh venv lacked `anthropic` (collect now pre-flights the policy), and a machine orphaned by that aborted process held one of Starter's two slots for 20 minutes (429 on every create) until reaped — `WorkerPool` now re-lists and kills orphans before each retry. |
| **Seeds 10–14, 2026-09-03 (`runs/teacher-f3-s10-14-8gb`)** | **3/5 verified → 11/17 over every family-3 teacher episode = 64.7 % [41.3, 82.7] Wilson.** Requested as 4 vCPU / 8 GB (`collect --cpu 4 --mem-mb 8192`) but the forks came up at 2 vCPU / 4031 MB (`sys.txt`): **`cpu`/`mem_mb` are ignored on `from_snapshot` creates**, like `disk_gb`, so this is five more 4 GB episodes and the memory experiment needs a golden rebuilt at the larger shape. Both failures are again the 60-action budget after Chrome crashes: seed 14 had five crashpad reports ("the tabbed frame page keeps crashing", "the chart view crashes the renderer"), seed 10 three crashes plus four Anthropic 529 overloads that each cost a budgeted step (the teacher now retries transient API errors with backoff, `usage.retries`). Over all 17 episodes crashpad reports average 1.2 in verified and 3.5 in failed episodes; `free -m` at episode end shows ≈ 2.8 GB available and load ≈ 0.2, so this is not memory pressure. $1.90 per episode in this run; median reset 92 s (slow host). `scripts/chrome_crash_probe.py` replays a verified trajectory on fresh forks under a chosen Chrome flag set and counts crashpad reports, with no model spend. |
| **Chrome crash probe + forks dying, 2026-09-03 02:30–03:00 UTC (`runs/chrome_probe/results.jsonl`)** | **Replaying a verified trajectory is deterministic**: three replays of seed 11 (two with `--use-angle=swiftshader`, one with the base flags) all verified at 1.0 with one crashpad report each, so on that path the flag set makes no difference. The stress phase (six cycles through the tabbed frame, chart, document list and PDF viewer by URL) never completed: **five of eight probe forks vanished mid-episode** at 14, 54, 73, 101 and 143 s after reset, during unrelated actions (login click, a wait, a scroll, typing a URL). The SDK raised `Not connected`, and re-dialling got HTTP 404 for 30 s — the VM was gone, not just the channel. Every identified casualty sat on one host, `desktop-pool-i-00cac13223691ff7d` (vm_000176 02:42:39Z, vm_000178 02:45:55Z, vm_000183 02:48:54Z); restores in the same window took 74–221 s against 20–35 s the day before. `SolariMachine` now re-dials and retries once on a dropped channel (`reconnects` counter). Once the host settled (03:00Z on) the stress replays completed: **swiftshader 4/4 replays verified (crashpad 1, 1, 0, 1; the full six-cycle stress run through tabbed frame, chart, documents and PDF viewer verified with one report, logged 13 s after the replay ended and derailing nothing); baseline 1/2 (one verified with 1 report; the full-stress run finished all 148 actions but scored 0 — a crash 56 s in, during the scripted login/search, left the rest of the replay clicking a dead tab and the appeal was never filed; a second crash came during the cycling).** Small numbers, same host, same seed; `--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader` on top of `--disable-gpu` is at worst neutral and the only configuration that never derailed a replay. **Final tally after two more plain replays each (03:50Z): swiftshader 5/6 completed replays verified (crashpad 1, 1, 0, 1, 1, 1), baseline 2/3 (1, 2, 2); 7 of 15 forks died before finishing, on three different hosts.** Crash reports occur under both flag sets at about one per replay, so the GPU path is not the whole story — but **fork deaths split 5/8 baseline vs 1/7 swiftshader**, and the last two pairs ran interleaved on the same host at the same minute (baseline 1 of 2 died, swiftshader 0 of 2). Time-of-day confounds the early rows, so this is suggestive, not proven. **Decision: run the next teacher batch (seeds 15–19) with `FORKLOOP_CHROME_FLAGS` set to the swiftshader flags and compare deaths/crashes with seeds 0–14 before changing `chrome_base_flags`.** |
| **OpenAI GPT-5.6 Luna as teacher, 2026-09-03 (`runs/luna-medium-s0-2`, `runs/luna-high-s0-2`, seeds 0–2, hosted through the OpenAI-compatible student path: one screenshot + last 8 actions as text, compact grammar, `reasoning_effort` medium/high)** | **0/4 verified** (medium: seed 0 only — the other two seeds hit a 429 after a fork never became ready and leaked; high: 0/3). Luna logs into OpenEMR and finds the Patient Finder on its own, then **loops**: 40+ clicks on an already-focused search box without typing, or repeated clicks on a date filter after 'No matching records found'. No Chrome crash in any of these episodes (screenshots checked). Fast and cheap: 1.5–2.2 s per model call (p50), ≈ 1.7k input tokens per call, **≈ $0.03 per 60-step episode** against ≈ $2.20 for Opus. The failure is the prompt path as much as the model — the compact prompt was written for 4B students and gives no anti-loop guidance, no previous screenshot, no form-entry rules — so a hosted-frontier prompt (`forkloop/policies/prompts/hosted_gui_agent.md`, `collect --system-prompt-file … --history-k 12`) is queued for a second run. Smoke test on the login page: all three effort levels clicked within 10 px of Opus's click. **xhigh (`runs/luna-xhigh-s0-2`): 0/2, one fork died** — seed 0 issued `wait(2)` 103 times and ran out the 600 s wall budget, seeds 1–2 looped on the search box again; effort does not touch the failure. Added a history-based loop warning to the hosted prompt path (`student.loop_warning`: three near-identical pointer actions, or three waits, append an explicit 'do not repeat' line) before the v2 run. **v2 (`runs/luna-high-v2-s0-2`, hosted prompt + loop warning + history 12): 0/3 but no loops** — Luna now logs in, finds the patient, opens Documents and reads an authorization number on two seeds (seed 1: AUTH-73Z20943, correct; seed 0: AUTH-24M33556, a decoy — the oracle would have caught it), then runs out of the 60-action budget while switching to the portal or trying to download the attachment; seed 2 spent its budget in OpenEMR's document tabs. It needs more actions than Opus (which fits in 60), so the next run doubles the budget (`collect --max-steps 120`, recorded in run.json as `budget_override`) and adds the previous screenshot (`--prev-shot`). **v3 (`runs/luna-high-v3-s0-2`, + `--prev-shot`, history 16, `--max-steps 120`): seed 1 VERIFIED at 1.0** — the first Luna trajectory the oracle accepted: 62 steps (60 actions, i.e. it would also have fit the 60-action budget), 193 s, 189k input / 6k output tokens, **$0.045** (Opus on the same seed: $1.83). Seed 0 failed a new way: the authorization letter opened in a Chrome *popup* window whose address bar is read-only, and Luna alternated ctrl+l / type(url) for 70 actions (the loop detector now catches alternating pairs); it had also picked a decoy letter that says 'for a different service'. Prompt v4 adds popup-window and decoy guidance. Seed 2 of v3 hit yet another infrastructure flake: the OpenEMR login page came up without its stylesheet after the fork restore (login button disabled), and Luna spent all 120 actions re-entering the password instead of reloading — the prompt now says to reload any broken-looking page. **v4 (`runs/luna-high-v4-s0-2`, popup/decoy/reload guidance, alternating-loop warning): 2/3 verified** — seed 1 in 57 steps for $0.043, seed 2 in 97 steps for $0.075; seed 0 (the four-decoy seed) still `NOT_DONE` after 137 steps ($0.105). **Luna volume run launched on seeds 3–19 with the v4 configuration** (`runs/luna-high-v4-s3-19`). First `WRONG_VALUE` verdicts are **transcription errors, not decoys**: seed 4 filed AUTH-72646694 for AUTH-72G46694 (G read as 6), seed 6 filed AUTH-84K274 for AUTH-84K27294 after reading it correctly 60 steps earlier. The hosted path sent images without OpenAI's `detail` hint (default `auto` may downscale); it now sends `detail: high` for hosted models, and prompt v5 adds an exact-transcription rule. **Volume result (seeds 0–19): Luna v4 11/20 = 55 % [34, 74] for $1.61 of tokens in total, $0.15 per verified trajectory; Opus 9/15 = 60 % [36, 80] for $31.97, $3.55 per verified. On the 15 shared seeds: Opus 9, Luna 8** (`scripts/compare_teachers.py`, `runs/exports/opus_vs_luna.md`). Luna's failures: 7 budget exhaustions at 120 actions, 2 transcription errors; it verified two seeds Opus failed (2, 14) and failed three Opus passed (0, 11, 13). Cost per verified trajectory is 24× lower with no measurable loss of accuracy at this sample size, and Luna's `WRONG_VALUE` episodes are exactly the kind the oracle exists to reject. |
| Golden v6 | Not produced: four from-scratch build attempts (needed because forks cannot be snapshotted) each lost the control channel mid-`build.sh` (code 1000), the last one within a minute of the upload on a fresh desktop. v5 stays live; `ClaimsOpsWorld.ensure_chrome_gpu_flag` relaunches Chrome per reset instead. |

## Measured on 2026-09-02 — Starter plan, real desktops

Desktops work on Starter (`create_desktop` ready in 0.3 s). Results:

| Spike | Result |
| --- | --- |
| 1 revert latency | **`revert()` → 409 `Not revertable` on a desktop too.** Not plan-gated; not available to this account at all. `snapshot()` on a paused machine → `Not snapshottable`. |
| 5 action round-trip (n=50) | screenshot p50 0.128 s (p99 0.138), click p50 0.187 s (p99 0.437), screenshot→click→screenshot loop p50 **0.447 s** (p95 0.665, p99 0.703) → ≈ 2.2 agent steps/s |
| 2 fork independence | Two desktops created from one snapshot **concurrently** (the Starter cap): ready in 31.1 / 30.5 s, create→health 13.2 / 13.3 s. The script's file-based independence check returned empty listings on the desktop (script bug, not evidence); independence was shown on sandboxes (a file written in one fork is absent in another created from the same snapshot) and every episode fork has had independent DB state. |
| 3 recording with snapshots | `record.start()/stop()` works on a plain desktop, on a `from_snapshot` desktop created with `record=True`, and on one created without the flag — each produces an in-VM mp4 (~150 KB for a few seconds). **`recordingUrl` never populates** through the unified `/sandboxes` route, so recordings must be pulled with `files.read`. The reviewer's "record is rejected with from_snapshot" claim is false at the guest level; the presigned upload is what is missing. `revert()` here again returned 409 and the machine's control channel was 404 afterwards. |
| 4 memory survival (fork variant) | A `create(from_snapshot)` desktop resumes with the snapshot's **kernel uptime continuing** (804 s vs 816 s on the original), services active, and the Chrome window present: forks restore RAM + processes + windows, not just disk. First fork 32.6 s to ready; subsequent 17–20 s. |
| golden desktop world | `snap_dl4e05ciyt1p` (v4): OpenEMR 8.3.0 + portal, Chrome logged into the portal, product-registration modal opted out, Chrome policy restricts URLs to localhost and disables password/translate bubbles, 1280×720, stable initial screen. Built after purging VS Code + LibreOffice (the 4 GB disk was 100% full otherwise). |
| desktop reset benchmark (fork, n=10, with initial-screen + stable-screen stages) | p50 25.0 s · p95 26.6 s · p99 26.7 s · restore p50 20.7 s · 1/10 failed (OpenEMR HTTP 500 on a fork whose 4 GB disk had filled — golden v5 now leaves ≈ 550 MB free) · ≈ $0.93 per 1k resets (Starter). `bench/reset_results_desktop.jsonl`, `bench/chart2_solari.png` |
| golden v5 | `snap_dl4e90g095y2`: v4 minus gcc, tarball cache, logs, Chrome cache; 16 MB InnoDB redo log; ≈ 550 MB free, growth ≈ 4 MB/min idle |
| scripted GUI episode | `scripts/gui_episode.py`: reset 20.9 s (restore 16.9 · seed 0.6 · health 0.6 · baseline 0.7 · initial screen 1.2 · stable screen 0.8), 16 steps through the agent channel, **reward 1.0**; decoy-number control → **`WRONG_VALUE`**, milestones 0.67. Frames in `docs/demo_episode/`. |

Things that bit and are now in the build scripts: Chrome refuses to run as
root (session user is `desktop` on Xvfb `:0`); the SDK presses `["ctrl","a"]`
as two keys, chords must be one string `"ctrl+a"` (fixed in the backend);
keyboard focus is not guaranteed after a fork, so navigation clicks the
omnibox first; `pkill -f google-chrome` kills the shell that runs it; the
OpenEMR session does not survive into the snapshot, so the OpenEMR family
starts on its login page with credentials in the instruction; the
`disk_gb` request is ignored on desktops as on sandboxes.

## Measured on 2026-09-01 — golden world + fork-mode reset benchmark (Free plan, sandboxes)

The `claims-ops-v1` world was built for real on a headless sandbox
(`forkloop build-world --backend solari` with `FORKLOOP_SOLARI_KIND=sandbox`):
OpenEMR 8.3.0 on Apache/PHP 8.3 (sury.org)/MariaDB 10.11, the portal on
:8080 under systemd, both base populations loaded, both health checks green.
Golden snapshot: `snap_dl4cngznmvr7` (≈ 6 GB image). Build wall-clock ≈ 9 min
of sandbox time across three resumed attempts.

`scripts/headless_check.py` then ran the full controller loop three times —
fork from golden, seed a `resolve_denial` episode, health, baseline hashes on
the real MariaDB + SQLite, a UI-path appeal submitted by `curl` inside the VM,
oracle — and the verdicts were exactly right: decoy authorization number →
`WRONG_VALUE` (milestones 0.4, audit tripwire and page-view checks passed);
correct number → reward 1.0 twice.

`forkloop reset-bench --backend solari --methods fork --trials 10` (Chart 2,
fork bar; `bench/reset_results.jsonl`, `bench/chart2_solari_fork.png`):

| method | n | fail | p50 s | p95 s | p99 s | restore p50 s | $/1k resets (Free) | state restored |
|---|---|---|---|---|---|---|---|---|
| create(from_snapshot) fork | 10 | 0/10 | 19.09 | 21.84 | 22.06 | 17.40 | 1.01 | disk + DBs (new machine id; RAM/process survival unverified) |

Per-stage (median): restore 17.4 s · seed 0.26 s · health 0.60 s · baseline
checksums (portal SQLite + OpenEMR MariaDB, 14 tables) 0.74 s. The
`revert()` bar is empty because `revert()` is refused on this account (above);
the local docker-compose bar and the cold-build bar have not been run.

## Measured on 2026-09-01 — Free plan, headless sandboxes (spike 0)

The key available for testing is on the **Free** plan: `create_desktop` returns
`402 FeatureRequiresPlan`, so spikes 1–6 as written (they need a desktop)
could not run. A headless probe (`spikes/spike_00_sandbox_probe.py`, same
snapshot API) measured the following on `base` sandboxes, 2 vCPU / 4 GB:

| Measurement | Result |
| --- | --- |
| create from template → first command | 0.60 s |
| `snapshot()` (full ~6.0 GB disk image) | 14.2 / 16.3 / 19.1 / 19.6 s (4 samples) |
| `revert()` on a *running* sandbox | **409 `Not revertable`**, and the machine was `Not found` afterwards (destroyed) |
| `revert()` on a *paused* sandbox | **409 `Not revertable`**; machine survived, `resume()` worked |
| `pause()` | 14.5 s |
| `resume()` → reattach → first command | 0.40 s |
| `create(from_snapshot)` → first command | 17.9 s; file state restored |
| Guest | Debian 12, root, systemd 252 as PID 1, Python 3.11, curl, apt; no sqlite3/ps/mysql/sudo; 3.9 GB disk (2.2 GB free); egress to github.com OK |

**Decision:** on this plan `reset()` must be fork-mode (`kill` + `create(from_snapshot=golden)`,
≈18 s) — the pool's `mode="fork"`. Whether `revert()` is gated by plan or by
machine kind is unknown; the docs describe it as available on both. Ask Solari
(Discord) with the exact 409 body. **Do not call `revert()` on a running
machine you care about until that is answered: on this account it destroyed the
machine.** The revert-latency row for Chart 2 stays empty until a plan where
`revert()` is allowed.

## Spike 1 — revert latency and state fidelity

**Question.** How long is `revert()` wall-clock (API call → guest `health().ready`
→ two identical screenshot hashes), and does the screen come back to the
post-snapshot state?

**Command.** `python spikes/spike_01_revert_latency.py --iterations 20`

| metric | value | unit |
| --- | --- | --- |
| snapshot_s | not yet measured | s |
| revert_api_p50 (HTTP call only) | not yet measured | s |
| revert_wall_p50 | not yet measured | s |
| revert_wall_p95 | not yet measured | s |
| revert_wall_p99 | not yet measured | s |
| state_back_rate (post-revert hash ∈ post-snapshot hash set) | not yet measured | fraction |
| screen_stable_rate (stable within 15 s) | not yet measured | fraction |
| marker_text_back (clipboard read) | not yet measured | bool |

**Decision rule.**
- `revert_wall_p50` ≤ 10 s → `reset()` uses `revert()` on a warm worker (plan default); Chart 2 headline is this number.
- 10–30 s → still revert, but the pool pre-reverts idle workers so the agent never waits.
- > 30 s or `state_back_rate` < 0.95 → fall back to `create_desktop(from_snapshot=...)` per episode (spike 2 number) and re-examine what the snapshot contains.

## Spike 2 — fork independence

**Question.** Do two desktops created with `from_snapshot` share nothing (separate disks), and how long until a fork is `ready`?

**Command.** `python spikes/spike_02_fork_independence.py --forks 2`

| metric | value | unit |
| --- | --- | --- |
| fork_count (requested 2) | not yet measured | count |
| fork_ready_s (fork 0) | not yet measured | s |
| fork_ready_s (fork 1) | not yet measured | s |
| forks_same_start | not yet measured | bool |
| fork_independent | not yet measured | bool |

**Decision rule.**
- `fork_independent` must be true; if false, the search/branching rung of the plan is off the table and every episode runs on its own reverted worker.
- `fork_ready_s` is the "cold reset" number on Chart 2 and the cost input for `cost_per_1k_resets("from_snapshot", ...)`.
- A `429` on the second fork on Starter means the base was still alive; the script kills it first — if it still happens, the cap counts something else (report it).

## Spike 3 — recording with snapshots

**Question.** Does `record=True` work with `create_desktop(from_snapshot=...)`? With plain create + `revert()`? Does `record.start()/stop()` work in each case and does `recordingUrl` resolve?

**Command.** `python spikes/spike_03_record_with_snapshot.py`

| step | ok | exact error / detail |
| --- | --- | --- |
| A.create(record=True) | not yet measured | |
| A.record.start / stop / recordingUrl | not yet measured | |
| A.revert then record.start / stop | not yet measured | |
| B.create(from_snapshot, record=True) | not yet measured | |
| B.record.start / stop / recordingUrl | not yet measured | |
| C.create(from_snapshot) then record.start / stop | not yet measured | |

**Decision rule.**
- B works → episode videos come from Solari for free; the recorder's `episode.mp4` is optional.
- Only A works → record on the golden worker only (demo videos); episodes use the `shots/*.png` → ffmpeg path from contracts §10.
- Nothing works → ffmpeg path only. Either way nothing in the plan blocks on this.

## Spike 4 — does memory survive revert?

**Question.** Is a snapshot memory + disk (processes, windows, tmpfs come back) or disk only?

**Command.** `python spikes/spike_04_memory_survives_revert.py`

| check | before snapshot | after kill | after revert |
| --- | --- | --- | --- |
| process `sleep 3600` | not yet measured | not yet measured | not yet measured |
| window (xdotool search mousepad) | not yet measured | not yet measured | not yet measured |
| tmpfs file /dev/shm/forkloop/mem.txt | not yet measured | not yet measured | not yet measured |
| streamUrl WebSocket connect | not yet measured | – | not yet measured |
| memory_restored (all three) | | | not yet measured |

**Decision rule.**
- `memory_restored` true → golden snapshot is taken **live**: Chrome open on the portal, MariaDB warm, logged in. `reset()` = revert + seed + navigate.
- false → golden snapshot is taken with apps **stopped**; `reset()` must start MariaDB/Apache/portal/Chrome after revert, and the readiness gate (contracts §11 step 3) becomes the main cost. Spike 6 becomes moot (DB is closed at snapshot time).
- stream dead after revert → the demo viewer must reconnect the VNC socket after every reset (harness detail, not a plan change).

## Spike 5 — action round-trip

**Question.** Latency of `screenshot()` and `mouse.click()`; bounds steps/s per VM.

**Command.** `python spikes/spike_05_action_roundtrip.py --iterations 50`

| call | p50 | p95 |
| --- | --- | --- |
| screenshot_s | not yet measured | not yet measured |
| click_s | not yet measured | not yet measured |
| loop_s (shot → click → shot) | not yet measured | not yet measured |
| png_bytes_mean | not yet measured | |

**Decision rule.**
- `loop_s` p50 < 1 s → env step overhead is negligible next to model latency; no batching work.
- 1–3 s → take the after-screenshot lazily (only when the policy asks) and drop the before/after pair to a single frame per step.
- > 3 s → open a ticket with Solari before scaling to 10 concurrent workers; this number × steps × episodes is the VM-hour bill.

## Spike 6 — DB consistency under snapshot

**Question.** Is MariaDB clean after reverting to a snapshot taken under write load, with and without `FLUSH TABLES WITH READ LOCK`?

**Command.** `python spikes/spike_06_db_consistency.py` (installs mariadb-server in the VM; ~5–10 min)

| mode | db_answer_s | rows after revert (≈ at snapshot) | mysqlcheck_ok | recovery_log_lines |
| --- | --- | --- | --- | --- |
| hot (writer running) | not yet measured | not yet measured | not yet measured | not yet measured |
| locked (FLUSH TABLES WITH READ LOCK) | not yet measured | not yet measured | not yet measured | not yet measured |
| lock_needed | | | not yet measured | |

**Decision rule.**
- both OK → golden snapshot taken live, no lock dance.
- only locked OK → the golden build script wraps `snapshot()` in a read lock (and the same for any mid-run re-snapshot).
- neither OK → snapshot with MariaDB stopped (`service mariadb stop` → snapshot → start on reset), which also depends on spike 4.

---

## Verified so far (no key needed) vs. needs the key

**Verified from `docs.getsolari.com` and the solari-sandbox 0.2.0 source** (`solari_sandbox/sandbox_client.py`, `solari_core/desktop.py`, `handle.py`, `errors.py`):

- Desktops that support `from_snapshot` are created with `SandboxClient(api_key=..., base_url="https://api.getsolari.com").create_desktop(template="default", resolution="1280x720", cpu=2, mem_mb=4096, timeout_ms=..., record=..., from_snapshot=..., metadata=..., lifecycle={"onTimeout": "kill"})` — the unified `POST /sandboxes` route with `kind:"desktop"`. `DesktopClient.create` has **no** `from_snapshot` parameter.
- Handle surface: `await d.connect()`, `await d.health()` → `.ready`, `await d.snapshot(name)` → snapshot id (`POST /sandboxes/:id/snapshots`), `await d.revert(snapshot_id)` (`POST /sandboxes/:id/revert`, same machine id), `await d.commands.run(cmd, args=[...])` → `.exitCode/.stdout/.stderr` (argv, **not** a shell — spikes use `sh -c`), `await d.screenshot(format="png")` → bytes, `d.mouse.click(x, y)`, `d.keyboard.type(text)`, `d.files.write(path, data)`, `d.files.read_text(path)`, `d.record.start()/stop()`, `d.streamUrl`, `d.recordingUrl`, `await d.kill()` (destroys; `close()` only drops the channel), `d.process.start(cmd, args=)`, `d.open(name)`.
- Client surface: `list_snapshots()`, `delete_snapshot(id)` (refused while a child VM is alive), `list_all(kind="desktop", metadata=...)` (async generator), `kill(id)` (idempotent).
- Errors: `PlanError` (402 `FeatureRequiresPlan` — desktops need a paid plan), `ConcurrencyLimitError` (429), `NoCapacityError` (503).
- Transport: default per-call timeout is 300 s (spikes set 30 s); `reconnect()` is a no-op while the channel still reports connected, so the spikes re-attach after revert with `close()` + `connect()`.
- Plan caps and prices (Sept 2026): Free 1 concurrent / $3 credits; Starter $20/mo, $20 credits, 2 concurrent VMs (20 browsers); Pro $200/mo, 10 concurrent. 2vCPU/4GB is $0.114/h on Starter + $0.02/h screen = $0.134/h → ≈ 149 VM-hours per $20. See `docs/cost.md`.
- OpenEMR 8.3.0 is the pinned version; the payer portal is FastAPI + SQLite on :8080 inside the VM.

**Needs the key (everything measured above).** In particular these are *assumptions the scripts make* that only a run can confirm:

- Whether the control WebSocket survives `revert()` (scripts assume it does not and redial).
- Whether `template="default"` may be sent together with `from_snapshot` (the contract says yes; if the gateway rejects the pair, drop `template` in `_common.create_desktop`).
- Whether `commands.run` runs as root (spike 6 falls back to `sudo -n`).
- Whether `xdotool` and `xfconf-query` exist in the `default` template (spike 4 window check; spike 1 caret-blink suppression is best effort either way).
- Whether `streamUrl` needs extra auth headers for a bare WebSocket connect (spike 4 tries without).
