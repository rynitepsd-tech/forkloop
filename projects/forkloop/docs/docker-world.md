# claims-ops-v1 on Docker

The claims-ops-v1 world — real OpenEMR 8.3.0, the synthetic payer portal, and Google Chrome on an XFCE
desktop (Xvfb `:0`, 1280x720x24, user `desktop`) — packaged as a Docker image, and a `DockerBackend` that
implements the `Backend`/`Machine` protocol (docs/contracts.md §2). The pool, `ResetController`, `Env`,
oracle, recorder, `forkloop run/collect/compare` and the correction engine's replay restore run on it
unchanged. It removes Solari's 2-desktop cap and ~100 s resets: dozens of worlds run on one host.

Everything below was measured on 2026-09-29 UTC (evening of 2026-09-28 US) unless marked otherwise.
Boxes: **dev** = `forkloop-dev` (26 vCPU, 221 GB; from ~03:08 UTC also another agent's training job, and
from ~04:25 another agent's kanboard worlds), **main** = `forkloop-main` (240 vCPU, 1.77 TB; the lead's
pilot worlds from ~03:15 UTC), **aux** = `forkloop-aux` (124 vCPU, idle when measured). Raw files are named
in each section; paths are under `projects/forkloop/runs/docker-world-20260928/` on the box named (runs/ is
not in git).

## Images

| Tag | Image ID | What | Status |
| --- | --- | --- | --- |
| `forkloop/claims-ops-v1:3` | `sha256:7324af036519…` | `:2` + world clock (every process at **2026-09-07 09:00:00 UTC at boot**, advancing in real time) + logins re-baked under that clock | **current**; on dev, main, aux |
| `forkloop/claims-ops-v1:2` | `sha256:52ab86606fb9…` | `:1` + XFCE boot fix (runtime update, pixel-identical to `:1`) | real clock — reschedule tasks are infeasible |
| `forkloop/claims-ops-v1:1` | `sha256:2fec2e906a76…` | first qualified golden | real clock; ~1 in 50 boots stalls 25 s |

Each is 2.84 GB (3.0 GB for `:3`); `docker save | gzip -1` streams it between hosts in 2–3 min.

## Quick start for a new developer

```bash
cd projects/forkloop
python3.11 -m venv venv && venv/bin/pip install -e ".[dev,world]"   # controller side, any x86-64 Docker host

# 1a. Use a built image: load it from a host that has it
ssh build-host 'docker save forkloop/claims-ops-v1:3 | gzip -1' | gunzip | docker load
# 1b. or build one (Docker >= 23 with BuildKit; ~2.5 min + ~1 min bake from a cold cache)
worlds/claims_ops_v1/docker/build_image.sh --version 4
#     script-only changes on top of an existing image (keeps the world and the Chrome profile byte-identical):
worlds/claims_ops_v1/docker/build_image.sh --version 4 --update-from forkloop/claims-ops-v1:3
#     ... plus re-running the logins (needed when the world clock changes):
worlds/claims_ops_v1/docker/build_image.sh --version 4 --update-from forkloop/claims-ops-v1:3 --rebake

# 2. Point forkloop at it (the image is the golden "snapshot")
export FORKLOOP_DOCKER_IMAGE=forkloop/claims-ops-v1:3
export FORKLOOP_DOCKER_CONCURRENCY=16          # cap of running worlds on this Docker host (default 8)

# 3. One episode, recorded
venv/bin/forkloop run --backend docker --policy random --family resolve_denial --seed 2 --max-steps 5 --runs runs/docker-demo

# 4. N worlds in parallel (one container per pool worker; revert = fresh container from the image)
venv/bin/forkloop collect --backend docker --concurrency 16 --policy <policy> --families resolve_denial --seeds 0-99
#    comparisons: `backend: docker` in the YAML, then `forkloop compare --config ... --out ...`

# 5. Qualification / measurements (JSON + PNGs under --out)
venv/bin/python worlds/claims_ops_v1/docker/qualify.py resets --out runs/q/resets --split train_v2 \
    --families reschedule_constrained update_insurance_reconcile resolve_denial compose_claims --seeds 10000,10001
venv/bin/python worlds/claims_ops_v1/docker/qualify.py gui     --out runs/q/gui       # UI solve + DIRECT_DB_WRITE controls
venv/bin/python worlds/claims_ops_v1/docker/qualify.py replay  --out runs/q/replay    # correction-engine replay determinism
venv/bin/python worlds/claims_ops_v1/docker/qualify.py latency --out runs/q/latency
venv/bin/python worlds/claims_ops_v1/docker/qualify.py restore --out runs/q/restore --n 12
venv/bin/python worlds/claims_ops_v1/docker/qualify.py load    --out runs/q/load --worlds 16 32 64
venv/bin/python worlds/claims_ops_v1/docker/qualify.py checkpoint --out runs/q/checkpoint

# Cleanup of anything left behind
docker rm -f $(docker ps -aq --filter label=forkloop.world_container=1)
```

