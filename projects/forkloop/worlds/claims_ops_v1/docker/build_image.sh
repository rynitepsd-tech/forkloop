#!/usr/bin/env bash
# Build the claims-ops-v1 golden Docker image (x86-64 host with Docker >= 23 / BuildKit).
#
#   worlds/claims_ops_v1/docker/build_image.sh [--version 1] [--tag forkloop/claims-ops-v1:1] [--no-cache]
#   worlds/claims_ops_v1/docker/build_image.sh --version 2 --update-from forkloop/claims-ops-v1:1
#   worlds/claims_ops_v1/docker/build_image.sh --version 3 --update-from forkloop/claims-ops-v1:2 --rebake
#
# --update-from GOLDEN  only replaces the boot/agent scripts on top of an existing golden image (same world
#                       state, same Chrome profile; Dockerfile.runtime), then runs phase 3. Use it for script
#                       fixes: a full re-bake gives Chrome a new random field-trial assignment and the toolbar
#                       renders slightly differently (docs/docker-world.md).
# --rebake              with --update-from: also re-run the bake (browser_setup.sh logins) on the updated
#                       image, reusing its Chrome profile. Needed when the world clock changes: the portal
#                       refuses a session cookie issued "in the future".
# --world-clock ISO     the clock every container starts at (default 2026-09-07T09:00:00Z, tasks' ANCHOR day);
#                       the bake runs one hour earlier so the baked session is valid from the first second.
#
# Phase 1  docker build   -> forkloop/claims-ops-v1-base:<v>   packages, OpenEMR 8.3.0, both DBs seeded
#                            (the unchanged build.sh + openemr/install.sh, through the systemctl shim)
# Phase 2  bake           -> boots the base image, runs browser_setup.sh (Chrome logs into both apps),
#                            stops everything cleanly and `docker commit`s the golden image
#                            forkloop/claims-ops-v1:<v>
# Phase 3  verify         -> boots the golden image once and checks it comes up logged into the portal
#
# The golden image is the Docker counterpart of FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1; point the
# backend at it with FORKLOOP_DOCKER_IMAGE (see docs/docker-world.md).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT="$(cd "$HERE/../../.." && pwd)"   # projects/forkloop
VERSION=1
TAG=""
UPDATE_FROM=""
REBAKE=0
WORLD_CLOCK=2026-09-07T09:00:00Z
BUILD_ARGS=()
OWNER="${FORKLOOP_DOCKER_OWNER:-forkloop}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --no-cache) BUILD_ARGS+=(--no-cache); shift ;;
    --update-from) UPDATE_FROM="$2"; shift 2 ;;
    --rebake) REBAKE=1; shift ;;
    --world-clock) WORLD_CLOCK="$2"; shift 2 ;;
    -h|--help) sed -n 2,16p "$0"; exit 0 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
TAG="${TAG:-forkloop/claims-ops-v1:$VERSION}"
BASE="forkloop/claims-ops-v1-base:$VERSION"
log() { printf '[image %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
LABELS=(--label forkloop=1 --label "forkloop_owner=$OWNER")
BAKE_CLOCK="$(date -u -d "@$(( $(date -u -d "$WORLD_CLOCK" +%s) - 3600 ))" +%Y-%m-%dT%H:%M:%SZ)"
BUILD_ARGS+=(--build-arg "WORLD_CLOCK=$WORLD_CLOCK")

if [[ -n "$UPDATE_FROM" ]]; then
  OUT="$TAG"; [[ $REBAKE -eq 1 ]] && { BASE="forkloop/claims-ops-v1-pre:$VERSION"; OUT="$BASE"; }
  log "runtime update: $UPDATE_FROM + this checkout's boot/agent scripts -> $OUT"
  DOCKER_BUILDKIT=1 docker build --platform linux/amd64 "${BUILD_ARGS[@]}" --build-arg "GOLDEN=$UPDATE_FROM" \
    --build-arg "VERSION=$VERSION" -f "$HERE/Dockerfile.runtime" -t "$OUT" "$CONTEXT"
fi
if [[ -z "$UPDATE_FROM" || $REBAKE -eq 1 ]]; then
if [[ -z "$UPDATE_FROM" ]]; then
log "phase 1: docker build $BASE (context $CONTEXT)"
t0=$(date +%s)
DOCKER_BUILDKIT=1 docker build --platform linux/amd64 "${BUILD_ARGS[@]}" \
  -f "$HERE/Dockerfile" -t "$BASE" "$CONTEXT"
log "phase 1 took $(( $(date +%s) - t0 )) s"
fi

log "phase 2: bake (browser_setup.sh inside a booted base container)"
t1=$(date +%s)
BAKE="fl-bake-$$"
cleanup() { docker rm -f "$BAKE" >/dev/null 2>&1 || true; }
trap cleanup EXIT
log "bake clock $BAKE_CLOCK (world clock $WORLD_CLOCK)"
docker run -d --name "$BAKE" "${LABELS[@]}" --security-opt seccomp=unconfined --shm-size 512m \
  -e "FORKLOOP_WORLD_CLOCK=$BAKE_CLOCK" "$BASE" >/dev/null
docker exec "$BAKE" /usr/local/forkloop/bake.sh
docker stop -t 30 "$BAKE" >/dev/null
docker commit \
  --change 'LABEL forkloop.kind=golden' \
  --change "ENV FORKLOOP_WORLD_CLOCK=$WORLD_CLOCK" --change "LABEL forkloop.world_clock=$WORLD_CLOCK" \
  --change "LABEL forkloop.world=claims-ops-v1 forkloop.version=$VERSION forkloop.base=$BASE" \
  --change "LABEL forkloop.built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "$BAKE" "$TAG" >/dev/null
docker rm "$BAKE" >/dev/null
log "phase 2 took $(( $(date +%s) - t1 )) s"
fi

log "phase 3: verify $TAG boots logged in"
VERIFY="fl-verify-$$"
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
[[ "$title" == "Claims - Meridian Provider Portal"* ]] || { log "golden image is not logged into the portal"; exit 1; }
docker rm -f "$VERIFY" >/dev/null
log "golden image: $TAG  ($(docker image inspect -f '{{.Id}}' "$TAG"))"
log "export FORKLOOP_DOCKER_IMAGE=$TAG"
