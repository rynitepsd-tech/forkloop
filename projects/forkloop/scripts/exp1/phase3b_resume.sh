#!/bin/bash
# exp1 phase 3 resume: reap worlds of the dead round-1 runner, give unscored/interrupted cells their
# replacement attempts (round 1 and demonstrations), then run both repair modes on round-1 failures.
set -u
source "$(dirname "$0")/env.sh"
L=~/programs/exp1/logs
sleep 200   # let the dead runner's heartbeat go stale (STALE_S = 180)
forkloop reap-machines --config configs/exp1.yaml >> $L/resume.log 2>&1
echo "$(date -u +%FT%TZ) reaped" >> $L/resume.log
forkloop record --config configs/exp1.yaml --role teacher --no-checkpoints --experiment exp1-demos \
  --families $FAMS --pool train --skip 30 --per-family 40 --concurrency 16 >> $L/demos.log 2>&1 &
for pass in 1 2; do
  forkloop record --config configs/exp1.yaml --role student --set model=sw --experiment exp1-round1 \
    --families $FAMS --pool train --skip 30 --per-family 40 --concurrency 56 >> $L/round1.log 2>&1
done
echo "$(date -u +%FT%TZ) round 1 complete" >> $L/resume.log
forkloop repair --config configs/exp1.yaml --experiment exp1-round1 --concurrency 32 >> $L/repair-checkpoint.log 2>&1 &
forkloop repair --config configs/exp1.yaml --mode full_restart --experiment exp1-restart --source-experiment exp1-round1 \
  --concurrency 32 >> $L/repair-restart.log 2>&1 &
wait
echo "$(date -u +%FT%TZ) repairs complete" >> $L/resume.log
