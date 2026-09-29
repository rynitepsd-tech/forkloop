# Execution ledger — correction engine program (from 2026-09-29)

Concise, append-only record of the current hypothesis, work, jobs, resources, experiments,
results, decisions and resumption commands. Historical handoffs stay in `product-handoff.md`
and `archive/`; their outcomes are not rewritten here.

## Current-state note (2026-09-29 01:50 UTC, before any change)

Verified against source and provider inventory, not against summaries.

- **Product today** is matched regression evaluation (`forkloop compare`) scored by a SQL
  verifier. Live-qualified only on `resolve_denial` with hosted models on Solari
  (gpt-5.6-luna 23/24, Sept 24). `update_insurance_reconcile`, `reschedule_constrained` and
  compositions have not been re-qualified live since the September repairs.
- **Search** (`forkloop/search.py`) is bounded best-of-N at *uncertain* steps; every branch runs
  to termination. In fork mode the winner's recorded result is adopted, but the root machine
  stays at the fork point: terminal adoption, not state promotion. There is no
  failure-to-correction workflow, no checkpoint policy, no restart-point selection, and no
  dataset lineage for corrections.
- **Checkpoint contract gap.** `EnvCheckpoint` holds a snapshot id, history and counters. Policy
  state (memory, RNG, model/config identity) is snapshotted separately and is not bound to the
  world checkpoint. Nothing verifies a restore beyond a successful boot.
- **Solari.** Starter plan: 2 concurrent VMs; restores are bimodal (≈22 s or 70–160 s).
  `SolariMachine.snapshot()` refuses creation from 2026-10-01 (date guard on
  `storage_starts_on`) and the built-in pricing review expires 2026-10-01; both are date
  assumptions, not a retention policy. Inventory: 0 machines, 1 snapshot
  (`snap_dlft9omnpkyw`, 9.6 GB golden). Venv SDK 0.2.0; PyPI now has solari-sandbox 0.2.3 /
  solari-core 0.3.0 (Sept 25), untested.
- **Lambda.** 0 instances, 0 filesystems (the two historical filesystems no longer exist; the
  only adapters are local in `checkpoints/`). SSH key "Solari Macbook GPU" registered.
- **OpenAI.** gpt-5.6-luna, gpt-5.6-sol/terra and gpt-6-* are listed.
- **Student.** Fara1.5-4B never reached a nonzero live full-workflow baseline (0/30 base, 1/10
  trained with notes). Offline notes/parity repairs did not transfer to live success.
- **Ledger.** `SessionLedger.create` defaults to ceilings of $20 OpenAI / $10 Solari / $0 GPU.

**Main bottleneck.** A controlled learning program needs thousands of episodes. Two Solari
desktops at ~100 s per reset plus 10+ minutes per episode deliver about 10 episodes an hour.
Second: no student with a nonzero baseline. Third: the correction engine does not exist.

## Plan (dependency order)

1. **Accounting and resources.** Resource registry, uncapped-but-accounted ledger services,
   replace the date guards with an explicit snapshot retention/cleanup policy.
2. **Scalable world backend.** Package claims-ops-v1 as a Docker world (OpenEMR 8.3 + portal +
   browser + Xvfb) behind the existing `Backend`/`Machine` protocol, so dozens of worlds run on
   one Lambda box and a developer can run the world locally. Qualify reset, feasibility and the
   verifier on all three families plus compositions. Solari remains the VM-snapshot backend.
3. **Checkpoint contract.** One checkpoint binds world state (Solari VM snapshot; Docker:
   deterministic replay with a fidelity check, and a native snapshot if one proves reliable)
   with policy state (history, explicit memory, RNG, model/config identity, step). Branch
   independence and restore tests with real mutations; interruption/failed-allocation tests.
4. **Correction engine.** Durable store with stable ids; CLI to record attempts, inspect
   failures, repair (restart-point selection, K verified continuations), export datasets with
   hashed lineage manifests, train, evaluate and inspect.
5. **Models.** Teacher with explicit memory under the actor information boundary; focused
   student qualification; training/serving parity harness at the processor level.
6. **Learning program.** Frozen splits, pre-registered design, demo vs Forkloop vs full-restart
   vs untrained student, matched collection cost, ≥3 training runs, held-out final evaluation.
7. **Flagship + product.** Solari demonstration with VM checkpoints; second world through the
   public interface; inspection report; README/site; release; video; independent review;
   submission copy; provider-confirmed cleanup.

## Log

- 2026-09-29 01:50 UTC — branch `correction-engine-20260929` from `78fa0f2`. Baseline offline
  test run started.
- 01:50–02:40 UTC — **Resources.** Lambda filesystem `forkloop-usw3` (us-west-3, registry
  `lambda-filesystem-0e09b1bb6b`) and instance `forkloop-dev-0929a` (gpu_1x_h100_pcie, $3.29/h,
  209.20.157.23, registry `lambda-instance-55ea6c9aa2`, lease 14 h, renewable). Registry:
  `~/.forkloop/resources.jsonl`; reaper: LaunchAgent `com.forkloop.reaper` runs `forkloop ops reap`
  every 10 min (remove: `launchctl bootout gui/$(id -u)/com.forkloop.reaper`). OpenAI key deployed to
  the box at `~/.config/forkloop/env` (0600). Program ledger (uncapped, authorization recorded):
  `runs/program-20260929/ledger-mac.sqlite`.
