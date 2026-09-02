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
