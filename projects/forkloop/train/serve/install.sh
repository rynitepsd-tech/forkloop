#!/bin/bash
# Pinned serving + training venv for the Qwen3.5-architecture students (Holo-3.1-9B, Qwen3.8-27B).
# Measured on forkloop-dev (1x H100 PCIe 80 GB, driver 570.148.08 = CUDA 12.8, Ubuntu 22.04), 2026-09-29.
#
# Why these pins:
#   - vllm 0.30.0 (2026-09-22) pins torch==2.13.0; the default PyPI torch 2.13.0 wheel is CUDA 13.0,
#     which needs driver >= 580. The +cu129 builds (vLLM GitHub release / wheels.vllm.ai, PyTorch cu129
#     index) run on this 12.8 driver through CUDA minor-version compatibility.
#   - transformers 5.17.0 (>= 5.2 has Qwen3_5ForConditionalGeneration; vllm needs >= 5.10.4). One venv
#     for serving AND training so the image processor and chat template code are byte-identical in
#     both paths (train/parity.py checks the token ids anyway).
#   - flash-linear-attention 0.5.2 + causal-conv1d 1.7.0 (built from source: no wheel on PyPI) give
#     transformers the fast Gated DeltaNet / short-conv kernels for training.
#
#   usage: train/serve/install.sh            (on the box; needs uv, ~5 min + ~10 min conv1d build)
#   Idempotent: re-running reuses the venv and uv skips packages already at the pinned version.
#   Driver: the cu129 wheels need an NVIDIA driver >= 525 (CUDA 12 minor-version compatibility); the
#   default PyPI builds (CUDA 13) would need >= 580. The script refuses older drivers.
set -euxo pipefail
export PATH=$HOME/.local/bin:/usr/local/cuda/bin:$PATH
V=${FORKLOOP_VENV:-/home/ubuntu/venvs/fl}
DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
if [ "${DRIVER%%.*}" -lt 525 ]; then echo "NVIDIA driver $DRIVER < 525: the cu129 wheels will not run" >&2; exit 1; fi
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
[ -x $V/bin/python ] || uv venv --python 3.12 $V
export VIRTUAL_ENV=$V
uv pip install "vllm==0.30.0+cu129" "torch==2.13.0+cu129" "torchvision==0.28.0+cu129" "torchaudio==2.11.0+cu129" \
  --extra-index-url https://wheels.vllm.ai/0.30.0/cu129 --extra-index-url https://download.pytorch.org/whl/cu129 \
  --index-strategy unsafe-best-match
uv pip install "transformers==5.17.0" "peft==0.21.0" "accelerate==1.15.0" "flash-linear-attention==0.5.2" \
  pytest httpx pillow pyyaml --extra-index-url https://download.pytorch.org/whl/cu129 --index-strategy unsafe-best-match
# causal-conv1d ships only an sdist: build for the local GPU arch (H100 = 9.0, A100 = 8.0), ~10 min
ARCH=${TORCH_CUDA_ARCH_LIST:-$($V/bin/python -c "import torch; m,n=torch.cuda.get_device_capability(); print(f'{m}.{n}')")}
MAX_JOBS=${MAX_JOBS:-16} TORCH_CUDA_ARCH_LIST=$ARCH uv pip install "causal-conv1d==1.7.0" --no-build-isolation
$V/bin/python -c "import torch, vllm, transformers, peft, fla, causal_conv1d; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), vllm.__version__, transformers.__version__, peft.__version__, fla.__version__, causal_conv1d.__version__)"
