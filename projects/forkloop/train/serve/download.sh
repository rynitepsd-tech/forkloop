#!/bin/bash
# Download the pinned student weights into $HF_HOME (the persistent NFS cache). No token needed (ungated).
#   usage: train/serve/download.sh [holo31-9b] [qwen38-27b]      (default: both; Holo ~19 GB, Qwen ~56 GB)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/env.sh"
export HF_HUB_OFFLINE=0
for p in "${@:-holo31-9b qwen38-27b}"; do
  for q in $p; do
    case "$q" in
      holo31-9b)  hf download Hcompany/Holo-3.1-9B --revision bcd6a36af9f100f57cba28e2bf6481ac3feedf33 ;;
      qwen38-27b) hf download Qwen/Qwen3.8-27B --revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 ;;
      *) echo "unknown model $q" >&2; exit 2 ;;
    esac
  done
done
