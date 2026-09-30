# Forkloop system overview

Updated September 30, 2026, after the correction engine and registered `exp1` experiment shipped
in [v0.3.0](https://github.com/rynitepsd-tech/forkloop/releases/tag/v0.3.0).

Forkloop turns recorded computer-use failures into verified training data, then evaluates the
retrained student alone on held-out tasks. It also supports matched policy regression tests.
This document describes the current implementation and the evidence for it. Interface details
are in [contracts.md](docs/contracts.md) and [correction.md](docs/correction.md); experiment
numbers come from the [generated results](docs/results-exp1.md) and
[final report](docs/final-report-20260929.md).

The previous module-by-module reference, including the Fara training ladder and September 7–22
status entries, is preserved in [the historical system reference](docs/archive/system-through-20260922.md).
Its current-state claims, prices, resource inventory and proposed next steps are historical.

## 1. Current state and evidence

The implemented workflow is:

`record` → `failures` → `repair` → `dataset` → train → `evaluate` → `evidence`

Checkpoints bind world state to agent state. A teacher continues from a restored checkpoint on
independent machines; database checks decide which paths can enter the dataset. The final
student evaluation has no teacher or branch search. The project includes Docker, Solari and
fake backends, plus two real application worlds.

| Capability | Evidence as of September 30 |
| --- | --- |
| Correction engine and CLI | Offline contracts and toy demo; recorded real repairs in OpenEMR/payer portal and Kanboard |
| Solari VM checkpoint repairs | Six student failures, four repaired: two from step-8 VM snapshots and two from full restarts. Four branches restored from two mid-episode snapshots in 79 seconds each, with equal checked tables and zero screen distance |
| Second world through the public interface | Kanboard: five verified repairs; all 29 measured restores had equal checked tables. No student-learning result is claimed for Kanboard |
| Student training and held-out evaluation | Completed exp1: 150 distinct final-test tasks, 1,650 counted evaluation cells, every cell scored |
| Policy regression testing | Earlier registered model-only comparison: 23/24 versus 4/24 verified completions on its specific agent and denial workflow |
| Publication | PR #3 merged; project site, video and release v0.3.0 published September 30 |
| Challenge submission | **Outstanding.** The owner confirmed on September 30 that it has not been submitted |

Recorded evidence: [flagship runs](docs/flagship-runs.md),
[exp1 repair](docs/evidence/exp1/index.html),
[Solari repair](docs/evidence/solari-flagship/index.html),
[Kanboard repair](docs/evidence/kanboard/index.html).

### What the learning experiment establishes

The student was Qwen3.8-27B with LoRA. A0 and S_W each contributed 150 cells; A1, A2 and A3
each contributed three training runs × 150 tasks. Results are family-balanced, not the raw
fraction of successful cells. Infrastructure replacements remain in the attempt records;
1,650 is the final scored-cell denominator, not the count of all attempts.

| Model / training data | Family-balanced success |
| --- | ---: |
| A0: untrained | 3.3% |
| S_W: shared warm-start demonstrations W only | 20.8% |
| A1: W + teacher demonstrations from initial states | 26.8% |
| A2: W + Forkloop corrections of student failures | 26.0% |
| A3: W + full-restart repairs of student failures | 26.9% |

**The registered hypothesis that corrections outperform demonstrations at matched collection
cost was not supported.** A2 − A1 was −0.008, with 95% CI [−0.042, +0.022]. Most of the
improvement over the untrained student came from shared warm-start data. The correction loop
works, but this experiment demonstrated neither better learning nor lower collection cost than
the comparison methods. None of the evaluated models solved the rescheduling family, and the
teacher supplied no verified training paths for it.

The matched budgets cover **counted work**, excluding voided collection work under a dated
protocol deviation. Recorded all-work totals were A1 $29.53, A2 $374.63 and A3 $270.58;
correction data cost roughly an order of magnitude more per verified path. A2's selected
dataset contained only 21 paths from 10 tasks, compared with A1's 78 paths from 78 tasks.
Intervals are conditional on one collected dataset per arm. Replay failures, inherited branch
time budgets and collection-selection effects limit generalization. The proposed half-cost
learning criterion was not tested.

Sources: [registered protocol and deviations](docs/protocol-learning-experiment.md),
[results and sensitivity analyses](docs/results-exp1.md),
[two independent reviews](docs/reviews/), [final interpretation](docs/final-report-20260929.md).

### Separate regression evidence

The September 24 comparison changed only the model from `gpt-5.6-luna` to `gpt-6-luna` in the
same agent. Verified success fell from 23/24 to 4/24; all 19 discordant pairs favored the older
model (exact two-sided McNemar p = 3.8 × 10⁻⁶). The newer model submitted 17 appeals, of which
13 had incorrect authorization numbers despite the portal's success banner. This demonstrates
the value of checking persisted effects for that workflow; it is not a general ranking of the
models. See [the paired report and its sensitivity checks](docs/live-model-upgrade-comparison.md).

## 2. Architecture and module map

Paths below are relative to `projects/forkloop/`. The correction engine uses the same environment,
world, recorder and verifier as the earlier evaluation product.

| Module / directory | Responsibility |
| --- | --- |
| `correction/project.py` | Versioned YAML for world, backend, store, policies, budgets, checkpoints and repairs |
| `correction/store.py` | SQLite identities and lifecycle rows for tasks, policies, attempts, checkpoints, repairs, branches, datasets and charges |
| `correction/runner.py` | Plans cells, skips finished work, applies replacement rules, heartbeats and recovers interrupted runners |
| `correction/record.py` | Runs an attempt through Env and captures evidence/checkpoints at configured boundaries |
| `correction/checkpoint.py` | Binds world reference/digest, policy state and trajectory counters; stores screenshot blobs by hash; classifies checkpoint state |
| `correction/digest.py` | Checked-table hashes and masked screen thumbnail for restore comparison |
| `correction/diagnose.py` | Restart points from near-miss values, damage, loops, latest clean state and step 0 |
| `correction/restore.py` | VM restore or reset plus recorded-prefix replay; reinstates counters/history and checks fidelity |
| `correction/repair.py` | Independent teacher continuations, per-branch verification and counted/unscored repair rules |
| `correction/dataset.py` | Verified steps, preference pairs, controller diagnostics, images and provenance manifests |
| `correction/budget.py` | Collection-unit selection under declared rates and counted-work/all-work rules |
| `correction/analysis.py` | Family-balanced outcomes, paired task/training-run bootstrap intervals and sign tests |
| `correction/evidence.py`, `correction/inspect_html.py` | Static HTML inspection of attempts, repairs, datasets and lineage |
| `correction/cli.py` | Correction workflow, evaluation, analysis, evidence, budget, offline demo and cleanup commands |
| `backends/{base,fake,docker,solari}.py` | Machine contract and backend implementations |
| `env.py`, `reset.py`, `pool.py` | Episode lifecycle, task reset, budgets, worker ownership and cleanup |
| `world.py`, `tasks.py`, `seed.py`, `observe.py` | World loading, task generation, seeding and observations |
| `oracle.py`, `dbaccess.py`, `util/` | SQL effects/invariants, checksums, audit checks, SQL/PDF helpers |
| `policies/`, `policy_config.py` | Agent adapters, explicit memory, observation rendering, action parsing and policy identities |
| `splits.py` | Training/validation/final-test pools and guards against final-test training data |
| `comparison.py`, `comparison_html.py`, `report.py`, `report_html.py` | Matched A/B evaluation and episode/comparison reports |
| `trajectories.py`, `exporters/`, `metrics.py`, `fixed_metrics.py` | Trajectory files, legacy exports, execution metrics and saved-observation scoring |
| `search.py` | Legacy best-of-N search; separate from the correction loop and exp1's student-only evaluator |
| `spending.py`, `ops/` | Provider reservations, resource registry, leases, inventory and cleanup |
| `actions.py`, `types.py`, `cli.py`, `doctor.py`, `controls.py`, `bench/` | Shared contracts, command routing, diagnostics, offline controls and reset/cost benchmarks |

Rows above without a `forkloop/` prefix refer to modules inside `forkloop/`. Other top-level paths:

- `worlds/claims_ops_v1/`, `worlds/kanboard_v1/`, `worlds/toy_counter/`: application worlds.
- `train/`, `train/serve/`: LoRA training, input parity, saved-state probes and vLLM serving.
- `configs/`, `examples/loop_agents.py`: experiment configurations and custom-agent interface.
- `scripts/exp1/`: collection/training/evaluation orchestration, reports and release packaging.
- `tests/`, `docs/`: offline checks, contracts, protocols, dated evidence and reviews.

## 3. Information boundaries and checkpoint state

| | Agent channel | Controller channel |
| --- | --- | --- |
| Receives | Instruction, screenshots, action history and the policy's explicit memory | Task manifests, expected values, SQL rows, verifier results and infrastructure state |
| Can do | Screenshot, click, type, key, scroll, drag, wait, done | Seed, execute setup, read databases, restore, verify, account and clean up |
| Used by | Student or teacher acting in the GUI | Environment, correction engine and evaluator |

Expected values and diagnostic reasons are not rendered into policy prompts. The controller can
use hidden task facts to choose a restart point, but the teacher receives `feedback: none` and
adopts only the checkpoint's declared agent state. The teacher is not told why the student failed.
Values intentionally present in a task instruction are public; hidden values must be read from
screenshots or remembered from the policy's earlier outputs.

`StudentPolicy` maintains explicit `Memory:` facts, screenshots, history and queued actions.
`agent_state()` / `load_agent_state()` expose the state another policy may adopt. Full snapshots
preserve declared local policy state. Usage accounting does not rewind with policy state.

A checkpoint binds the world strategy/reference, checked table hashes, screen thumbnail,
policy identity/state, explicit memory, history, step, charged-action count, invalid count and
elapsed time. Step 0 is a fresh seeded reset. Boundaries include periodic steps, typing and
selected key presses; a cap limits provider snapshots.

Attempts and branches are recorded before machine preparation. Stable cell identities and new
attempt numbers preserve retries without rerunning a finished cell for a better score. Dead
runners' unfinished rows become `interrupted`. Exp1's special operator-stop and provider-outage
annotations remain in the stores and dated protocol.

A repair can try several restart points, including step 0. Thus checkpoint mode can export both
`correction_suffix` and `restart_demo` records. Under exp1's replacement rule, any unscored
branch voids the whole repair and the first clean repair counts; this can select on branch
length/outcome. It does not erase the earlier branch evidence.

Dataset audits check memory folding and report hidden-value occurrences, with limits: the first
step's own memory seeds the audit rather than independently proving the branch boundary, and a
hidden-value count does not establish its source. Review 1 checked concrete recorded boundaries.
See [the correction contract](docs/correction.md) and [review 1](docs/reviews/review-1.md).

## 4. Worlds, backends and verification

`Env.reset()` uses `WorkerPool` and `ResetController` to prepare a machine, seed the task, run
world preparation and health/feasibility checks, capture baseline hashes/audit watermarks, and
open the initial screen. A successful VM restore alone does not establish a successful reset.
The correction runner uses independent machines for cells and branches.

| Backend / strategy | What restoration preserves |
| --- | --- |
| Solari VM snapshot | Running desktop memory and disk; the flagship demonstrated mid-episode restores with matching checked state |
| Docker filesystem snapshot | Flushed filesystem/database state; services restart, so this is not a running-browser checkpoint |
| Recorded-prefix replay | Resets and repeats recorded GUI actions, then compares persisted tables and screen state |
| Fake backend | Local simulated state and labelled synthetic screens for offline tests/demos |

Every correction restore checks its digest against the checkpoint: table hashes must match and
screen distance must meet the configured threshold (0.10 in exp1). Failures are recorded as
`restore_failed`, with bounded retries. Replay is not guaranteed deterministic: **53% of 1,608
exp1 replay restores passed**, versus 85% of 1,120 full-restart resets. Digest equality does not
establish equality of all application or process state.

The Docker backend uses containers, an in-container helper, Xvfb screenshots and xdotool input.
The Solari backend handles desktop creation, snapshots and control-channel reconnection. See
[Docker qualification](docs/docker-world.md) and [Solari platform notes](docs/solari-platform-notes.md).

**Claims operations** combines real OpenEMR 8.3/MariaDB with a synthetic FastAPI payer portal
using SQLite. Exp1's four families are `resolve_denial`, `update_insurance_reconcile`,
`reschedule_constrained` and `compose_claims`. V2 variations include prior rejected appeals,
mistyped prior resubmissions and occupied appointment slots. Compositions pair subtasks on the
same patient or a household. Older generators remain frozen for historical comparisons;
`resolve_denial_easy` is a separate diagnostic variant.

`splits.py` and `worlds/claims_ops_v1/tasks/splits_manifest.json` define pools and held-out
structures. `train_v2`, `val_v2` and `final_test` are distinct generator streams. Export and
training reject final-test sources. Exp1's 150 tasks contain 60 compositions and 30 tasks from
each other family; the metric weights each family equally. See [tasks and splits](docs/tasks-and-splits.md).

**Kanboard** wraps version 1.2.54 through `world.yaml`, a `World` subclass, seeded generators and
an SQL oracle. Its families are `move_and_assign` and `due_and_comment`. It demonstrates world
integration and verified repairs, not cross-world student generalization. See [second-world qualification](docs/second-world.md).

**Toy counter** is a Pillow-rendered simulator backed by SQLite. Scripted agents exercise the
record/repair/export workflow without accounts or model calls.

`oracle.py` checks effects/invariants using task SQL, baseline checksums and audit evidence:
right record/value, exact-one effects, configured collateral edits, UI paths and forbidden
screens. A portal success banner or a policy's `done` does not establish success. Missing
infrastructure/oracle evidence remains unscored rather than becoming a pass or model failure.
The verifier covers selected tables and effects; it does not establish global application
safety. OpenEMR audit checks are coarse and forbidden-screen tracking is portal-only.
[Verifier coverage and gaps](docs/verifier.md) document the limits.

## 5. Policies, datasets, training and evaluation

Custom agents implement `async act(observation) -> (action, metadata)`; optional declared state
supports checkpoint handoff. `policy_config.py` records model/options, prompt hashes and
constructor identity. `StudentPolicy` handles OpenAI-compatible endpoints, including vLLM and
the hosted exp1 teacher. `TeacherPolicy` is the separate Anthropic adapter. Scripted, random
and callback policies support controls and tests.

The frozen [exp1 config](configs/exp1.yaml) uses Qwen3.8-27B, a `gpt-5.6-luna` teacher,
`agent_memory_v3`, explicit memory, paired screenshots and 12-action history. Student rendering
uses normalized coordinates, 1.5× image scale and a 1920-pixel maximum side, with thinking
disabled. Episode budgets are 120 actions and 3,600 seconds. These settings differ from earlier
qualification recipes; reproduction must use the frozen config and released training arguments.

Provider/transport errors are distinct from invalid model actions. Self-hosted requests have
bounded transport retries. Hosted retries preserve uncertain reservations and reserve each new
send; received quota-exhaustion refusals fail fast. Memory and observation rendering are shared
with the dataset/training path.

| Dataset artifact | Purpose |
| --- | --- |
| `records.jsonl` | Valid acted steps from verified paths, with input/target and origin: correction, restart demo or initial-state demo |
| `preferences.jsonl` | Chosen/rejected evidence; not SFT input |
| `diagnostics.jsonl` | Controller-only restart reasons and outcomes; never policy input |
| `images/` | Content-addressed screenshots of the recorded path |
| `manifest.json` | File/source hashes, dataset identity, selection, splits and audits |

`train/train_lora.py --dataset` validates hashes and rejects final-test records before model
loading. It supports dataset mixtures, language-model LoRA, masked assistant targets, explicit
memory and training metadata. `train/parity.py` checks training/serving token, image and mask
agreement. `train/serve/` contains the pinned serving stack and adapter loading scripts; probe
tools inspect saved observations separately from live navigation.

Exp1 trained S_W on W for 50 optimizer steps. Each of three runs per A1/A2/A3 trained on W plus
its arm's data for 100 steps, sampling 800 examples. Arm runs differ in the amount/composition
of arm-specific data seen, as well as collection method. The main analysis includes all three
runs; the report also gives sensitivity to A3 seed 3, which trained on a different GPU host.

`forkloop evaluate` runs the student alone. `correction/analysis.py` pairs outcomes by task and
reports family-balanced success with bootstrap uncertainty over tasks and training runs.
`scripts/exp1/report.py --final` and `report_extra.py` generate results and sensitivities from
the stores. The release includes datasets, adapters, stores, attempt-level `cells.csv`,
checksums and `REPRODUCE.md`.

The Fara 4B ladder and saved-observation v3 gains are historical evidence, distinct from exp1's
live evaluation. Legacy `train/eval.py` and best-of-N `search.py` remain research utilities;
they are not the exp1 final evaluator. See [training](train/README.md),
[student qualification](docs/student-qualification.md) and the historical system reference.

## 6. Commands and evidence inspection

| User path | Commands / entry points |
| --- | --- |
| Offline correction demo | `forkloop demo-loop --out runs/demo-loop`, then open `evidence/index.html` |
| Real correction workflow | `record`, `failures`, `repair`, `dataset`, `evaluate` with project YAML |
| Inspect and analyze | `status`, `inspect`, `evidence`, `budget`; exp1 analysis through `scripts/exp1/report.py` |
| Matched regression test | `compare`, `compare-report` with versioned policy configs |
| Earlier episode workflows | `run`, `collect`, `export`, `metrics`, `report`, `demo` |
| Setup and operations | `doctor`, `build-world`, `reset-bench`, `ledger`, `ops`, `reap`, `reap-machines`, `cleanup` |

`report_html.py` and `comparison_html.py` render episode/comparison evidence;
`correction/evidence.py` renders the correction loop. HTML is static and script-free.
Bundling/cropping does not guarantee anonymization of screenshots or free text. All included
patient and claims examples are synthetic.

The offline install/demo is in the [project README](README.md). Real-run configs such as
`configs/exp1.yaml` contain experiment-specific endpoints, paths and concurrency; adapt them to
a prepared environment. A Docker image, model endpoint and teacher access are separate from
installing the Python package.

## 7. Operations, accounting and publication

`spending.py` manages provider reservations/reconciliation. `ops/registry.py` records ownership
and leases; `ops/lambda_cloud.py` manages Lambda lifecycle and ambiguous-create reconciliation.
Runner heartbeats and `reap-machines` identify orphaned worlds. `ops reap` handles expired
resource leases; `cleanup` handles eligible checkpoint snapshots. Solari idle timeouts are not
used as the sole lifetime bound.

Correction-store charges are append-only but written at attempt/branch completion, including
handled errors. A hard-killed process can lose in-flight charge records while its interrupted
attempt remains visible. Provider reservations, counted experiment cost, recorded all-work cost
and invoices are different quantities. Unknown billing remains uncertain, not actual spend.

The **recorded September 30, 16:50 UTC inventory** reported no running Lambda instances or
Solari machines. Three Lambda filesystems and the pre-existing Solari golden were retained;
all 30 flagship checkpoint snapshots were deleted. This is a dated cleanup record, not a live
provider query. Reported program cost was approximately $1,664, excluding ongoing filesystem
storage and subject to authoritative invoices. See [final report §§4–6](docs/final-report-20260929.md)
and [the execution ledger](docs/execution-ledger.md).

Code, site, video and release are published. **Posting and challenge submission remain owner
actions, explicitly outstanding as of September 30.** Storage-retention decisions also remain
with the owner. This documentation update does not perform a submission or change resources.
See [operations](docs/operations.md) for the operational mechanics.

## 8. Validation and known limits

The September 30 final report records a passing offline suite (669 tests, one skipped). CI in
`.github/workflows/forkloop-tests.yml` at the repository root installs Python 3.11 dependencies,
runs pytest and exercises offline consumer paths. Tests cover correction, Docker, explicit
memory, parity, dataset guards, task splits/compositions, adversarial verifier cases, Kanboard,
operations and release packaging. Fake/mocked tests do not measure live model reliability.
The offline correction demo was also exercised during the September 30 documentation review;
it remains a labelled simulation.

Material open limits of the shipped system and experiment include:

- **Replay reliability:** 53% of measured exp1 restores passed fidelity. Digest equality does
  not establish complete process-state identity.
- **Repair budgets:** checkpoint branches inherit elapsed time, leaving deep restarts less
  time than full restarts. Step-0 fallbacks add tries. The like-for-like analysis found five
  checkpoint-only versus three restart-only repairs (p = 0.73).
- **Scoring and selection:** counted-work budgets exclude voided work; whole-repair voiding
  can depend on branch length/outcome. Docker's `ctrl+-` key-name failure is triggered by policy
  actions and was retained through exp1 for consistent conditions.
- **Coverage:** no verified rescheduling training paths, no successful final rescheduling
  episodes, and one small collected dataset per trained arm.
- **Restart diagnosis:** the damage classifier can miss wrong writes to fields already
  populated at reset; restart selection is heuristic.
- **Durability and provenance:** hard kills can lose in-flight charges; read-only files and
  hash manifests are not tamper-proof storage; memory audits have the boundary limits in §3.
- **Verifier scope:** only configured effects, tables and audit checks are covered; broader
  safety and production readiness are not established.

These limits and dated protocol changes remain part of the result. The published outcome is
the completed system and an experiment that did not establish the proposed correction advantage.
