#!/bin/bash
# exp1 phase 3d (protocol deviation 2026-09-29 15:28): replacement repairs for void repairs, both modes, in
# the matched-cost selection order, 18 concurrent branches each; budget-v2 as soon as every selected
# failure is settled (dry run until then); then phase 4 on budget-v2 → adapters-v2. The remaining
# replacement repairs keep running for the descriptive repair statistics.
set -u
source "$(dirname "$0")/env.sh"
L=~/programs/exp1/logs; D=~/programs/exp1/datasets
export PYTHONUNBUFFERED=1
B="forkloop budget --config configs/exp1.yaml --demo-experiment exp1-demos --attempt-experiment exp1-round1 \
  --checkpoint-experiment exp1-round1 --restart-experiment exp1-restart"
echo "$(date -u +%FT%TZ) replacement repairs started" >> $L/resume.log
forkloop repair --config configs/exp1.yaml --experiment exp1-round1 --order budget --concurrency 18 \
  >> $L/repair2-checkpoint.log 2>&1 &
forkloop repair --config configs/exp1.yaml --mode full_restart --experiment exp1-restart --source-experiment exp1-round1 \
  --order budget --concurrency 18 >> $L/repair2-restart.log 2>&1 &
until $B --dry-run --out /dev/null >> $L/budget-v2-dryrun.log 2>&1; do
  echo "--- $(date -u +%FT%TZ) not settled" >> $L/budget-v2-dryrun.log
  sleep 600
done
$B --out $D/budget-v2 > $L/budget-v2.log 2>&1 || { echo "$(date -u +%FT%TZ) budget-v2 FAILED" >> $L/resume.log; wait; exit 1; }
echo "$(date -u +%FT%TZ) budget-v2 built" >> $L/resume.log
BUDGET_NAME=budget-v2 ADAPTERS_NAME=adapters-v2 scripts/exp1/phase4_train_arms.sh >> $L/phase4-v2.log 2>&1
echo "$(date -u +%FT%TZ) phase 4 v2 launched (8 runs; A3-s3: BUDGET_NAME=budget-v2 ADAPTERS_NAME=adapters-v2 train_on_dev.sh)" >> $L/resume.log
wait
echo "$(date -u +%FT%TZ) replacement repairs complete" >> $L/resume.log
