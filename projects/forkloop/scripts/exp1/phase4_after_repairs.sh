#!/bin/bash
# exp1 on main: start phase 4 once phase3b_resume.sh has logged "repairs complete".
# (Replaces phase3c_then_4.sh after its A0/S_W shard was moved to phase 5: repairs + that evaluation
# exceeded the host's Docker world cap.)
set -u
source "$(dirname "$0")/env.sh"
L=~/programs/exp1/logs
until grep -q "repairs complete" $L/resume.log; do sleep 60; done
scripts/exp1/phase4_train_arms.sh >> $L/phase4.log 2>&1
echo "$(date -u +%FT%TZ) phase 4 launched (8 runs; A3-s3 via train_on_dev.sh)" >> $L/resume.log
