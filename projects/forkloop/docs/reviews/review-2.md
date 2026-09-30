# Independent review 2: exp1 collection phase, today's code, collection numbers, claims

**Date:** 2026-09-30, ~11:45 UTC. **Reviewer:** independent (Claude), read-only.

**Scope and method.**
- **Protocol:** `docs/protocol-learning-experiment.md` (registered `fe41105`, notes from 05:20 Sep 29 to 08:45 Sep 30) and `docs/execution-ledger.md`.
- **Code at HEAD `3e9622f`:**
  - `forkloop/correction/{repair,runner,budget,cli,store,dataset,evidence,analysis,restore,record,checkpoint,diagnose}.py`
  - `forkloop/policies/student.py` and `forkloop/env.py`
  - `scripts/exp1/*` and `train/train_lora.py` (training args)
  - the three named test files
- **Data:** the three files in `runs/review2/`, and the claim documents named in the brief.
- **Tests:** offline suite (`venv/bin/python -m pytest -q -p no:warnings`) collected 665 tests: 664 passed, 1 skipped, exit 0.
- **Local stores:** I read copies of the local Kanboard and Solari-flagship stores (placed in the session scratch directory, outside the repo). I reproduced one bug on a synthetic store in the same scratch directory.
- **Not done:** no remote host, no exp1 store, no final-test outcome read.
- **Refused commands:** the session's permission classifier refused two local commands:
  - running `forkloop demo-loop` into the scratch directory;
  - a grep of `forkloop/correction/project.py` and the task generators for how `max_seconds` reaches a repair branch.

  Points that depend on them are marked *unverified*.
- **Changes:** the git tree is unchanged, and this file is the only file written in the repo.

## Verdict

The collection phase produced internally consistent datasets. The evaluation code path was not changed after the first cells ran, apart from the student policy's retry behaviour. Two things must be fixed in the write-up before any result is presented.

1. **Matched cost.** The registered comparison at matched collection cost has become a comparison at matched cost of *clean* work. The 15:28 deviation came after the first datasets showed A1 78 verified paths against A2 9, and it rested on a diagnosis that was later withdrawn. It voids and replaces any repair with an unscored branch, and it charges all void work to no arm. That void work includes failed replay restores, which are a cost of the checkpoint method itself (197 vs 25 restore-failed branches). Voiding also depends on the outcome.
2. **Repair-mode comparison.** The per-failure checkpoint-vs-restart comparison (7 vs 1, p = 0.07) is not like for like:
   - it sets up to 6 teacher branches against 3;
   - it counts successes from the step-0 fallback as checkpoint successes (at least one of the 7);
   - its protocol note says the window was settled, but the report shows 9 of 50 failures not settled.

Separately:
- At matched cost, A2 has 21 verified paths from 10 tasks and A3 has 24 from 9, against 78 from 78 for A1. The trained-arm contrasts therefore rest on one very small dataset per arm, and their intervals exclude the variance of data collection.
- `ctrl+-` failures are triggered by the policy, not by chance.
- A0/S_W have fewer attempts per cell because operator stops are treated inconsistently. This can be fixed before outcomes are read.
- `package_release.py` crashes.
- "Deterministic replay" contradicts a replay-restore pass rate of 53%.

None of this needs new collection; it needs the disclosures and sensitivity numbers listed below.

## Blocker

### B1. "Matched collection cost" no longer measures the checkpoint method's cost

**Where:** protocol note 2026-09-29 15:28, items 1–2; `budget.py:15-21, 101-142, 165-178`; `repair.py:231-251`.

**What is wrong.** At registration, `budget.py` (docstring at `fe41105`) defined an A2 unit as:

> "one student attempt … plus, if it failed, one checkpoint repair (all its branches, restores and replays, failed ones too)"

The 15:28 note changed this in two ways:
- A repair with any unscored branch is voided and replaced (up to 2 replacements).
- "The cost of unscored work (unscored attempts, void repairs) is charged to no arm."

"Unscored" includes `restore_failed` (`repair.py:231`), which covers replay restores that fail the fidelity check.

