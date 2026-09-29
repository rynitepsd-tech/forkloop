# Student qualification: serving, probes, parity and LoRA smoke (2026-09-29)

This document holds raw numbers only. Everything was measured on **forkloop-dev**: 1× H100 PCIe 80 GB, driver
570.148.08 (CUDA 12.8), Ubuntu 22.04. The date is 2026-09-29 UTC, on the stack pinned in
[`train/serve/README.md`](../train/serve/README.md): vllm 0.30.0+cu129, torch 2.13.0+cu129, transformers 5.17.0,
peft 0.21.0, flash-linear-attention 0.5.2, causal-conv1d 1.7.0, Python 3.12.

The probe numbers are **development evidence for choosing a model**. They come from 42 recorded states and are not
an evaluation. All click numbers measure **agreement with the teacher's click**, not correctness. Raw rows and
summaries are local, in `runs/student-qualification-20260929/` (git-ignored). The copies on the box are in
`/lambda/nfs/forkloop-usw3/{runs,logs,checkpoints}/`.

The following were **not measured** because this session could not access forkloop-main:

- multi-GPU runs (TP=2, FSDP, 8× A100)
- A100 timings
- Holo-3.1-9B training speed
- Holo-3.1-9B at 1.5× (the run was stopped after the lead chose Qwen)

## Models

| Model | Revision | Architecture | License | Native coordinates |
| --- | --- | --- | --- | --- |
| `Hcompany/Holo-3.1-9B` | `bcd6a36a…` | `Qwen3_5ForConditionalGeneration`, 9.41B | Apache-2.0 | 0–1000 ("integers in [0, 1000]", H Company element-localization docs) |
| `Qwen/Qwen3.8-27B` | `1d4bf0f2…` | `Qwen3_5ForConditionalGeneration`, 27.8B | Apache-2.0 | 0–1000 relative (Qwen3-VL computer-use cookbook: `coordinate / 1000 * width`) |

Both processors are `Qwen3VLProcessor` / `Qwen2VLImageProcessor` (torchvision backend): patch 16, merge 2,
`shortest_edge` 65,536 px, `longest_edge` 16,777,216 px. A 1280×720 PNG becomes **1280×704**, a 44×80 grid, or
**880 visual tokens**. The full resolution is kept horizontally and resampled vertically by 2.2%, because 720 is not
a multiple of 32. At 1.5× (1920×1080) it becomes 1920×1088, a 68×120 grid of 2,040 tokens. At 2× (2560×1440) it
becomes a 90×160 grid of 3,600 tokens.

Both chat templates think by default: the generation prompt ends in `<think>\n`. With
`chat_template_kwargs: {"enable_thinking": false}` the prompt ends in `<think>\n\n</think>\n\n`, and the reply
lands in `message.content`; the reasoning field is empty. On one read, Holo spent 155 reasoning tokens with thinking
on and 12 completion tokens with it off.

## Serving throughput (vLLM 0.30.0, bf16, one H100)

Setup for every run:

- Each request is rendered by `StudentPolicy.build_request`: the compact prompt, thinking off, and PNG data URLs.
- Each screenshot gets a unique one-pixel perturbation per request, so image tokens are never reused from cache.
- Prefix caching is on.
- Output is fixed at 64 tokens (`max_tokens=64`, `ignore_eos`).
- Command: `python -m train.serve.bench --model M --states <probe states> --images 1 2 --concurrency 1 8 32 [--image-scale S | --proc-scale S]`.

In the tables, **p50 / p90 are latencies in seconds**, and "prompt" is the mean number of prompt tokens.

**Holo-3.1-9B, native (1280×720):**

| Images | Prompt | c=1 req/s | c=1 p50 / p90 | c=8 req/s | c=8 p50 / p90 | c=32 req/s | c=32 p50 / p90 |
| --- | ---: | ---: | --- | ---: | --- | ---: | --- |
| 1 | 1,426 | 1.03 | 0.97 / 0.98 | 5.28 | 1.54 / 1.60 | 10.88 | 2.89 / 3.11 |
| 2 | 2,448 | 0.94 | 1.05 / 1.09 | 4.10 | 2.02 / 2.05 | 6.47 | 4.84 / 5.22 |

