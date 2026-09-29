# Protocol: does checkpoint-based correction train a better computer-use student?

*Registered 2026-09-29 ~05:20 UTC, before any final-test task was run, in the commit that adds this
line. Later deviations are appended as dated notes at the end, never edited in place.*

## Question

Does Forkloop's checkpoint-based correction produce more useful training experience per unit of
collection cost than (a) teacher demonstrations collected from the initial state and (b) teacher
corrections that restart from the beginning, and does the resulting student, **running alone**,
succeed more often on new full workflows?

## Fixed components

| Component | Value |
| --- | --- |
| World | claims-ops-v1 on Docker, image `forkloop/claims-ops-v1:3` = `sha256:7324af036519fd11dcedff3af257aca04f67ef9e3a6ed9026076d4675c51b0d7`, world clock 2026-09-07 09:00 UTC at boot |
| Task code / splits | code at commit `1882f81` (branch `correction-engine-20260929`); split manifest `worlds/claims_ops_v1/tasks/splits_manifest.json` sha256 `9d65a566ac44…` (policy v1) |
| Families | `reschedule_constrained`, `update_insurance_reconcile`, `resolve_denial`, `compose_claims` |
| Student (S) | Qwen/Qwen3.8-27B @ `1d4bf0f2`, vLLM 0.30.0, thinking off, greedy, max 384 output tokens |
| Observation (all policies) | `agent_memory_v3` system prompt; instruction, explicit memory (own `Memory:` lines), last 12 actions, previous + current screenshot; student images upscaled 1.5× (1920×1080), coordinates 0–1000 |
| Teacher (T) | gpt-5.6-luna, reasoning effort high, image detail high, same prompt/memory/history |
| Episode budget | 120 actions; 3,600 s wall (never the binding limit by design; latency is reported) |
| Checkpoints (collection) | replay strategy, every 4 steps + before each `type` + before `Return` |
| Repair | `k = 3` branches per restart point, ≤ 2 restart points (evidence-ordered, step 0 last), stop after a verified branch, teacher feedback: none; each branch may take up to 120 actions after its restore point (the same allowance as a demonstration from the initial state) |

## Pools

From `forkloop/splits.py`: `train` (generator split `train_v2`, held-out structures removed),
`final_test` (frozen list of 150 tasks: reschedule 30, insurance 30, denial 30, compose 60, with
quotas of held-out structures H1–H6; generator split `final_test`, fresh random stream and surname
pool). Train-pool slices by position per family: pilots 0–9 (development only, never trained on),
warm start 10–29, round-1 collection 30–69. The legacy sealed denial block is not evaluated here.
`val` is unused (no model selection: fixed recipe and step count).

## Data collection

1. **Warm start W.** T attempts the 80 warm-start tasks from their initial states; every verified
   path becomes demonstrations. `S_W` = S + LoRA(W), seed 0, 50 optimizer steps. W is shared by
   every trained arm.
2. **Round 1** on the 160 collection tasks:
   - `S_W` attempts each task once with checkpoints (the agent under repair);
   - **A2 Forkloop:** each scored failure is repaired from checkpoints (mode `checkpoint`);
   - **A3 full restart:** each scored failure is repaired from step 0 (mode `full_restart`), same `k`;
   - **A1 demonstrations:** T attempts each of the 160 tasks from its initial state.
3. **Matched cost.** Unit costs (`forkloop/correction/budget.py`): teacher USD from provider usage +
   world time × **$0.35 per world-hour** (main box $22.32/h ÷ 64 worlds) + student steps ×
   **$0.001 per step** (7 A100 replicas ≈ $19.53/h at ≈ 6 steps/s). A1 units: teacher attempts. A2/A3
   units: every `S_W` attempt (finding failures costs) plus its repair. Units are taken in task-seed
   order until the budget `B` = the smallest of the three arms' total round-1 cost is reached; the
   arm's training set is W ∪ the verified paths of its selected units. Nested subsets at B/4, B/2
   support the data-scaling curve (reported descriptively).

## Training