**Evidence.**
- **Restore failure is specific to the method, not to the provider:**
  - 197 of 1,715 checkpoint-mode branches ended `restore_failed`, against 25 of 984 full-restart branches (`branches_all`).
  - Replay restores passed in 53% of 1,608 tries (p90 403 s).
  - None of this cost is charged to A2 any more.
- **The change was decided after collection yields were seen,** on a diagnosis that was withdrawn at 20:10. The 15:28 note quotes the first datasets ("A1 78 verified paths, A2 9, A3 10"). After the change, A2/A3 reached 21/24 paths.
- **The promised disclosure is missing.** The note says "its numbers are reported", but no local artifact reports them. `--count-unscored-cost` would not produce them either: it charges only the latest repair per failure (`budget.py:108, 122-123`). The unreported work is:
  - 96 void tries in checkpoint mode and 60 in full-restart mode;
  - about 239 and 188 repairs marked as not a try;
  - 382 and 328 repairs in total for 121 failures.
- **Collection conditions differ between arms:**
  - World time makes up 76% of A2's unit cost and 74% of A3's, against 50% for A1.
  - Counted repairs ran at different times: 33 of 47 counted checkpoint repairs ran after 20:18, against 43 of 80 counted full-restart repairs (20:25 note: 14 and 37 clean repairs kept). That means different concurrency and different retry code.

**Fix or required disclosure.**
- Call the accounting "cost of counted (clean) work" wherever it appears.
- Add an all-work cost table per arm, split by cause: provider outage, transport drop, restore failure, xdotool key-name bug, operator stop.
- Show which budget windows and verified paths each accounting would have selected. At least one sensitivity row should charge restore failures to A2.
- Fix `--count-unscored-cost` so it sums every repair of a failure.

### B2. The per-failure checkpoint-vs-restart comparison is not like for like, and its protocol note is inaccurate

**Where:** `scripts/exp1/report.py:60-91`; protocol notes 01:35 and 08:45; `diagnose.py:162-166`.

**What is wrong and the evidence.**

1. **Checkpoint mode gets up to twice as many teacher branches.** It always appends step 0 after one evidence point (`max_restart_points: 2`; `diagnose.py:162-166`). A failure therefore gets up to 6 teacher branches in checkpoint mode against 3 in full-restart mode, so a "checkpoint" success can be a full restart with extra tries.
   - Example: compose 010073 is counted `ckpt+/restart-` (A2 unit `rep-10c2bcf60aac` with 2 paths; A3 unit `rep-d8b2e82cf77a` with 0).
   - Its 2 paths are the 165 `restart_demo` records in A2-b025. The only other repair in that dataset, denial 010036, is the 45 correction-suffix records shown on `docs/evidence/exp1/index.html`.
   - It used 482 teacher steps (about 6 branches) against 208 for the full restart.
   - Reclassified as a checkpoint failure, the discordant count becomes 6 vs 1, and the sign test p rises from 0.070 to 0.125.
2. **The void rule favours checkpoint mode** (see M1). All 4 exhausted failures are in checkpoint mode.
3. **The 08:45 note is contradicted by the report.** The note says:

   > "(fixed before any outcome; all settled in both modes or exhausted)"

   The report shows 9 of the 50 window failures as "not both scored", yet only 4 failures are exhausted anywhere (checkpoint: reschedule 2, insurance 2; full restart: 0). So at least 5 window failures are unsettled in one mode. The window was also not fixed before any outcome:
   - The 50-failure window was set at 20:25, after the first batch's 51 clean repairs were known.
   - The restriction to it was committed in `e03f3a2` at 08:33 UTC, 5 minutes after budget-v2 settled the window at 08:28.

**Fix.**
- Report the comparison as exploratory only, and never cite it in the README, site or video.
- Split checkpoint successes by restart point, and add a table that counts evidence-point branches only.
- Report per-branch verified/scored rates for each mode.
- List the 9 unsettled failures with the mode and cause for each.
- Correct the 08:45 note with a new dated note.
- As a like-for-like view, add the 14 failures inside A2's own budget window: checkpoint verified 10, restart verified 7, discordant 3 vs 0, of which 1 came from the step-0 fallback.

