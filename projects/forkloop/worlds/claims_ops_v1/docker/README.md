# claims-ops-v1 as a Docker image

The same world the Solari golden snapshot holds (OpenEMR 8.3.0 + the synthetic payer portal +
Google Chrome on an XFCE desktop, Xvfb `:0` 1280x720x24, user `desktop`) as a local image, driven by
`forkloop/backends/docker.py`. Full write-up, measurements and limitations: `docs/docker-world.md`.

Current image: `forkloop/claims-ops-v1:3` (sha256:7324af036519…): every process in the container runs on a
world clock that starts at 2026-09-07 09:00:00 UTC at boot (`FORKLOOP_WORLD_CLOCK`, libfaketime).

```bash
# build (x86-64 Docker host with BuildKit; ~3 min from scratch, the world layer is cached after that)
worlds/claims_ops_v1/docker/build_image.sh --version 4                                     # full build + bake
worlds/claims_ops_v1/docker/build_image.sh --version 4 --update-from forkloop/claims-ops-v1:3   # scripts only
worlds/claims_ops_v1/docker/build_image.sh --version 4 --update-from forkloop/claims-ops-v1:3 --rebake

# use it (the image is the golden "snapshot")
export FORKLOOP_DOCKER_IMAGE=forkloop/claims-ops-v1:3 FORKLOOP_DOCKER_CONCURRENCY=16
forkloop run --backend docker --policy random --family resolve_denial --seed 2 --max-steps 5
forkloop collect --backend docker --concurrency 16 --families resolve_denial --seeds 0-99 ...
# comparison configs: `backend: docker`
```

| File | Role |
| --- | --- |
| `Dockerfile` | base image: desktop + Chrome, LAMP, then the **unchanged** `build.sh`/`openemr/install.sh`; libfaketime |
| `Dockerfile.runtime` | `--update-from`: new boot/agent scripts (and the world clock) on top of an existing golden image |
| `Dockerfile*.dockerignore` | build context = `worlds/` + `forkloop/` only |
| `chrome-wrapper.sh` | `/usr/bin/google-chrome`: gives Chrome the libfaketime build without FAKE_PTHREAD (it segfaults with it) |
| `systemctl` | shim so the Solari scripts' `systemctl` calls work without systemd (image only) |
| `svc.sh` | start/stop MariaDB, php-fpm, Apache and the portal unit cleanly |
| `entrypoint.sh` | boot: services, Xvfb, XFCE, Chrome (browser_setup.sh's flags) → `/run/forkloop/ready` |
| `agent.py` | in-container helper on one persistent `docker exec -i` pipe: exec/files (controller channel), XGetImage screenshots and xdotool input (agent channel) |
| `bake.sh` | golden bake inside a booted base container (world clock at 08:00): `browser_setup.sh`, clean Chrome quit, clean DB stop |
| `build_image.sh` | build → bake → `docker commit` → verify (`--update-from`, `--rebake`, `--world-clock`) |
| `xfce/` | panel/session/theme settings matching the Solari desktop |
| `qualify.py` | qualification and measurement commands (resets, gui, replay, latency, restore, load, checkpoint) |

`docker commit` checkpoints (`Machine.snapshot`) are **filesystem** checkpoints: restoring one boots the
container again (services and Chrome restart, the world clock restarts at 09:00; tabs, unsaved form text and
the OpenEMR PHP session are not kept). `DockerBackend.snapshot_semantics == "filesystem"`.
