# Forkloop training ladder

Everything here consumes the trajectory layout of `docs/contracts.md` §10 and
drives the Env API of §11. Run every command from `projects/forkloop/` with
`PYTHONPATH=.` (or `pip install -e .[train]`).

| File | Purpose |
| --- | --- |
| `make_sft.py` | verified trajectories (`reward == 1.0`) -> `sft.jsonl` (one record per step, compact-form target) |
| `train_lora.py` | LoRA SFT with transformers + peft for Fara 1.5 / Qwen3.5-VL students |
| `eval.py` | held-out evaluation through `forkloop.make`, Wilson CIs, optional best-of-N |
| `plot.py` | chart 1 (learning curve) and chart 2 (reset benchmark); `--demo` renders synthetic placeholders |
| `bakeoff.py` | candidate-model bake-off table (`bakeoff.md`) |
| `wilson.py` | Wilson score interval helper |
| `../forkloop/policies/student.py` | the student policy (OpenAI-compatible endpoint, vLLM) |
| `../forkloop/policies/action_parse.py` | compact / JSON / Fara tool-call parsers + coordinate scaling |

## The rungs

### Rung 0 — bake-off (pick the student)

Serve each candidate with vLLM on its own port, then:

```bash
vllm serve microsoft/Fara1.5-4B --port 8001 --dtype auto --max-model-len 16384
vllm serve Qwen/Qwen3.5-VL-4B-Instruct --port 8002 --dtype auto --max-model-len 16384

PYTHONPATH=. python -m train.bakeoff --example-config > bakeoff.json   # edit endpoints
PYTHONPATH=. python -m train.bakeoff --config bakeoff.json --out-dir bakeoff
```

`bakeoff/bakeoff.md` lists base success, action-format validity, tokens/step,
train seconds/step and VRAM for each candidate. Pick the highest base success
with validity >= 95% that trains on your card. Fara 1.5 speaks its own
tool-call format (`--prompt-style fara`, coordinates in a 1000x1000 space);
Qwen-VL and UI-TARS work with `--prompt-style compact` or `json`.

### Rung 1 — verified data + best-of-N (no training)

1. Collect teacher trajectories into `runs/<run_id>/` (the teacher/recorder are
   outside this directory). Only episodes with `verdict.json: reward == 1.0`
   are ever used for training.
2. Baseline the student and the student with search on held-out seeds:

```bash
PYTHONPATH=. python -m train.eval --world claims-ops-v1 \
  --families resolve_denial,update_insurance_reconcile,reschedule_constrained \
  --split heldout_seeds --n-episodes 50 --n-seeds 3 --concurrency 8 --backend solari \
  --policy student --base-url http://localhost:8001/v1 --model microsoft/Fara1.5-4B \
  --prompt-style fara --tag base --checkpoint base

PYTHONPATH=. python -m train.eval ... --best-of 4 --tag base_bo4 --checkpoint base   # same args + search
```

Each tag writes `evals/<tag>/episodes.jsonl` and `evals/<tag>/eval_summary.json`
(`success.rate/low/high` is the Wilson 95% CI, `n = tasks x n_seeds`).

### Rung 2 — LoRA SFT on 25 / 50 / 100 / 200 / all verified trajectories

```bash
for N in 25 50 100 200; do
  PYTHONPATH=. python -m train.make_sft --run-dir runs/teacher_v1 --exclude-split 'heldout_*' \
    --limit $N --out data/sft_$N.jsonl
done
PYTHONPATH=. python -m train.make_sft --run-dir runs/teacher_v1 --exclude-split 'heldout_*' --out data/sft_all.jsonl
```

`--limit` takes the first N episodes in `task_id` order, so the subsets nest.
Then, per checkpoint:

```bash
N=200
PYTHONPATH=. python -m train.train_lora --model microsoft/Fara1.5-4B --data data/sft_$N.jsonl \
  --output-dir ckpt/fara_sft_$N --prompt-style fara --epochs 2 --lr 1e-4 \
  --lora-r 16 --lora-alpha 32 --lora-dropout 0.05 --batch-size 1 --grad-accum 8 \
  --max-image-side 1280 --save-steps 200 --merge-out ckpt/fara_sft_$N/merged

vllm serve ckpt/fara_sft_$N/merged --port 8011 --served-model-name fara-sft-$N --dtype auto
PYTHONPATH=. python -m train.eval ... --base-url http://localhost:8011/v1 --model fara-sft-$N \
  --tag sft_$N --checkpoint sft_$N
PYTHONPATH=. python -m train.eval ... --base-url http://localhost:8011/v1 --model fara-sft-$N \
  --best-of 4 --tag sft_${N}_bo4 --checkpoint sft_$N
```

(`vllm serve microsoft/Fara1.5-4B --enable-lora --lora-modules sft=ckpt/fara_sft_$N/final`
serves the adapter without merging; then `--model sft`.)

Assemble the learning-curve JSON from the summaries and render chart 1:

```bash
python - <<'EOF'
import json, pathlib
xs = [0, 25, 50, 100, 200, "all"]
def s(tag):
    p = pathlib.Path(f"evals/{tag}/eval_summary.json")
    return json.loads(p.read_text())["success"] if p.exists() else None
series = {}
for name, fmt in [("base", None), ("base+best-of-4", "base_bo4"), ("SFT", "sft_{n}"), ("SFT+best-of-4", "sft_{n}_bo4")]:
    pts = []
    for x in xs:
        tag = ("base" if name == "base" else fmt) if x == 0 or fmt is None or "{n}" not in fmt else fmt.format(n=x)
        if x == 0 and "{n}" in (fmt or ""):
            tag = "base" if name == "SFT" else "base_bo4"
        pts.append(s(tag))
    series[name] = {"rate": [p["rate"] if p else None for p in pts],
                    "low": [p["low"] if p else None for p in pts],
                    "high": [p["high"] if p else None for p in pts]}
json.dump({"title": "Success on heldout_seeds vs verified trajectories", "x": xs, "series": series,
           "n_per_point": 150, "synthetic": False}, open("evals/curve.json", "w"), indent=2)
EOF
PYTHONPATH=. python -m train.plot chart1 evals/curve.json evals/chart1.png
```

### Rung 2.5 — corrective loop (self-generated verified data)

Run the SFT student itself with best-of-N on **train** seeds, keep only
verified successes, add them to the pool, retrain:

```bash
PYTHONPATH=. python -m train.eval --split train --seeds 0:400 --n-seeds 1 --best-of 4 \
  --base-url http://localhost:8011/v1 --model fara-sft-200 --prompt-style fara \
  --run-dir runs/student_bo4_round1 --tag student_bo4_round1 --backend solari
PYTHONPATH=. python -m train.make_sft --run-dir runs/teacher_v1 --run-dir runs/student_bo4_round1 \
  --exclude-split 'heldout_*' --out data/sft_round1.jsonl
PYTHONPATH=. python -m train.train_lora --model microsoft/Fara1.5-4B --data data/sft_round1.jsonl \
  --output-dir ckpt/fara_sft_round1 --prompt-style fara --epochs 2 --merge-out ckpt/fara_sft_round1/merged
```

Repeat while held-out success keeps rising. Because the recorder only stores
what the oracle verified, no failed trajectory ever reaches the training set.

### Rung 3 — GRPO (optional)

Only worth it once the SFT student succeeds on >= 20% of train seeds (so the
group has a reward signal). Use TRL's `GRPOTrainer` (or verl) with the reward
function `env.verify().reward` from a fresh `env.reset(seed)` per rollout, 4–8
rollouts per prompt, KL 0.02, LoRA r=16 on the merged SFT model. This directory
does not ship a GRPO script; `eval.py` is the evaluator for it.

## GPU rental guidance

On a fresh Lambda-style box, `train/box_setup.sh <commit>` does the whole setup (python3.11, the two venvs on local disk, `HF_HOME` on the NFS mount, `~/forkloop-env.sh`); see its header for the gotchas it encodes.

A single 24–48 GB card is enough for the whole ladder (a 4B student, LoRA r=16):

| Card | Fits | Notes |
| --- | --- | --- |
| RTX 4090 / L4 / A10G (24 GB) | SFT at batch 1, grad-accum 8; vLLM serving of the 4B model | run training and serving sequentially, not together |
| RTX 6000 Ada / A6000 / L40S (48 GB) | SFT at batch 2–4 **and** vLLM serving on the same card | most convenient for the corrective loop |
| A100 40/80 GB | everything, faster | overkill for a 4B model |

VRAM numbers in `train_lora.py` are **estimates**; watch `peak_vram_gb` in
`ckpt/*/train_log.jsonl`. The eval sweeps are bound by the Solari pool, not by
the GPU: keep `--concurrency` <= the backend's concurrency cap.

## Expected wall-clock (estimates)

| Step | Wall-clock |
| --- | --- |
| bake-off (3 candidates x 20 episodes + smoke) | 1–2 h |
| teacher collection, 200 verified episodes | 3–6 h (depends on teacher success rate) |
| `make_sft` | seconds |
| LoRA SFT, 200 episodes (~3k steps), 2 epochs, 4090 | ~2.5–4 h |
| LoRA SFT, 25 episodes | ~20–30 min |
| eval, 50 seeds x 3 families x 3 repeats, concurrency 8 | ~1.5–3 h per tag (best-of-4: ~4x) |
| corrective round (400 train seeds best-of-4 + retrain) | ~8–12 h |

## Local checks (no GPU, no Solari)

```bash
PYTHONPATH=. venv/bin/pytest tests/test_train.py tests/test_student_policy.py
PYTHONPATH=. python -m train.plot --demo          # writes SYNTHETIC charts to train/examples/
PYTHONPATH=. python -m train.train_lora --help
```

## Interface assumptions not in the contract

* `forkloop.policies.base.Policy` is a Protocol with `async act(obs) -> (Action | None, meta)`;
  `Observation` has `screenshot, instruction, step, history, width, height`.
* `forkloop.backends.fake.FakeBackend()` / `forkloop.backends.solari.SolariBackend()`
  (env vars such as `SOLARI_API_KEY` configure the latter); one shared
  `forkloop.pool.WorkerPool(backend, world, size=concurrency)` and a
  `forkloop.trajectories.Recorder(root, run_id)` — both optional in `eval.py`
  (`Env` owns its own pool when none is given).
* `env.step(action, meta=...)` receives the policy meta (`raw_action`,
  `model_latency_s`, `tokens`, `note`) so the recorder can write it.
* `forkloop.search.best_of_n(env, policy, n, seed, family=) -> Verdict`.
* `forkloop.metrics.summarize_run(run_dir) -> dict` (merged into `eval_summary.json` when `--run-dir` is given).
* `env.step()` accepts the raw model string when parsing failed, so the env
  records the invalid action itself (contracts.md §3).