`scripts/train_run.sh`: LoRA r=16, α=32, dropout 0.05, language-model projections only; lr 1e-4,
warmup 5%, batch 1 × grad-accum 8, **100 optimizer steps** (800 examples sampled from the arm's
data, weighted uniformly by record), seq ≤ 16,384, image scale 1.5. Three independent runs (seeds
1, 2, 3) for each of A1, A2, A3. Identical optimizer budget across arms; total GPU-hours reported.

## Final evaluation (student alone)

Models: A0 = S, `S_W`, A1×3, A2×3, A3×3. Each attempts each of the 150 final-test tasks once:
no teacher, no search, no retries except the infrastructure rule. Same verifier. Serving: vLLM
multi-LoRA, all models on the same servers, interleaved.

**Infrastructure rule.** A cell whose attempt is unscored (reset/feasibility failure,
backend/transport failure, oracle error, interruption) gets up to 2 replacement attempts; the
first *scored* attempt counts; original attempts stay in the store and reports. Cells still
unscored after 3 attempts are excluded pairwise and reported.

## Analysis (`forkloop/correction/analysis.py`)

- Arm success = mean over the four families of the family mean of task outcomes averaged over the
  arm's runs.
- **Primary comparisons:** A2 − A0, A2 − A1, A2 − A3, each a paired difference on the same tasks
  with a 95% bootstrap interval resampling tasks within family and training runs within arm
  (10,000 replicates, seed 20260929); secondary exact sign test on tasks whose run-averaged
  outcomes differ.
- **Practically meaningful effect:** A2 − A0 ≥ 0.20; and A2 − A1 > 0 with the interval excluding 0,
  or A2 reaching A1's success at ≤ half its collection cost (scaling curves, descriptive).
- Reported per family and per run; wrong-record and duplicate-side-effect rates; unscored cells;
  steps; latency; collection yield; cost per verified example and per successful task.
- **Power.** 150 paired tasks: a 20-point improvement from a low base (e.g. 5% → 25%) is detected
  with power > 0.99; a 10-point difference between trained arms has power of roughly 0.6–0.8 with
  three runs per arm. Smaller arm differences may remain inconclusive and will be reported as such.

## What does not count

Any result on pilot/dev tasks, search-assisted or teacher-assisted success, success on training
tasks, or a subset chosen after seeing outcomes. If an arm's training fails for infrastructure
reasons, it is re-run with the same seed and data before any evaluation of that arm.

## Deviations and notes (dated, append-only)

- 2026-09-29 05:20 UTC — registration. Pilots (train positions 0–9, images `:1`, prompts v2/v3)
  are development evidence only.
- 2026-09-29 05:30 UTC — evaluation runs on two servers (main: 8×A100-80, TP1; aux: 8×A100-40,
  TP2). For **every** model the final-test list is split the same way: list position mod 2 = 0 on
  main, = 1 on aux, so any server effect is balanced across arms. Cells of one model may run at
  different times; nobody reads final-test outcomes until all planned cells have run.
- 2026-09-29 05:40 UTC — observation during phase 1 (not a change): the registered teacher solved
  0/40 `reschedule_constrained` tasks from their initial states so far (mostly 120-step budget
  exhaustion; wrong current date; OpenEMR date-picker trouble), against 18/20 insurance and 10/12
  denial. No change to teacher, prompt or budget; rescheduling stays in training collection and
  evaluation as registered and is reported per family.
- 2026-09-29 05:50 UTC — **deviations after the independent review, before round 1 or any
  training** (review report `runs/review/review-1.md`, local):
  1. Matched-cost selection takes units **round-robin across families** (each in seed order)
     instead of pure seed order, which would have dropped `compose_claims` from truncated arms.
  2. A model-server failure (HTTP error, timeout: `metadata.error`) is **infrastructure**, not the
     policy's invalid action; and an episode the infrastructure interfered with is **unscored
     whatever its outcome** (previously only failures), then replaced under the same rule.
  3. Restart-point "origin" evidence ignores dates and, for memory notes, values stated in the
     instruction (typed near misses still count).
  Phase-1 teacher collection (W, A1) ran before these changes; (2) could only have turned a few
  teacher failures into unscored cells, and those cells are not training data either way.
