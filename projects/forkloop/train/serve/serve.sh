#!/bin/bash
# Launch one OpenAI-compatible vLLM server for a student (binds 127.0.0.1; other processes on the box call it).
#
#   usage: train/serve/serve.sh holo31-9b|qwen38-27b|qwen38-27b-fp8 [extra vllm args...]
#   env:   PORT (8000)  GPU_UTIL (0.90)  TP (1: tensor-parallel GPUs)  MAX_LEN (32768)
#          LORA_MODULES="name=/abs/adapter [name2=/abs/adapter2 ...]"  enables multi-LoRA serving
#          MAX_LORA_RANK (16; must be >= every adapter's r)  MAX_LORAS (4: adapters resident per batch)
#   e.g.   tmux new-session -d -s vllm "train/serve/serve.sh holo31-9b 2>&1 | tee $LOGS/vllm.log"
#          LORA_MODULES="smoke=/lambda/nfs/forkloop-usw3/checkpoints/holo-smoke/final" train/serve/serve.sh holo31-9b
#
# Requests: model = the served name (holo-3.1-9b / qwen3.8-27b) or a LoRA name; always send
# {"chat_template_kwargs": {"enable_thinking": false}} (both models think by default).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/env.sh"
PROFILE=${1:?usage: serve.sh holo31-9b|qwen38-27b|qwen38-27b-fp8 [extra args]}; shift
ARGS=(--host 127.0.0.1 --port "${PORT:-8000}" --dtype bfloat16 --enable-prefix-caching
      --chat-template-content-format openai --limit-mm-per-prompt '{"image": 2, "video": 0}'
      --reasoning-parser qwen3 --gpu-memory-utilization "${GPU_UTIL:-0.90}"
      --tensor-parallel-size "${TP:-1}" --max-model-len "${MAX_LEN:-32768}")
case "$PROFILE" in
  holo31-9b)      MODEL=Hcompany/Holo-3.1-9B; REV=bcd6a36af9f100f57cba28e2bf6481ac3feedf33; NAME=holo-3.1-9b ;;
  qwen38-27b)     MODEL=Qwen/Qwen3.8-27B; REV=1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0; NAME=qwen3.8-27b
                  # bf16 weights are 51.1 GiB: the default max-num-seqs (256) OOMs while vLLM profiles CUDA
                  # graph memory on one 80 GB card (measured 2026-09-29); 32 = our highest measured concurrency.
                  ARGS+=(--max-num-seqs 32) ;;
  qwen38-27b-fp8) MODEL=Qwen/Qwen3.8-27B; REV=1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0; NAME=qwen3.8-27b
                  ARGS+=(--quantization fp8 --max-num-seqs 64) ;;   # not measured
  *) echo "unknown profile $PROFILE" >&2; exit 2 ;;
esac
if [ -n "${LORA_MODULES:-}" ]; then
  # shellcheck disable=SC2206
  MODS=($LORA_MODULES)
  ARGS+=(--enable-lora --max-loras "${MAX_LORAS:-4}" --max-lora-rank "${MAX_LORA_RANK:-16}" --lora-modules "${MODS[@]}")
fi
exec vllm serve "$MODEL" --revision "$REV" --served-model-name "$NAME" "${ARGS[@]}" "$@"
