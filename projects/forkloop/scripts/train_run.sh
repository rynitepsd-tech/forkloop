#!/bin/bash
# One LoRA training run of the Qwen3.8-27B student on one GPU, with the frozen experiment recipe.
#   usage: scripts/train_run.sh <run_id> <gpu> <seed> <max_steps> <out_root> <dataset_dir> [more dataset dirs...]
#   env:   MODEL_DIR (local weights; default ~/models/qwen3.8-27b), PROMPT (system prompt file), HISTORY_K (12)
# The recipe (lr, LoRA shape, batch, image scale) is identical for every arm; only data and seed differ.
set -euo pipefail
RUN=${1:?run id}; GPU=${2:?gpu}; SEED=${3:?seed}; STEPS=${4:?max optimizer steps}; OUT=${5:?out root}; shift 5
[ $# -ge 1 ] || { echo "need at least one dataset dir" >&2; exit 2; }
HERE=$(cd "$(dirname "$0")/.." && pwd)
source "$HERE/train/serve/env.main.sh"
MODEL_DIR=${MODEL_DIR:-$HOME/models/qwen3.8-27b}
PROMPT=${PROMPT:-$HERE/forkloop/policies/prompts/agent_memory_v3.md}
DS=(); for d in "$@"; do DS+=(--dataset "$d"); done
mkdir -p "$OUT/$RUN"
cd "$HERE"
CUDA_VISIBLE_DEVICES=$GPU exec python -m train.train_lora \
  --model "$MODEL_DIR" "${DS[@]}" \
  --coord-space norm1000 --image-scale 1.5 --max-image-side 1920 --history-k "${HISTORY_K:-12}" \
  --system-prompt-file "$PROMPT" \
  --lora-r 16 --lora-alpha 32 --lora-dropout 0.05 --target-modules auto \
  --lr 1e-4 --epochs 50 --max-steps "$STEPS" --batch-size 1 --grad-accum 8 --warmup-ratio 0.05 --weight-decay 0 \
  --max-grad-norm 1.0 --seed "$SEED" --save-steps 1000000 --log-steps 5 --max-seq-len 16384 \
  --output-dir "$OUT/$RUN" > "$OUT/$RUN/train.log" 2>&1