## Major

### M1. Voiding a whole repair for any unscored branch selects on outcome

**Where:** protocol note 15:28, item 1; `repair.py:234-251`; `runner.py:191-203`.

**What is wrong.** The chance that a repair is voided grows with its number of branches and its branch length:
- Failing branches run to the 120-action allowance, while verified ones end early. On the exp1 evidence page, the verified branches took 22–23 steps and the failed one 102.
- Failing checkpoint repairs run 6 branches instead of 3.

So a repair that fails is more likely to be voided than one that verifies (P(void | unrepaired) > P(void | verified)), and "the first clean repair counts" over-represents verified repairs. The effect is stronger in checkpoint mode.

**Illustration** (not a measurement): take a void chance of 1 in 8 for a long branch (ledger 01:24: "≈ 1 in 8 branches hits `ctrl+-`") and 1 in 30 for a short one.

| Mode | P(clean \| verified) | P(clean \| unrepaired) | Counted rate when the true rate is 0.40 |
| --- | --- | --- | --- |
| Checkpoint | ≈0.82 | ≈0.45 | ≈0.55 |
| Full restart | ≈0.82 | ≈0.67 | ≈0.45 |

**Evidence consistent with this** (family mix confounds the branch-level figures):
- Rescheduling, which the teacher never solves, had 41 counted void tries in checkpoint mode against 12 in full-restart mode.
- The only 4 exhausted failures are all in checkpoint mode.
- Share of scored branches that verified:

| Mode | Counted repairs | Other repairs |
| --- | --- | --- |
| Checkpoint | 65/201 = 32% | 24/315 = 8% |
| Full restart | 50/240 = 21% | 25/81 = 31% |

- Restore failures also void about 17% of replay-restored branches, even after 3 tries. A failure whose chosen checkpoint does not replay is eventually exhausted and leaves A2's population.

**Fix.** Report these sensitivity analyses next to the counted numbers. All three use collection data only.
- (a) The first repair started for each failure, excluding only provider-outage repairs, with unscored branches scored as failures.
- (b) The branch-level verified rate per mode and per restart point.
- (c) The void rate per mode, broken down by branch outcome and branch length.

### M2. `ctrl+-` and other xdotool key-name failures are treated as random infrastructure

**Where:** protocol note 11:12; `env.py:177-189` and `:397-401` (a `BackendError` counts as infrastructure).

**What is wrong.** The 11:12 note says:

> "any episode in which a policy presses it has a backend failure and is unscored under the infrastructure rule"

These failures are not random:
- The failure is caused by the content of a valid action the policy chose. The project's own rule (`CLAUDE.md`) classifies a content rejection as `apply failed:`, not as infrastructure.
- **Collection:** about 1 branch in 8 hit it (ledger 01:24: "5 xdotool key names incl. 4 `ctrl+-`"). The notes cite 7, 26 and 11 branches voided this way, so it voids the repairs of struggling branches (M1).
- **Evaluation:** decoding is greedy (`configs/exp1.yaml`, `temperature: 0.0`). A model that zooms out in a cell will probably do so again in its replacements, and after 3 attempts the cell is excluded pairwise. Exclusion then depends on the model's behaviour, and probably on the hardest cells.
- The rate can differ by arm, because no training episode contains the key: every such episode was voided.

**Fix.**
- Report, per arm and per shard, the cells and attempts with a key-name failure.
- Add a sensitivity analysis that scores those cells as failures for the model that pressed the key.
- Do not call them infrastructure in the report.

### M3. A0/S_W: fewer attempts, a different server, and an inconsistent operator-stop rule

**Where:** protocol notes 09:00, 10:31 and 00:25; `mark_transport_voids.py:29-30, 47-49`; `phase5_when_ready.sh:13-19`.

