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

