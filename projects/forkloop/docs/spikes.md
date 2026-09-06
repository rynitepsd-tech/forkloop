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

## Measured on 2026-09-03 (evening) — re-check after Solari support said the email items were "all set"

Same scripts as `docs/solari-repro.md`, run back to back on throwaway machines (`runs/logs/solari_verify.log`);
nothing touched the golden. Account still Starter.

| Email item | Before (2026-09-01/02) | Now |
| --- | --- | --- |
| 1. `revert()` | 409 `Not revertable` everywhere; a failed revert on a running machine destroyed it | **Works.** Desktop (`spike_01`, n=3): API 17.6 s p50, guest back 2.5 s later, screen stable 1.6 s later, **21.5 s p50 total, state came back 3/3**. Running sandbox (`spike_00`): reachable 19.0 s after the call. Paused sandbox: a clear 409 "revert needs a running sandbox — resume first" and the machine survives. On a **fork of the 8.5 GB golden**, `revert(golden)` returned 503 "no desktop host has capacity right now … could not restore this snapshot in time" — **but the machine stayed alive and answered commands afterwards**, so a refused revert no longer destroys it. Whether reverting to the golden works on a good host is not yet measured (one attempt). |
| 2. `snapshot()` on a `from_snapshot` machine | 409 `Not snapshottable` | **Works:** `snapshot()` on a fork of the golden succeeded in 20.8 s and the snapshot deleted cleanly (`scripts/solari_verify_fork.py`). Fresh-desktop snapshots still 18 s. This unblocks `best_of_n` checkpoints and patch-and-resnapshot golden builds — neither re-run yet. |
| 3. `recordingUrl` | empty | **Unchanged:** `None` on a plain desktop, a `from_snapshot` desktop with `record=True`, and one without; in-VM mp4 still written (126–145 KB). New: `record.stop` **timed out (30 s)** after a revert on the plain desktop. |
| 4. `disk_gb` | 4 GB | **Unchanged:** sandbox `/dev/root 3.9G` (42 % used on `base`), golden fork `3.9G` at 86 %. `cpu`/`mem_mb` also still ignored on forks (`nproc` 2, 4031 MB). |
| 5. Snapshot lineage / storage | ancestors undeletable | **Not re-tested** (a successful delete would remove the live golden's ancestor). Listing unchanged: four snapshots, 7.7–8.5 GB, parent links intact, no flatten field. |
| Forks slow / dying | bimodal restores, ~2–5 % deaths | The golden fork in this check took 246 s to be ready (the slow tail again); spike-2 fork 18.4 s. Two deaths in the 90-restore family-3 run earlier today. Unchanged. |

Bottom line: the two blockers that shaped the harness (no revert, no fork snapshots) are lifted; the three
resource/recording items are not. Next measurements, in order: `forkloop reset-bench --methods revert fork` on the
golden for the Chart 2 revert bar (revert-mode pool: ~22 s vs fork p50 ~80 s), then `collect --best-of 2
--search-mode fork` on a few family-3 seeds.

## Measured on 2026-09-03 (night) — `reset-bench --methods revert fork` on the live golden, and the first best-of-2 search

`forkloop reset-bench --world claims-ops-v1 --methods revert fork --trials 10 --no-fallback` (new flag: a refused
`revert()` is a failed trial, not a silent switch to fork mode; the revert pool is warmed first so every trial is a
revert, not the initial fork). One worker, golden `snap_dl4e90g095y2` (v5, 8.5 GB), 14:50–15:20 local.
Rows in `bench/reset_results_desktop_0903.jsonl`, summary `bench/reset_summary_desktop_0903.json`, chart
`bench/chart2_solari_0903.png`.

| method | n | fail | p50 s | p95 s | p99 s | restore p50 s | $/1k resets | state restored |
|---|---|---|---|---|---|---|---|---|
| revert() to golden | 10 | 0/10 | 100.9 | 151.7 | 163.4 | 87.9 | 3.76 | RAM + disk + windows |
| create(from_snapshot) fork | 10 | 0/10 | 92.0 | 169.4 | 172.6 | 80.1 | 3.43 | disk + DBs, new machine id |

- **`revert(golden)` on a fork of the golden works, 10/10**, and it is a real revert: the worker kept one machine id
  (`…vm_001996…`, listed by `forkloop reap --dry-run` before, during and after) for all ten trials. The 503 seen on
  2026-09-03 evening ("no desktop host has capacity") did not recur. **This is the Chart 2 revert bar** and revert is
  now the pool's reset mode (the CLI default was already `--pool-mode revert`, with the fork fallback kept).
- **Both methods are bimodal and the modes are the same**: restore is either ≈ 22 s (revert 4/10 at 21.7–22.3 s,
  fork 3/10 at 21.1–22.3 s) or 70–160 s (revert 37, 73, 103, 105, 112, 122; fork 72, 78, 82, 84, 105, 126, 161).
  Revert never kills or creates a machine, so the slow mode is **not** the pool's 429 backoff after a kill (the
  hypothesis from the v6 run); it is the host restoring the 8.5 GB snapshot. `bench/restore_bimodality_0903.png`
  overlays these 20 restores on the 90 fork restores of the SFT run (p50 81 s, 33/90 under 30 s, 6 over 190 s).
  Reverting is therefore not faster than forking on this account; its value is the stable machine id (no create
  churn, no orphan accounting against the Starter cap, no 429 tail) and RAM/window fidelity.
- Two trials (one per method) had a 48–52 s `seed` stage against a normal 0.3 s (MariaDB slow right after a restore,
  not yet diagnosed); the totals above include them.
- The `before_episode` Chrome relaunch (`--disable-gpu` on the v5 golden) is 7.8–8.0 s of every reset in both modes.
- Cost model: $3.4–3.8 per 1k resets at these latencies (Starter desktop $0.134/h), 4× the $0.93 of the 25 s resets
  measured on 2026-09-02 — the slow mode is what the reset costs now.
- Fixed on the way: `forkloop reset-bench --world …` was rejected by the CLI (`argparse.REMAINDER` swallowed the
  leading option); it now hands the benchmark its own argv. `best_of_n` now deletes its checkpoint and branch-end
  snapshots after the branch point (`SearchStats.snapshots_deleted`); before this every branch point left a full
  disk image on the account.

