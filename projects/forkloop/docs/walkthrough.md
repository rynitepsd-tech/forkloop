# Walkthrough: improve your own computer-use agent with Forkloop

This takes a developer from a clean machine to a verified correction dataset for their own agent
and an evaluation of the retrained agent. Steps 1–2 need no account or key.

## 1. Install and check

```bash
git clone https://github.com/rynitepsd-tech/forkloop.git
cd forkloop/projects/forkloop
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e '.[world]'
pytest -q                                   # ~3 min, offline
```

## 2. See the whole loop offline (simulation)

```bash
forkloop demo-loop --out runs/demo-loop
open runs/demo-loop/evidence/index.html
```

What happened: a scripted toy "student" attempted four toy tasks, clicked the wrong counter at
step 1, and failed. `failures` located the damage (the first checkpoint where the verifier saw a
collateral edit) and proposed restarting just before it. `repair` restored that checkpoint twice —
world state by replaying the recorded prefix, agent state by restoring its explicit memory — and a
scripted "teacher" continued; the verifier accepted both branches. `dataset` exported the
teacher's steps with the exact inputs it saw and hashed every source file. Nothing here involves a
browser or a model: it is the mechanics only. Real recorded runs are in
[flagship-runs.md](flagship-runs.md).

## 3. Get a world

Bundled: the claims-operations world (real OpenEMR 8.3 + a synthetic payer portal, four task
families, SQL verifier). On any x86-64 Docker host:

```bash
worlds/claims_ops_v1/docker/build_image.sh --version 3          # ~4 min
export FORKLOOP_DOCKER_IMAGE=forkloop/claims-ops-v1:3 FORKLOOP_DOCKER_CONCURRENCY=16
forkloop run --backend docker --policy random --family resolve_denial --seed 10001 --split train_v2
```

On Solari instead (VM snapshots that preserve the running desktop): see
[solari-platform-notes.md](solari-platform-notes.md) and `configs/loop-solari-student.yaml`.

Your own application: write `worlds/<name>/world.yaml`, a `World` subclass, seeded task generators
and an oracle ([contracts.md](contracts.md) §4–6). [second-world.md](second-world.md) shows the
whole process for Kanboard.

## 4. Plug in your agent and a teacher

A project file names the world, backend, store, your agent and the teacher
(`configs/exp1.yaml` is a complete example). Your agent is either the built-in adapter for any
OpenAI-compatible endpoint:

```yaml
student:
  name: my agent
  policy: student
  system_prompt_file: ../forkloop/policies/prompts/agent_memory_v3.md
  options: {base_url: http://127.0.0.1:8000/v1, model: my-model, coord_space: norm1000, memory: true}
```

or your own Python class (`factory: my_package.agents:make_agent`, contract in
[`examples/loop_agents.py`](../examples/loop_agents.py)). The teacher uses the same observation
contract; the built-in example uses gpt-5.6-luna (`OPENAI_API_KEY`; paid calls reserve their worst
case in a session ledger, `forkloop ledger`).

## 5. Record, repair, export

```bash
forkloop record   --config my.yaml --role student --pool train --per-family 20 --experiment r1
forkloop failures --config my.yaml --experiment r1        # why each attempt failed, where to restart
forkloop repair   --config my.yaml --experiment r1        # k verified continuations per failure
forkloop dataset  --config my.yaml --experiment r1 --out datasets/r1
forkloop evidence --config my.yaml --dataset datasets/r1 --out evidence/r1
forkloop status   --config my.yaml                        # counts, unscored cells, cumulative charges
```

Every attempt, checkpoint, branch and charge is in the project's SQLite store; failed and
unscored work stays visible. Re-running a command resumes: finished cells are skipped, unscored
ones retried under the declared rule.

## 6. Train and evaluate

```bash
python -m train.train_lora --model <base> --dataset datasets/r1 --image-scale 1.5 ...   # or your trainer
forkloop evaluate --config my.yaml --pool final_test --final --set model=<adapter> --label mine --experiment eval
```

`evaluate` runs your agent alone — no teacher, no search, one attempt per task — and scores the
final application state with the same verifier. `forkloop/correction/analysis.py` gives
family-balanced success and paired comparisons.

## What to expect

- Checkpoint restores are verified: persisted tables must match the checkpoint and the screen must
  match within a small tolerance, or the branch is recorded as a failed restore.
- Teachers are not told why the attempt failed. Hidden task values never enter any prompt.
- Costs: see [operations.md](operations.md) and the experiment report for measured numbers.
