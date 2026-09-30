#!/bin/bash
# exp1 phase 5 launcher (run on main or aux): waits until all 9 trained adapters are present (and, on
# main, no repair process is running: the Docker world cap cannot hold both), then runs that box's
# phase-5 script. Main: shard 0/2, PER_MODEL 8 (11 models = 88 worlds < 96). Aux: shard 1/2, PER_MODEL 7
# (9 models left = 63 < 64).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
BOX=${1:?main|aux}
AN=${ADAPTERS_NAME:-adapters-v2}
if [ "$BOX" = main ]; then source "$HERE/env.sh"; AD=/home/ubuntu/programs/exp1/$AN; L=~/programs/exp1/logs; PM=${PER_MODEL:-8}; S=phase5_eval.sh
else source "$HERE/env-aux.sh"; AD=/home/ubuntu/programs/exp1aux/$AN; L=~/programs/exp1aux/logs; PM=${PER_MODEL:-7}; S=phase5_eval_aux.sh; fi
until [ "$(ls $AD/A?-s?/final/adapter_model.safetensors 2>/dev/null | wc -l)" -eq 9 ]; do sleep 60; done
if [ "$BOX" = main ] && [ "${STOP_REPAIRS:-0}" = 1 ]; then
  # protocol note 2026-09-30 08:45: repairs outside the budget window stop when training ends
  for s in exp1-repair4 exp1-repair5; do tmux kill-session -t $s 2>/dev/null; done
  echo "$(date -u +%FT%TZ) repairs outside the window stopped (training done)" >> $L/resume.log
  sleep 200
  forkloop reap-machines --config configs/exp1.yaml >> $L/reap-phase5.log 2>&1
fi
until { [ "$BOX" = aux ] || ! pgrep -f "[f]orkloop repair" >/dev/null; } && [ "$(docker ps -q | wc -l)" -le 4 ]; do sleep 60; done
echo "$(date -u +%FT%TZ) phase 5 ($BOX) starting" >> $L/resume.log
ADAPTERS_NAME=$AN PER_MODEL=$PM "$HERE/$S"
echo "$(date -u +%FT%TZ) phase 5 ($BOX) done" >> $L/resume.log