**Qwen3.8-27B, bf16, `--max-num-seqs 32`.** The default of 256 OOMs while vLLM profiles CUDA-graph memory. The KV
cache holds 270,336 tokens.

| Scale | Images | Prompt | c=1 req/s | c=1 p50 / p90 | c=8 req/s | c=8 p50 / p90 | c=32 req/s | c=32 p50 / p90 |
| --- | --- | ---: | ---: | --- | ---: | --- | ---: | --- |
| 1× | 1 | 1,426 | 0.42 | 2.37 / 2.42 | 2.01 | 3.98 / 4.05 | 3.33 | 9.60 / 9.70 |
| 1× | 2 | 2,448 | 0.38 | 2.63 / 2.64 | 1.49 | 5.34 / 5.42 | 2.16 | 14.69 / 14.83 |
| 1.5× client | 1 | 2,586 | 0.38 | 2.67 / 2.68 | 1.42 | 5.60 / 5.64 | 1.97 | 16.20 / 16.31 |
| 1.5× client | 2 | 4,768 | 0.31 | 3.20 / 3.21 | 0.91 | 8.78 / 8.85 | 1.16 | 27.45 / 27.53 |
| 2× client | 1 | 4,146 | – | – | 0.98 | 8.13 / 8.19 | 1.25 | 25.63 / 25.86 |
| 2× client | 2 | 7,884 | – | – | 0.57 | 13.98 / 14.11 | 0.68 | 46.18 / 63.31 |
| 2× in processor | 1 | 4,146 | – | – | 0.98 | 8.09 / 8.20 | 1.25 | 25.61 / 28.34 |
| 2× in processor | 2 | 7,882 | – | – | 0.58 | 13.79 / 14.07 | 0.69 | 45.72 / 63.89 |

"2× in processor" means `mm_processor_kwargs: {"size": {"shortest_edge": 2560*1440, "longest_edge": 16777216}}`.

**LoRA serving at 1.5×, 2 images.** The server ran with `--enable-lora --max-loras 4 --max-lora-rank 16` and the
smoke adapter loaded:

| Model name | c=1 req/s | c=1 p50 / p90 | c=8 req/s | c=8 p50 / p90 |
| --- | ---: | --- | ---: | --- |
| adapter `smoke` | 0.27 | 3.77 / 3.80 | 0.80 | 9.94 / 10.09 |
| base, same server | 0.34 | 2.91 / 2.96 | 0.94 | 8.43 / 8.50 |

Startup times:

- Holo: about 4 min (143 s engine init).
- Qwen, first load: 272 s to read the checkpoint over NFS/virtiofs.
- Qwen, next load: 159 s to read, then 90 s engine init.

## Offline probe (no world; development evidence)

**States.** 42 frozen states from the verified teacher episodes (gpt-5.6-luna) of
`runs/model-upgrade-live-20260924/compare-{a,b}/runs/A` (23 episodes, `heldout_seeds` split). They were selected by
`python -m train.probe_select --run-dir …/compare-a/runs/A --run-dir …/compare-b/runs/A --out data/probe_states_20260928 --n-read 14 --n-click 28`.
`states.jsonl` sha256 is `83fee7fa91f7e9a70e0282839abb55b6ebdd222b9beb930ad90bdbb486db2936`.

- **Read (14).** The first step whose teacher reply transcribes the true letter's number, exactly or within 2 edits.
  All 14 screenshots were checked by eye: an authorization letter is on screen.
  States (arm-episode-step): compare-a A-000000-015, A-000001-037, A-000002-022, A-000003-020, A-000004-040,
  A-000005-020, A-000006-041, A-000007-027, A-000008-028, A-000009-017, A-000010-019, A-000011-020;
  compare-b A-000000-014, A-000001-018.