**What is wrong.** The same event, an operator stop, is treated three ways. The strict treatment falls on the baselines' evaluation cells:
- **A0/S_W evaluation cells.** The 10:31 note says:

  > "the operator interruptions count as unscored attempts: about 56 of main's A0/`S_W` cells have used 2 of their 3 attempts"
- **Repairs stopped at 00:08.** The 00:25 note says repairs "interrupted by the stop" are "not replacement tries". This is the lenient treatment, and it applied to collection for A2/A3.
- **Window replacements killed at 10:17** (`STOP_REPAIRS`). These count as tries.

The A0/S_W aux shard also differs:
- It ran 08:52–10:05 on the collection-phase server (base model plus `sw`, TP2×DP4, 60 concurrent).
- The aux trained arms run in phase 5 on a server with 10 LoRA adapters.

**Fix.** This can be done now, while outcomes are unread.
- Add a dated note: operator stops are never tries, for cells and repairs alike.
- For main A0/S_W cells that end unscored after phase 5, replace the attempts the operator stopped.
- Report per arm: attempts used per cell, results per shard, and a main-shard-only sensitivity for A2−A0 and S_W−A0.
- Report when and on which server each counted A0/S_W cell ran, including any main cells scored during the 09:00–09:08 or 10:24–10:31 runs.

### M4. Trained-arm contrasts rest on one very small dataset per arm

**Where:** `forkloop/correction/analysis.py:87-115`; `budget-v2-report.json`; the protocol's Analysis section.

**Data per arm at B:**

| Arm | Verified paths | Tasks | Records | Paths by family | Notes |
| --- | --- | --- | --- | --- | --- |
| A1 (`-b100`) | 78 | 78 | 4,167 | — | |
| A2 (`-b100`) | 21 | 10 | 1,014 | compose 9, denial 5, insurance 7 | 244 records (24%) are step-0 `restart_demo`; A2-b025 is 79% `restart_demo` |
| A3 (`-b100`) | 24 | 9 | 1,197 | compose 6, denial 11, insurance 7 | |

- No arm, and not the warm-start set W either, contains a verified rescheduling path.
- With k = 3, one task can contribute up to 3 near-duplicate paths.

**Training exposure.** Each run takes 100 steps × 8 = 800 examples sampled from W ∪ arm data, which is 0.12–0.23 epochs. The expected share of examples from the arm's own data:

| Arm | Arm-specific share |
| --- | --- |
| A1 | ≈63% |
| A2 | ≈29% |
| A3 | ≈33% |

**What is wrong.**
- `paired` resamples tasks and training seeds, but there is only one collected dataset per arm. An interval for A2−A3 or A2−A1 that excludes 0 is therefore conditional on which ~10 tasks happened to be repaired, and on each arm's family mix.
- A2−A0 (the registered ≥ 0.20 criterion) includes the effect of W and of 100 training steps.
- "A2 reaching A1's success at ≤ half its collection cost" cannot be tested: no B/4 or B/2 model was trained (`phase4_train_arms.sh` trains `-b100` only).

**Fix.**
- State all of the above.
- Show S_W−A0 next to A2−A0.
- Label the trained-arm intervals "conditional on the collected datasets".
- Report results per family.
- Say that the half-cost criterion was not tested.

### M5. Checkpoint branches get less wall time than full-restart branches

**Where:** `restore.py:135`; `checkpoint.py:256`.

**What is wrong.** `restore.py:135` sets:

```python
ep.started_at = time.monotonic() - float(ckpt["elapsed_s"])
```

So a branch inherits the student's elapsed time. That includes about 26 s for capturing that checkpoint, because `elapsed_s` is taken after the digest and oracle run (`checkpoint.py:256`; review-1 m3, still unfixed).

The numbers:
- Round-1 student episodes used 2,900–3,050 s for 120 steps (09:08 note), about 25 s per step.
- Repair steps took 13–15 s (ledger 11:05), so 120 actions need about 1,600–1,800 s.
- From a checkpoint beyond step ~70, the wall limit therefore binds before the action allowance. Full-restart branches start at 0.

