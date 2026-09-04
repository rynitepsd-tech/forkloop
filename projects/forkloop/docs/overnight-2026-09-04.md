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