- **Agents (parallel, explicit file ownership).** Docker world backend (`worlds/claims_ops_v1/docker/`,
  `forkloop/backends/docker.py`); student serving + parity (`train/`); task families, compositions,
  split policy, adversarial verifier tests (`worlds/claims_ops_v1/tasks/`, `forkloop/splits.py`).
- **Built and committed** (`91f0f7a`, `1db06d2`, `f4b8953`): correction engine
  (`forkloop/correction/`), explicit agent memory, CLI `record/failures/repair/dataset/evaluate/
  status/inspect`, `forkloop ops`, uncapped-but-accounted ledger, registry-tracked Solari snapshots
  (date guard removed), Solari pricing re-reviewed (`configs/pricing/solari-starter-2026-09-29.json`).
  Offline suite green; toy-world end-to-end tests for both checkpoint strategies.
- 02:37 UTC — **Live Solari qualification started**: `configs/loop-solari-flagship.yaml`,
  experiment `solari-flagship-1`, gpt-6-luna (agent under repair) on `resolve_denial` train
  900001–900002 with snapshot checkpoints. Resume: `runs/loop-solari-flagship/record.sh`.
  Competitor/deadline research: `docs/` pending; deadline confirmed 2026-09-30 (organizer's X post).
- 03:05–03:35 UTC — **Main box** `forkloop-main-0929a` (gpu_8x_a100_80gb_sxm4, us-east-1, $22.32/h,
  132.145.133.47, registry `lambda-instance-fa558e495a`, lease 24 h) + filesystem `forkloop-useast1`
  (`lambda-filesystem-935505d54a`). Student stack pinned by the student agent (vLLM 0.30.0+cu129,
  torch 2.13.0+cu129, transformers 5.17.0, peft 0.21.0); servers: Qwen3.8-27B on GPU0 :8000,
  Holo-3.1-9B on GPU1 :8001; Mac tunnel 18000/18001. Docker world image `forkloop/claims-ops-v1:1`
  (reset p50 12.4 s single, 30.1 s with 64 concurrent; replay fidelity exact).
- **Decisions.** Student = Qwen3.8-27B (memory format 42/42 zero-shot vs Holo 5/42; grounding 25/28 vs
  21/28; LoRA fits one 80 GB GPU; parity 32/32 live). Observation adds a 1.5x client-side upscale
  (exact authorization reads 4/14 → 12/14; tokens per image 921 → 2081). Teacher = gpt-5.6-luna.
  Prompt `agent_memory_v2` for both. Split policy `forkloop/splits.py` (train_v2/val_v2/final_test).
- **Solari measured** (`solari-flagship-1`, gpt-6-luna under the memory prompt, dev seeds): 2/2
  succeeded; snapshot capture 68–93 s; two-branch restore exact; lookups inconsistent (see
  platform notes); all 12 checkpoint snapshots deleted. This run predates the checkpoint-clock fix.
- 03:35 UTC — **Pilots started** on main: `pilot-baseline` (Qwen 1.5x alone, 10 train tasks/family ×
  4 families), `pilot-teacher` (gpt-5.6-luna record with replay checkpoints, same tasks). Store
  `~/programs/pilot/forkloop.sqlite`, synced to `/lambda/nfs/forkloop-useast1/programs/pilot`.
  **Solari flagship** `solari-student-1` (Mac): student on 6 dev tasks with snapshot checkpoints →
  failures → repair. Resume: `runs/loop-solari-student/run.sh`.
- 04:00 UTC — **Pilot findings.** (1) Teacher gpt-5.6-luna (agent_memory_v2) on Docker :1, 10 train
  tasks/family: insurance 8/10, denial 3/10 (6 failures typed an exact *decoy* authorization from a
  letter that plainly says "for a different service (not this claim)"), compose 3/10, reschedule 0/10.
  (2) Reschedule root cause is a **world defect**, not the agent: tasks are generated relative to
  ANCHOR 2026-09-07 but Docker containers run on the real clock (2026-09-29), so the calendar opens
  weeks after the appointment (on Solari the VM clock restores from the Sept 15 snapshot). Fix
  requested: image `:3` with libfaketime at 2026-09-07 09:00 + a world-clock health check. Pilot
  results on `:1` for reschedule/compose are void as capability evidence. (3) Prompt
  `agent_memory_v3`: record a value only after checking the letter approves this claim's service.
  (4) Student serving is prefill-bound: ~6–7 s/step at 40 concurrent on 7 A100 replicas (two 1.5x
  screenshots ≈ 5.7k prompt tokens).
- 04:10 UTC — **Aux box** `forkloop-aux-0929a` (gpu_8x_a100 40 GB, us-west-2, $15.92/h,
  129.146.162.105, registry `lambda-instance-9c6d8f264c`, lease 24 h) + filesystem `forkloop-uswest2`
  (`lambda-filesystem-3ceb9e5846`): Qwen TP=2 ×4 serving + worlds. No 8×80 GB capacity was available.