This contradicts two statements in the protocol:
- "each branch may take up to 120 actions after its restore point (the same allowance as a demonstration …)"
- "3,600 s wall (never the binding limit by design)"

The bias works against A2. Which `max_seconds` applies to a branch (the project's 3,600 s or the task's own) is *unverified*.

**Fix.**
- Count branches ending on `max_seconds` per mode and restart depth.
- Record the wall budget each branch actually had.
- Disclose both.

### M6. `package_release.py` crashes and points at the void adapters

**Where:** `scripts/exp1/package_release.py:4-5, 86`.

**What is wrong.**
- **Crash.** Line 86 reads `a["attempt_no"]`, but there is no such column (`store.py:31-36`). The attempt number is stored in `info` (`record.py:86`).
  - Reproduced on a synthetic store: `KeyError 'attempt_no'`.
  - The crash comes after all datasets and adapters are tarred and the stores are copied, so there is no `cells.csv`, `release.json` or `SHA256SUMS`.
- **Wrong adapters.** The docstring passes `--adapters ~/programs/exp1/adapters`, which holds the void v1 runs. Because `--adapters` takes a single directory, S_W (`adapters/sw-seed0`) and `adapters-v2` cannot both be packaged.
- **Wrong store path.** The docstring's aux store path (`~/programs/exp1/aux-store/…`) differs from the aux store used elsewhere.

**Fix.**
- Read `a["info"].get("attempt_no")`.
- Make `--adapters` repeatable.
- Label `datasets/budget` and the v1 adapters void in `release.json`.
- Add a test.

### M7. A3-s3 provenance anomaly, and seeds missing from the report

**Where:** `scripts/exp1/train_on_dev.sh`; `report.py:106`; `train/train_lora.py:934`.

**What is wrong.**
- **A3-s3 ran elsewhere.** It is the only run on the 1×H100 dev box, with its own software stack and a copy of the base weights whose hash was not checked.
- **Its first loss is an outlier.** Its first logged loss is 1.049, against 1.497–1.716 for the other eight runs. A3-s1 and A3-s2, on the same data, logged 1.529 and 1.646.
- **Seeds are not shown.** `report.py:106` reads `"seed": args.get("seed")`, but `train_lora.py:934` writes the seed under `optim.seed`. Every run in `exp1-collection.json` therefore shows `"seed": null`, and the report does not show that seeds 1–3 were used.

**Fix.**
- Before analysis, compare A3-s3's `training_args.json` with A3-s1/s2: `versions`, `model_revision`, `system_prompt_sha256`, `chat_template_kwargs`, `mixture`, `argv`, and the record ids of the first batch.
- If anything differs, report A3 with and without s3.
- Read the seed from `optim.seed`.

### M8. "Deterministic replay" is contradicted by exp1's own restore data

**Where:** `README.draft.md:51`; `docs/final-report-20260929.md:12, 43`; site `runs/pages-20260922/index.html:105`; `docs/docker-world.md:283`.

**What is wrong.** Four documents describe replay as deterministic:
- `README.draft.md:51`: "deterministic replay of the recorded prefix (any backend)"
- `docs/final-report-20260929.md:12, 43`: "deterministic replay"
- site, `index.html:105`: "VM snapshot or deterministic replay"
- `docs/docker-world.md:283`: "use `replay` (measured deterministic above)". The measurement is 2 replays of about 30 actions (line 201).

In exp1:
- 1,608 replay restores passed the fidelity check 53% of the time, although tables were equal in 91%.
- Restore time was p50 61 s and p90 403 s.
- 197 checkpoint branches failed all 3 restore tries.

**Fix.**
- Reword as "replay with a fidelity check; 53% of exp1 replay restores passed (n = 1,608); failed restores are retried, then unscored".
- Break the failures down by cause (exception, tables, screen) and by replay depth.

## Minor

