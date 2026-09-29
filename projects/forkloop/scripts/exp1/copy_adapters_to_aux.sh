#!/bin/bash
# exp1 phase 4 → 5 (controller): copy main's finished adapters (A1/A2/A3 × seeds) to aux and verify
# sha256 of every file under final/. A3-s3 comes from dev via train_on_dev.sh fetch (to both boxes).
set -euo pipefail
MAIN=forkloop-main AUX=forkloop-aux
MA=/home/ubuntu/programs/exp1/adapters XA=/home/ubuntu/programs/exp1aux/adapters
for run in A1-s1 A1-s2 A1-s3 A2-s1 A2-s2 A2-s3 A3-s1 A3-s2; do
  ssh $MAIN "test -f $MA/$run/final/adapter_model.safetensors" || { echo "$run not finished on main" >&2; exit 1; }
  sum=$(ssh $MAIN "cd $MA/$run && find final -type f | sort | xargs sha256sum | sha256sum")
  ssh $MAIN "tar -C $MA -cf - $run" | ssh $AUX "mkdir -p $XA && tar -C $XA -xf - && chmod -R a+r $XA/$run"
  got=$(ssh $AUX "cd $XA/$run && find final -type f | sort | xargs sha256sum | sha256sum")
  [ "$got" = "$sum" ] || { echo "hash mismatch for $run" >&2; exit 1; }
  echo "$run → aux ok ${sum%% *}"
done
