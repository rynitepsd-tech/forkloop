#!/bin/bash
# exp1 phase 1: teacher demonstrations from initial states.
#   warm start W: train pool positions 10-29 per family (80 tasks)
#   demonstration arm A1: positions 30-69 per family (160 tasks), the same tasks round 1 uses
set -u
source "$(dirname "$0")/env.sh"
mkdir -p ~/programs/exp1/logs
forkloop record --config configs/exp1.yaml --role teacher --no-checkpoints --experiment exp1-warmstart \
  --families $FAMS --pool train --skip 10 --per-family 20 --concurrency 32 > ~/programs/exp1/logs/warmstart.log 2>&1 &
forkloop record --config configs/exp1.yaml --role teacher --no-checkpoints --experiment exp1-demos \
  --families $FAMS --pool train --skip 30 --per-family 40 --concurrency 40 > ~/programs/exp1/logs/demos.log 2>&1 &
wait