- **m1. Void-annotation scripts.** `mark_transport_voids.py:23, 44` has a start time but no end time. Running it again would exempt later transport- or stop-voided repairs (e.g. those killed at 10:17), beyond the "20:18 batch" the 00:25 note covers. `mark_provider_outage.py:23, 45-46` starts at 14:00 (credit ran out at about 14:05) and also treats any branch containing "429 Too Many Requests" as outage. Both over-include. The report should list the annotated repair ids by cause.
- **m2. Annotations can be lost.** `store.py:279-283` (`finish_repair`) overwrites `result_json`. This drops a `void_reason` that `annotate_repair` (285-292) added to a repair still running. The scripts ran after reaps, so this is probably harmless here; merge instead of overwriting.
- **m3. Refused hosted requests stay "uncertain" at full price.** `student.py:879-904`: a hosted request still refused with 429 after `RATE_LIMIT_RETRIES` is reconciled as `uncertain` at the full worst-case reservation (≈ $0.53 per send). The comment at line 914 says a 429 is "never billed". About 1,214 outage branches × up to 3 refused steps (`env.max_infra_errors = 3`) means the session ledger can overstate OpenAI spend by up to ≈ $1.9k. `_error_meta` still records only the status line, which is how `insufficient_quota` was misread as a rate limit. Fixes:
  - release the reservation on a received 429;
  - record `error.code`;
  - fail fast on `insufficient_quota`;
  - take spend from the invoice.
- **m4. `report.py`:**
  - `void_repairs` (line 51) leaves out the ~239/188 not-a-try repairs; report them by cause.
  - `checkpoint_tradeoffs(stores)` (line 113) has no experiment filter. `capture_seconds.reset` (n = 836) includes about 300 exp1-eval step-0 captures (836 minus at most 541 collection attempts). These are not outcomes, but the section is labelled collection.
  - `video_lines` (131-135) omit the third primary comparison, A2−A3, and call the family-balanced rate "of held-out tasks".
- **m5. Budget selection.**
  - `budget.py:168-169` skips an excluded failure together with its student attempt, so finding that failure is free. No excluded failure is inside A2's window, so budget-v2 is not affected.
  - `select` stops at the first unit that does not fit. A2-b100 spent $25.29 of B = $27.27, 7% under A1.
- **m6. Audit counts steps, not records.** `dataset.py:125` counts every acted step. Records need an action, `valid` and a screenshot (line 142). Hence `memory_provenance_ok` exceeds the record count — this is not a data error. Label the audit "steps".

  | Dataset | `memory_provenance_ok` | Records |
  | --- | --- | --- |
  | A1-b100 | 4,171 | 4,167 |
  | W | 2,427 | 2,425 |
  | A2 | 1,015 | 1,014 |
  | A3 | 1,199 | 1,197 |

  The check is also still tautological at the branch boundary: the fold is seeded from the first acted step (lines 121-123; review-1 m2). So `docs/correction.md:91-94` overstates it.
- **m7. Evidence page record counts.** `evidence.py:224-229` says "N … records from the verified branch" but counts every verified branch. On the exp1 page, "45 … from the verified branch (steps 12–34)" is 23 records from `br-f2bcb5b4a23e` plus 22 from `br-8119628f9b8c`. Review-1 found the same bug on the Solari page.
- **m8. Protocol record.**
  - The protocol says notes are "never edited in place", but two note timestamps were: `380b5de` (09:25→09:17) and `20a1ad3` (15:45→15:28).
  - The 15:28 note says A1 was collected "at half that concurrency" / "16 + 16", but `phase1_collect_teacher.sh:8-11` ran W at 32 and the demonstrations at 40 concurrently.
  - Correct both from the logs in a dated note.
- **m9. Warm-start cells never replaced.** W was not completed under the replacement rule: 3 unscored cells (compose 2, reschedule 1) were never replaced. So `docs/final-report-20260929.md:38-39` "0/20 warm start" should read 0/19 scored.
- **m10. Stopped processes lose charges or are misclassified.**
  - `tmux kill-session` sends SIGHUP, which also skips `finally`. The interrupted branches (82 checkpoint, 11 restart) and the attempts stopped at 08:35, 00:08 and 10:17 have no charges. `docs/correction.md:128-130` blames only "SIGKILL, power loss". `README.draft.md:60-61` "Every model call, machine-second … is an append-only charge" overstates (review-1 M5).
  - A SIGINT-cancelled branch is recorded `infra_error` (`repair.py:95, 151-164`) rather than `interrupted`. `mark_transport_voids.cause` then treats it as "other", so it counts as a try.
