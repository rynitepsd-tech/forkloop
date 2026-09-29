#!/bin/bash
# exp1 phase 4: matched-cost datasets, then A1/A2/A3 x seeds 1-3 (100 optimizer steps each) on main GPUs 0-7.
# The 9th run (A3 seed 3) goes to forkloop-dev (1x H100) via scripts/exp1/train_on_dev.sh from the controller.
set -u
source "$(dirname "$0")/env.sh"
OUT=/home/ubuntu/programs/exp1/datasets/budget
[ -f "$OUT/budget-report.json" ] || forkloop budget --config configs/exp1.yaml --demo-experiment exp1-demos \
  --attempt-experiment exp1-round1 --checkpoint-experiment exp1-round1 --restart-experiment exp1-restart --out "$OUT" \
  > ~/programs/exp1/logs/budget.log 2>&1 || { echo "budget failed"; exit 1; }
cp -r "$OUT" /lambda/nfs/forkloop-useast1/datasets/exp1-budget 2>/dev/null || true
tmux kill-session -t vllm-qwen-dp 2>/dev/null; tmux kill-session -t vllm-qwen 2>/dev/null; sleep 20
W=/home/ubuntu/programs/exp1/datasets/W
AD=/home/ubuntu/programs/exp1/adapters
g=0
for arm in A1 A2 A3; do
  for seed in 1 2 3; do
    [ "$arm$seed" = "A33" ] && continue
    tmux new-session -d -s "train-$arm-s$seed" "scripts/train_run.sh $arm-s$seed $g $seed 100 $AD $W $OUT/$arm-b100; chmod -R a+r $AD/$arm-s$seed"
    g=$((g+1))
  done
done
