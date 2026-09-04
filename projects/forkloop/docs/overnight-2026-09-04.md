# Overnight ledger — 2026-09-04

Budgets: $20 OpenAI (Luna only), $10 Solari, $0 Anthropic. Launch stop lines: $17 OpenAI / $8.50 Solari.
Every cost line below is the output of `forkloop metrics --run runs/<id> --model gpt-5.6-luna`
(tokens = OpenAI, VM = Solari). Prior-night reference: v7 7/10 (first pass 6/10, median 155),
v8 7/10 (first pass 4/10, median 103).

Setup at 12:xx local (2026-09-04): fresh venv, 197 tests passed offline, `reap --dry-run` = 0 machines.

## Runs

Snapshot baseline at launch (`list_snapshots`, read-only): the four golden-lineage snapshots plus **four leftover
`cp-*` checkpoints** from `runs/luna-v5-f3-bo2-smoke` (2026-09-04 01:27–01:44 UTC, 8.47 GB each, parent = the v5 golden):
`snap_dl653aqfaxvj` (cp-resolve_denial-train-000000-25), `snap_dl659r69cfxr` (…000001-0), `snap_dl65abca5pz1`
(…000001-8), `snap_dl65gnxs09ey` (…000002-2). Handled in step 3.

### luna-v9-fam1-s0-9 — prompt v9, family 1, seeds 0–9 (launched 02:00 local)

Ran 02:00–03:05 local (15 attempts: 10 + 5 retries). `forkloop metrics --run runs/luna-v9-fam1-s0-9 --model gpt-5.6-luna`:

```
episodes                    10 selected of 15 attempts (5 superseded)
success                     60.0% [31.3, 83.2] (n=10)
median steps                83.0
median reset (s)            85.785
cost / success (USD)        0.289
cost / episode (USD)        0.1156
  of which VM / tokens      0.2408 / 1.4931
tokens in / out             5701627 / 293999
reason codes: OK=6, WRONG_SLOT=4
```

- **Verified 6/10 within two attempts; first pass 5/10** (seeds 0, 1, 5, 6, 8; seed 3 on its retry). Verified
  episodes: 66, 70, 70, 74, 92, 116 actions (**median 72**), 6/6 end with `done()`. Gate (first pass ≥ 6/10 and
  verified median < 120): **not met on first pass** → v10 (below).
- Cost: **$1.49 tokens (OpenAI), $0.24 VM (Solari)**. Running totals: OpenAI $1.49 / Solari $0.24.
- Pool: revert mode held for all 15 restores (n = 15, p50 109 s, 5/15 under 30 s, 5/15 over 120 s, max 192 s); no
  `revert_*` events, no fork deaths, nothing reaped; `reap --dry-run` = 0 after the run.
- Failure classes over the 9 failed attempts (measured in `docs/spikes.md`): oracle false negative
  `DIRECT_DB_WRITE` ×2 (seeds 3, 7 attempt 1: all effect checks passed, 40/37 actions, no dashboard visit),
  date drift ×3 (4a1, 4a2, 9a1), crash budget ×3 (2a1, 7a2, 9a2), calendar click cycle ×1 (2a2).
- Two harness findings from the failures, both fixed offline (200 tests green): (1) `collect` never passed
  `--history-k` to the `Env`, so the model saw 8 previous actions, not 16; (2) the history is compact actions
  only, so the prompt's "CURRENT → TARGET" memory line never reached the next turn — `--history-notes` now shows
  the model's own reasoning line next to each previous action. Oracle: a `ui_path` failure now records the
  post-watermark audit rows (`audit_rows_after_watermark`) — the live OpenEMR log format is being captured by
  a scripted replay of seed 7 (`runs/probe-audit-s7`, no tokens).

### probe-audit-s7 — scripted replay of seed 7's 37 actions on a fresh fork (03:11–03:20 local, no tokens)

