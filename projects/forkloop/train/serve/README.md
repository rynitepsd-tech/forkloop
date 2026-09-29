# Student serving and training stack (Qwen3.5-architecture students)

This directory pins one venv for **both** vLLM serving and LoRA training of the Forkloop student.
The candidates are `Hcompany/Holo-3.1-9B` and `Qwen/Qwen3.8-27B`, both `Qwen3_5ForConditionalGeneration`.
Using one venv means the image processor and chat-template code are the same in both paths.
`train/parity.py` still checks the token ids. Measured numbers and the model recommendation are in
[`docs/student-qualification.md`](../../docs/student-qualification.md).

## Pinned versions (installed and run on forkloop-dev, 2026-09-29)

| Package | Version | Why |
| --- | --- | --- |
| Python | 3.12.14 (uv) | vLLM 0.30 supports 3.10–3.14 |
| vllm | **0.30.0+cu129** (GitHub release / `wheels.vllm.ai/0.30.0/cu129`) | ≥ 0.28 is required for Holo4 and has Qwen3.5 LoRA, including the GDN `in_proj_qkvz`/`in_proj_ba` packing. The default PyPI build is CUDA 13. |
| torch / torchvision / torchaudio | 2.13.0+cu129 / 0.28.0+cu129 / 2.11.0+cu129 | vLLM 0.30.0 pins torch 2.13.0. The cu129 builds run on driver 570 (CUDA 12.8) through minor-version compatibility. |
| transformers | 5.17.0 | ≥ 5.2 has `Qwen3_5ForConditionalGeneration`; vLLM needs ≥ 5.10.4 |
| peft | 0.21.0 | regex `target_modules` for language-model-only LoRA |
| accelerate | 1.15.0 | |
| flash-linear-attention (fla-core) | 0.5.2 | fast Gated DeltaNet kernels for training |
| causal-conv1d | 1.7.0 (built from sdist, ~10 min) | fast short-conv kernels for training |

Model revisions are pinned in `serve.sh` and `download.sh`:

| Model | Revision |
| --- | --- |
| Holo-3.1-9B | `bcd6a36af9f100f57cba28e2bf6481ac3feedf33` |
| Qwen3.8-27B | `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` |

## Setup on a fresh box

```bash
# from the repo copy on the box (rsync from the Mac, see the project README)
cd /home/ubuntu/repo/projects/forkloop
train/serve/install.sh                  # idempotent; refuses NVIDIA drivers < 525; builds causal-conv1d for the local arch
source train/serve/env.sh               # HF_HOME on the NFS mount, offline hub, venv on PATH (ninja for vLLM's JIT)
train/serve/download.sh                 # Holo (~19 GB, 39 s on forkloop-dev) + Qwen (~56 GB, 100 s); no token needed
```

`env.sh` picks the first NFS mount that exists: `/lambda/nfs/forkloop-useast1/hf` (forkloop-main), then
`/lambda/nfs/forkloop-usw3/hf` (forkloop-dev). Set `HF_HOME` yourself to override it. The venv defaults to
`/home/ubuntu/venvs/fl`; override it with `FORKLOOP_VENV`.

## Launch

The server binds `127.0.0.1:8000` by default. Run it under tmux and log to NFS:

```bash
LOGS=/lambda/nfs/forkloop-usw3/logs
# THE STUDENT: Qwen3.8-27B, bf16, one 80 GB GPU (--max-num-seqs 32 is built in: the default 256 OOMs during
# CUDA-graph profiling). This is the server left running on forkloop-dev (tmux session `vllm`, port 8000).
tmux new-session -d -s vllm "GPU_UTIL=0.92 train/serve/serve.sh qwen38-27b 2>&1 | tee $LOGS/vllm_qwen38_27b_final.log"

# Holo-3.1-9B (fallback), bf16, one GPU
tmux new-session -d -s vllm "train/serve/serve.sh holo31-9b 2>&1 | tee $LOGS/vllm_holo31_9b.log"

# tensor parallel across 2 GPUs (not measured)
TP=2 train/serve/serve.sh qwen38-27b

# multi-LoRA: the base model plus named adapters (request "model": "<name>")
LORA_MODULES="smoke=/lambda/nfs/forkloop-usw3/checkpoints/holo-smoke-20260929/final other=/abs/path/final" \
  MAX_LORAS=4 MAX_LORA_RANK=16 train/serve/serve.sh holo31-9b
```

