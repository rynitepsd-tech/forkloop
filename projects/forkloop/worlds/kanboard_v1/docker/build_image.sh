#!/usr/bin/env bash
# Build the kanboard-v1 golden Docker image (x86-64 host with Docker >= 23 / BuildKit).
#
#   worlds/kanboard_v1/docker/build_image.sh [--version 1] [--tag forkloop/kanboard-v1:1] [--no-cache]
#
# Phase 1  docker build  -> forkloop/kanboard-v1-base:<v>   Kanboard v1.2.54 migrated + base population
# Phase 2  bake          -> boots the base image, logs Chrome into Kanboard (bake.sh), quits Chrome cleanly,
#                           stops the container and `docker commit`s the golden image forkloop/kanboard-v1:<v>
# Phase 3  verify        -> boots the golden image once and checks Chrome comes up logged in
#
# Every container and image carries forkloop=1 and forkloop_owner=$FORKLOOP_DOCKER_OWNER (default
# second-world-agent). Point the backend at the result with FORKLOOP_DOCKER_IMAGE (docs/second-world.md).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT="$(cd "$HERE/../../.." && pwd)"   # projects/forkloop
VERSION=1
TAG=""
BUILD_ARGS=()
OWNER="${FORKLOOP_DOCKER_OWNER:-second-world-agent}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --no-cache) BUILD_ARGS+=(--no-cache); shift ;;
    -h|--help) sed -n 2,13p "$0"; exit 0 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
TAG="${TAG:-forkloop/kanboard-v1:$VERSION}"
BASE="forkloop/kanboard-v1-base:$VERSION"
log() { printf '[image %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
LABELS=(--label forkloop=1 --label "forkloop_owner=$OWNER")
LOGIN_USER="$(cd "$CONTEXT" && python3 -c 'from worlds.kanboard_v1.base_data import AGENT_LOGIN; print(AGENT_LOGIN[0])')"
LOGIN_PASS="$(cd "$CONTEXT" && python3 -c 'from worlds.kanboard_v1.base_data import AGENT_LOGIN; print(AGENT_LOGIN[1])')"
BASE_SHA="$(cd "$CONTEXT" && python3 -c 'from worlds.kanboard_v1.base_data import base_sha256; print(base_sha256())')"

log "phase 1: docker build $BASE (context $CONTEXT)"
t0=$(date +%s)
DOCKER_BUILDKIT=1 docker build --platform linux/amd64 "${BUILD_ARGS[@]}" "${LABELS[@]}" \
  -f "$HERE/Dockerfile" -t "$BASE" "$CONTEXT"
log "phase 1 took $(( $(date +%s) - t0 )) s"

log "phase 2: bake (Chrome logs into Kanboard inside a booted base container)"
t1=$(date +%s)
BAKE="fl-kb-bake-$$"
cleanup() { docker rm -f "$BAKE" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$BAKE" "${LABELS[@]}" --security-opt seccomp=unconfined --shm-size 512m "$BASE" >/dev/null
docker exec "$BAKE" /usr/local/forkloop/bake.sh "$LOGIN_USER" "$LOGIN_PASS"
docker stop -t 30 "$BAKE" >/dev/null
docker commit \
  --change 'LABEL forkloop.kind=golden' \
  --change "LABEL forkloop.world=kanboard-v1 forkloop.version=$VERSION forkloop.base=$BASE" \
  --change "LABEL forkloop_owner=$OWNER forkloop.base_sha256=$BASE_SHA" \
  --change "LABEL forkloop.built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "$BAKE" "$TAG" >/dev/null
docker rm "$BAKE" >/dev/null
log "phase 2 took $(( $(date +%s) - t1 )) s"

log "phase 3: verify $TAG boots logged in"
VERIFY="fl-kb-verify-$$"
trap 'docker rm -f "$VERIFY" >/dev/null 2>&1 || true' EXIT
t2=$(date +%s%N)
docker run -d --name "$VERIFY" "${LABELS[@]}" --security-opt seccomp=unconfined --shm-size 512m "$TAG" >/dev/null
for _ in $(seq 1 600); do
  docker exec "$VERIFY" test -e /run/forkloop/ready -o -e /run/forkloop/failed && break
  sleep 0.2
done
docker exec "$VERIFY" cat /run/forkloop/boot.json
log "container start-to-ready: $(( ($(date +%s%N) - t2) / 1000000 )) ms (includes docker exec polling)"
docker exec "$VERIFY" test -e /run/forkloop/ready || { log "golden image did not become ready"; exit 1; }
title="$(docker exec "$VERIFY" runuser -u desktop -- env DISPLAY=:0 xdotool getactivewindow getwindowname)"
log "window title: $title"
[[ "$title" != *"Login"* ]] || { log "golden image is not logged into Kanboard"; exit 1; }
docker rm -f "$VERIFY" >/dev/null
log "golden image: $TAG  ($(docker image inspect -f '{{.Id}}' "$TAG"))"
log "export FORKLOOP_DOCKER_IMAGE=$TAG"