- **m11. A live attempt's outcome can be discarded.** `record.py:144-146` swallows the `ValueError` for an attempt already marked `interrupted`. If a live `evaluate` process goes more than 180 s without a heartbeat (`STALE_S`; synchronous SQLite with 60 s lock waits under 88 concurrent episodes), another process marks its rows interrupted and the real outcome is lost. After all cells have run, count exp1-eval attempts that are `interrupted` yet have a `verdict.json`.
- **m12. Claims.**
  - **Denial students at the login.** `docs/flagship-runs.md:17-18` says "3 NOT_DONE on denial, stuck at the OpenEMR login" and "evidence: the start of the login loop".
    - This is true for 900012 and 900014: their step-89 screens show the login page.
    - The featured 900011 logged in and stalled on the calendar: its step-22 and step-89 screens show `main.php`, with evidence `stall_start: 9`.
    - The site is right; fix `flagship-runs.md`.
  - **Kanboard failures.** The site says "5 of its 6 recorded failures were repaired". The Kanboard store has 13 failures across three configurations:
    - kb-loop: 6 failures, none repaired;
    - kb-loop2: 1 failure, repaired twice, unrepaired;
    - kb-loop3: 6 failures, 5 verified.

    Give the totals.
  - **Hidden values.** `docs/walkthrough.md:99` says "Hidden task values never enter any prompt." But 1,404 of A1's 4,167 records carry a hidden value in the text input, through the teacher's own memory. Write "the harness never inserts them".
  - **README links.** `README.draft.md:35` says "Recorded evidence from real runs is linked below", but nothing is linked.
  - **Final report.** `docs/final-report-20260929.md:11` gives "660 tests"; the suite has 665. Its §3 omits the credit outage and its misdiagnosis, the transport drops, the key-name bug, the restore pass rate, and the void v1 datasets and 9 void training runs.
  - **Solari dataset name.** It is still "student failures repaired from VM snapshots", although only 4 of 8 paths (189 of 425 records) are.
  - **Video.** It shows "tables identical … in 3 of 3" for the featured repair; add the experiment-wide pass rate.

## What the final report must say

1. **Yield at B = $27.27** (counted-work accounting):

   | Arm | Paths | Tasks | Records | Notes |
   | --- | --- | --- | --- | --- |
   | A1 | 78 | 78 | 4,167 | |
   | A2 | 21 | 10 | 1,014 | 24% step-0 restarts; spent $25.29 |
   | A3 | 24 | 9 | 1,197 | |

   Give the per-family paths and cost for each arm.
2. **The accounting change was data-dependent.** The 15:28 extension was made after the first datasets were seen, partly on a wrong diagnosis. Give the all-work cost per arm by cause, and what the registered accounting would have selected.
3. **Repair counts** (checkpoint / full restart):
   - repairs for 121 failures: 382 / 328
   - counted: 47 / 80
   - void tries: 96 / 60
   - not-a-try: ~239 / ~188
   - exhausted: 4 / 0
   - pending when stopped at 10:17: 70 / 41

   Per-mode totals over all failures are not comparable.
4. **Restores and capture:**
   - replay restores: 53% pass (n = 1,608), p90 403 s;
   - restore-failed branches: 197 vs 25;
   - replay checkpoint capture: median 26 s, charged to A2 only.
5. **The repair-mode comparison is exploratory:**
   - up to 6 vs 3 teacher branches;
   - share of checkpoint successes from the step-0 fallback;
   - 9 of 50 failures unsettled;
   - window fixed after the first batch's outcomes were known.
