# Reproducing the Solari issues from the 2026-09-02 support email

One entry point per item in the email (`docs/solari-message.md`). Every
script kills what it creates and deletes the snapshots it takes; nothing here
touches the golden images. All of them need `SOLARI_API_KEY` and a paid plan
(desktops are 402 on Free; spike 0 is the headless variant for Free keys).

```bash
cd projects/forkloop
source ~/.config/forkloop/env            # or export SOLARI_API_KEY=...
./venv/bin/pip install -e ".[dev,world,teacher]"
```

| Email item | What it shows | Command | Where the numbers landed |
| --- | --- | --- | --- |
| 1. `revert()` → 409 `Not revertable`, running machine destroyed | revert on running + paused sandbox, on a desktop after `snapshot()`; machine state afterwards | `./venv/bin/python spikes/spike_00_sandbox_probe.py` (sandbox) and `./venv/bin/python spikes/spike_01_revert_latency.py --iterations 3` (desktop) | `spikes/results.jsonl`, `docs/spikes.md` |
| 2. `snapshot()` → 409 `Not snapshottable` on a `from_snapshot` machine | fork the golden, call `snapshot()` three times, then the same on a fresh desktop | `./venv/bin/python spikes/spike_02_fork_independence.py --forks 1` (fork), then `spike_01` (fresh desktop snapshot succeeds) | `docs/limitations.md` "Forks cannot be snapshotted" |
| 3. `recordingUrl` empty on `/sandboxes` desktops | record.start/stop on a plain desktop, a `from_snapshot` desktop with `record=True`, and one without; prints `recordingUrl` and the in-VM mp4 size | `./venv/bin/python spikes/spike_03_record_with_snapshot.py` | `docs/spikes.md` spike 3 |
| 4. `disk_gb` ignored (4 GB disk) | `build-world` requests `disk_gb: 10`; the build log shows the 4 GB device (`df -h /`) | `./venv/bin/python -m forkloop.cli build-world --disk-gb 10 --attach <machine-id>` on a machine created with `disk_gb=10`; `spikes/spike_00_sandbox_probe.py` prints `df -h /` for a sandbox | `docs/buildlog.md`, `docs/limitations.md` |
| 5. Snapshot lineage cannot be flattened | `delete_snapshot(parent)` while a child exists | `./venv/bin/python -c "import asyncio; from forkloop.backends.solari import SolariBackend as B; b=B(); print(asyncio.run(b.delete_snapshot('snap_dl4driq97904')))"` → 409 while `snap_dl4e05ciyt1p` exists | `docs/HANDOFF.md` |
| (new) forks vanish mid-episode | a `from_snapshot` desktop dies 14–143 s after creation: control channel drops, reconnect answers HTTP 404, `list` no longer shows it; 5 of 8 in 30 min on host `desktop-pool-i-00cac13223691ff7d` (vm_000176/178/183, 02:42–02:49Z on 2026-09-03), then two more on `i-00cac…` (vm_000216, 03:11Z) and `desktop-pool-i-087d4044cdb8ae6ef` (vm_000022, 03:40Z) during teacher runs — so not a single host | `./venv/bin/python scripts/chrome_crash_probe.py --replay 'runs/teacher-f3-s10-14-8gb/episodes/resolve_denial-train-000011-*' --repeat 3` → rows with `error` in `runs/chrome_probe/results.jsonl` carry the machine id (host and vm number decode from its base64 prefix) | `docs/spikes.md` |
| (new) `cpu`/`mem_mb` ignored on `from_snapshot` | a fork requested at 4 vCPU / 8 GB reports `nproc` 2 and 4031 MB in `free -m` | `./venv/bin/python -m forkloop.cli collect --seeds 10 --cpu 4 --mem-mb 8192 …` then read `runs/<run>/episodes/*/diagnostics/sys.txt` | `docs/limitations.md` |
| 429 `Too many concurrent sessions` with zero sessions listed | `list_machines` shows the account's sessions next to a failing `create` | `./venv/bin/python -m forkloop.cli reap --dry-run` (lists), then any `collect`/`run` command (creates); the pool logs `create_retry` events with the 429 body | `runs/<run>/run.json`, `docs/spikes.md` |

`bash spikes/run_all.sh` runs spikes 1–6 back to back (about 25 minutes and
$0.40 of Starter compute) and leaves a summary table in `spikes/results.jsonl`.

## What the harness does about each one today

- **revert()**: `WorkerPool` catches the refusal, logs
  `revert_unsupported_fell_back_to_fork`, and switches to fork mode for the
  rest of the run (`forkloop/pool.py`). Nothing calls `revert()` on this
  account any more.
- **Fork snapshots**: `collect --best-of 1` only; golden images are rebuilt
  from scratch with `forkloop build-world`.
- **recordingUrl**: the recorder pulls the mp4 out with `files.read`; nothing
  depends on the presigned upload.
- **Disk**: the world build purges VS Code and LibreOffice; episodes have
  about 200 MB of headroom.
- **Stuck sessions / 429**: `WorkerPool._create` re-lists the account's
  forkloop-tagged machines and kills orphans (a different `run_id`) before
  every retry, and `collect --reset-retries 2` re-queues a seed whose reset
  failed instead of dropping it. A session that Solari counts but does not
  list is still invisible to us; that is the case only Solari can clear.