`serve.sh holo31-9b` expands to:

```bash
vllm serve Hcompany/Holo-3.1-9B --revision bcd6a36af9f100f57cba28e2bf6481ac3feedf33 --served-model-name holo-3.1-9b \
  --host 127.0.0.1 --port 8000 --dtype bfloat16 --enable-prefix-caching --chat-template-content-format openai \
  --limit-mm-per-prompt '{"image": 2, "video": 0}' --reasoning-parser qwen3 --gpu-memory-utilization 0.90 \
  --tensor-parallel-size 1 --max-model-len 32768
```

With LoRA it appends `--enable-lora --max-loras 4 --max-lora-rank 16 --lora-modules name=path ...`.
`--max-lora-rank` must be at least the adapter's `r`. An adapter directory written by `train/train_lora.py`
(`<output-dir>/final`) loads as-is. This was tested on 2026-09-29 with the Qwen3.8-27B smoke adapter: vLLM logged
"Loaded new LoRA adapter: name 'smoke'", and requests with `"model": "smoke"` used it. PEFT writes
`adapter_model.safetensors` with mode 600, so the server must run as the same user or the file needs `chmod a+r`.

Startup on forkloop-dev took about 4 min for Holo (weights, torch.compile, CUDA graphs) and about 6 min for Qwen on
the first load (the checkpoint read from NFS/virtiofs took 272 s; the second load took 159 s).

## Request contract (what `StudentPolicy` must send)

```python
StudentPolicy(base_url="http://127.0.0.1:8000/v1", model="qwen3.8-27b",   # or a LoRA name
              prompt_style="compact", coord_space="norm1000", image_scale=1.5, image_max_side=1920,
              prev_screenshot=True, history_k=8, temperature=0.0, max_tokens=256,
              memory=True, system_prompt=<text of forkloop/policies/prompts/agent_memory_v1.md>,
              extra_body={"chat_template_kwargs": {"enable_thinking": False}})
```

- **Thinking.** Both models think by default. Without `enable_thinking: false`, Holo spent 155 reasoning tokens
  on a one-line read. With it, the generation prompt ends in `<think>\n\n</think>\n\n` and the reply is in
  `message.content`. The `qwen3` reasoning parser leaves `reasoning` empty in that case.
- **Coordinates.** Both models are natively 0–1000 normalized: the H Company element-localization docs
  ("integers in [0, 1000]") and the Qwen3-VL computer-use cookbook (`coordinate / 1000 * width`). Pixel
  prompts fail; see the qualification doc.
- **Images.** The processor resizes 1280×720 to **1280×704** (a 44×80 patch grid, 880 visual tokens per
  image), because 720 is not a multiple of 32. The student config upscales on the client first
  (`resize_for_model`, 1.5× LANCZOS). The processor then turns 1920×1080 into 1920×1088 (a 68×120 grid, 2,040
  tokens), because 1080 is not a multiple of 32 either. Do not upscale with `mm_processor_kwargs` `min_pixels`: vLLM
  honours it, but transformers 5.17 ignores it, so training would not match serving. vLLM loads the processor with `use_fast=True`, the same
  torchvision backend transformers uses by default. `train/parity.py` checks that `pixel_values` are
  identical.
- **Token ids.** `"return_token_ids": true` in a chat request returns vLLM's `prompt_token_ids` after
  image-token expansion; `train/parity.py --base-url` uses this. `POST /tokenize` with `messages` returns
  the same count.

## Tools here

| File | Purpose |
| --- | --- |
| `install.sh`, `env.sh`, `download.sh`, `serve.sh` | the pinned stack, environment, weights, and launch (above) |
| `bench.py` | latency/throughput with the exact serving request (`--images 1 2 --concurrency 1 8 32`, `--image-scale`, `--proc-scale`) |
| `../probe_select.py`, `../probe_student.py` | frozen recorded states and the reading/grounding/format probe |
| `../parity.py` | training/serving parity per record (offline HF-as-vLLM and live vLLM prompt ids) |
| `../probe_records.py` | base vs LoRA predictions and target log-likelihood on dataset records, through the serving request |
| `../smoke_dataset.py` | a synthetic-but-realistic `forkloop.dataset.v1` directory from recorded teacher episodes |