Reproduced the false negative exactly (all effect checks passed, `DIRECT_DB_WRITE`, 71 scripted actions incl. 1 s
settles) and captured the live audit rows: **OpenEMR 8.3 stores `log.comments` base64-encoded**. The calendar save
is row 508006, `scheduling-update`, `patient_id 0`, comment = base64 of `UPDATE openemr_postcalendar_events SET … WHERE
pc_eid = ? (… '507000' …)`, so the 2026-09-03 plain-text LIKE fallback could never match it; the verified episodes only
passed because opening the dashboard writes rows under the patient's id. Fix: the tripwire decodes comments before
matching (write rows only), tested with a base64 fixture. Cost: one revert restore (134 s) ≈ **$0.01 VM**.
The two v9 false negatives (seeds 3 and 7, attempt 1) cannot be re-scored (VMs gone); they stay 0 in the v9 numbers.
Running totals: OpenAI $1.49 / Solari $0.25.

### luna-v10-fam1-s0-9 — prompt v10 + `--history-notes`, family 1, seeds 0–9 (launched 03:27 local)

Ran 03:18–03:58 local (11 attempts: 10 + 1 retry). `forkloop metrics --run runs/luna-v10-fam1-s0-9 --model gpt-5.6-luna`:

```
episodes                    10 selected of 11 attempts (1 superseded)
success                     100.0% [72.2, 100.0] (n=10)
median steps                48.5
median reset (s)            108.636
cost / success (USD)        0.0885
cost / episode (USD)        0.0805
  of which VM / tokens      0.1076 / 0.7779
tokens in / out             3293417 / 99368
reason codes: OK=10
```

- **Verified 10/10 within two attempts; first pass 9/10** (all but seed 5, a crash-budget loss: 9 crashpad reports,
  9 re-logins, a wrong save; its retry verified in 81 actions). Verified episodes 31, 36, 40, 41, 45, 52, 61, 64, 81,
  102 actions (**median 48.5**), 10/10 end with `done()`. **Gate met: v10 + `--history-notes` is the family-1 prompt
  for the rest of the night.** Against v9 on the same seeds: first pass 5/10 → 9/10, verified median 72 → 48.5,
  $0.29 → $0.089 per verified seed. Zero date drifts (seeds 4 and 9, drift ×3 on v9, verified in 45 and 102);
  seed 7 verified without a dashboard visit and passed `ui_path` (the base64 fix confirmed live).
- Cost: **$0.78 tokens (OpenAI), $0.11 VM (Solari)**. Running totals: OpenAI $2.27 / Solari $0.36.
- Pool: revert mode held; two 503 reverts (`revert_failed_replaced_machine`, 03:32 and 03:53) each replaced one
  machine with a fresh fork (238 s and 243 s to ready); restores n = 11, p50 105 s, 2/11 under 30 s, 3/11 over
  120 s; no fork deaths; `reap --dry-run` = 0 after the run.

## Step 2 — SFT volume, families 1–2, fresh seeds 10–34

### luna-v10-fam1-s10-34 — family 1, seeds 10–34, v10 + notes (launched 03:59 local)

Mid-run note (04:35 local, 11 first-pass verdicts in): first pass 3/11 so far — a very different picture from
seeds 0–9 (9/10). Explained offline with the pure generator: the requested window (morning/afternoon) is sampled
independently of the appointment's current time, and **on seeds 0–9 the current time already lay inside the target
window for 8/10 seeds** (only seeds 3 and 5 needed a time change), while **on seeds 10–34 it does for only 11/25**.
Of the fresh seeds that needed a time change so far (10, 11, 13, 14, 15, 17, 18, 19), one verified (seed 10) and
five failed with the right date and the old time (`event_time_window`, seeds 11, 15, 17, 18, 19); two (13, 14)
clicked an empty calendar slot and saved a *new* appointment through the add-event form (duration 0 → Save refused
at y=680 until fixed, then `no_collateral` + `single_event` fail). So v10's "change the time only if the requested
window needs it" is the one rule left that Luna does not follow; the prompt stays frozen at v10 for this step as
planned, and this is next action #1.