In Python:

```python
from forkloop.backends.docker import DockerBackend
from forkloop.env import Env
from forkloop.pool import WorkerPool
from forkloop.world import load_world

world = load_world("claims-ops-v1")
backend = DockerBackend.for_world(world, concurrency_cap=16)   # sets the world's golden id to the image if unset
pool = WorkerPool(backend, world, size=16, mode="revert")
env = Env(world, backend, family="resolve_denial", pool=pool)
obs, info = await env.reset(seed=2)
```

Configuration (environment, like the other backends): `FORKLOOP_DOCKER_IMAGE` (default
`forkloop/claims-ops-v1:1` — set it), `FORKLOOP_DOCKER_HOST` (a `DOCKER_HOST` value, e.g. `ssh://user@box`;
default local daemon), `FORKLOOP_DOCKER_CONCURRENCY` (default 8; counts every running world container on
the daemon, other users' included), `FORKLOOP_DOCKER_OWNER` (`forkloop_owner` label),
`FORKLOOP_DOCKER_SECCOMP` (default `unconfined`, see Limitations), `FORKLOOP_DOCKER_NETWORK` (default
Docker's bridge; `none` works and is slightly faster at scale), `FORKLOOP_DOCKER_SNAPSHOT_DB`
(`flush`|`stop`|`none`), `FORKLOOP_DOCKER_READY_TIMEOUT_S` (120), `FORKLOOP_DOCKER_PNG_LEVEL` (1),
`FORKLOOP_DOCKER_KEEP=1` (keep containers on `backend.close()`), `FORKLOOP_WORLD_CLOCK` (ISO time or `real`;
passed to containers when set; the image default is `2026-09-07T09:00:00Z`). The pool reads the golden id
from `FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1`; `DockerBackend.for_world` (used by the CLI) sets it to the
image when unset, and a Solari id (`snap_…`) left there is refused with an explanation.

## How it works

| Contract (§2) | Docker |
| --- | --- |
| `Backend.create(from_snapshot=img)` | `docker run -d --pull never` of the golden image or a committed checkpoint; wait for `/run/forkloop/ready` |
| `exec`, `read_file`, `write_file` (controller channel, root) | the in-container agent (`agent.py`) over one persistent `docker exec -i` pipe, JSON lines matched by id |
| `screenshot` (agent channel) | same pipe: `XGetImage` of `:0` → PNG (no cursor, like Solari) |
| `click/double_click/move/scroll/drag/type_text/press` | same pipe: one `xdotool` call; chords are one xdotool string (`ctrl+a`); `scroll` = Page_Down/Page_Up `round(amount/3)` times, exactly `SolariMachine.scroll` |
| `snapshot()` | DB flush, then `docker commit --pause` → `<repo>:snap-<hex>` — a **filesystem** checkpoint |
| `revert(img)` | `docker rm -f` + `docker run img` under the same container name (= machine id) |
| `kill` / `kill_machine` | `docker rm -f` |
| `list_machines` / `list_snapshots` / `delete_snapshot` | `docker ps` / `docker images` by `forkloop*` labels / `docker rmi` (the configured golden image is refused) |

Why a pipe instead of `docker exec` per call: one `docker exec … true` costs 66–110 ms; a request on the
open pipe costs 2–4 ms. Every container carries `forkloop=1`, `forkloop_owner=<owner>`,
`forkloop.world_container=1`, the image, and each metadata key as `forkloop.meta.<key>` (the pool's
`run_id` reaping works as on Solari). `DockerBackend.snapshot_semantics == DockerMachine.snapshot_semantics
== "filesystem"`. A container that misses its ready deadline leaves its `docker logs` tail in
`DockerBackend.boot_failures` and in the error.

**Image build.** `Dockerfile` installs the desktop (Ubuntu 22.04, as the Solari `default` template: Xvfb,
XFCE 4.16 with the Yaru theme, Google Chrome stable 154.0.8037.57, Ubuntu/Liberation fonts), pre-installs
the LAMP packages, then runs the **unchanged** `build.sh --headless` → `openemr/install.sh --with-demo-data`
(MariaDB 10.6.23, PHP 8.3.35, OpenEMR 8.3.0 with the forkloop base population; portal DB seeded). A
`systemctl` shim that exists only in the image maps their `systemctl` calls onto `svc.sh`; the Solari path
never sees it. `build_image.sh` then boots the base image with the world clock one hour before its start,
runs the **unchanged** `browser_setup.sh` (portal login agent/agent, OpenEMR login admin/pass, claims
list), closes Chrome through the window manager so the profile is flushed (the portal's 30-day
`portal_session` cookie is on disk), stops the databases cleanly and `docker commit`s the golden image.

**Boot** (`entrypoint.sh`, under tini): world clock first (below), then services in parallel (MariaDB,
php-fpm + Apache, the portal unit read from world.py's `PORTAL_SYSTEMD`, OpenEMR's first request), Xvfb, a
session D-Bus at `/run/desktop/bus`, the XFCE components in xfce4-session's own order (settings daemon →
window manager → panel → desktop, each waited for), then Chrome as `desktop` with exactly
`browser_setup.sh`'s flags and profile on `http://localhost:8080/claims`, then `/run/forkloop/ready`.
Per-boot stage timings are in `/run/forkloop/boot.json`; `DockerMachine.last_boot` has them plus the
controller's `docker rm`/`run` times.

## World clock (image `:3`)

Tasks are generated relative to `ANCHOR = 2026-09-07` (tasks/common.py): appointments are "within the next
two weeks", claims and documents are dated relative to it. On Solari the VM clock comes back from the
snapshot (Sept 15). In `:1`/`:2` containers ran on the real date (Sept 29), OpenEMR's calendar opened weeks
after the appointments and every reschedule task was infeasible (lead's pilot: teacher 0/10).

`:3`: every process in the container sees one clock that starts at `FORKLOOP_WORLD_CLOCK` (default
`2026-09-07T09:00:00Z`, image label `forkloop.world_clock`) when the container boots and advances in real
time. libfaketime 0.9.10 (built from the pinned upstream tarball) is preloaded into every process
(`LD_PRELOAD` in the image env); the entrypoint computes ONE relative offset before anything starts and
writes it to `/etc/faketimerc`; the monotonic clock is never faked (`FAKETIME_DONT_FAKE_MONOTONIC=1`).
Details that took measurement:

- With libfaketime's `FAKE_PTHREAD` feature (Ubuntu's 0.9.8 package and upstream 0.9.10 alike) headed
  Chrome segfaults at start, even at a zero offset. Without it, MariaDB's 1 s realtime waits return at
  once and InnoDB aborts ("Long wait (601 seconds) for double-write buffer flush"). So the library is built
  twice: the full one for everything, one without `FAKE_PTHREAD` for Chrome, which `/usr/bin/google-chrome`
  (`chrome-wrapper.sh`) swaps in. Chrome's own timed waits use the monotonic clock.
- A process that starts with `FAKETIME` in its environment and later clears it (php-fpm's `clear_env`)
  falls back to the **real** clock after libfaketime's 10 s cache; one that never had it keeps re-reading
  `/etc/faketimerc`. So the offset is not exported, except to Chrome (its sandboxed renderers cannot read
  files and it never clears its environment).
- The portal refuses a session token issued "in the future" (itsdangerous), so the bake runs its logins at
  08:00 of the world day: the baked cookie is valid from the first second of every boot.

Verified inside containers made by `DockerBackend.create` (dev and main; `v3/`, and the lead's own check on
main): `date`, MariaDB `NOW()`, PHP CLI, PHP-FPM (an OpenEMR request), the portal's Python and Chrome's JS
`new Date()` (a live tab after 25 s) all read 2026-09-07 09:00:0x right after ready and advance correctly
(checked again after 60 s). The XFCE panel reads "Mon 7 Sep, 09:01". OpenEMR's
calendar opens on Monday, September 7, 2026; for reschedule_constrained seed 10000 (train_v2) the seeded
appointment (Wed 09/09 11:00, Xavier Ruiz, Dr. Marchetti) is in the current week, and a GUI edit to Thu
09/10 09:00 saved through "Provider not available, use it anyway?" → OK, with `pc_time` and the audit rows
stamped 2026-09-07 09:02 (`v3/reschedule-ui/step1…7.png`, `appointment_after_save.tsv`). No process
busy-loops (`docker stats` 45 s after boot: 21 % of one core for the whole world, no process above 2.1 %). `world.health` now includes a `world_clock` check
(enforced ±1 day on Docker; on Solari only reported).

## Fidelity with the Solari desktop

- **Screen layout: the same as recorded Solari episodes.** 26 px top panel (Applications menu, tasklist
  with the active-window underline, pager, sound, clock `%a %e %b, %H:%M`, user menu), Chrome 1280x720 at
  (0, 27), omnibox at (640, 90), page origin at y=114. Chrome is **not** maximised: on Solari every reset of
  the Sept-15 golden relaunches Chrome with `--window-position=0,0 --window-size=1280,720` and nothing
  maximises it, so all recorded episode screens show the unmaximised window (4 px frame, maximise button).
  `FORKLOOP_CHROME_MAXIMIZE=1` (container env) gives browser_setup.sh's maximised window instead.
- **Pixel comparison** of Docker step-0 screens with Solari step-0 screens of the same task (`fidelity/`
  on dev; image of 02:35 UTC): 2.05 % of pixels differ for resolve_denial 100351 heldout_seeds (Solari
  `model-upgrade-live-20260924` A-000011), 1.73 % for reschedule 2 train and 2.08 % for update_insurance 1
  train (Solari `luna-v5-fam12-s0-2`); threshold 24/255 per channel. The differences are glyph
  anti-aliasing and 1–2 px text advances; layout, colours and content match. The panel clock differs by
  nature (`:3` shows the world day, Solari the golden's frozen time).
- **Sessions.** Portal: logged in at every boot from the baked cookie. OpenEMR: logged out at every boot
  (session cookie) — the reschedule family starts on the OpenEMR login page by design, as on Solari.

## Qualification

| Check | Result | Evidence (dev unless noted) |
| --- | --- | --- |
| Full `ResetController.reset`, image `:3`, `train_v2`, 4 families × seeds 10000/10001/10002/10004 | 16/16 ok; feasibility (incl. compose_claims `part*` checks) and `world_clock` pass; 0 boot failures | `v3/resets-train_v2/` |
| Same, image `:3`, seeds 10005/10006 (aux) | 8/8 ok | aux `aux-v3/resets-train_v2/` |
| Same, image `:2`, 4 families × 4 seeds | 16/16 ok | `v2/resets-train_v2/` |
| Image `:1`, `train`, 3 families, revert (12) and fork (9) | 21/21 ok (one took 68.8 s: the XFCE stall fixed in `:2`) | `final/resets-*/` |
| Step-0 screens | portal families: logged in on `claims?status=DENIED` with the seeded claims; reschedule: OpenEMR login page | `*/shots/*-step0.png` (looked at) |
| UI solve of resolve_denial seed 2 through `Env.step` (scripts/gui_episode.py's actions), `:1`, `:2`, `:3` | reward 1.0, `OK`; `ui_path` passes | `final/gui/`, `v2/gui/`, `v3/gui/` |
| Same end state written by SQL (appeal row + claim status) | reward 0, `DIRECT_DB_WRITE`; every effect passes, only `ui_path` fails | same |
| OpenEMR login + chart through the GUI | `ui_milestones` `openemr_login` and `openemr_chart` true; viewing alone keeps `ui_path` + `no_collateral` OK | same |
| Direct SQL edit of an OpenEMR patient the GUI never touched | `DIRECT_DB_WRITE` | same |
| OpenEMR calendar edit (reschedule, `:3`) | saved via the "Provider not available" dialog | `v3/reschedule-ui/` |
| `forkloop run --backend docker --policy random` | episode recorded (6 steps, verdict, report) | `cli/random-uir-5/` |
| `forkloop compare` with `backend: docker` (2 scripted arms × 2 seeds) | complete; "Integrity issues: none detected" | `compare-scripted/` |
| Correction-engine replay (~30 GUI actions, 2 replays on fresh containers), `:1`, `:2`, `:3` | `tables_equal` true and `screen_distance` 0.0 at steps 0, 5, 16, 24, 30 | `replay/`, `v2/replay/`, `v3/replay/` |
| Checkpoint semantics (`snapshot` → more edits → `revert(snapshot)`), `:1` | rows before the checkpoint present, rows after it gone, health ok, Chrome restarted on the claims list | `final/checkpoint/` |

One oracle property surfaced (not Docker-specific): OpenEMR's audit match is `loose` (world.yaml), so a
direct SQL edit of the patient whose chart the GUI had opened passes `ui_path` (any log row for that patient
after the watermark counts). `no_collateral` still catches it in a real task.

## Measurements

### Single world, image `:3` (aux, idle, 124 vCPU; `aux-v3/`, 05:19–05:22 UTC)

| | p50 | p90 | n |
| --- | --- | --- | --- |
| screenshot (PNG zlib level 1, ~118 KB) | 27.7 ms | 29.0 ms | 100 |
| screenshot, zlib level 6 (~107 KB) | 50.0 ms | 53.8 ms | 30 |
| click | 4.7 ms | 5.1 ms | 100 |
| move | 4.3 ms | 4.8 ms | 100 |
| key press | 17.6 ms | 18.3 ms | 100 |
| type 10 characters (xdotool 12 ms/char) | 74.4 ms | 83.2 ms | 10 |
| exec `true` (controller channel) | 2.1 ms | 2.3 ms | 100 |
| read_file | 0.6 ms | 0.7 ms | 100 |
| one `docker exec … true` (for comparison) | 66.2 ms | 72.0 ms | 20 |
| restore: revert (`docker rm -f` 0.54 s + `docker run` 0.22 s + boot) | 4.61 s | 4.65 s | 12 |
| restore: fork (`create`) | 3.85 s | 4.06 s | 12 |
| in-container boot (`services` stage) | 3.77 s | 3.81 s | 12 |
| full `ResetController.reset`, 4 families (train_v2) | 9.04 s | 9.21 s | 8 |
| — restore / seed / health / feasibility / baseline | 4.39 / 0.05 / 0.18 / 0.05 / 0.25 s | | |
| — initial_screen (portal families include a fixed 3 s wait in `open_initial_screen`) / stable_screen | 3.46 / 0.48 s | | |

On the shared dev box the same operations were slower (e.g. `:2` 30 reverts p50 6.58 s / p90 8.00 s and 30
forks p50 6.12 s with 0 boot failures in 60 boots, `v2/restore/`; `:3` full reset p50 13.9 s, `v3/`;
screenshot p50 29.7 ms at 03:07 and 53.5 ms at 03:43 UTC with other agents' load). A single click was
109.5 ms p50 before `--delay 0` (xdotool sleeps 100 ms after a click by default).

`snapshot()` (docker commit of a live world, `:1`): 2.75 s in `flush` mode, 3.06 s in `stop` mode;
checkpoint layer 185 MB / 172 MB; reverting to it 7.1–7.3 s (`final/checkpoint/checkpoint.json`).

### Many worlds on one host (main, 240 vCPU, image `:1`)

Worst case: every world resets at the same moment. Round 1 = create + full reset; steady = 60 s (30 s in
the reruns) of screenshot/click/key/exec rounds on every world with 0.5 s think time; round 2 = revert +
full reset. `main-load/` used the first backend (create lock held across `docker run`), the two 64-world
reruns the current one. Files on main under `~/dw/repo/projects/forkloop/runs/docker-world-20260928/`.

| worlds | round 1: all done / per-reset p50 / p90 | round 2 (revert): all done / p50 / p90 | screenshot p50/p90 | click p50/p90 | key p50/p90 | exec p50/p90 | loadavg |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 16 | 19.0 s / 12.4 / 16.6 s | 15.7 s / 13.2 / 15.5 s | 30.4 / 38.8 ms | 6.8 / 8.1 ms | 19.5 / 20.7 ms | 2.6 / 3.2 ms | 7.6 |
| 32 | 30.1 s / 18.2 / 26.7 s | 24.3 s / 19.1 / 24.1 s | 30.5 / 39.0 ms | 7.3 / 8.9 ms | 20.2 / 21.8 ms | 2.8 / 3.5 ms | 16.6 |
| 64 | 54.7 s / 30.1 / 49.7 s | 42.4 s / 35.3 / 41.6 s | 36.2 / 51.1 ms | 10.3 / 16.8 ms | 22.5 / 29.4 ms | 3.3 / 6.2 ms | 31.0 |
| 64, create-lock fix | 36.7 s / 29.3 / 35.8 s | 43.1 s / 33.5 / 42.4 s | 40.6 / 60.4 ms | 11.6 / 21.9 ms | 23.1 / 33.1 ms | 3.4 / 6.9 ms | 67.3 |
| 64, fix + `--network none` | 34.4 s / 27.7 / 33.3 s | 35.5 s / 30.6 / 35.0 s | 44.7 / 61.1 ms | 14.0 / 23.1 ms | 23.5 / 34.8 ms | 3.6 / 8.0 ms | 122.3* |

All 64/64 resets succeeded in every round; 0 boot failures. *The lead's pilot worlds may have started
during this run. Per world at steady state: ~550 MB RAM, ~10 % of one core (`docker stats`). `:3` was not
load-tested at 16/32/64 (the lead's collection occupies main); per-world behaviour is unchanged by the
clock (single-world numbers above).

What grows with N is only the `restore` stage (p50 9 → 15 → 30 s for 16 → 32 → 64); seed, health,
baseline and initial screen stay flat. Inside the container every boot stage stretches, even trivial
ones, while the CPU is far from saturated (load 31 of 240). The cause is disk writes: MariaDB's startup
copies ~170 MB of its datadir into each container's writable layer (a fresh world's writable layer is
181 MB, of which 170 MB is `/var/lib/mysql`), so 64 simultaneous boots write ~11 GB. Experiment (dev, 16
simultaneous boots, `~/dw/burst.sh`): with the datadir copied into a tmpfs at boot, the writable layer drops
to 3.2 MB and the in-container boot p50 from 9.3 s to 7.5 s (all 16 ready in 10.7 s instead of 12.8 s; the
rest is Chrome start-up on 26 vCPU). Not enabled: see Decisions.

Capacity reading: steady-state interaction stays well inside the 150 ms target at 64 worlds (screenshot
p90 ≤ 61 ms). Resets are the limit only when they are synchronised; in a normal run episodes end at
different times, so most resets of a 64-world pool cost close to the single-world figure. A desynchronised
throughput run (resets per hour at N workers) was not measured.

## Snapshot semantics (read this before using `snapshot()`)

`DockerMachine.snapshot()` is `docker commit` of the running container: a **filesystem checkpoint, not a
running-process checkpoint**. Restoring it (`revert`, or `create(from_snapshot=…)`) boots a new container
from that filesystem: every service restarts; Chrome starts from its profile on disk. Preserved:
everything persisted — both databases (flushed first), seeded files, downloads, Chrome's persistent
cookies (portal session). **Lost:** browser memory — open tabs other than the start page, scroll positions,
history navigation, unsaved form input, focus — and the OpenEMR PHP session (its cookie is a session
cookie). **The world clock restarts** at `FORKLOOP_WORLD_CLOCK` (09:00) on every boot, so rows written
before a checkpoint can carry later timestamps than the restored clock. A Solari snapshot restores the
running desktop; the correction engine's `snapshot` strategy should not be used with Docker for
mid-episode checkpoints — use `replay` (measured deterministic above) or `reset`.

Database consistency (`FORKLOOP_DOCKER_SNAPSHOT_DB`): `flush` (default: `FLUSH TABLES` + `sync`, then a
*paused* commit — every process is frozen while the layer is copied, so the image is crash-consistent;
committed InnoDB transactions are in the redo log; the portal's SQLite rollback journal is replayed on
open), `stop` (clean MariaDB shutdown, commit, restart; no recovery needed at restore, +0.3 s), `none`.
Both modes restored exactly the pre-checkpoint rows in the test above. A committed profile says "Chrome
was running"; the entrypoint marks it cleanly exited before launch so no "Restore pages?" bubble appears.

CRIU (`docker checkpoint`) was not tried: it needs the daemon's experimental mode and the `criu` package on
the host, i.e. a restart of a shared Docker daemon, which was out of bounds for this work.

## Determinism for the correction engine

Measured on `:1`, `:2` and `:3`: identical tables and screens after replaying ~30 actions (portal appeal +
OpenEMR login + chart) on fresh containers. What keeps it so: every container boots from the same image
(same DB bytes, same Chrome profile) at the same world time; XGetImage has no cursor; the panel clock is
outside the digest (top 28 px masked); the entrypoint normalises Chrome's exit state (no restore bubble);
the OpenEMR session is never persisted (always the login page); Chrome's field-trial randomness is fixed
per image (Findings 4). Remaining sources of variation on screen: the portal's claim page prints `Last
updated <timestamp>` (world time of the write, so it varies by seconds between runs; below the digest's
thumbnail tolerance). The portal cookie in `:3` expires 30 days of world time after 08:00 on 2026-09-07,
i.e. never within an episode.

## Findings in existing world code and tools (small, documented, Docker-side workarounds)

1. `build.sh`'s space-saving `apt-get purge gcc g++ cpp` also purges **xfce4-session and
   x11-xserver-utils** on Ubuntu 22.04 (apt history). Harmless on a live Solari VM; a booted image has no
   XFCE session. The Dockerfile reinstalls them after build.sh.
2. After browser_setup.sh logs into OpenEMR, its last navigation to the portal is swallowed by OpenEMR's
   `beforeunload` **"Leave site?"** dialog (Chrome 154). `bake.sh` accepts it and navigates again. Agents
   meet the same dialog whenever they leave OpenEMR's main page in the same tab.
3. Through `xfce4-session`, 2 of the ~100 boots of 2026-09-29 (one in each of two 12-reset runs) stalled
   ~25 s (a D-Bus timeout) before starting any client; the boot missed its deadline, the pool replaced the
   machine and that reset took 65–69 s. From `:2` the components start directly (0 failures in the 100+
   boots since).
4. **Chrome field trials make separately baked images differ**: two bakes of the same Dockerfile rendered
   toolbar icons with different stroke weights (870 toolbar pixels), because Chrome randomises its
   field-trial entropy per profile. `:2`/`:3` therefore reuse `:1`'s profile (`--update-from`); `:2` is
   pixel-identical to `:1`. A policy `ChromeVariations: 2` would make bakes reproducible (and would equally
   apply to the Solari golden) — a world change, so not made here.
5. SIGTERM makes Chrome do a "SessionEnded" fast exit that left the cookie store unflushed; the bake closes
   the window through the window manager instead.
6. libfaketime: see World clock (FAKE_PTHREAD vs Chrome and MariaDB; php-fpm `clear_env`).

## Known limitations

- Snapshots are filesystem checkpoints and restart the world clock (above). No CRIU.
- `seccomp=unconfined` is the default so Chrome keeps its own namespace sandbox (Docker's default profile
  blocks the user namespaces it needs; the alternative, `--no-sandbox`, would change Chrome's command line).
- A from-scratch `build_image.sh` produces a new Chrome profile (new field-trial assignment, Findings 4);
  prefer `--update-from` for anything but a deliberate new world build.
- Images are x86-64 only (Chrome stable has no arm64 Linux build).
- `forkloop doctor` and `forkloop reap` are Solari/fake only; `forkloop report` prints "backend not
  recorded" for docker runs (label map in report.py, see Decisions).
- `record`, `template`, `timeout_ms` and `disk_gb` of `create()` have no Docker meaning and are ignored.
- The concurrency cap counts every running world container on the Docker daemon, including other users'.

## Decisions for the lead

1. Default Chrome geometry: unmaximised (matches recorded Solari screens) vs maximised (browser_setup.sh's
   intent). Implemented: unmaximised; `FORKLOOP_CHROME_MAXIMIZE=1` switches.
2. MariaDB datadir on tmpfs (measured faster at scale, 181 MB → 3 MB writable layer) needs `snapshot()` to
   copy the datadir back before committing; worth doing if runs are reset-bound at N ≥ 32.
3. `ChromeVariations: 2` for reproducible bakes (Findings 4).
4. `forkloop/report.py` `BACKEND_NOTES` (and the banner in report_html.py) have no `docker` entry; proposed:
   `"docker": "Recorded local Docker world (real OpenEMR 8.3 + synthetic payer portal, synthetic data, image
   FORKLOOP_DOCKER_IMAGE); verdict recorded from application databases, not re-executed"`.
5. `forkloop doctor --backend docker` is refused by doctor.py's backend check.
6. Checkpoints and the world clock: resume the clock from the checkpoint's world time (store it at
   `snapshot()`, read it at boot) if the `snapshot` strategy is ever used on Docker.
