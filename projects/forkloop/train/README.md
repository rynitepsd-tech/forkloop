# Forkloop training ladder

Everything here consumes the trajectory layout of `docs/contracts.md` §10 and
drives the Env API of §11. Run every command from `projects/forkloop/` with
`PYTHONPATH=.` (or `pip install -e .[train]`).

## Current student path (2026-09-29): Qwen3.5-architecture students, `forkloop.dataset.v1`

The student candidates are `Qwen/Qwen3.8-27B` (recommended) and `Hcompany/Holo-3.1-9B`, both
`Qwen3_5ForConditionalGeneration`. Evidence is in [`docs/student-qualification.md`](../docs/student-qualification.md).
The pinned stack, weights and vLLM launch commands are in [`serve/README.md`](serve/README.md).

### The student recipe (Qwen3.8-27B, image_scale 1.5, explicit memory)

These flags must match the serving `StudentPolicy`: `prompt_style="compact"`, `coord_space="norm1000"`,
`image_scale=1.5`, `image_max_side=1920`, `history_k=8`, `memory=True`,
`system_prompt=<agent_memory_v1.md>`, `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`.

```bash
cd /home/ubuntu/repo/projects/forkloop && source train/serve/env.sh
export CUDA_VISIBLE_DEVICES=0            # train_lora.py is single-GPU: one run per GPU (no DDP/FSDP code)
python -m train.train_lora \
  --model Qwen/Qwen3.8-27B --revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 \
  --dataset /lambda/nfs/forkloop-useast1/datasets/<ds-dir> \
  --coord-space norm1000 --image-scale 1.5 --max-image-side 1920 --history-k 8 \
  --system-prompt-file forkloop/policies/prompts/agent_memory_v1.md \
  --lora-r 16 --lora-alpha 32 --lora-dropout 0.05 --target-modules auto \
  --lr 1e-4 --epochs 2 --max-steps <N> --batch-size 1 --grad-accum 8 --warmup-ratio 0.03 --weight-decay 0 \
  --max-grad-norm 1.0 --seed 0 --save-steps 100 --log-steps 5 --max-seq-len 16384 \
  --output-dir /lambda/nfs/forkloop-useast1/checkpoints/<run-id>
```

- **Datasets.** Add `--dataset DIR2 --dataset-weights 3,1` to mix sources (`--samples-per-epoch N` optional). Every
  dataset is verified before the model loads: the manifest file sha256s, every screenshot sha256, and no final-test
  source task (`forkloop.splits.final_reasons`, checked per record).
- **Targets and thinking.** `--target-modules auto` puts LoRA on the language model only: full-attention
  q/k/v/o_proj, Gated DeltaNet in_proj_qkv/z/a/b and out_proj, and MLP gate/up/down_proj. The vision tower, lm_head
  and MTP are frozen. The loss covers the target span and `<|im_end|>` only. Thinking is off, via
  `enable_thinking=False` in the chat template, as when serving.
- **Evaluation.** Training never evaluates. Evaluate adapters with `forkloop evaluate` against a vLLM server that
  loads them.
- **Output.** `<output-dir>/final` (and each `checkpoint-N`) is a PEFT adapter that vLLM loads directly with
  `--lora-modules name=<output-dir>/final`; the reload was tested in `docs/student-qualification.md`. Each directory
  carries `training_args.json` with the dataset ids and manifest sha256s, the model revision, image_scale, the
  template kwargs, the LoRA regex and the package versions. `train_log.jsonl` has loss, s/step, tokens and peak VRAM.
- **Speed.** Measured on one H100 80 GB at 1.5×: 4.66 s per example (~5.2k tokens, 2 screenshots), peak 72.9 GB
  including the smoke-only reference pass. One optimizer step at grad-accum 8 takes about 37 s. A100 speed is not
  measured.
- **Smoke flag.** Do not pass `--smoke` for real runs: it adds a full-logits reference forward and prints
  diagnostics.

```bash
# check training == serving for the records (offline; add --base-url/--served-model for vLLM's own ids)
python -m train.parity --model Qwen/Qwen3.8-27B --dataset /path/ds --all --image-scale 1.5 --max-image-side 1920 \
  --system-prompt-file forkloop/policies/prompts/agent_memory_v1.md --coord-space norm1000
# serve the adapter next to the base and compare them on records
GPU_UTIL=0.92 LORA_MODULES="sft=/lambda/nfs/.../checkpoints/<run>/final" MAX_LORA_RANK=16 train/serve/serve.sh qwen38-27b
python -m train.probe_records --dataset /path/ds-val --model qwen3.8-27b --model sft --image-scale 1.5 \
  --image-max-side 1920 --system-prompt-file forkloop/policies/prompts/agent_memory_v1.md --out runs/<probe>
```

Student evaluation runs through `forkloop evaluate` (the correction CLI, `docs/correction.md`).
`eval.py` below is **legacy**: its `--split` choices predate the split policy of `forkloop/splits.py`
(`train_v2`, `val_v2`, `final_test`). `make_sft.py` (legacy `sft.jsonl`) and `train_lora.py --dataset` both refuse
final-test tasks (`splits.final_reasons`).

The rest of this file describes the 2026-09 Fara ladder and is kept for reference.

| File | Purpose |
| --- | --- |
| `make_sft.py` | verified trajectories (`reward == 1.0`) -> `sft.jsonl` (one record per step, compact-form target) |
| `train_lora.py` | LoRA SFT with transformers + peft for Qwen3.5-architecture students (Holo-3.1, Qwen3.8, Fara 1.5); `--dataset` (dataset.v1) or `--data` (sft.jsonl) |
| `parity.py` | training/serving parity per record (token ids, images, grids, pixel values, thinking prefix, label mask) |
| `probe_select.py`, `probe_student.py`, `probe_records.py` | frozen recorded probe states; reading/grounding/format probe; base vs LoRA on dataset records |
| `smoke_dataset.py` | a synthetic-but-realistic dataset.v1 directory from recorded teacher episodes |
| `serve/` | pinned vLLM + training venv, launch scripts, throughput benchmark |
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
