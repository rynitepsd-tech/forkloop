#!/bin/bash
# Serve a student from a LOCAL model directory (fast loads for many replicas; NFS loads timed out at DP=7).
#   usage: serve-path.sh <model_dir> <served_name> [extra vllm args...]   (env: PORT, CUDA_VISIBLE_DEVICES)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/env.main.sh"
DIR=${1:?model dir}; NAME=${2:?served name}; shift 2
exec vllm serve "$DIR" --served-model-name "$NAME" --max-model-len 32768 --host 127.0.0.1 --port "${PORT:-8000}" \
  --dtype bfloat16 --enable-prefix-caching --chat-template-content-format openai \
  --limit-mm-per-prompt "{\"image\": 2, \"video\": 0}" --reasoning-parser qwen3 --gpu-memory-utilization "${GPU_UTIL:-0.90}" \
  --max-num-seqs 32 "$@"
