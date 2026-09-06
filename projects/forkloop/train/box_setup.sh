#!/bin/bash
# One-command setup of a rented GPU box for the Forkloop training ladder (measured on Lambda
# Ubuntu 22.04 + Lambda Stack, 2026-09-05/06). Reproduces every fix that cost time the first night:
#   - the box ships Python 3.10; the project needs 3.11 (apt has it, but only after `apt-get update`)
#   - `pip install -e ".[train]"` needs torchvision for the Qwen3-VL processor (now in the extra)
#   - vLLM pins its own torch, so it gets its own venv; its JIT shells out to `ninja`, which lives in
#     that venv's bin and must be on PATH or the engine core dies at startup
#   - HF_HOME on the persistent NFS mount so the 8.5 GB model survives the instance; the repo, venvs
#     and run directories (many small files) go on the local disk
#
#   usage: train/box_setup.sh <commit>          (run on the box, from anywhere)
#   env:   FORKLOOP_NFS (default /lambda/nfs/Forkloop), CLONE_DIR (default ~/forkloop-repo),
#          REPO_URL (default the GitHub origin)
#
# Afterwards `source ~/forkloop-env.sh` in every shell. The model cache is used OFFLINE by default;
# for a first download run once with `HF_HUB_OFFLINE=0 HF_TOKEN=<token>` in the environment (never
# write the token to a file in the repo).
set -euo pipefail
COMMIT=${1:?usage: box_setup.sh <commit>}
REPO_URL=${REPO_URL:-https://github.com/rynitepsd-tech/forkloop.git}
NFS=${FORKLOOP_NFS:-/lambda/nfs/Forkloop}
CLONE=${CLONE_DIR:-$HOME/forkloop-repo}
PROJ="$CLONE/projects/forkloop"

echo "== python3.11"
if ! command -v python3.11 >/dev/null 2>&1; then
  sudo apt-get update -q
  sudo apt-get install -y -q python3.11 python3.11-venv python3.11-dev
fi
python3.11 --version

echo "== NFS layout at $NFS"
mkdir -p "$NFS/hf" "$NFS/checkpoints"
df -h "$NFS" | tail -1

echo "== repo at $CLONE @ $COMMIT (local disk)"
if [ ! -d "$CLONE/.git" ]; then git clone -q "$REPO_URL" "$CLONE"; fi
git -C "$CLONE" fetch -q origin
git -C "$CLONE" checkout -q "$COMMIT"
git -C "$CLONE" log --oneline -1
cd "$PROJ"

echo "== train venv (torch + transformers + peft + torchvision)"
[ -x venv/bin/python ] || python3.11 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -e ".[train]"
./venv/bin/python -c "import torch, torchvision, transformers, peft; print('train venv ok: torch', torch.__version__, 'torchvision', torchvision.__version__, 'transformers', transformers.__version__, 'peft', peft.__version__, 'cuda', torch.cuda.is_available())"

echo "== serving venv (vLLM, separate torch pin)"
[ -x venv-vllm/bin/python ] || python3.11 -m venv venv-vllm
./venv-vllm/bin/pip install -q --upgrade pip
./venv-vllm/bin/pip install -q vllm
./venv-vllm/bin/python -c "import vllm; print('vllm', vllm.__version__)"

echo "== shell environment -> ~/forkloop-env.sh (no secrets)"
cat > "$HOME/forkloop-env.sh" <<ENV
# source me: Forkloop training/serving environment on this box
cd $PROJ
export PYTHONPATH=. HF_HOME=$NFS/hf HF_HUB_OFFLINE=\${HF_HUB_OFFLINE:-1} TOKENIZERS_PARALLELISM=false
export PATH=$PROJ/venv-vllm/bin:\$PATH   # vLLM's JIT needs ninja on PATH
ENV

echo "== GPU"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
echo "== done; next: source ~/forkloop-env.sh"