**First real best-of-N search (`runs/luna-v5-f3-bo2-smoke`, 15:22–15:50 local):** `collect --policy student
(gpt-5.6-luna, v5 prompt, history 16, prev-shot, 120 actions / 900 s) --best-of 2 --search-mode fork --pool-mode
revert --concurrency 1 --families resolve_denial --seeds 0-2`. **3/3 verified at 1.0**, every episode branched once
(the student reports no confidence, so branch points are random at 20 % per step, max 3): seed 0 at step 25 —
candidates `click(101,617)` → 0.0 and `click(27,616)` → 1.0, winner adopted; seed 1 at step 8 and seed 2 at step 2
— both candidates 1.0, first kept. Mechanics verified live: `env.checkpoint()` = `snapshot()` on the revert-mode
worker, one `create(from_snapshot=checkpoint)` per candidate (restores 33, 83, 18, 19, 68, 18 s — the same bimodal
distribution), the losing branch's trajectory stays on disk under `branches/`, the winner's steps are adopted into
the main trajectory (59 / 64 / 64 steps, 444–479 s wall each). Priced by `forkloop metrics`: $0.26 for the run
($0.21 tokens, $0.05 VM), $0.087 per verified episode — about 40 % over the single-rollout $0.061 because each
branch point rolls two full continuations. Two bugs found and fixed from the pool log: (1) **every branch pool
reaped the main worker at start-up** (`reaped ids=[…vm_002060…]` at 15:27:04, the machine that had just been
reverted for seed 0) — `reap_orphans` kills every forkloop-tagged machine the calling pool does not own, and a
branch pool owns nothing yet. The episode survived only because fork-mode search never returns to the main machine
and the pool's revert-mode restore recreated it (`healthy()` false → fresh fork, 80 s) for the next seed; at
`--concurrency 2` it would have killed the other worker's live episode. Branch pools now pass
`reap_orphans_enabled=False` (test `test_branch_pool_never_reaps_its_parents_machine`). (2) **a finished branch's
fork stayed alive** because the branch `Env` does not own its pool; the next branch's reap was what killed it (the
second `reaped` line of every seed). Branch pools are now closed after each branch
(`test_fork_search_leaves_the_main_worker_alive` on the fake backend, where the leak showed as a create hitting the
cap). One leftover fork from the last branch was killed with `forkloop reap` after the run. Still open: the three
`cp-*` checkpoint snapshots were not deleted (`snapshots_deleted: 0`, reason not captured by the code that ran;
the SDK says a snapshot with live children is refused and the branch fork was still listed at that moment) — the
next run records the error text in `search.snapshot_delete_errors`; delete them from the console meanwhile.

**Family 1 on prompt v7, seeds 0–9, `--max-steps 150 --max-seconds 1200 --retry-failed 1 --pool-mode revert --concurrency 2`
(`runs/luna-v7-fam1-s0-9`, 15:54–17:25 local, 14 attempts):** **7/10 seeds verified = 70 % [39.7, 89.2]; first pass 6/10**
(seeds 0, 1, 3, 4, 6, 9), seed 2 recovered on its retry, seeds 5 and 8 failed twice (`WRONG_SLOT`), seed 7's retry died
with its fork after 23 actions (control channel closed 1000, the run's only fork death). Against v6 on the same seeds:
first pass 0/10, 3/10 within three attempts. Priced by `forkloop metrics`: $2.40 for the run ($2.07 tokens, upper bound,
$0.33 VM), **$0.34 per verified seed**, median 155 steps / 644 s. The v7 rules did what they were written for — every
verified episode clicked OK on the provider warning and saved the right date early (seed 0 at action 71, seed 1 at 39) —
and then exposed the next problem: **no verified episode called `done()`**; all seven ran to the 150-action ceiling
re-opening the appointment and re-saving the same date, because OpenEMR's appointment-search panel keeps showing the
old date and the prompt asked Luna to "confirm the new date" there. Both `WRONG_SLOT` seeds are the same loop gone
wrong: after the correct save Luna took the *new* date as "current" and recomputed "next Friday" from it (seed 5: saved
Sep 18 at action 114, re-saved Sep 25 at 130; seed 8 attempt 2 walked Sep 11 → 18 → 25 → Oct 2 → 9 → 16 → 23 in the
form without saving); seed 2 attempt 1 and seed 7 lost the appointment after Chrome crashes (4 and 11 crashpad reports)
in the Calendar Finder — malformed end-date field, then a 50-action scroll loop the loop detector does not catch (the
scroll coordinates vary). Prompt v8 (`hosted_gui_agent_v8.md`, **not yet run**): the CURRENT date is the date before
the first edit and is never recomputed from the form, the list, a reload or a crash; clicking OK completes the task —
`done()` on the next turn, no re-open, no second save; find the appointment from the patient dashboard's Appointments
card rather than the Calendar search form; "All Users" in the Providers box if the calendar is used. Crashes: median 6
crashpad reports per attempt (range 2–11), 1–3 RCU stalls; still the dominant source of lost context. Restores: n = 14, p50 78 s, 5/14 under 30 s, 5/14 over 120 s, max 210 s;
**the pool fell back from revert to fork mode at the third reset of each worker** (16:05 → 16:18, both workers' next
restores were `mode=fork` at 137 s and 127 s) — the refusal's error text was not persisted (`pool.events` only, fixed:
the fallback now logs to stderr with the error), so which of the two refusals (503 capacity, or something new) it was
is not known; the family-2 run that follows records it.

**Family 2, two-system variant (OpenEMR insurance edit + portal resubmission), prompt v7, seeds 0, 3, 4, 5, 6, 7 — the six
seeds that were 0/6 over 18 attempts on v6 before the seeding fix (`runs/luna-v7-fam2-2sys`, 17:26–17:59 local, 7
attempts):** **6/6 verified = 100 % [61, 100]; first pass 5/6**, seed 3 on its retry. Every verified episode ended with
`done()` in 60–87 actions (median 66) and 205–289 s — the same shape as the portal-only variant — and passed all eight
checks (policy number and plan in OpenEMR, claim `RESUBMITTED` with the new member id, one resubmission, no appeal, the
other claim and both distractors untouched). Priced by `forkloop metrics`: $0.58 for the run ($0.51 tokens, $0.07 VM),
**$0.097 per verified seed**. The one failure (seed 3 attempt 1, `WRONG_VALUE` = nothing changed): Luna had the
Insurance section's pencil under the cursor at action 55 when the tab crashed; after logging in again it never found the
Insurance section on the dashboard and spent the last 100 actions on `scroll`/`End`/`ctrl+f` with varying coordinates,
which the loop detector (same kind within 20 px) does not catch. So the family-2 seeding fix (subscriber sex + address on
the policy) and v7's insurance-edit navigation are confirmed live; what remains for both families is crash recovery.
Restores: n = 7, p50 85 s, 1/7 under 30 s, 2/7 over 120 s, max 174 s. **Pool: the same revert→fork flip as the family-1
run, now with the reason captured** — `revert_unsupported_fell_back_to_fork worker=0 error=BackendError: machine
…vm_000028… not reachable after revert: desktop … not ready` at 17:32:59, i.e. the revert API call was accepted and the
guest simply did not answer within the backend's 90 s post-revert window (the slow restore mode), after which the pool
discarded the machine and ran the remaining 5 attempts in fork mode. Fix (offline-tested, not yet run live): a revert
that times out (`RevertTimeoutError`) or gets a 503 replaces that one machine and keeps revert mode
(`revert_failed_replaced_machine`); only a real refusal switches the pool; the post-revert ready window is 240 s
(`SolariBackend.revert_ready_timeout_s`).

