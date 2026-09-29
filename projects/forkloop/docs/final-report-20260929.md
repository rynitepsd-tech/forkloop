# Forkloop correction loop: final report (2026-09-29/30)

Status of every deliverable, what worked, what did not, what it cost, and what is still running or
retained. Numbers come from the stores, ledgers and generated reports named in each line; anything
not yet measured says so. <!-- PENDING sections are filled only from generated outputs. -->

## 1. What was built

| Deliverable | State | Where |
| --- | --- | --- |
| Correction engine: record → failures → repair → dataset → evaluate → evidence, API and CLI | Working; offline test suite passes (`pytest`, 660 tests) | `forkloop/correction/`, `docs/correction.md` |
| Bound checkpoints (world + agent state) | VM snapshots on Solari; deterministic replay on Docker and any backend; every restore fidelity-checked (table digests + screen distance) | `checkpoint.py`, `restore.py` |
| Evidence-based restart points | origin (near-miss of a required value), damage, stall, latest clean, start | `diagnose.py` |
| Qualified world: claims-ops-v1 | Four families (denial, insurance, rescheduling, compositions), SQL verifier, Docker image `forkloop/claims-ops-v1:3` with the world clock at the task anchor | `docs/docker-world.md`, `docs/verifier.md`, `docs/tasks-and-splits.md` |
| Second world through the public interface | Kanboard v1.2.54, two families, SQL oracle | `worlds/kanboard_v1`, `docs/second-world.md` |
| Controlled learning experiment (A0, S_W, A1 demonstrations, A2 Forkloop, A3 full restart; matched cost; 3 runs per arm; fresh final test; paired statistics) | PENDING (running) | `docs/protocol-learning-experiment.md`, `docs/results-exp1.md` |
| Infrastructure: leases, reaper, accounting, cleanup | Registry with leases (`forkloop ops`), runner heartbeats and orphan-world reaping, append-only charges, cleanup verified against provider listings | `docs/operations.md` |
| Inspection interface | `forkloop status/inspect/evidence` (static HTML bundles, no scripts) | `docs/evidence/` |
| README, project site, video, release | PENDING | |
| Independent review | Review 1 done (1 blocker, 5 majors; fixed except M4/M5, documented as limitations); review 2 PENDING | `runs/review/` (local) |
| Submission copy | Drafts prepared, not sent | `~/Desktop/Solari/posts/2026-09-29/` |

## 2. What worked (measured)

- **Solari flagship** (`docs/flagship-runs.md`): 6/6 student failures; 4 repaired, 2 of them from
  step-8 VM snapshots (79 s restores, all checksummed tables equal, screen distance 0); dataset
  `ds-fc98852a715d` (425 records); all 30 checkpoint snapshots deleted afterwards.
- **Kanboard** (second world): 5 of 8 repairs verified, 29/29 restores with equal tables; dataset
  `ds-a504e6fca3fb` (136 records); OpenAI $0.63.
- **exp1 collection** (training pool only): teacher warm start W (`ds-ff04067bf950`, 2,425
  records); `S_W` trained (50 steps, loss 1.84 → 0.58); round 1 of `S_W` on 160 training tasks:
  39 OK (denial 29/40, insurance 6/40, compose 4/40, reschedule 0/40), all 160 cells scored after
  replacements; 121 failures sent to both repair modes. PENDING: repair yields, matched-cost
  datasets, training runs, final evaluation.

## 3. What did not work, or not fully

- The registered teacher (gpt-5.6-luna) cannot solve `reschedule_constrained` from the initial
  state (0/20 warm start, 0/40 demonstrations): no verified rescheduling data for any arm.
- Solari: "Snapshot not found" on fork for 12 flagship branches (fallback to step 0); snapshot
  deletes reported success without deleting until a second round; restores are bimodal (≈22 s or
  70–160 s).
- Docker `commit` snapshots are filesystem-only, so Docker checkpoints use deterministic replay.
- vLLM transport `ReadError`s (~3 per student episode) made round-1 episodes unscored until a
  self-hosted transport retry (`3a02943`).
- Operator errors: killing the phase chain's tmux session interrupted 37 round-1 attempts
  (kept, replaced under the registered rule); an evaluation run under pre-review scoring
  (`exp1-final`) was voided unread.
- A0/`S_W` were not evaluated interleaved with the trained models on aux (deviation, 09:00); on
  main, two early starts were stopped unread (server latency vs the episode time limit; Docker world
  cap), which used 2 of 3 attempts for ~56 cells.
- Not done by this session (permission checks): pushing branch `correction-engine-20260929`, reading
  `~/.forkloop/reap.log`, `launchctl print`, and a restart of the checkpoint-repair process at
  higher concurrency.

## 4. Results of the learning experiment

PENDING — generated `docs/results-exp1.md` (all planned cells run before any outcome is read).

## 5. Spend

PENDING — OpenAI (session ledgers), Solari (reservation upper bound and billing), Lambda (instance
hours × list price; the invoice is authoritative).

## 6. Resources remaining

PENDING — final `forkloop ops inventory`, Solari listing, what is retained and why.

## 7. Actions left for the owner

- Push branch `correction-engine-20260929` and merge; publish the release and the site if this
  session could not.
- Posting and the challenge submission (drafts in `~/Desktop/Solari/posts/2026-09-29/`).