Ran 03:59–06:12 local (38 attempts: 25 + 13 retries). `forkloop metrics --run runs/luna-v10-fam1-s10-34 --model gpt-5.6-luna`:

```
episodes                    25 selected of 38 attempts (13 superseded)
success                     60.0% [40.7, 76.6] (n=25)
median steps                55
median reset (s)            142.535
cost / success (USD)        0.1989
cost / episode (USD)        0.0785
  of which VM / tokens      0.3827 / 2.6008
tokens in / out             10978010 / 337661
reason codes: OK=15, WRONG_SLOT=10
```

- **Honest fresh-seed family-1 estimate: 15/25 verified = 60.0 % [40.7, 76.6]; first pass 12/25 = 48 %**
  (retries recovered 3/13: seeds 14, 21, 29). Verified episodes 35–141 actions (**median 55**), 15/15 end with `done()`.
- Cost: **$2.60 tokens (OpenAI), $0.38 VM (Solari)**; $0.199 per verified seed. Running totals: OpenAI $4.87 / Solari $0.74.
- Failure classes over the 23 failed attempts (every one read): **window 13** (right date, the appointment's old time
  kept when the requested half-day differs — seeds 11, 15, 17, 18, 19, 25, 27, 29 first pass, and 11, 17, 18, 19, 25
  retries), **add-event 7** (the "click the TIME" rule from v10 lands on the calendar's hour labels, which open the
  *Add New Event* form; a second appointment is saved after fixing the form's duration=0 — seeds 13, 14, 21, 23 first
  pass, 15, 23, 27 retries), **date arithmetic 3** (seed 34 ×2: "next Tuesday after Monday Sep 14" → Sep 22; seed 13
  retry: a week late). No crash-budget losses this run (crashpad median 2, max 9; the 141-action verified seed 26 had 9).
- Pool: revert mode held across 38 restores (p50 112 s, 8/38 under 30 s, 15/38 over 120 s, max 242 s); **six 503
  reverts** (`revert_failed_replaced_machine`) each replaced one machine (203–242 s to ready); no fork deaths;
  `reap --dry-run` = 0 afterwards.

### luna-v10-fam2-s10-34 — family 2 (both variants by seed), seeds 10–34, v10 + notes (launched 06:14 local)

Mid-run note (07:55 local): 23/24 first-pass verdicts in, one failure — **seed 31 (portal-only variant) started with
no Chrome on screen** (first screenshot: a file-manager window on the bare desktop). `chrome.log` shows the
`before_episode` relaunch printed "Opening in existing browser session" at 17:46:30 VM time: the new Chrome attached
to the one `pkill` was still killing and died with it. Luna launched Chrome from the dock (default profile, no
`--disable-gpu`, no portal session) and spent 151 actions guessing portal credentials (14 logins tried). Harness fix,
offline-tested: the relaunch now waits for the old processes to exit (≤ 10 s), removes the profile's Singleton*
files, launches, verifies a `forkloop-chrome` Chrome is running (≤ 12 s), retries once, and raises (failing the
reset stage, so `--reset-retries` re-queues the seed) instead of starting a doomed episode. Applies from the next
run; the current process has the old code.

Ran 06:14–08:06 local (26 attempts: 25 + 1 retry). `forkloop metrics --run runs/luna-v10-fam2-s10-34 --model gpt-5.6-luna`:

```
episodes                    25 selected of 26 attempts (1 superseded)
success                     100.0% [86.7, 100.0] (n=25)
median steps                48
median reset (s)            90.335
cost / success (USD)        0.0695
cost / episode (USD)        0.0668
  of which VM / tokens      0.228 / 1.5091
tokens in / out             6690466 / 142487
reason codes: OK=25
```

- **Honest fresh-seed family-2 estimate: 25/25 verified = 100 % [86.7, 100]; first pass 24/25 = 96 %.** Both variants
  by seed: 9 portal-only (8–12 actions) and 16 two-system (42–142 actions, OpenEMR insurance edit + portal
  resubmission); verified median 48, 25/25 end with `done()`, all 8–9 checks passed on every verified episode.