- **Click (28).** Teacher clicks with a reasoning line at 1/3 and 2/3 of each episode's click list. The tab strip and
  omnibox (y < 70) and read states are excluded.
  States: compare-a A-000000-012, -028; A-000001-017, -034; A-000002-014, -029; A-000003-014, -030;
  A-000004-020, -035; A-000005-015, -027; A-000006-018, -040; A-000007-015, -028; A-000008-015, -027;
  A-000009-012, -026; A-000010-014, -030; A-000011-017, -037; compare-b A-000000-011, -023; A-000001-015, -037.

**Command.** `python -m train.probe_student --model M --states … --out …`. Decoding is greedy with thinking off.
Agreement means the click lands within 20 px (Euclidean) of the teacher's click.

| Probe | Holo-3.1-9B | Qwen3.8-27B |
| --- | --- | --- |
| read: exact authorization, native | 5/14 | 4/14 |
| read, 1.5× client upscale | not measured | 12/14 |
| read, 1.5× in processor | not measured | 11/14 |
| read, 2× client upscale | 14/14 | 14/14 |
| read, 2× in processor | not measured | 14/14 |
| read prompt tokens, 1× / 1.5× / 2× | 910 / – / – | 921 / 2,081 / 3,641 |
| ground: teacher's reasoning line as target, reply in 0–1000: valid; agree ≤20 px; ≤40 px; median | 25/28; 21/28; 23/28; 6.8 px | 28/28; 25/28; 26/28; 3.6 px |
| ground: same prompt, reply in 1280×720 pixels: valid; agree; median | 27/28; 0/28; 222.6 px | 28/28; 1/28; 201.2 px |
| agent, compact prompt, norm1000: strict format; lenient parse | 38/42; 38/42 | 32/42; 42/42 |
| … next-click agreement (click states); median distance | 9/28; 52.6 px | 16/28; 4.5 px |
| … authorization in the reply (read states) | 6/14 | 3/14 |
| agent, compact prompt, pixel coordinates: strict; next-click agreement; median | 36/42; 0/28; 179.5 px | 27/42; 7/28; 35.0 px |
| agent, `agent_memory_v1.md` + `memory=True` (empty), norm1000: strict format | **5/42** | **42/42** |
| … next-click agreement; median distance | 6/28; 6.3 px | 10/28; 92.0 px |
| … authorization in the reply; in a `Memory:` line | 6/14; 1/14 | 5/14; 5/14 |

Holo's native misreads were `AJTH-83Z80771`, `AUTH-52079995`, `AUTH-71T98804`, `AUTH-98E43686`, `AUTH-76092506`,
`AUTH-55C52187`, `AUTH-84628026`, `AUTH-35C96628` and `AUTH-46A18483`. They are the 0/8, D/0, U/0 and G/6
confusions of a 9-px font. Both models read 14/14 at 2×, so the misreads are resolution-limited.

In the memory-prompt runs, base Holo usually writes `Memory:` lines and then no action line. The base
`_MEMORY_LINE_RE` also lost facts written as "Memory:\n- a\n- b"; the lead has since fixed it.

**Parity trap.** vLLM honours `mm_processor_kwargs: {"min_pixels": …}`, but the transformers 5.17 image processor
ignores that kwarg: the grid stays 44×80. Only `size={"shortest_edge": …}` works in both. Client-side upscaling is
therefore the parity-safe option. The lead implemented it as `observation.resize_for_model(image_scale=1.5, image_max_side=1920)`.

## Training/serving parity (`train/parity.py`)

Records: the synthetic-but-realistic dataset
`python -m train.smoke_dataset --run-dir runs/luna-v5-f3-s20-99 --episodes 2 --per-episode 16 --out data/smoke-ds-20260929`.
It has 32 records from 2 verified train-split teacher episodes (seeds 20 and 21), in `forkloop.dataset.v1` layout,
with id `ds-9c0955524df5`. It includes 16 memory-bearing records and 30 two-image records, and the `Memory:` lines
are synthesized.

Checks per record:

- `messages_equal`, `images_equal` (decoded request pixels vs training images, in order)
- `prompt_ids_offline` (HF processor as vLLM runs it) and `prompt_ids_live` (vLLM `return_token_ids`)
- `image_grid_equal`, `pixel_values_equal`
- `thinking_disabled`, `boundary`, `labels_mask` (only target + `<|im_end|>` supervised), `concat_stable`, `no_truncation`

