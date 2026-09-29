#!/bin/bash
# exp1 phase 4, 9th run (A3 seed 3) on forkloop-dev (1x H100), driven from the controller.
#   scripts/exp1/train_on_dev.sh start   # copy the arm's matched-cost dataset from main, start training in tmux
#   scripts/exp1/train_on_dev.sh fetch   # after it finishes: copy the adapter to main and aux, verify sha256
# Same recipe as scripts/exp1/phase4_train_arms.sh: dataset W + <arm>-b100, 100 optimizer steps.
set -euo pipefail
RUN=${RUN:-A3-s3}; SEED=${SEED:-3}; ARM=${RUN%-s*}
MAIN=forkloop-main DEV=forkloop-dev AUX=forkloop-aux
MB=/home/ubuntu/programs/exp1/datasets
case "${1:?start|fetch}" in
start)
  ssh $MAIN "test -f $MB/budget/budget-report.json" || { echo "budget not built on main yet" >&2; exit 1; }
  [ "$(ssh $MAIN "sha256sum < $MB/W/manifest.json")" = "$(ssh $DEV "sha256sum < ~/exp1/datasets/W/manifest.json")" ] \
    || { echo "dataset W differs between main and dev" >&2; exit 1; }
  ssh $MAIN "tar -C $MB -cf - budget/$ARM-b100" | ssh $DEV "mkdir -p ~/exp1/datasets && tar -C ~/exp1/datasets -xf -"
  [ "$(ssh $MAIN "sha256sum < $MB/budget/$ARM-b100/manifest.json")" = \
    "$(ssh $DEV "sha256sum < ~/exp1/datasets/budget/$ARM-b100/manifest.json")" ] || { echo "copy mismatch" >&2; exit 1; }
  ssh $DEV "test -f ~/models/copy.done" || { echo "local weights not ready on dev" >&2; exit 1; }
  ssh $DEV "cd ~/repo/projects/forkloop && tmux new-session -d -s train-$RUN \
    'MODEL_DIR=/home/ubuntu/models/qwen3.8-27b scripts/train_run.sh $RUN 0 $SEED 100 /home/ubuntu/exp1/adapters \
     /home/ubuntu/exp1/datasets/W /home/ubuntu/exp1/datasets/budget/$ARM-b100; echo \$? > /home/ubuntu/exp1/adapters/$RUN.exit'"
  echo "started train-$RUN on $DEV" ;;
fetch)
  [ "$(ssh $DEV "cat ~/exp1/adapters/$RUN.exit 2>/dev/null")" = 0 ] || { echo "$RUN not finished (or failed) on dev" >&2; exit 1; }
  sum=$(ssh $DEV "cd ~/exp1/adapters/$RUN && find final -type f | sort | xargs sha256sum | sha256sum")
  ssh $DEV "tar -C ~/exp1/adapters -cf - $RUN" | ssh $MAIN "tar -C /home/ubuntu/programs/exp1/adapters -xf - && chmod -R a+r /home/ubuntu/programs/exp1/adapters/$RUN"
  ssh $DEV "tar -C ~/exp1/adapters -cf - $RUN" | ssh $AUX "mkdir -p /home/ubuntu/programs/exp1aux/adapters && tar -C /home/ubuntu/programs/exp1aux/adapters -xf - && chmod -R a+r /home/ubuntu/programs/exp1aux/adapters/$RUN"
  for h in "$MAIN:/home/ubuntu/programs/exp1/adapters" "$AUX:/home/ubuntu/programs/exp1aux/adapters"; do
    got=$(ssh "${h%%:*}" "cd ${h#*:}/$RUN && find final -type f | sort | xargs sha256sum | sha256sum")
    [ "$got" = "$sum" ] || { echo "adapter hash mismatch on ${h%%:*}" >&2; exit 1; }
  done
  echo "$RUN copied to main and aux: $sum" ;;
esac
