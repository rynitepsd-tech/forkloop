# Local full-state-restore baseline

The fair comparison for **Chart 2** ("seconds to a clean, ready world"): what
it costs to get *the same state* back on a laptop with Docker, versus one
`revert()` on a Solari desktop.

Nothing here has been measured yet. `results.jsonl` is created by the first
`./restore.sh` run.

## What is restored (and why it is the same state)

The Solari golden snapshot holds three things forkloop cares about:

| State | On Solari (in the VM) | Here |
| --- | --- | --- |
| OpenEMR 8.3.0 + MariaDB | native install, `openemr` database | `openemr/openemr:8.3.0-2026-08-30` + `mariadb:10.11`, logical dump reloaded |
| Payer portal (FastAPI + SQLite) | `/var/lib/forkloop/portal/portal.db` + `uploads/` | same package bind-mounted into a `python:3.11-slim` container; `portal.db` + `uploads/` copied back |
| OpenEMR documents | `sites/default/documents` | copied back into the `openemr` container |
| Logged-in Chrome profile | in the VM's home dir | `run/chrome-profile/` on the host, copied back |

`restore.sh` is timed from `docker compose down` until **both HTTP health
endpoints return 200 and MariaDB answers `SELECT 1`** — the same readiness
gate forkloop's `reset()` applies on Solari (`docs/contracts.md` §11 step 3).
So both sides of Chart 2 stop the clock at "apps healthy, data back".

## What it does not restore

A Solari `revert()` is a memory + disk restore. This baseline is disk-only:

- **RAM**: no running processes, no warm caches, PHP opcache cold, MariaDB buffer pool cold.
- **Window layout**: Chrome is not running afterwards; a user (or the harness) has to launch it with `--user-data-dir=run/chrome-profile` and navigate.
- **Cursor/focus/clipboard**: gone.

That makes the baseline *optimistic* for the local side: a user who also had
to relaunch and re-position windows would take longer still. We report it
anyway because it is the honest "how would you do this without snapshots" number.

## Image tag

`openemr/openemr:8.3.0-2026-08-30` — the date-stamped build of the 8.3.0
release, checked on <https://hub.docker.com/r/openemr/openemr/tags>
(2026-09-01). The plain `8.3.0` tag is rebuilt daily; the dated tag is
immutable. `7.0.3` does not exist on Docker Hub (`7.0.4` is the last 7.x).
The VM install pins OpenEMR 8.3.0 too, so both sides run the same app version.

## Run

```bash
cd projects/forkloop/forkloop/bench/local_baseline
./bootstrap.sh          # up + portal schema/base data (one time)
# optional: log Chrome in and leave the profile at run/chrome-profile
./snapshot.sh           # capture state/  (local analogue of snapshot())
./restore.sh            # one timed restore (local analogue of revert())
./bench_local.sh 10     # N restores, prints p50/p95/p99
```

Requirements: Docker with Compose v2, `curl`, `python3` on the host.
OpenEMR first boot (schema install) can take several minutes; that is
one-time and not part of the timed restore.