| Run | Records ok | Failed checks |
| --- | --- | --- |
| Holo-3.1-9B processor, offline, 1× | 32/32 | none |
| Qwen3.8-27B, live against vLLM, 1× | 32/32 (prompt ids identical: e.g. 3,066 tokens, 1,760 image tokens) | none |
| Qwen3.8-27B, live, **1.5× / max side 1920** (the student config) | 32/32 (e.g. 5,400 prompt tokens, 4,080 image tokens, grid 68×120 ×2, pixel_values equal) | none |
| Qwen3.8-27B, live against the LoRA name `smoke`, 1.5× | 2/2 (indices 28, 29) | none |

The commands follow this pattern (1.5× shown):
`CUDA_VISIBLE_DEVICES= python -m train.parity --model Qwen/Qwen3.8-27B --dataset …/smoke-ds-20260929 --all --system-prompt-file forkloop/policies/prompts/agent_memory_v1.md --coord-space norm1000 --image-scale 1.5 --max-image-side 1920 --base-url http://127.0.0.1:8000/v1 --served-model qwen3.8-27b --out …`.

**Mismatches found in the previous `train_lora.py` and fixed:**

1. **Thinking.** Training rendered the template without `enable_thinking`, so the prefix was `<think>\n`. Serving
   sends `enable_thinking=false`, whose prefix is `<think>\n\n</think>\n\n`. Both paths now use the same
   `chat_template_kwargs`.
2. **Processor override.** Training loaded the processor with `max_pixels = max_image_side²` (1,638,400). At 1.5×
   that turns 1920×1080 into a 60×106 grid (1,590 tokens) in training, against 68×120 (2,040) in vLLM. The override
   is removed: both paths now use processor defaults.
3. **Label mask.** The labels also supervised the template's trailing `\n` after `<|im_end|>`, which is never
   generated. It is now masked.
4. **LoRA targets.** The default names `q/k/v/o/gate/up/down` missed the Gated DeltaNet projections
   (`in_proj_qkv/z/a/b`, `out_proj`) of 48 of the 64 layers. The new `--target-modules auto` regex covers the whole
   language model and excludes `model.visual.*`, `lm_head` and MTP.

Offline tests:

- On the Mac, torch-free: `tests/test_train_dataset.py`.
- On the box: `tests/test_parity_contract.py` with the real processor, skipped when it is not cached. It ran 22
  passing tests with `FORKLOOP_PARITY_MODEL=Qwen/Qwen3.8-27B`, and 13 earlier with Holo.

## LoRA training on one H100 (Qwen3.8-27B)

Configuration: bf16 base; LoRA r=16, alpha 32, dropout 0.05 on the language model only (992 LoRA tensors, 116.7M
trainable = 0.42%); gradient checkpointing; fla + causal-conv1d kernels found; SDPA. The loss uses logits on the
target span only; in `--smoke` it equals the full-logits loss, 1.567919 against 1.567919 and 1.594988 against
1.594988.

In the table, s/example is the steady state and excludes the first step. The first step adds 30–120 s of one-time
kernel JIT.

| Image scale | Tokens/example (image tokens) | s/example | Tokens/s | Peak VRAM | Run |
| --- | --- | ---: | ---: | ---: | --- |
| 1× | 2,972 mean (1,662) | 2.90 | ~1,060 | 65.4 GB | `--smoke --max-steps 8`, grad-accum 1 |
| 1.5× / 1920 | 5,210 mean (3,895) | 4.66 (9.33 s per step of 2) | ~1,130 | 72.9 GB (includes the smoke full-logits reference pass) | the smoke run below |
| 2× | 8,516 mean (7,200) | 7.59 | ~1,120 | 67.9 GB | `--max-steps 6 --image-scale 2`, no `--smoke` |

**Smoke run.**

