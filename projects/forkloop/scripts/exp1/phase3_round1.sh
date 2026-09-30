#!/bin/bash
# exp1 phase 3: S_W attempts the 160 round-1 tasks with checkpoints; its failures are repaired
# (A2: from evidence-chosen checkpoints; A3: from step 0), same k and teacher.
set -u
source "$(dirname "$0")/env.sh"
AD=/home/ubuntu/programs/exp1/adapters/sw-seed0/final
[ -f "$AD/adapter_config.json" ] || { echo "no S_W adapter at $AD" >&2; exit 2; }
if ! curl -s -m 3 127.0.0.1:8010/v1/models | grep -q '"sw"'; then
  tmux kill-session -t vllm-qwen-dp 2>/dev/null
  tmux new-session -d -s vllm-qwen-dp "VLLM_ENGINE_READY_TIMEOUT_S=1800 CUDA_VISIBLE_DEVICES=1,2,3,4,5,6,7 PORT=8010 train/serve/serve-path.sh /home/ubuntu/models/qwen3.8-27b qwen3.8-27b --data-parallel-size 7 --enable-lora --max-lora-rank 16 --max-loras 4 --lora-modules sw=$AD 2>&1 | tee /home/ubuntu/programs/exp1/logs/vllm-sw.log"
  until curl -s -m 3 127.0.0.1:8010/v1/models | grep -q '"sw"'; do sleep 15; done
fi
forkloop record --config configs/exp1.yaml --role student --set model=sw --experiment exp1-round1 \
  --families $FAMS --pool train --skip 30 --per-family 40 --concurrency 56 > ~/programs/exp1/logs/round1.log 2>&1
forkloop repair --config configs/exp1.yaml --experiment exp1-round1 --concurrency 36 > ~/programs/exp1/logs/repair-checkpoint.log 2>&1 &
forkloop repair --config configs/exp1.yaml --mode full_restart --experiment exp1-restart --source-experiment exp1-round1 \
  --concurrency 36 > ~/programs/exp1/logs/repair-restart.log 2>&1 &
wait
