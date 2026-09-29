# source me: student serving/training environment (no secrets). Works on forkloop-dev and forkloop-main.
if [ -z "${HF_HOME:-}" ]; then
  for d in /lambda/nfs/forkloop-useast1/hf /lambda/nfs/forkloop-usw3/hf; do
    if [ -d "$(dirname "$d")" ]; then export HF_HOME=$d; break; fi
  done
fi
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1} TOKENIZERS_PARALLELISM=false
export VIRTUAL_ENV=${FORKLOOP_VENV:-/home/ubuntu/venvs/fl}
export PATH=$VIRTUAL_ENV/bin:$HOME/.local/bin:/usr/local/cuda/bin:$PATH   # vLLM's JIT needs ninja on PATH
