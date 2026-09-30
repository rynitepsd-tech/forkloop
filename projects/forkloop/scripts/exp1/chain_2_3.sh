#!/bin/bash
# Wait for the warm-start cells, then phase 2 (train S_W) and phase 3 (round 1 + repairs).
set -u
source "$(dirname "$0")/env.sh"
while true; do
  n=$(python - <<'PY'
from forkloop.correction import Store
s = Store("/home/ubuntu/programs/exp1/forkloop.sqlite")
rows = s.attempts(experiment_id="exp1-warmstart")
print(sum(1 for a in rows if a["status"] == "running") + max(0, 80 - len(rows)))
PY
)
  [ "$n" = "0" ] && break
  sleep 60
done
echo "$(date -u +%FT%TZ) warm start collected" >> ~/programs/exp1/logs/chain.log
GPU=1 scripts/exp1/phase2_warmstart_train.sh >> ~/programs/exp1/logs/chain.log 2>&1 || { echo "phase2 failed" >> ~/programs/exp1/logs/chain.log; exit 1; }
echo "$(date -u +%FT%TZ) S_W trained" >> ~/programs/exp1/logs/chain.log
scripts/exp1/phase3_round1.sh >> ~/programs/exp1/logs/chain.log 2>&1
echo "$(date -u +%FT%TZ) phase 3 done" >> ~/programs/exp1/logs/chain.log