6. **Evaluation reporting:**
   - unscored cells by arm × shard × cause, with key-name failures separate;
   - attempts used per cell;
   - a sensitivity analysis with unscored = failure;
   - A0/S_W results per shard;
   - `max_seconds` endings and latency by arm and shard.
7. **Interpretation limits:**
   - A2−A0 includes W; show S_W−A0 beside it.
   - A2−A1 and A2−A3 are conditional on one dataset per arm.
   - Arm-specific share of training examples: ≈63% (A1), 29% (A2), 33% (A3).
   - No arm has any rescheduling data.
   - The half-cost criterion was not tested.
8. **Incident timeline.** Credit outage (≈14:05–20:10), transport drops, and every operator stop (08:35, 09:08, 10:31, 00:08, 10:17), with what each one voided.
9. **Training provenance.** Seeds (from `optim.seed`), A3-s3's hardware and software, and the hashes of datasets and adapters.
10. **Protocol edits.** The two in-place timestamp edits of the protocol.
11. **Spend.** Take it from the invoices. The ledger's "uncertain" amount includes 429 refusals that were never billed.

## Checks that passed

- **Offline suite:** 664 passed, 1 skipped, exit 0.
- **Collection arithmetic:**
  - Round 1 has 160 cells (292 attempts, 132 unscored) and 121 failures (compose 36, reschedule 40, denial 11, insurance 34).
  - The per-mode family counts sum to 121.
  - For A2 and A3, units + pending + excluded = 160.
  - Every b100 cost equals world-hours × 0.35 + teacher $ + steps × 0.001 (A1 27.27, A2 25.29, A3 27.08).
- **Windows and lineage:**
  - A2-b100 and A3-b100 share their first 17 units in the same order.
  - No excluded unit lies inside A2's window.
  - Path counts per unit sum to 21 and 24, and task counts to 10 and 9 (= manifest `tasks_checked`).
  - All 9 runs trained on W `ds-ff04067bf950` plus their arm's budget-v2 b100 dataset, with the same record hashes on dev.
  - The window-first ordering was the same for both modes (`phase3d_replace_repairs.sh:16-18`).
- **A2's `restart_demo` records** are by design (step 0 is the last restart point) and trace to compose 010073 (165 records) and 010075 (79).
- **Repair-mode window definition:** the first 50 failures in the interleaved order over finished round-1 student attempts. This matches `repair --order budget --limit 50` and the wording of the 08:45 note.
- **Restore and capture figures are internally consistent:**
  - about 1.5 replay restores per replay-restored branch;
  - p90 403 s ≈ 50 replayed steps × 8.1 s;
  - capture = table digest + oracle (`checkpoint.py:229-233`).
- **No evaluation-path change after 05:50 Sep 29** except the student policy's retries. This covers env, backends, oracle, world, prompts, action parsing and `record`/`runner` cells. The retry commits:
  - `3a02943` at 08:37, before the aux A0/S_W cells;
  - `9d1b8a8`, the 429 re-send;
  - `229eaf9`, hosted endpoints only.
- **Correction-engine and policy mechanics:**
  - `counted_repair`, `_tries`, annotations and the live-runner skip behave as documented and are tested.
  - `select` refuses to cut the budget past an unsettled failure, and `cmd_budget` refuses when pending arms leave B undetermined.
  - The hosted transport retry keeps each dropped send's reservation and reserves each re-send; this is tested.
  - `RepairConfig` refuses any feedback mode other than `none` (`repair.py:52-53`).
- **Claims:**
  - The Solari flagship claims (4/6 repaired, 2 from step-8 snapshots, 79 s restores, screen distance 0) match the local store.
  - The Kanboard claims (29/29 tables equal, 5 verified repairs) match the local store.
  - Nothing claims invented snapshots, GUI benchmarks, deterministic verification, agent debugging or corrective training (`README.draft.md:86-88`; site "What is and isn't new").
- **Protocol integrity:**
  - The protocol notes were not rewritten in substance, and the wrong 15:28 diagnosis was corrected by a later note.
  - `report.py` reads final-test outcomes only with `--final`.
