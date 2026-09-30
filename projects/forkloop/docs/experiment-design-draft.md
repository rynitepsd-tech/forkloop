# Learning experiment — design draft (not yet registered)

*Draft of 2026-09-29. Numbers marked TBD are set from development pilots and frozen in the
registered protocol before any final-test task is generated or run.*

## Question

Does checkpoint-based correction (Forkloop) produce more useful training experience per unit of
collection cost than collecting teacher demonstrations from the beginning, and does the resulting
student succeed more often on **new** full workflows, running alone?

## Arms

All arms share the student base model, the observation contract (screenshots, instruction, own
action history, explicit memory; `agent_memory_v1`), serving settings, and evaluation budget.

| Arm | Training data | Collection |
| --- | --- | --- |
| A0 base | none | — |
| W warm start (if needed) | teacher demonstrations from initial states on `N_w` train tasks | shared by A1–A3 |
| A1 demonstrations | W ∪ teacher demonstrations from initial states on further train tasks | teacher runs from step 0 |
| A2 Forkloop | W ∪ verified correction suffixes | the warm-started student attempts train tasks; failures are repaired from evidence-chosen checkpoints, `k` teacher branches per restart point |
| A3 full restart | W ∪ verified teacher trajectories from step 0 on the **same** failed student tasks, same `k` | isolates checkpoints from extra attempts |

A warm start is used only if the base student's train-task failures are mostly immediate (so that
checkpoints add nothing); it is identical across A1–A3.

## Matched collection cost

Each arm collects until the same budget `B` is spent, counted in teacher-model USD plus world
machine time at a fixed hourly rate (both reported separately). Forkloop's cost includes the
student attempts, checkpoint capture, restores/replays, and every failed branch. Data-scaling
curves use nested subsets at `B/4`, `B/2`, `B`. Equal-example and equal-token comparisons are
reported as mechanism analyses.

## Training

LoRA SFT with identical hyperparameters and optimizer steps across arms (TBD from pilots);
≥ 3 independent training runs (seeds) per trained arm. Total training compute reported.

## Pools

Disjoint seed and structure pools from `forkloop/splits.py`: `dev` (everything used before
2026-09-29 and all pilots), `train`, `val` (model selection/early stopping only), `final_test`
(fresh generator split name; structure partition: some difficulty combinations and composition
pairings appear only here). Final-test tasks are generated only when the protocol is registered.

## Evaluation (student alone)

One attempt per task per trained model; no teacher, no search, no retries beyond the declared
infrastructure rule (≤ 2 replacement attempts for unscored cells; the original attempt stays
visible). Same verifier. Scale TBD by power analysis (default: 100 tasks per family + 100
compositions).

## Analysis

Primary: full-task success on `final_test`, families equally weighted. Paired comparisons on the
same tasks (A2 vs A0, A2 vs A1, A2 vs A3) with a task-level cluster bootstrap over training runs;
per-family results; run-to-run variation. Secondary: wrong-record and duplicate-side-effect
rates, unscored cells, steps, latency, collection yield, cost per verified example and per
successful task. Practically meaningful effect (TBD before evaluation): target ≥ 20 points over
A0, and either a meaningful advantage over A1 or about twice the collection efficiency at
comparable performance.
