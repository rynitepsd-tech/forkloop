# Independent review 1: Forkloop correction engine (branch correction-engine-20260929, HEAD d7d5f97)

Reviewed on 2026-09-29 (UTC). Read-only: no cloud calls, no code or doc edits. Offline suite: 655 passed, 1 skipped (exit 0).
I checked the real Solari store `runs/loop-solari-student` on a copy of the SQLite file, using an audit script that re-derives each record's lineage.

## Blocker

**B1. Matched-cost unit order leaves compose_claims out of the truncated arms.**
`budget._task_key` (budget.py:71-73) sorts units by seed across families (budget.py:87, 118). `select` then takes a prefix of that order (budget.py:121-129, cli.py:550). compose_claims skips 59% of train seeds for held-out structures (manifest `train_pool_exclusion_rate_first_500`). So its round-1 slice (positions 30-69) has seeds 10073-10163, while the other three families end at 10078-10081.
I measured the family mix of the first N of the 160 units:

| First N units | compose_claims units |
| --- | --- |
| 40 | 0 |
| 80 | 0 |
| 120 | 3 |
| 160 | 40 |

Any arm cut below about 73% of its units therefore gets no round-1 compose data. The arm with the smallest total keeps all 40, and that is probably A1 (one teacher attempt per task). The B/4 and B/2 scaling datasets have no compose data at all. Compose is 60 of the 150 final tasks and a quarter of the family-balanced metric, so A2−A1 and A2−A3 are confounded with family coverage.
*Fix, before phase 4 and as a dated deviation:* order units round-robin by within-family position, or split B evenly across families.

## Major

**M1. "Origin" restart evidence fires on correct behaviour.**
`restart_points` passes `values=produced_values(task)` (diagnose.py:141-142). With `values` set, the instruction exclusion is skipped (diagnose.py:69-71), although the docstring at lines 66-68 says instruction values are excluded. This undoes the fix in 80f08d9.
- **Reschedule:** in 40 of 40 round-1 tasks, both the appointment's current date and the world date 2026-09-07 are within 2 edits of the target date. A correct memory note such as "Current appointment 2026-09-16 09:00" returns `origin (2 edits)`.
- **Insurance:** a correct note "form prefilled with <typo>; replace with <new id>" returns `origin (1 edit)`.

For reschedule and for compositions with a reschedule part, A2 degenerates towards early restarts, and `recovery_by_reason` gets labels that are wrong.
*Fix:* only flag values the agent types into a field, exclude values the instruction or the pre-action screen state, and add these cases as tests.

**M2. Model and serving transport errors are scored as the policy's failures.**
`StudentPolicy.act` turns HTTP errors, timeouts and disconnects into `(None, {"error": True})` (student.py:956). Retries are off (student.py:885). `Env.step` then counts them as invalid actions (env.py:158-166), and ten of them end the episode as a scored failure. The infra rule only applies to backend failures (env.py:241-250).
- A wrong or not-yet-loaded `--set model=<adapter>`, or overloaded vLLM replicas, would give an arm scored zeros rather than unscored cells.
- It already happened once in the real data: `att-c5a04ebb1f7b` has `request failed: RemoteProtocolError: Server disconnected` counted as an invalid step.

*Fix:* count policy transport errors as `infra_total`, check before evaluating that the served model name exists, and report the error counts.

**M3. The infrastructure rule for scoring is asymmetric.**
A failed episode with any dropped action becomes INFRA_ERROR and is retried. A successful episode with a dropped action counts (env.py:241). Failing episodes run to 120 steps and so have the most chances to drop an action. The result is an upward bias whose size grows with each arm's infra rate and failure length.
*Fix:* treat infra-affected episodes the same way whatever their outcome, or at least report a sensitivity analysis that excludes all of them.

**M4. The damage classifier misses irreversible wrong writes to rows that already exist.**
`classify` (checkpoint.py:193-200) counts a value error as damage only if the checked value was empty at reset. Two checks break this:
- `claim_member` already holds the old member ID at reset (update_insurance_reconcile.py:104).
- `appeal_auth_number` reads the latest appeal (resolve_denial.py:132), which in the v2 prior-appeal variant is the seeded decoy appeal.

So a wrong resubmission, or a wrong new appeal in the lure variant, is classified `clean`. Typing the old member ID is a realistic error that also escapes origin detection. In those cases `latest` restart points after unrecoverable damage waste k teacher branches, which is charged to A2.
*Fix:* compare row identity (new row id or count) rather than value presence, or declare which checks are irreversible for each task.

**M5. The cost ledger undercounts crashed work and repeated repairs.**
Wall-time and model charges are written only in `finally` at the end of an episode (record.py:137-140, repair.py:159-161). A killed process records nothing: the four `interrupted` branches in the student store have no charge rows even though restores and teacher calls ran.
`repair_units` also keeps one repair per attempt (`reps` dict, budget.py:94), so earlier interrupted repairs are dropped from the arm's cost.
The README claim that every model call is an append-only charge is overstated.
*Fix:* write charges step by step, sum all repairs of an attempt, and reconcile per-arm totals against the session ledger.

