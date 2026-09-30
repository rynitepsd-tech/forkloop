# Forkloop

**Turn your computer-use agent's failures into verified training data, then check whether the
retrained agent actually does better on tasks it has never seen.**

Your agent attempts a workflow in real software. Forkloop records enough state to reproduce the
attempt. When the agent fails, Forkloop restores an earlier checkpoint — world and agent memory —
lets a teacher try alternative continuations on independent copies, verifies each one against the
applications' databases, and exports the steps of the verified paths as a provenance-preserving
dataset. You train on it; `forkloop evaluate` then runs your agent alone on held-out tasks.

**What we measured** ([registered experiment](docs/protocol-learning-experiment.md),
[results](docs/results-exp1.md)): on 150 held-out tasks in real OpenEMR and a payer portal, training a
27B open-weights student on Forkloop's verified corrections of its own failures raised it from 3% to
26% family-balanced success. Teacher demonstrations of the same collection cost did as well (27%), as
did full-restart repairs (27%), and most of the gain came from shared warm-start data. The registered
hypothesis — that corrections beat demonstrations at matched cost — was **not supported**, and
correction data cost far more per verified path. The loop itself works; the report says exactly where it
did not.

## What you install, supply, run and receive

| | |
| --- | --- |
| **Install** | `pip install -e '.[world]'` (Python 3.11). For live runs: Docker (the bundled OpenEMR + payer-portal world) or a Solari account (VM snapshots). |
| **Supply** | Your agent (any object with `async act(observation) -> (action, metadata)`; built-in adapter for OpenAI-compatible endpoints such as vLLM), a teacher (e.g. an OpenAI model key), and a world (bundled, or yours through `world.yaml` + a verifier). |
| **Run** | `forkloop record` → `forkloop failures` → `forkloop repair` → `forkloop dataset` → train → `forkloop evaluate` |
| **Receive** | Verified correction datasets with sha256 lineage to every source trajectory, preference pairs, per-branch evidence, regression cases, and a paired evaluation of the retrained agent. |

## Two minutes, no account, no key

```bash
git clone https://github.com/rynitepsd-tech/forkloop.git
cd forkloop/projects/forkloop
python3.11 -m venv .venv && . .venv/bin/activate && pip install -e '.[world]'
forkloop demo-loop --out runs/demo-loop     # the whole loop on a toy world (offline simulation)
open runs/demo-loop/evidence/index.html
```

The toy world is a labelled simulation (synthetic screens, scripted agents): it shows the
mechanics of recording, checkpoints, restart points, independent branches, verification and the
immutable dataset. Recorded evidence from real runs needs no key either:
[exp1 (Docker)](docs/evidence/exp1/index.html) · [Solari desktops](docs/evidence/solari-flagship/index.html) ·
[Kanboard](docs/evidence/kanboard/index.html).

## The loop on real software

```bash
# a project file names the world, the backend, your agent (student) and the teacher
forkloop record  --config configs/exp1.yaml --role student --pool train --per-family 20 --experiment round1
forkloop failures --config configs/exp1.yaml --experiment round1     # restart points and why
forkloop repair  --config configs/exp1.yaml --experiment round1      # k verified continuations per failure
forkloop dataset --config configs/exp1.yaml --experiment round1 --out datasets/round1
python -m train.train_lora --dataset datasets/round1 ...             # or your own trainer
forkloop evaluate --config configs/exp1.yaml --pool final_test --final --set model=<adapter> --experiment eval
forkloop evidence --config configs/exp1.yaml --out evidence/        # shareable HTML, no scripts
```

- **Checkpoints bind world and agent state.** World: provider VM snapshot (Solari: memory and
  disk of the running desktop) or replay of the recorded prefix (any backend; not guaranteed deterministic —
  in exp1 53% of 1,608 replay restores passed the fidelity check, the rest were retried or left unscored), each
  restore checked against a digest of persisted tables and the screen. Agent: its explicit memory,
  history, counters and identity. A failed restore is recorded as unscored, never as a failure.
- **Restart points come from evidence**: the first near-miss of a value the verifier requires,
  the first damaged checkpoint, the start of a loop, the latest clean checkpoint, or step 0.
- **Information boundary.** Teacher and student see only screenshots, the instruction, their own
  action history and explicit memory. The teacher is never told why the attempt failed.
- **Verified means the database agrees**: effects and invariants in SQL (right record, right value,
  no duplicate, no collateral edit, UI path only, no forbidden screens). No LLM judges.
- **Accounting never rewinds.** Model calls, machine-seconds, snapshots and replays are append-only
  charges written when an episode or branch ends (a process killed hard or by SIGHUP loses its
  in-flight charges; its rows become `interrupted`); datasets are immutable with sha256 manifests.

## Bring your own agent, teacher or world

- Agent: `examples/loop_agents.py` documents the contract (optional `agent_state()` lets a teacher
  adopt your agent's memory at a checkpoint).
- World: `worlds/<name>/world.yaml` + a `World` subclass + seeded task generators + an oracle.
  `worlds/kanboard_v1` is a second, compact world built only through that interface.
- Backends: Docker (`forkloop/backends/docker.py`, dozens of worlds per machine), Solari desktops,
  and an in-process fake for tests.

## Results

The controlled experiment (`exp1`, protocol registered before collection, dated deviations,
two independent reviews, 1,650 evaluation cells all scored):

| Student (Qwen3.8-27B, LoRA) | Family-balanced success | Difference vs A2 (95% CI) |
| --- | ---: | --- |
| A0 untrained | 3.3% | A2 − A0 = +0.226 [+0.165, +0.290] |
| S_W warm start only | 20.8% | |
| A1 teacher demonstrations (matched cost) | 26.8% | A2 − A1 = −0.008 [−0.042, +0.022] |
| **A2 Forkloop corrections** | **26.0%** | |
| A3 full-restart repairs (matched cost) | 26.9% | A2 − A3 = −0.010 [−0.064, +0.054] |

- Corrections were not better than demonstrations or full restarts at matched collection cost; the
  registered effect criterion is not met.
- Counting all work, correction data cost an order of magnitude more per verified path ($374.63 and
  $270.58 against $29.53 for demonstrations), and restarting from an evidence-chosen checkpoint repaired
  no more failures than restarting from the beginning with the same number of tries (5 vs 3, p = 0.73).
- No model solved any rescheduling task (the teacher could not either).

Full numbers, sensitivity analyses and every limitation: [final report](docs/final-report-20260929.md),
[results](docs/results-exp1.md), [independent reviews](docs/reviews/). Evidence bundles:
[exp1](docs/evidence/exp1/index.html) · [Solari VM snapshots](docs/evidence/solari-flagship/index.html) ·
[Kanboard](docs/evidence/kanboard/index.html).

## Documentation

[docs/correction.md](docs/correction.md) (the loop's contract) ·
[docs/protocol-learning-experiment.md](docs/protocol-learning-experiment.md) ·
[docs/verifier.md](docs/verifier.md) · [docs/tasks-and-splits.md](docs/tasks-and-splits.md) ·
[docs/docker-world.md](docs/docker-world.md) · [docs/operations.md](docs/operations.md) ·
[docs/student-qualification.md](docs/student-qualification.md) ·
[docs/solari-platform-notes.md](docs/solari-platform-notes.md) · [docs/contracts.md](docs/contracts.md)

Earlier results (model-selection evidence, regression comparisons) are in
[docs/README.md](docs/README.md). Forkloop does not claim to have invented snapshots, GUI
benchmarks, deterministic verification, agent debugging or corrective training; it puts them into
one working loop. All patient and claims data is synthetic. MIT.
