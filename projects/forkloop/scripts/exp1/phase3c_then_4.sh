#!/bin/bash
# exp1 on main, alongside phase3b_resume.sh: once round 1 is complete, evaluate A0 and S_W on main's
# final-test shard (0/2) while the teacher repairs run (the student server is otherwise idle then),
# and start phase 4 when both the repairs and that evaluation are done.
set -u
source "$(dirname "$0")/env.sh"
L=~/programs/exp1/logs
until grep -q "round 1 complete" $L/resume.log; do sleep 60; done
echo "$(date -u +%FT%TZ) a0/sw main shard started" >> $L/resume.log
for pass in 1 2 3; do   # later passes only retry unscored cells (the registered replacement rule)
  for m in A0 sw; do
    SET=""; [ "$m" != "A0" ] && SET="--set model=$m"
    forkloop evaluate --config configs/exp1.yaml --pool final_test --final --per-family 1000 --shard 0/2 \
      --families $FAMS --label $m $SET --experiment exp1-eval --concurrency 30 >> $L/eval-$m.log 2>&1 &
  done
  wait
done
echo "$(date -u +%FT%TZ) a0/sw main shard done" >> $L/resume.log
until grep -q "repairs complete" $L/resume.log; do sleep 60; done
scripts/exp1/phase4_train_arms.sh >> $L/phase4.log 2>&1
echo "$(date -u +%FT%TZ) phase 4 launched (8 runs; A3-s3 via train_on_dev.sh)" >> $L/resume.log