- Cost: **$1.51 tokens (OpenAI), $0.23 VM (Solari)**; $0.07 per verified seed. Running totals: OpenAI $6.38 / Solari $0.97.
- The one failure (seed 31, attempt 1) was the Chrome relaunch race above (no browser at episode start, 151 actions
  of credential guessing); its retry verified in 10 actions. No policy failure in the family.
- Pool: revert mode held across 26 restores (p50 69 s, 9/26 under 30 s, 7/26 over 120 s, max 199 s); two 503 reverts
  replaced in place; no fork deaths; `reap --dry-run` = 0 afterwards. Crashpad median 0 per attempt (max 7).
- SFT export: `runs/exports/sft_luna-v10-fam2-s10-34.jsonl`, **1,301 records from 25 episodes**.

## Step 3 — does search recover the hard seeds?

Seeds that failed both attempts in steps 1–2 (v10 runs): family 1 **11, 13, 15, 17, 18, 19, 23, 25, 27, 34** (10 seeds:
8 window, 1 add-event ×2 [23], 1 date arithmetic [34]); family 2 none. Run once with `--best-of 2 --search-mode fork
--concurrency 1` under `luna-v10-bo2-hard`.

## Step 4 — SFT export (`train/make_sft.py`, verified attempts only, shortest per seed)

| file | runs | episodes | records |
| --- | --- | --- | --- |
| `runs/exports/sft_luna-v9-fam1-s0-9.jsonl` | luna-v9-fam1-s0-9 | 6 | 488 |
| `runs/exports/sft_luna-v10-fam1-s0-9.jsonl` | luna-v10-fam1-s0-9 | 10 | 553 |
| `runs/exports/sft_luna-v10-fam1-s10-34.jsonl` | luna-v10-fam1-s10-34 | 15 | 946 |
| `runs/exports/sft_luna-v10-fam2-s10-34.jsonl` | luna-v10-fam2-s10-34 | 25 | 1,301 |
| `runs/exports/sft_luna-v10-bo2-hard.jsonl` | luna-v10-bo2-hard (search winners) | 2 | 90 |
| **`runs/exports/sft_luna-v10-fam12.jsonl`** (combined families 1–2) | the three v10 runs + the search run | **52** (27 + 25) | **2,890** (family 1: 1,589; family 2: 1,301) |

The combined file takes the v10 runs only: the v9 episodes cover seeds 0–9 that v10 also verified with shorter,
`done()`-terminated trajectories, so they are exported separately rather than duplicated. Rebuilt after step 3 with the two seeds the search recovered (17 and 34).

## Solari changelog (Discord announcement the user relayed at ~07:30 local) mapped to our open items