- 2026-09-29 05:55 UTC — experiment id `exp1-final` (A0, aux shard) was started at 05:25 under the
  pre-review scoring rules and stopped at 05:55 with its outcomes unread. It is **void** and never
  analysed; all final-test cells run under `exp1-eval` with the fixed code (`4d7ef5d`).
- 2026-09-29 08:45 UTC — round 1 showed ~3 vLLM transport `ReadError`s per 120-step student
  episode; under the symmetric rule those episodes were unscored (correctly), leaving few scored
  failures to repair. The student policy now re-sends a request that failed in transport to a
  self-hosted endpoint (no response was received; hosted, billable endpoints never retry). The
  round-1 recorder was also interrupted at 08:30 by an operator error (killing the phase chain
  closed its tmux session); its 37 in-flight composition attempts are kept as `interrupted`. All
  unscored/interrupted round-1 and demonstration cells receive their replacement attempts under
  the registered rule (≤ 2 extra attempts per cell, original attempts kept).
- 2026-09-29 09:00 UTC — A0 and `S_W` do not depend on round 1, so their final-test cells run now
  (aux shard 1 from 08:52, main shard 0 from 09:00), on the same two boxes but on the
  collection-phase servers (base model + the `sw` adapter only; main TP1 × DP7, aux TP2 × DP4)
  rather than the phase-5 multi-LoRA servers. Weights, vLLM version, sampling, prompt, budgets
  and verifier are identical; outcomes stay unread until every planned cell has run. The
  registered "all models interleaved on the same servers" therefore holds for the nine trained
  models but not for A0 and `S_W`; this is reported as a limitation. Reason: the student
  server was ~75% idle during collection (episode time is dominated by the world), and moving
  these 300 episodes off the last phase shortens it by hours of 3-box time.
- 2026-09-29 09:08 UTC — main's early A0/`S_W` run (09:00) was stopped after 8 minutes, outcomes
  unread: with it, main's server latency rose from ~6 s to ~21 s per request, and round-1
  120-step episodes were already using ~2,900–3,050 s of the 3,600 s episode limit, so the load
  would have changed round-1 outcomes and could make evaluation outcomes depend on server load.
  Its in-flight cells are `interrupted` (kept) and get replacements under the registered rule;
  main's A0/`S_W` shard runs again when main's GPUs are otherwise idle (teacher repairs). The aux
  shard (server serving only this evaluation, ~11 s per request) continues.
- 2026-09-29 09:17 UTC — before any repair started: the policy adapter now re-sends a request
  refused with HTTP 429 (rate limited; a refusal is not billed) after the server's Retry-After or
  an exponential backoff, at most 5 times (`9d1b8a8`). Repairs run 64 concurrent teacher branches
  (twice the collection concurrency, where no 429 occurred); without this, one rate-limited step
  would make a whole branch unscored. It applies identically to both repair modes, which start
  together, and to any later run; no outcome was inspected to motivate it.