**Family 1 on prompt v8, seeds 0–9, same budget/retry, `--pool-mode revert` (`runs/luna-v8-fam1-s0-9`, 2026-09-04 00:40–01:47
local, 16 attempts):** **7/10 verified within two attempts (first pass 4/10: seeds 1, 3, 5, 8; retries recovered 4, 6, 7)** —
the same total as v7 with a worse first pass, but the verified episodes are far shorter: 34, 59, 72, 88, 141, 146, 154
actions (median 103 vs 155), six of seven ending with `done()` (v7: none). $1.94 for the run, $0.28 per verified seed.
Every one of the nine failed attempts is `WRONG_SLOT` by exactly one week (Sep 25 for 18, Sep 28 for 21, Sep 29 for 22…):
Luna saved the right date, clicked OK, and then a blank modal titled **"Available Appointments Calendar"** appeared over
the page (screenshot `…/000000-6fd6eb/shots/056_after.png`); v8's "done() on the next turn" lost to the visible modal —
Luna closed it, went looking for the appointment again, and after ~16 actions its own CURRENT/TARGET line had scrolled
out of the 16-action history window, so it re-derived "next Friday" from the saved date and saved again. v8's other
hint made that worse: "read the Appointments card on the dashboard" sends it below the fold, and mouse-wheel scrolls
at the dashboard's right edge do not scroll the iframe — 5–6-action scroll runs in every failure (seed 7: four of
them), invisible to the loop detector because the coordinates varied. Fixes: (1) the loop detector now also fires on
five consecutive scrolls in one direction regardless of coordinates (`loop_warning`, tested); (2) prompt v9
(`hosted_gui_agent_v9.md`, **not yet run**) is v7's navigation plus the fixed-CURRENT rule, and names the modal:
"a blank 'Available Appointments Calendar' window appears after OK, needs no action, and your next action must be
done()". Pool: **revert mode held for all 16 attempts** — one revert got a 503 at 01:07 (`revert_failed_replaced_machine
worker=1 … CapacityError … could not restore this snapshot in time`), that one machine was replaced with a fresh
fork (183 s) and the pool stayed in revert mode; restores n = 16, p50 52 s, 6/16 under 30 s, 1/16 over 120 s. No fork
deaths.

**Family 1 on prompt v9, seeds 0–9, same budget/retry, `--pool-mode revert --concurrency 2` (`runs/luna-v9-fam1-s0-9`,
2026-09-04 02:00–03:05 local, 15 attempts):** **6/10 verified within two attempts, first pass 5/10** (seeds 0, 1, 5, 6, 8;
seed 3 on its retry). The verified episodes are the shortest yet — 66, 70, 70, 74, 92, 116 actions (median 72 vs 103 on v8
and 155 on v7), 6/6 ending with `done()` two actions after OK — so v9's modal rule did what it was written for. $1.73 for
the run ($1.49 tokens, $0.24 VM), $0.29 per verified seed. The nine failed attempts fall into four classes, all read
end to end:

| class | attempts | what happened |
| --- | --- | --- |
| **oracle false negative** (`DIRECT_DB_WRITE`) | 2 (seeds 3, 7, attempt 1) | The two cleanest episodes of the run: 40 and 37 actions, `done()` after OK, every effect check passed (date, window, provider, single event) — scored 0 because `ui_path` found the `openemr_postcalendar_events` change unaudited. Both went calendar → Finder → editor without ever opening the patient's dashboard; all six verified episodes opened the dashboard (which writes a `log` row keyed by the patient id, the branch of the loose tripwire that matches). So OpenEMR's calendar-save log row does not match the "comments name the table and pk" fallback added on 2026-09-03 (its live format was never confirmed). Seed 3's retry verified in 92 actions via the dashboard. Fix in flight: the verdict now keeps `audit_rows_after_watermark` on a `ui_path` failure, and a scripted replay of seed 7's 37 actions on a fresh fork (`runs/probe-audit-s7`, no tokens) captures the live rows. |
| **date drift** (`WRONG_SLOT` by one week, or an unsaved chain) | 3 (4a1, 4a2, 9a1) | Not the modal this time — the recompute happened *inside the edit form*, one step after the date field showed the new date: seed 9a1 picked Sep 15 in the date picker, scrolled, then wrote "CURRENT 2026-09-15 → TARGET 2026-09-22" and saved Sep 22 (34 actions, `done()`); seed 4a2 typed Sep 18, scrolled, and saved Sep 25; seed 4a1 chained Sep 11 → 18 → 25 → … → Nov 13 through seven picker/typing rounds because its Save clicks at (347, 680) never registered (the row of buttons was at y≈632 in every verified episode), leaving the appointment untouched. Root cause found in the harness, not the prompt: the history the model sees is compact actions only (`env.py`, `ep.history.append(parsed.to_compact())`), so the "CURRENT → TARGET, copy it every step" line never reaches the next turn — the policy re-derives both dates from the screen every step and drifts the moment the field shows the new date. Second finding: `collect` never passed `--history-k` to the `Env`, so `--history-k 16` showed the env default of 8. Both fixed offline: the env keeps `max(history_k, 8)` actions and `--history-notes` renders the model's own reasoning line (`note_from_reply`, ≤ 160 chars, action line stripped) next to each previous action; prompt v10 tells it that the notes are its only memory and that the form's date field is never a CURRENT source, and forbids clicking days in the picker (both drift seeds used it; the verified ones typed). |
| **crash budget** | 3 (2a1, 7a2, 9a2) | 10, 4 and 14 crashpad reports, 10, 5 and 18 re-logins, 154–168 actions, the appointment never edited (dates unchanged). Seed 9a2 spent 12 `F5` reloads on "Aw, Snap!"; each re-login costs 4–6 actions and the Finder search is repeated after every crash. Same class as the family-3 crash failures; not a prompt problem. |
| **calendar click cycle** | 1 (2a2) | 124 clicks in 154 actions: clicking the appointment entry in the calendar opened the patient's chart (the name link), Luna went "back to the calendar", clicked the same entry again — a three-action cycle (entry → chart → calendar) invisible to the loop detector; 1 crash. v10 adds: click the TIME of the entry (the name opens the chart), and never repeat a click that opened the chart. |

Crashes overall: median 2 crashpad reports per attempt (range 0–14), 1–3 RCU stalls each, 2–18 re-logins; the four
attempts with ≥ 4 crash reports all failed. Restores: n = 15, p50 109 s, 5/15 under 30 s, 5/15 over 120 s, max 192 s —
**revert mode held for all 15** (no `revert_*` events, no 503, no fork deaths, nothing reaped; `reap --dry-run` = 0
afterwards). The v9 gate (first pass ≥ 6/10) was missed by one seed, with two of the misses being oracle false negatives
on otherwise perfect episodes; v10 (`hosted_gui_agent_v10.md` + `--history-notes`) runs next on the same seeds.

**Family 1 on prompt v10 + `--history-notes`, seeds 0–9, same budget/retry (`runs/luna-v10-fam1-s0-9`, 2026-09-04
03:18–03:58 local, 11 attempts):** **10/10 verified within two attempts, first pass 9/10** — the gate (first pass ≥ 6/10,
verified median < 120) met with room. Verified episodes 31, 36, 40, 41, 45, 52, 61, 64, 81, 102 actions (**median 48.5**
vs 72 on v9, 103 on v8, 155 on v7), 10/10 ending with `done()`; $0.89 for the run ($0.78 tokens, $0.11 VM), **$0.089 per
verified seed** (v9 $0.29, v8 $0.28, v7 $0.34). What changed and what it did:

- *The memory.* The history the model sees was compact actions only, so the "CURRENT → TARGET" line never survived a
  turn; `--history-notes` renders the model's own reasoning line next to each previous action and v10 says those notes
  are its only memory. **Date drift went from 3 attempts on v9 to 0**: seed 4 (drifted twice on v9) typed Sep 18,
  scrolled, and still saved Sep 18 at action 42 — the note "CURRENT 2026-09-11 → TARGET 2026-09-18" was in front of it;
  seed 9 (drifted at action 29 on v9) verified in 102. Also fixed on the way: `collect` never passed `--history-k` to
  the `Env`, so every earlier hosted run showed 8 previous actions, not 16.
- *The oracle.* Seed 7 verified in 36 actions through the calendar with no dashboard visit and `ui_path` passed — the
  path that was a false `DIRECT_DB_WRITE` on v9 — because the tripwire now base64-decodes OpenEMR's `log.comments`
  (measured in `runs/probe-audit-s7`: a scripted replay of the v9 seed-7 episode reproduced the false negative and
  captured row 508006, `scheduling-update`, patient_id 0, comment = base64 of the UPDATE with `'507000'` bound).
- *The one failure* (seed 5, attempt 1): crash budget — 9 crashpad reports, 9 re-logins, 43 scrolls, and a wrong save
  (Sep 17 instead of 18, wrong window) after the third re-login; the retry verified in 81 actions on 2 crash reports.
  Crashes remain the only failure class left on this family: 4/11 attempts had ≥ 3 crash reports, and the two with the
  most (9 and 6) were the longest (161 and 102 actions).

Pool: revert mode held for the run; **two 503 reverts** (`revert_failed_replaced_machine`, 03:32 and 03:53, "no warm
desktop hosts … could not restore this snapshot in time") each replaced that machine with a fresh fork (238 s and 243 s
to ready) without leaving revert mode — the 2026-09-03 fix working as designed twice. Restores n = 11, p50 105 s, 2/11
under 30 s, 3/11 over 120 s; no fork deaths; `reap --dry-run` = 0 afterwards. v10 + notes is the family-1 prompt from
here; seeds 10–34 of families 1–2 follow for SFT volume.

**Family 1 on v10 + `--history-notes`, fresh seeds 10–34, same budget/retry (`runs/luna-v10-fam1-s10-34`, 2026-09-04
03:59–06:12 local, 38 attempts):** **15/25 verified = 60.0 % [40.7, 76.6] within two attempts; first pass 12/25 = 48 %**;
$2.98 for the run ($2.60 tokens, $0.38 VM), $0.20 per verified seed; verified median 55 actions, 15/15 `done()`. This is
the honest family-1 number — the 9/10 on seeds 0–9 was partly luck of the draw: the requested half-day is sampled
independently of the appointment's time, and **on seeds 0–9 the appointment already sat in the requested window for 8/10
seeds, on seeds 10–34 for only 11/25** (computed offline from the pure generator). The 23 failed attempts:

| class | attempts | what happened |
| --- | --- | --- |
| **window** | 13 (8 first pass + 5 retries; seeds 11, 15, 17, 18, 19, 25, 27, 29) | Right date, `done()`, but the appointment kept its old time when the task asked for the other half of the day (`event_time_window`). v10's "change the time only if the requested window needs it" is never acted on: the notes carry CURRENT/TARGET dates but no window, and the reasoning never mentions the half-day. Of the 14 fresh seeds that needed a time change, 3 verified first pass (10, 20, 26). Retries do not recover it (0/5). |
| **add-event** | 7 (4 first pass + 3 retries; seeds 13, 14, 21, 23, 15, 27) | v10's "click the TIME to open the appointment editor" lands on the hour labels at the left of OpenEMR's calendar grid, which open the *Add New Event* form for that slot. Luna fills the target date, the Save at y≈680 is refused (duration 0, field marked invalid), it sets 30 min and saves a **second** appointment (`single_event` 2, `no_collateral` add), original untouched. Same signature every time (Save at 680 vs 632 in the edit form). |
| **date arithmetic** | 3 (seed 34 ×2, seed 13 retry) | Clean execution of a wrong date: "next Tuesday after Monday 2026-09-14" → Sep 22 (both attempts, 40 and 22 actions); seed 13 retry a week late. |

No crash-budget loss this run (the retry pass had 0 attempts with ≥ 4 crash reports failing for that reason); crashpad
median 2 per attempt (max 9). Pool: revert mode held for all 38 restores (p50 112 s, 8/38 under 30 s, 15/38 over 120 s)
through **six 503 reverts**, each replaced in place in 203–242 s; no fork deaths. Prompt frozen at v10 for the night as
planned; the v11 candidates are (a) a window rule with an explicit "CURRENT time → TARGET time" note, (b) "click the
appointment's own text, never the hour labels; Cancel any form titled Add New Event".

**Family 2 on v10 + `--history-notes`, fresh seeds 10–34, both variants by seed (`runs/luna-v10-fam2-s10-34`, 2026-09-04
06:14–08:06 local, 26 attempts):** **25/25 verified = 100 % [86.7, 100]; first pass 24/25.** 9 portal-only seeds verified in
8–12 actions, 16 two-system seeds (OpenEMR "Edit Current Insurance" + portal resubmission) in 42–142 (median of all 25:
48); every verified episode ends with `done()` and passes all checks (policy number and plan in OpenEMR, claim
`RESUBMITTED` with the new member id, one resubmission, no appeal, other claim and distractors untouched, audit path).
$1.74 for the run ($1.51 tokens, $0.23 VM), **$0.07 per verified seed**; 1,301 SFT records. The single miss (seed 31,
attempt 1) was not the policy: the `before_episode` Chrome relaunch raced its own `pkill` — `chrome.log` "Opening in
existing browser session", then no browser — so the episode began on a bare desktop; Luna opened Chrome from the dock
(default profile, no portal session) and spent 151 actions trying portal credentials. Fixed the same hour (relaunch
waits for the old Chrome to exit, clears the profile's Singleton files, verifies the new one, and raises so the reset
stage fails and the seed is re-queued); the retry verified in 10 actions. Restores n = 26, p50 69 s, 9/26 under 30 s,
7/26 over 120 s; two 503 reverts replaced in place; no fork deaths; crashpad median 0 per attempt.

**Best-of-2 fork search on the 10 family-1 seeds that failed both v10 attempts (`runs/luna-v10-bo2-hard`, 2026-09-04
08:02–10:40 local, `--best-of 2 --search-mode fork --concurrency 1`, one attempt each):** **2/10 recovered** (seeds 17
and 34) = 20 % [5.7, 51.0], $1.06 for the run ($0.85 tokens, $0.20 VM), **$0.53 per recovered seed** — 2.7× the
$0.20 of a plain retry-verified seed, and the plain retry pass had already recovered 3/13 on this family. 16 branch
points (random, no confidence signal from the hosted model), 20 branch forks, 2 wins: seed 17 branched at action 8 between
the patient search box and "All Users" (1.0 vs 0.0) and seed 34 at action 16. Every loss branched and **both candidates
failed the same check** (window ×6, date ×2): a random fork at a click does not change a rule the policy never applies,
so search cannot substitute for the missing half-day rule. **Checkpoint deletes work now**: `snapshot_delete_errors`
empty in all ten verdicts, 10/16 deleted; the other six leaked silently through the path where the two candidates
deduplicate to one (checkpoint taken before `_dedupe`, no delete on that path) — fixed and tested (`backend.snapshots`
empty after a deterministic-policy search). All ten leftover `cp-*` snapshots (four from the 2026-09-04 01:xx smoke,
six from this run) were deleted with `backend.delete_snapshot` afterwards, 10/10 succeeded, leaving the four
golden-lineage snapshots. Operational: the controller Mac slept ~80 min mid-run (battery); on wake the pending revert
POST failed with a bare `ConnectionError`, which the pool classified as a refusal and switched the run to fork mode —
fixed (only 409/"Not revertable"/paused flips the mode now; other errors replace the machine and keep revert mode).

**Family 3 on prompt v5, fresh seeds 100–139, `--max-steps 120 --max-seconds 900 --retry-failed 1 --pool-mode revert
--concurrency 2` (`runs/luna-v5-f3-s100-139`, 2026-09-04 11:05–13:40 local, 45 attempts):** **38/40 verified = 95.0 %
[83.5, 98.6]; first pass 35/40**, verified median 59 actions (43–128), $3.08 for the run ($2.69 tokens, $0.39 VM),
**$0.081 per verified seed** — the same picture as seeds 20–99 (96.2 %, $0.08). Combined seeds 0–139: 132/140. The seven
failed attempts are the oracle's own target classes: transcription ×2 (an extra digit; `Q` read as `9`), a decoy letter's
number ×1, the 120-action budget spent among decoy documents ×2, one crash-budget loss (16 crash reports, 13 re-logins), and one
budget exhausted while attaching the letter. Restores n = 45, p50 82 s, 9/45 under 30 s, 11/45 over 120 s (max 418 s);
three 503 reverts replaced in place; one replacement create timed out (240 s), its retry hit the 429 cap and the
pool's reaper removed the orphan; no fork deaths. Export: 2,377 SFT records from 38 episodes.

**Overnight totals (2026-09-04, seven runs, `docs/overnight-2026-09-04.md`):** 121 seed-runs, 146 attempts, 96 verified,
$9.92 tokens + $1.56 VM; 5,267 new SFT records (families 1–2: 2,890 from 52 episodes; family 3: 2,377 from 38). Revert
mode held through 166 restores and thirteen 503 reverts; 0 fork deaths; the account ends with four snapshots and 0
machines.

**Families 1–2 status after v7 (seeds 0–9 / two-system seeds):**

| family / variant | v6 (2026-09-03 morning, 3 attempts) | v7 (2026-09-03 night, 2 attempts) | v7 first pass | $/verified (v7) |
| --- | --- | --- | --- | --- |
| 1 reschedule_constrained, seeds 0–9 | 3/10 (first pass 0/10) | **7/10** | 6/10 | $0.34 |
| 1 reschedule_constrained, seeds 0–9, **v8** (2026-09-04) | — | **7/10** | 4/10 | $0.28 (median 103 actions, 6/7 end with done()) |
| 1 reschedule_constrained, seeds 0–9, **v9** (2026-09-04) | — | **6/10** | 5/10 | $0.29 (median 72, 6/6 done(); 2 of the misses were oracle false negatives, fixed) |
| 1 reschedule_constrained, seeds 0–9, **v10 + history notes** (2026-09-04) | — | **10/10** | 9/10 | $0.089 (median 48.5, 10/10 done()) |
| 1 reschedule_constrained, **fresh seeds 10–34, v10 + history notes** (2026-09-04) | — | **15/25 = 60 % [40.7, 76.6]** | 12/25 | $0.20 (median 55; window 13, add-event 7, date 3 of 23 failed attempts) |
| 2 update_insurance_reconcile, portal-only (seeds 1, 2, 8, 9) | 4/4 | not re-run | — | — |
| 2 update_insurance_reconcile, two-system (seeds 0, 3–7) | 0/6 (18 attempts) | **6/6** | 5/6 | $0.097 |
| 2 update_insurance_reconcile, **fresh seeds 10–34, both variants, v10 + history notes** (2026-09-04) | — | **25/25 = 100 % [86.7, 100]** | 24/25 | $0.07 (median 48; the one miss was a Chrome relaunch race, fixed) |
| 3 resolve_denial, **fresh seeds 100–139, v5** (2026-09-04) | — | **38/40 = 95 % [83.5, 98.6]** | 35/40 | $0.081 (median 59; transcription 2, decoy 1, budget 4 of 7 failed attempts) |
| 3 resolve_denial, **student: base microsoft/Fara1.5-4B (bf16, mlx-vlm, 1.5 s/call), fresh seeds 200–229, as shipped** (2026-09-04, `runs/fara15-4b-base-f3-s200-229`) | — | **0/30 = 0 % [0, 11.3]** (one attempt) | 0/30 | $0.007 VM/episode (invalid-action rate 20.8 %, all `visit_url`; 24/30 ended by the 10-invalid limit inside ~30 steps) |
| 3 resolve_denial, **student: base Fara1.5-4B + `--nav-macro`, same seeds** (2026-09-04, `runs/fara15-4b-base-f3-s200-229-nav`) | — | **0/30 = 0 % [0, 11.3]** (one attempt) | 0/30 | $0.013 VM/episode (invalid 0.0 %; 30/30 stuck at the OpenEMR login: `admin` and `pass` typed into one field, then guessed passwords; 10 stopped via `ask_user_question`, 19 out of steps) |
| 3 **resolve_denial_easy** (page-1 number, no distractors), **student: base Fara1.5-4B + `--nav-macro`, same seeds** (2026-09-04, `runs/fara15-4b-base-f3easy-s200-229-nav`) | — | **0/30 = 0 % [0, 11.3]** (one attempt) | 0/30 | $0.014 VM/episode (invalid 0.0 %; identical login wall — the easier document is never reached; 10 `ask_user_question`, 20 out of steps) |
| 3 resolve_denial, **student probes, base Fara1.5-4B + `--nav-macro`, seeds 200–204, 60 steps** (2026-09-05, `runs/fara15-4b-probe-{a-note,b-prompt,c-notes,ab}-f3-s200-204`, `docs/student-2026-09-06.md`) | — | 0/5 each (one attempt) | 0/5 | $0.04–0.05 VM per probe. **OpenEMR login rung** (`verdict.details.ui_milestones`): (a) `--instruction-note` spelling out username/password fields **5/5**, (b) `fara_no_user_v1.md` prompt (no critical points, v5 conventions) **4/5** with 0 `ask_user_question` stops, (c) `--history-notes` 1/5 (retypes `pass` into the same field), (a)+(b) **5/5**. No probe reached the patient chart: after the login the model navigates by invented URLs (`/openemr/patient/1`) and drops the session |
| 3 resolve_denial, **student: base Fara1.5-4B, the fair configuration (`--nav-macro` + `fara_no_user_v1.md` + `--instruction-note`), seeds 200–229, 120 steps** (2026-09-05, `runs/fara15-4b-fair-f3-s200-229`) | — | **0/30 = 0 % [0, 11.3]** (one attempt) | 0/30 | $0.018 VM/episode ($0.54 the run). Staircase: **openemr_login 30/30**, openemr_chart 0/30, everything after 0/30; invalid 0.0 % (1/3,643); 0 `ask_user_question`; 25/30 navigate OpenEMR by invented URLs (92 calls) and drop the session, 12/30 hit the post-login "Aw, Snap!"; median 3.78 s per call with two episodes sharing the server |
| 3 resolve_denial, **student: base Fara1.5-9B, the same fair configuration and seeds** (2026-09-05, `runs/fara15-9b-fair-f3-s200-229`, mlx-vlm bf16 on the Mac) | — | **0/30 = 0 % [0, 11.3]** (one attempt) | 0/30 | $0.032 VM/episode ($0.95 the run). Staircase: openemr_login 30/30, **openemr_chart 4/30 = 13 % [5.3, 29.7]**, Documents tab 4/30, document view 0/30, auth_typed 0/30; 8.09 s per call shared (4.6 alone), 6/30 episodes cut by the 900 s wall; 28/30 repeat one click position ≥ 10 times, 27/30 type invented `/openemr/login…` URLs, 19/30 post-login "Aw, Snap!" |

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
| **OpenAI GPT-5.6 Luna as teacher, 2026-09-03 (`runs/luna-medium-s0-2`, `runs/luna-high-s0-2`, seeds 0–2, hosted through the OpenAI-compatible student path: one screenshot + last 8 actions as text, compact grammar, `reasoning_effort` medium/high)** | **0/4 verified** (medium: seed 0 only — the other two seeds hit a 429 after a fork never became ready and leaked; high: 0/3). Luna logs into OpenEMR and finds the Patient Finder on its own, then **loops**: 40+ clicks on an already-focused search box without typing, or repeated clicks on a date filter after 'No matching records found'. No Chrome crash in any of these episodes (screenshots checked). Fast and cheap: 1.5–2.2 s per model call (p50), ≈ 1.7k input tokens per call, **≈ $0.03 per 60-step episode** against ≈ $2.20 for Opus. The failure is the prompt path as much as the model — the compact prompt was written for 4B students and gives no anti-loop guidance, no previous screenshot, no form-entry rules — so a hosted-frontier prompt (`forkloop/policies/prompts/hosted_gui_agent.md`, `collect --system-prompt-file … --history-k 12`) is queued for a second run. Smoke test on the login page: all three effort levels clicked within 10 px of Opus's click. **xhigh (`runs/luna-xhigh-s0-2`): 0/2, one fork died** — seed 0 issued `wait(2)` 103 times and ran out the 600 s wall budget, seeds 1–2 looped on the search box again; effort does not touch the failure. Added a history-based loop warning to the hosted prompt path (`student.loop_warning`: three near-identical pointer actions, or three waits, append an explicit 'do not repeat' line) before the v2 run. **v2 (`runs/luna-high-v2-s0-2`, hosted prompt + loop warning + history 12): 0/3 but no loops** — Luna now logs in, finds the patient, opens Documents and reads an authorization number on two seeds (seed 1: AUTH-73Z20943, correct; seed 0: AUTH-24M33556, a decoy — the oracle would have caught it), then runs out of the 60-action budget while switching to the portal or trying to download the attachment; seed 2 spent its budget in OpenEMR's document tabs. It needs more actions than Opus (which fits in 60), so the next run doubles the budget (`collect --max-steps 120`, recorded in run.json as `budget_override`) and adds the previous screenshot (`--prev-shot`). **v3 (`runs/luna-high-v3-s0-2`, + `--prev-shot`, history 16, `--max-steps 120`): seed 1 VERIFIED at 1.0** — the first Luna trajectory the oracle accepted: 62 steps (60 actions, i.e. it would also have fit the 60-action budget), 193 s, 189k input / 6k output tokens, **$0.045** (Opus on the same seed: $1.83). Seed 0 failed a new way: the authorization letter opened in a Chrome *popup* window whose address bar is read-only, and Luna alternated ctrl+l / type(url) for 70 actions (the loop detector now catches alternating pairs); it had also picked a decoy letter that says 'for a different service'. Prompt v4 adds popup-window and decoy guidance. Seed 2 of v3 hit yet another infrastructure flake: the OpenEMR login page came up without its stylesheet after the fork restore (login button disabled), and Luna spent all 120 actions re-entering the password instead of reloading — the prompt now says to reload any broken-looking page. **v4 (`runs/luna-high-v4-s0-2`, popup/decoy/reload guidance, alternating-loop warning): 2/3 verified** — seed 1 in 57 steps for $0.043, seed 2 in 97 steps for $0.075; seed 0 (the four-decoy seed) still `NOT_DONE` after 137 steps ($0.105). **Luna volume run launched on seeds 3–19 with the v4 configuration** (`runs/luna-high-v4-s3-19`). First `WRONG_VALUE` verdicts are **transcription errors, not decoys**: seed 4 filed AUTH-72646694 for AUTH-72G46694 (G read as 6), seed 6 filed AUTH-84K274 for AUTH-84K27294 after reading it correctly 60 steps earlier. The hosted path sent images without OpenAI's `detail` hint (default `auto` may downscale); it now sends `detail: high` for hosted models, and prompt v5 adds an exact-transcription rule. **Volume result (seeds 0–19): Luna v4 11/20 = 55 % [34, 74] for $1.61 of tokens in total, $0.15 per verified trajectory; Opus 9/15 = 60 % [36, 80] for $31.97, $3.55 per verified. On the 15 shared seeds: Opus 9, Luna 8** (`scripts/compare_teachers.py`, `runs/exports/opus_vs_luna.md`). Luna's failures: 7 budget exhaustions at 120 actions, 2 transcription errors; it verified two seeds Opus failed (2, 14) and failed three Opus passed (0, 11, 13). Cost per verified trajectory is 24× lower with no measurable loss of accuracy at this sample size, and Luna's `WRONG_VALUE` episodes are exactly the kind the oracle exists to reject. **v5 retry on the nine seeds v4 failed (`runs/luna-high-v5-retry`: v5 prompt — one tab per app, exact transcription, keep the number in reasoning — plus `detail: high` images): 7/9 verified**, including the four-decoy seed 0 and both transcription-error seeds (4, 6); seed 10 failed `WRONG_ATTACHMENT` (appeal filed with the right number but the wrong file), seed 13 `NOT_DONE`. **Union over seeds 0–19: Luna verified 18/20 within two attempts.** |
| **Luna v5, clean single-attempt pass on seeds 0–19 (`runs/luna-high-v5-s0-19`, 2026-09-03 20:09–21:08 local)** | **17/20 verified = 85 % [64, 95]; $1.24 of tokens for the run, $0.073 per verified trajectory; Opus on the same family: 9/15 = 60 % [36, 80] at $3.55 per verified; on the 15 shared seeds Luna 12, Opus 9** (`scripts/compare_teachers.py`, `runs/exports/opus_vs_luna_v5.md`). Median 69 steps and 227 s per episode; the three failures (seeds 4, 10, 13) are all `NOT_DONE` at the 120-action budget, none wrong-value or collateral. The desktop cost $0.19 for the twenty episodes, 13 % of the total — Solari is no longer negligible once the teacher is this cheap. SFT export: 1,122 examples from the 17 verified episodes (`runs/exports/sft_luna_v5_s0-19.jsonl`). Caveats: the prompt was iterated on these seeds (v2→v5) so 85 % is optimistic until seeds 20+ and families 1–2 confirm it; Luna runs at 120 actions vs Opus's 60, and Opus's failures were mostly Chrome crashes. |
| **Luna v5 on families 1–2, seeds 0–2 (`runs/luna-v5-fam12-s0-2`, 2026-09-03 21:10–21:40 local)** | **Family 1 (calendar reschedule) 1/3, family 2 (insurance update + portal resubmission) 2/3 — the first end-to-end completions of both families by any policy** (the plan's family-1 'blocker' is closed for good). Family-1 failures: seed 1 read 'the next Monday' as next Monday from today, never found the appointment and moved nothing (`WRONG_SLOT` = date unchanged); seed 2 found and moved it but to the second Friday after (Sep 25 for Sep 18) and its edit left no patient-keyed `log` row (`ui_path` failed — OpenEMR's calendar save via the 'provider unavailable' prompt logs differently; the audit tripwire is coarse, `docs/limitations.md`). The generator now says 'the next <weekday> <half> after its current date (the appointment is within the next two weeks)'. Family-2 failure: seed 0 spent 600 s trapped in the patient-search DOB date picker. Seed 2's fork failed to become ready and the new `--reset-retries` path re-queued it (verified on the retry). Prompt v6 adds date-picker (Escape, type YYYY-MM-DD) and calendar guidance. About $0.50 of tokens for six episodes. |
| **Luna v5 on family 3, seeds 20–99 (the SFT set), `--max-steps 120 --max-seconds 900 --retry-failed 2` (`runs/luna-v5-f3-s20-99`, 2026-09-03)** | **77/80 verified = 96.2 % [89.5, 98.7] (Wilson, `forkloop metrics`); first pass 73/80 = 91 %, the retries recovering 4 of 7 (both `NOT_DONE` budget seeds and two of five `WRONG_VALUE`).** $6.12 total ($5.21 tokens at full input price — the hosted path logs no cache reads, so an upper bound; 22.0M in / 0.66M out — plus $0.92 VM), **$0.08 per verified trajectory**, median 63 steps / 244 s. **SFT export: 5,232 examples from the 77 verified seeds** (`runs/exports/sft_luna_v5_f3_s20-99.jsonl`; no failed or superseded attempt leaks in). Fresh-seed confirmation that seeds 0–19 (85 %) understated the family: the prompt was tuned on 0–19, yet unseen 20–99 scored higher. The three unrecovered seeds are exactly what the oracle exists to reject: seed 44 read the same `AUTH-18Q90667` as `...18090667` on all three attempts (a systematic Q→0 misread — three distinct seeds, 44/77/82, made it), seed 47 picked a decoy letter twice then ran the budget out, seed 82 mixed a misread with a budget exhaustion. No collateral, duplicate or wrong-record verdicts in 80 episodes. **Combined family 3 (seeds 0–99, `scripts/compare_teachers.py`, `runs/exports/opus_vs_luna_v5_f3_full.md`): Luna v5 94/100 = 94 % [88, 97] at $0.061 per verified; Opus 5 9/15 = 60 % [36, 80] at $3.55; Luna 12 of the 15 shared seeds, Opus 9.** Infrastructure over the run: restores n=90, p50 83 s, 29/90 over 120 s, max 353 s (the same bimodal 429 tail); 2 fork deaths (control channel closed 1000) and 2 create timeouts, every one absorbed by `--reset-retries` and the pool's 429 reap without losing a seed; one ~3.5 h stall was this Mac sleeping (both live forks expired; the pool recreated them on wake). `--retry-failed 2` and the reset-retry path together carried the run through all of it unattended. |
| **Luna v6 on families 1–2, seeds 0–9, `--max-steps 150 --max-seconds 1200 --retry-failed 2` (`runs/luna-v6-fam12-s0-9`, started 2026-09-03T08:29:30+00:00, ≈ 7 h wall, first use of `collect --retry-failed`)** | **7/20 seeds verified within three attempts = 35 % [18, 57]; family 1 3/10 (first pass 0/10; seeds 2 and 6 verified on attempt 2, seed 4 on attempt 3), family 2 4/10 (the portal-only variant 4/4 on the first pass in 8–12 steps; the two-system variant 0/6 over 18 attempts).** 50 attempts, $7.05 of tokens as priced by `forkloop metrics` (27.0M in / 1.37M out at full input price — the hosted path records no cache reads, so this is an upper bound) plus $1.22 of VM; $1.18 per verified seed; every family-1 and two-system attempt ran to the 150-action budget (median 154 steps, 744 s). Failure analysis (`scripts/inspect_episode.py`, every attempt read): **(a) prompt bug, family 1** — v6's rule for OpenEMR's "Provider not available, use it anyway?" dialog (cancel, pick another time) is wrong: the dialog appears on *every* save because the seeded providers have no schedule, "Find Available" opens an empty overlay, and Luna loops on Cancel (up to 22 turns per attempt) and then re-derives "next <weekday>" from the date it has already typed (seed 1 saved Sep 21 for Sep 14; seeds 0, 3–9 left the date unchanged). Seed 7 attempt 1 isolates it: zero Chrome crashes, ten cancels, no save. The verified pilot episode had clicked OK. Prompt v7 (`hosted_gui_agent_v7.md`) says click OK and never recompute the target from a typed date; not yet run. **(b) oracle false negative, family 1** — seed 2 attempt 1 moved the appointment correctly (every effect passed) but scored `DIRECT_DB_WRITE`: OpenEMR's `EventAuditLogger::auditSQLEvent` writes `log.patient_id` from the *session's* active chart (0 when the appointment is opened from the Finder with no chart open) and puts the SQL with its bound values in `comments`; the loose `ui_path` match now also accepts a log row whose SQL names the changed table and primary key (tested offline). `scripts/audit_probe.py` replayed seed 2 on a fresh fork but did not reproduce the calendar edit (`checked=0`), so the live scheduling-update comment format is not yet observed; the same probe did confirm the family-2 seeding fix live — the insurance row now carries `subscriber_sex=Male`, `subscriber_city=Austin`, `subscriber_state=TX`, which are valid OpenEMR `sex`/`state` list option ids. **(c) world seeding gap, family 2** — the two-system variant is not completable: OpenEMR 8.3's insurance editor requires subscriber sex, street, city, state and ZIP, and the seeded policy had none (seeds 0 and 5 reached the form, set plan and policy number correctly and were refused on Save; seeds 3, 4, 6, 7 never found the Insurance edit icon on the dashboard or lost the budget to crashes). The per-episode seeding now copies the patient's sex and address onto the policy (same RNG draw order, so every existing seed's instruction, expected values and patient row are byte-identical). **(d) Chrome crashes** — 13 of the 20 first-pass episodes logged ≥ 4 crashpad reports (median 5.5; median 4 F5 reloads), against about one per family-3 replay earlier; the calendar and patient dashboard crash more than the document viewer. **(e) restores** — n = 50, p50 76 s, p90 137 s, max 239 s, 17/50 above 120 s and 14/50 below 30 s, bimodal; no fork died (50/50 attempts reached a verdict). Hypothesis, not yet measured: the slow half is the pool's exponential 429 backoff (1+2+…+60 s = 123 s) while Solari still counts the just-killed fork against the cap; the pool now logs `create_retry`/`restored` events to stderr and caps the 429 backoff at 15 s, so the next run measures it. |
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

## SFT ladder, family 3 (resolve_denial), held-out seeds 200–229 — the table the README draws from

Every row is one `collect` run with the fair configuration (`--nav-macro`, `fara_no_user_v1.md`, the credentials
note, 120 steps / 900 s, greedy, retries off) against the same vLLM serving stack; the milestone staircase is
`scripts/milestone_staircase.py` (chart: `docs/images/staircase-f3-ladder.png`). "invented" = an appeal submitted with
a number that is neither the task's nor one of its decoys.

| row | recipe | run | success | login | chart | document | auth_typed | invented | portal claim | appeal form | submitted | invalid | loop/budget-out | GPU, wall, $ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | — | `fara15-4b-base-vllm-fair-f3-s200-229` (2026-09-06) | **0/30** [0, 11.3] | 30 | 2 | 0 | 0 | 0 | 2 | 0 | 0 | 0.2 % (8/3,663) | 30 (24 looping) | serve only |
| ckpt-25 v1 | **v1-actions-only (ablation)**: tool call only, empty `<think>` | `fara15-4b-sft25-fair-f3-s200-229` (2026-09-06) | 0/30 [0, 11.3] | 0 | 0 | 0 | 0 | **19** | 26 | 23 | 19 | 0.0 % (0/1,701) | 11 | A6000, 5.53 h, $6.0 |
| ckpt-25 v2 | **v2-reasoning**: teacher reasoning line, then the tool call | `fara15-4b-sft25v2-fair-f3-s200-229` (2026-09-06) | 0/30 [0, 11.3] | 13 | 12 | 9 | 0 | 13 | 29 | 19 | 13 | 0.0 % (0/2,432) | 17 (16 looping) | H100, 2.28 h, $7.5 |
| ckpt-50 v2 | v2-reasoning | not run (gate: `auth_typed` 0/30) | — | | | | | | | | | | | |

The 2026-09-05 mlx-vlm base run (`fara15-4b-fair-f3-s200-229`, 0/30, login 30/30, chart 0/30) is the go/no-go
number only; the chart uses the vLLM base row.

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