| changelog item | our item | status / next |
| --- | --- | --- |
| `disk_gb` up to 20 GB on fresh sandboxes/desktops (invalid values now fail loudly) | support email item 4 (4 GB disk, "table 'log' is full", lean golden) | needs a golden **v6 rebuild** with `disk_gb` set (fresh machines only; forks of v5 stay at 4 GB); not re-measured tonight |
| Recording URLs in desktop API responses | item 3 (`recordingUrl` never populated) | re-run spike 3 (`docs/solari-repro.md`) on one throwaway desktop; not re-measured tonight (both slots busy) |
| Revert starts a fresh machine and swaps it in when ready | item 1 (revert semantics) | matches tonight's ten 503 "could not restore this snapshot in time" reverts, all replaced in place by the pool; machine id stayed constant per worker all night, so whether the swap changes ids is **not yet measured** |
| Sessions stuck as `running` fixed; idempotency keys; stricter create validation | the 429 "Too many concurrent sessions" with zero sessions listed; the orphan reaper | idempotency keys would let the pool retry creates safely; not adopted yet |
| Ancestor snapshots deletable; snapshot storage billed from 2026-10-01 (10 GB free, $0.05/GB/month) | item 5 | account holds 8 snapshots ≈ 67 GB (4 golden lineage + 4 `cp-*` leftovers ≈ 34 GB) → ≈ $2.85/month from October unless cleaned; step 3 reports whether checkpoint deletes work now |
| Chromium 151 pool, CDP isolation, proxies, cert errors after resume | — | not used by forkloop (we drive the desktop's own Chrome) |

Not addressed by the changelog: bimodal 70–160 s restores, Chrome tab crashes / RCU stalls in the guest, `cpu`/`mem_mb`
ignored on forks.

Mid-run note (10:10 local): **the controller Mac slept from about 08:42 to 10:03** (`pmset -g log`: Deep Idle sleeps,
wake at 10:03:11; the Mac is on battery, 100 %). The run stalled for the duration; on wake the pending revert POST
died with a bare `ConnectionError` and the pool logged `revert_unsupported_fell_back_to_fork`, switching the rest of the
search run to fork mode (functionally fine, but a misclassification: not a refusal). Fixed and committed (d07b0b4):
only a real refusal (409 / "Not revertable" / paused) flips the mode; anything else replaces the machine and keeps
revert mode; `--no-fallback` still raises. `caffeinate -i` is now bound to the collect process to block idle sleep
(it cannot block a lid-close sleep). Search seeds 11, 13, 15 not recovered (both candidates failed the same check);
seed 17 recovered through a branch at action 8 (search box vs "All Users"); checkpoint deletes 4/4 clean so far.

### luna-v10-bo2-hard — best-of-2 fork search on the 10 double-failed family-1 seeds (08:02–10:40 local, incl. an ~80 min Mac sleep)

```
episodes                    10
success                     20.0% [5.7, 51.0] (n=10)
median steps                65.0
cost / success (USD)        0.529
cost / episode (USD)        0.1058
  of which VM / tokens      0.2043 / 0.8537
tokens in / out             3610123 / 109715
reason codes: OK=2, WRONG_SLOT=8
```

- **Recovered 2/10** (seeds 17 and 34) = 20 % [5.7, 51.0]; **$0.53 per recovered seed** ($1.06 for the run:
  $0.85 tokens, $0.20 VM). Running totals: OpenAI $7.23 / Solari $1.17.
- Search mechanics: 16 branch points over 10 episodes (1–3 each, at actions 0–16), 20 branch forks, 2 wins. Seed 17 won
  at action 8 (search box vs "All Users": 1.0 vs 0.0); seed 34 at action 16. The 8 losses branched but **both
  candidates failed the same check** every time (window ×6, date ×2): a random branch at a click cannot fix a rule the
  policy never applies, so search does not recover the window class. Seed 23 moved from the add-event class to the
  window class (the branch steered it past the hour labels).
- `search.snapshot_delete_errors`: **empty in all 10 verdicts; 10/16 checkpoints deleted.** The other 6 leaked without an
  error: they are the branch points whose two candidates deduplicated to one (the checkpoint is taken before
  `_dedupe`, and that path never reached the delete) — fixed below. Leftover `cp-*` ids at the end of the run:
  `snap_dl6qbtykeeat` (…000011-6), `snap_dl6r10xbmbvb` (…000017-7), `snap_dl6tc770jljz` (…000023-3),
  `snap_dl6tttw4b94m` (…000027-3), `snap_dl6u24ijedvw` (…000034-9), `snap_dl6u2hdx6sbq` (…000034-11), plus the four
  from 2026-09-04 01:xx. **All ten deleted with `backend.delete_snapshot` at 10:45 local (10/10 succeeded)**; the
  account now holds only the four golden-lineage snapshots (`snap_dl4driq97904`, `snap_dl4cngznmvr7`,
  `snap_dl4e90g095y2`, `snap_dl4e05ciyt1p`).
- Pool: after the Mac-sleep `ConnectionError` the run finished in fork mode (restores n = 30, p50 33 s — fork restores
  of the small branch forks are fast); no fork deaths; `reap --dry-run` = 0 afterwards.

## Step 5 — family 3, seeds 100–139, prompt v5 (`runs/luna-v5-f3-s100-139`, 11:05–13:40 local, 45 attempts)

`forkloop metrics --run runs/luna-v5-f3-s100-139 --model gpt-5.6-luna`:

```
episodes                    40 selected of 45 attempts (5 superseded)
success                     95.0% [83.5, 98.6] (n=40)
median steps                59.0
median reset (s)            91.931
cost / success (USD)        0.081
cost / episode (USD)        0.0684
  of which VM / tokens      0.3923 / 2.6873
tokens in / out             11334871 / 350277
reason codes: NOT_DONE=2, OK=38
```

- **38/40 verified = 95.0 % [83.5, 98.6] within two attempts; first pass 35/40** (retries recovered 132, 136, 137;
  119 and 130 failed twice). Verified median 59 actions (43–128), all `done()`. Consistent with seeds 20–99 (96.2 %).
  Configuration as measured before (v5 prompt, `--history-k 16 --prev-shot`, 120 actions / 900 s, no
  `--history-notes`), except that the env now really keeps 16 actions of history (it kept 8 before the fix).
- Cost: **$2.69 tokens (OpenAI), $0.39 VM (Solari)**; $0.081 per verified seed. Running totals: OpenAI $9.92 / Solari $1.56.
- SFT export: `runs/exports/sft_luna-v5-f3-s100-139.jsonl`, **2,377 records from 38 episodes**.
- The 7 failed attempts: transcription ×2 (119a1: `AUTH-73M61656` typed with an extra digit; 132a1: `Q` read as `9`),
  decoy number ×1 (137a1 submitted a different letter's code), decoy/budget ×2 (130 ×2: the 120-action budget spent
  cycling through decoy letters), crash budget ×1 (119a2: 16 crash reports, 13 re-logins), attachment/budget ×1
  (136a1: budget ran out while attaching the letter, no crashes). All are classes the oracle is built to reject.
- Pool: revert mode held across 45 restores (p50 82 s, 9/45 under 30 s, 11/45 over 120 s, max 418 s); three 503 reverts
  replaced in place; one replacement create timed out at 240 s, the retry hit the 429 cap and the reaper killed the
  orphan (designed path; the other worker's episode was untouched); no fork deaths; `reap --dry-run` = 0 afterwards.

## Wrap-up

### Every run of the session

| run id | prompt | family / seeds | verified (within 2 attempts) | first pass | median actions (verified) | $ tokens | $ VM |
| --- | --- | --- | --- | --- | --- | --- | --- |
| luna-v9-fam1-s0-9 | v9 | 1 / 0–9 | 6/10 = 60 % [31.3, 83.2] | 5/10 | 72 | 1.49 | 0.24 |
| probe-audit-s7 | (scripted replay) | 1 / 7 | 0/1 (oracle diagnostic, reproduced the false negative) | — | — | 0.00 | 0.01 |
| luna-v10-fam1-s0-9 | v10 + notes | 1 / 0–9 | 10/10 = 100 % [72.2, 100] | 9/10 | 48.5 | 0.78 | 0.11 |
| luna-v10-fam1-s10-34 | v10 + notes | 1 / 10–34 | 15/25 = 60 % [40.7, 76.6] | 12/25 | 55 | 2.60 | 0.38 |
| luna-v10-fam2-s10-34 | v10 + notes | 2 / 10–34 | 25/25 = 100 % [86.7, 100] | 24/25 | 48 | 1.51 | 0.23 |
| luna-v10-bo2-hard | v10 + notes, best-of-2 fork | 1 / 11,13,15,17,18,19,23,25,27,34 | 2/10 = 20 % [5.7, 51.0] (one attempt) | 2/10 | 45 | 0.85 | 0.20 |
| luna-v5-f3-s100-139 | v5 | 3 / 100–139 | 38/40 = 95 % [83.5, 98.6] | 35/40 | 59 | 2.69 | 0.39 |
| **total** | | 121 seed-runs, 146 attempts | **96 verified** | | | **$9.92** | **$1.56** |

### Spend against the budgets

| budget | limit | stop line | spent | left |
| --- | --- | --- | --- | --- |
| OpenAI (Luna only) | $20.00 | $17.00 | **$9.92** (metrics upper bound, cache reads not priced) | $10.08 |
| Solari | $10.00 | $8.50 | **$1.56** (VM hours at $0.134/h; snapshot storage unbilled until 2026-10-01) | $8.44 |
| Anthropic | $0 | — | $0.00 | — |

### Every failure class, with counts (attempts, all read)

| class | count | runs | one-line signature |
| --- | --- | --- | --- |
| window (right date, old time kept when the half-day differs) | 18 (+1 mixed with date) | fam1 s10–34 (13), bo2 (5, +1 date+window) | `event_time_window` only; v10's rule never acted on |
| add-event (hour label → *Add New Event* → second appointment) | 7 | fam1 s10–34 | `single_event` 2 + `no_collateral` add; Save at y≈680 refused until duration set |
| date drift (CURRENT re-derived from the form's date field) | 3 | v9 | one-week drift or an unsaved chain; fixed by `--history-notes` (0 on v10) |
| date arithmetic (wrong next-weekday, clean execution) | 5 (+1 mixed) | fam1 s10–34 (3), bo2 (2) | `event_date` a week off |
| crash budget (≥ 4 crash reports, re-login loops, nothing saved) | 5 | v9 (3), v10 s0–9 (1), f3 (1: 119a2) | 4–16 crashpad reports, 5–18 re-logins |
| calendar click cycle (entry → chart → calendar …) | 1 | v9 | 124 clicks, never edited |
| oracle false negative (`DIRECT_DB_WRITE` on a perfect episode) | 3 | v9 (2), probe (1) | fixed: base64 audit comments |
| Chrome relaunch race (no browser at episode start) | 1 | fam2 | fixed: relaunch waits/verifies/raises |
| transcription (extra digit, look-alike) | 2 | f3 | `appeal_auth_number` one character off |
| decoy number / decoy budget / attachment budget | 1 / 2 / 1 | f3 (137a1 / 130 ×2 / 136a1) | wrong letter's code; 120 actions among decoys; budget out while attaching |
| **total failed attempts** | **50** of 146 | | |

### Leftover snapshots

None beyond the golden lineage. The account holds exactly four snapshots: `snap_dl4e90g095y2` (v5 desktop golden, live),
`snap_dl4e05ciyt1p` (v4, ancestor), `snap_dl4driq97904` (v1, ancestor), `snap_dl4cngznmvr7` (sandbox golden). All ten
`cp-*` checkpoints (four from the 2026-09-04 01:xx smoke, six from `luna-v10-bo2-hard`) were deleted at 10:45 local
with `backend.delete_snapshot`, 10/10 succeeded. `search.snapshot_delete_errors` was empty in every verdict; the six
that leaked did so through the no-branch path, fixed in e9d0e23.

### Three most useful next actions

1. **Prompt v11 for family 1, then fresh seeds 35–59 (≈ $3).** Two rules fix 20 of the 23 v10 losses on fresh seeds:
   put "CURRENT time → TARGET window" in the note and change the time whenever the current time is outside the
   requested half-day; click the appointment's own text (never the hour labels) and Cancel any form titled
   "Add New Event". Neither retries (3/13) nor best-of-2 (2/10) recover these; only the prompt can.
2. **Golden v6 at `disk_gb` 20 with `--disable-gpu` baked in** (`forkloop build-world`, ~10 min, then re-point
   `FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1`), re-measuring `recordingUrl` and the post-revert machine id on the
   way; then delete the v4/v1 ancestors before 2026-10-01 storage billing (≈ 25 GB).
3. **Student bake-off on a rented GPU** with the 5,267 new records (families 1–2: 2,890; family 3: 2,377) plus the
   6,354 family-3 records from 2026-09-03: `train/` is wired, the student has never run.

Session end: full test suite green (see the commit), `reap --dry-run` = 0 machines, 26 local commits unpushed.