```bash
python -m train.train_lora --model Qwen/Qwen3.8-27B --revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 \
  --dataset /lambda/nfs/forkloop-usw3/datasets/smoke-ds-20260929 --coord-space norm1000 --image-scale 1.5 \
  --max-image-side 1920 --system-prompt-file forkloop/policies/prompts/agent_memory_v1.md --batch-size 1 \
  --grad-accum 2 --lr 2e-4 --lora-r 16 --lora-alpha 32 --max-steps 20 --save-steps 0 --log-steps 1 --seed 0 \
  --smoke --limit 32 --output-dir /lambda/nfs/forkloop-usw3/checkpoints/qwen38-27b-smoke-20260929
```

- `--epochs 1` capped the run at **16 optimizer steps** (32 examples).
- Loss fell from 1.438 (step 1) to 0.549 (step 16).
- Gradient norms on the LoRA parameters ran from 8.75 to 2.19, all nonzero.
- All 992 LoRA tensors changed (max |Δ| 0.00169).
- The adapter is at `/lambda/nfs/forkloop-usw3/checkpoints/qwen38-27b-smoke-20260929/final`. Its `training_args.json`
  records the dataset id, the manifest sha256, image_scale 1.5, the template kwargs, the LoRA regex and the versions.

**Reload through vLLM.** Launched with
`LORA_MODULES="smoke=…/final" MAX_LORA_RANK=16 train/serve/serve.sh qwen38-27b`. vLLM logged
"Loaded new LoRA adapter: name 'smoke'" and `/v1/models` listed `['qwen3.8-27b', 'smoke']`.

Base against adapter on the 32 training records, through the serving request (1.5×, memory prompt, greedy):
`python -m train.probe_records --dataset … --model qwen3.8-27b --model smoke --system-prompt-file … --image-scale 1.5 --image-max-side 1920`.

| On the 32 training records | Base `qwen3.8-27b` | Adapter `smoke` |
| --- | ---: | ---: |
| target mean log-prob per token (teacher forcing via `continue_final_message` + `prompt_logprobs`) | −1.517 | **−0.437** |
| target NLL per record (sum) | 61.5 | **18.6** |
| strict format | 31/32 | 32/32 |
| action line exactly equal to the target | 6/32 | 9/32 |
| action type equal | 22/32 | 25/32 |
| click within 20 px of the target (both clicks) | 6/13 (median 90.6 px) | 11/12 (median 2.0 px) |
| `Memory:` facts equal to the target's | 11/32 | 30/32 |

Example, step 28 of seed 20, where the letter is on screen:

- The base wrote `Memory: Authorization number for Zane Alvarado: AUTH-62M89371 …`, which is a misread.
- The adapter wrote `Memory: Authorization number: AUTH-62M80371`, which is correct. It differs from the target only
  in the colon, so it does not count as an exact memory match.

These are training records, so this shows the mechanics (gradients, save, reload, serving), not generalization.

## Recommendation

The lead's decision is **`Qwen/Qwen3.8-27B` (rev `1d4bf0f2`) at image_scale 1.5 / image_max_side 1920**, and the
numbers above support it.

- **Following the contract.** It follows the explicit-memory reply format zero-shot on 42/42 states; Holo does on
  5/42. The student needs a nonzero full-workflow baseline, so this matters most.
- **Grounding.** 25/28 against Holo's 21/28 (median 3.6 against 6.8 px). Next-click agreement with the teacher is
  16/28 against 9/28.
- **Reading.** It is resolution-limited for both models: 4 or 5 of 14 at 1×. At 1.5× Qwen reads 12/14 for 2.3× the
  image tokens; at 2× both read 14/14.
- **Trainability.** It trains on one 80 GB GPU at 1.5× with a 72.9 GB peak (smoke) at 4.66 s per example. 1,000
  records × 2 epochs would take about 2.6 h on one H100. That is extrapolated from the measured rate; the A100 is not
  measured.
- **Cost.** Serving is 2.5–3× slower than Holo: 2 images at 1.5× run at 0.91 req/s, p50 8.8 s, at c=8 on one H100.
  Holo at 1× runs at 4.10 req/s, p50 2.0 s.
- **Fallback.** Holo-3.1-9B stays the cheap option: 4× the throughput, one GPU for training and serving. It needs
  SFT before it emits the memory format.
- **License.** Both are Apache-2.0. Holo4-27B, which is CC-BY-NC, was not evaluated.
