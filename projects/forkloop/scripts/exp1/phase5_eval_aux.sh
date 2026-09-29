#!/bin/bash
# exp1 phase 5 on forkloop-aux (shard 1/2): the same models as main, served with multi-LoRA on TP2 x DP4.
# A0's aux shard runs separately (exp1-eval). Adapters are copied to ~/programs/exp1aux/adapters.
# Each model's evaluate is re-run until it has no retryable (unscored) cells left (3 passes).
set -u
source "$(dirname "$0")/env-aux.sh"
AD=/home/ubuntu/programs/exp1aux/adapters
MODS="sw=$AD/sw-seed0/final"
for arm in A1 A2 A3; do for s in 1 2 3; do MODS="$MODS $arm-s$s=$AD/$arm-s$s/final"; done; done
if ! curl -s -m 3 127.0.0.1:8010/v1/models | grep -q '"A3-s3"'; then
  tmux kill-session -t vllm-dp 2>/dev/null; tmux kill-session -t vllm-eval 2>/dev/null; sleep 20
  tmux new-session -d -s vllm-eval "VLLM_ENGINE_READY_TIMEOUT_S=1800 CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 PORT=8010 train/serve/serve-path.sh /home/ubuntu/models/qwen3.8-27b qwen3.8-27b --tensor-parallel-size 2 --data-parallel-size 4 --enable-lora --max-lora-rank 16 --max-loras 10 --max-cpu-loras 12 --lora-modules $MODS 2>&1 | tee /home/ubuntu/programs/exp1aux/logs/vllm-eval.log"
  until curl -s -m 3 127.0.0.1:8010/v1/models | grep -q '"A3-s3"'; do sleep 15; done
fi
for pass in 1 2 3; do
  for m in A0 sw A1-s1 A1-s2 A1-s3 A2-s1 A2-s2 A2-s3 A3-s1 A3-s2 A3-s3; do
    SET=""; [ "$m" != "A0" ] && SET="--set model=$m"
    forkloop evaluate --config configs/exp1-aux.yaml --pool final_test --final --per-family 1000 --shard 1/2 \
      --families $FAMS --label $m $SET --experiment exp1-eval --concurrency ${PER_MODEL:-5} \
      >> ~/programs/exp1aux/logs/eval-$m.log 2>&1 &
  done
  wait
done