## Minor

- **m1. Resume can duplicate work.** `plan_cells` (runner.py:51-56) and `run_repairs` (runner.py:186-192) do not skip cells or repairs that a live runner still has `running`. A second process starts attempt or repair N+1 at the same time, which duplicates demos and uses up retry slots. The student store shows repairs of the same attempt started 55 s apart by two runners (Mac-11036 and Mac-12010). `finish_branch` also has no status guard, unlike `finish_attempt`.
- **m2. The provenance audit is weaker than documented.** `memory_provenance_ok` seeds its fold from the first acted step's own memory (dataset.py:117-124), so the branch boundary is never checked. `hidden_in_text_input` counts hidden values but does not check where they came from.
  - On the real dataset I checked this independently. For every path the first-step memory equals the checkpoint `agent_state`, which equals the fold of the attempt prefix. Checkpoint history equals the prefix actions, current and previous screenshot hashes match the branch or attempt files, and no records come from replayed steps.
  - All 39 `hidden_in_text_input` records hold values the teacher itself wrote in the same branch after reading them on screen (AUTH-14X40175, member IDs). That is legitimate.
  - Make the audit check the boundary and the provenance so a real leak would fail the export.
- **m3. Checkpoint elapsed time includes the snapshot.** `elapsed_s` (checkpoint.py:256) is computed before record.py:121 pauses the clock, so it includes that checkpoint's snapshot time. In the store, step 16 (snapshot) shows 143.5 s and step 24 (replay) shows 108.2 s. Branches restored from snapshots start about 70-90 s later on the task clock than they should.
- **m4. `unrepaired` also covers repairs that never got a scored branch.** A repair whose branches all failed to restore is still marked `unrepaired` (repair.py:222), and it is never retried (runner.py:187).
- **m5. Replay restores can fail or diverge.** Replay re-applies actions the backend rejected ("apply failed") or dropped (restore.py:117-121), which fails or diverges the restore. The fidelity check allows 10% of cells on a 64×36 thumbnail, so unsaved text in the wrong field goes undetected.
- **m6. `forkloop repair` has no final-test guard** (cli.py:183-200). Export (`_refuse_final`) and `train_lora.assert_no_final_test` still block training, so there is no leak. Record and evaluate guards, the frozen manifest hashes and the per-record training check are otherwise sound.
- **m7. Infra retries need a manual re-run.** They only happen when the command is run again. `chain_2_3.sh` and the phase scripts never re-run. Loop until no retryable cells remain, especially for phase 5.
- **m8. Datasets are only partly immutable.** Only files are set to 0444; directories stay writable. Re-exporting identical records with different preferences returns a manifest that does not match the files on disk (dataset.py:304-316).
- **m9. Per-arm and paired analyses use different task sets.** `arm_success` excludes unscored tasks per arm, while `paired` excludes them pairwise, so headline per-arm rates are not over the paired task set. Two labels that map to the same (arm, run) silently overwrite each other.
- **m10. Stall and cost details.** Three identical scrolls or waits count as a "stall". `model_usd` ignores the long-context rates that the ledger applies.

## Claims

- **README "Two minutes":** the clone URL points to origin, where this branch is not pushed (43 commits ahead; origin/main has no `forkloop/correction`). `worlds/kanboard_v1` and `docs/docker-world.md` are untracked. "Recorded evidence … linked below" links nothing, and `runs/` is gitignored.
- **Flagship dataset name:** "student failures repaired from VM snapshots", but only 4 of 8 verified paths (189 of 425 records) come from snapshot restores. The rest are step-0 restarts after "Snapshot not found".
- **Flagship failure labels:** the three insurance "WRONG_VALUE" failures are the legacy-split mislabel for "nothing done". The claim stayed DENIED and OpenEMR was not changed.
- **Evidence page:** "60 records from the verified branch" actually spans two branches.
- **correction.md:** the `memory_provenance_ok` and hidden-value wording overstates the audit (m2). The information-boundary claims themselves hold: the teacher gets only the checkpoint's memory, `feedback: none`, diagnostics are never rendered, and I found no path from hidden values into policy inputs.

## Fresh user

From `git archive HEAD` in a clean Python 3.11.14 venv, `pip install -e '.[world]'` and `forkloop demo-loop` both succeeded. The loop ran end to end, 1 of 4 toy failures was repaired, the dataset was exported, and the evidence page had no broken links.

Problems:
- A real clone gets main, which has no `demo-loop` (see Claims).
- The "real software" example uses `configs/exp1.yaml`, which has a hard-coded `/home/ubuntu` store and needs the x86 `:3` Docker image.
- `demo-loop` copies `examples/` from the source tree, so it works only with an editable install.