- 2026-09-29 10:31 UTC — main's A0/`S_W` shard, restarted at 10:24 alongside the teacher repairs,
  was stopped again (outcomes unread): together they asked for ~124 concurrent Docker worlds
  against the host cap of 96, so 6 evaluation cells failed at reset (`ConcurrencyError`,
  unscored) and 2 checkpoint-repair branches failed to restore (`restore_failed`, unscored; kept).
  Main's A0/`S_W` cells now run in phase 5 **interleaved with the nine trained models on the
  phase-5 server, as registered** (aux's shard ran earlier; see 09:00). Under the registered
  infrastructure rule the operator interruptions count as unscored attempts: about 56 of main's
  A0/`S_W` cells have used 2 of their 3 attempts. Any cell still unscored after its third attempt is
  excluded pairwise and reported, as registered.
- 2026-09-29 11:12 UTC — observation (no change): the Docker backend cannot send `ctrl+-` (xdotool
  needs the key name `minus`), so any episode in which a policy presses it has a backend failure and
  is unscored under the infrastructure rule. Early in the repairs 7 of 64 finished branches (both
  modes) were lost this way. The mapping is left unchanged until exp1 ends, so every collection and
  evaluation cell runs with the same backend; affected cells are reported with the unscored counts.
- 2026-09-29 15:28 UTC — **repairs were dominated by infrastructure; the registered infrastructure
  rule is extended to repairs** (before any trained model was evaluated; no final-test outcome of any
  model has been read). Of 1,029 repair branches, 622 failed on OpenAI HTTP 429 (the account's rate
  limit at 64 concurrent teacher branches; the bounded retry gave up), 26 on the `ctrl+-` backend
  bug and ~28 on hosted transport `ReadError`s; 60 checkpoint branches failed to restore. The
  demonstration arm (A1) was collected at half that concurrency and its few unscored attempts were
  replaced under the registered rule, so the arms were treated asymmetrically, and the first
  matched-cost datasets (B = $29.53: A1 78 verified paths, A2 9, A3 10) mostly measured the rate
  limit. Changes:
  1. A repair with any unscored branch (infrastructure error, failed restore, interruption) is
     unscored as a whole and replaced, up to 2 replacements; the first clean repair of a failure
     counts (`repair.counted_repair`) — the same rule as for attempts and evaluation cells. A
     failure with 3 unscored repairs is excluded and reported.
  2. The cost of unscored work (unscored attempts, void repairs) is charged to no arm (it had been
     charged to every arm). The earlier accounting remains available (`forkloop budget
     --count-unscored-cost`) and its numbers are reported.
  3. Replacement repairs run in the matched-cost selection order, 18 concurrent branches per mode
     (the demonstrations ran 16 + 16 with no rate limit), with a longer 429 backoff (8 tries, up to
     60 s). `forkloop budget` refuses to cut a budget past a failure whose repair is not settled.
  The eight training runs on main and the dev run started 15:08–15:11 on the earlier datasets are
  void and never evaluated; retraining uses `datasets/budget-v2` and `adapters-v2`. The earlier
  datasets (`datasets/budget`) stay on disk and in the report.
- 2026-09-29 20:10 UTC — **correction of the 15:28 note: the cause was not the rate limit.** A
  diagnostic request returned HTTP 429 with `insufficient_quota` / `credit_balance_exhausted` ("You
  have no credits remaining"): the OpenAI account's prepaid credit ran out at about 14:05 UTC.
  Teacher calls succeeded throughout 10:24–14:00 at 64 concurrent branches (≈ 32,000 calls, 3
  isolated 429s); from 14:30 none succeeded. Branches only recorded the status line, which read
  "Too Many Requests", and the 15:28 note attributed it to concurrency without checking the error
  body. Consequently the replacement repairs started 15:31 (18 + 18 concurrent) produced no scored
  branch (604 infrastructure errors, 592 of them this refusal; 112 failed restores) and were stopped
  at 20:05. Repairs run while the provider refused every request carry no information about the
  teacher or the method; they will not count toward the replacement limit of the 15:28 rule. The
  rest of the 15:28 extension stands (repair-level replacement; unscored work charged to no arm).
  Collection cannot continue until credit is added. No final-test outcome has been read.
- 2026-09-29 20:25 UTC — credit restored ≈ 20:10 (verified by a test request). Repairs voided by the
  outage are annotated `void_reason=provider_outage` (`scripts/exp1/mark_provider_outage.py`: not clean
  AND started 14:00–20:10 UTC or a branch refused with the 429): 212 checkpoint and 156 full-restart
  repairs; they are not replacement tries. 14 void repairs with other causes (e.g. `ctrl+-`, failed
  restores) still count. Clean repairs kept: 14 checkpoint, 37 full restart. Replacement repairs
  resume at 32 concurrent branches per mode (the level that ran without refusals 10:24–14:00), first
  for the first 50 failures in selection order (the budget window is at most 49 / 44 failures), then
  the rest; `repair --order budget` now follows the full selection order over all scored attempts.
