#!/bin/bash
# exp1 phase 2: export the warm-start dataset W and train S_W (seed 0, 50 optimizer steps) on one GPU.
set -eu
source "$(dirname "$0")/env.sh"
DS=/home/ubuntu/programs/exp1/datasets/W
[ -d "$DS" ] || forkloop dataset --config configs/exp1.yaml --experiment exp1-warmstart --no-corrections --name "exp1 warm start W" --out "$DS"
cp -r "$DS" /lambda/nfs/forkloop-useast1/datasets/exp1-W 2>/dev/null || true
GPU=${GPU:-1} scripts/train_run.sh sw-seed0 "${GPU:-1}" 0 50 /home/ubuntu/programs/exp1/adapters "$DS"
chmod -R a+r /home/ubuntu/programs/exp1/adapters/sw-seed0
