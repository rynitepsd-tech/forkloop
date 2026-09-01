# Day-1 spikes — results template

**Status: no numbers have been measured yet.** Every cell that says
*not yet measured* is exactly that. The scripts in `spikes/` are written
against the verified SDK surface (see "Verified so far" at the bottom) but
have not been run: they need a `SOLARI_API_KEY` on a paid plan (desktops
return `402 FeatureRequiresPlan` on Free).

Run everything: `SOLARI_API_KEY=... ./spikes/run_all.sh`. Each spike prints a
table and appends `{"spike", "ts", "metric", "value", "unit", "notes"}` lines
to `spikes/results.jsonl`. Fill this file from that log. Leftover VMs from a
crashed run: `python spikes/_common.py --reap` (kills everything tagged
`metadata.forkloop=spike`).

---

## Measured on 2026-09-01 — Free plan, headless sandboxes (spike 0)

The key available for testing is on the **Free** plan: `create_desktop` returns
`402 FeatureRequiresPlan`, so spikes 1–6 as written (they need a desktop)
could not run. A headless probe (`spikes/spike_00_sandbox_probe.py`, same
snapshot API) measured the following on `base` sandboxes, 2 vCPU / 4 GB:

| Measurement | Result |
| --- | --- |
| create from template → first command | 0.60 s |
| `snapshot()` (full ~6.0 GB disk image) | 14.2 / 16.3 / 19.1 / 19.6 s (4 samples) |
| `revert()` on a *running* sandbox | **409 `Not revertable`**, and the machine was `Not found` afterwards (destroyed) |
| `revert()` on a *paused* sandbox | **409 `Not revertable`**; machine survived, `resume()` worked |
| `pause()` | 14.5 s |
| `resume()` → reattach → first command | 0.40 s |
| `create(from_snapshot)` → first command | 17.9 s; file state restored |
| Guest | Debian 12, root, systemd 252 as PID 1, Python 3.11, curl, apt; no sqlite3/ps/mysql/sudo; 3.9 GB disk (2.2 GB free); egress to github.com OK |

**Decision:** on this plan `reset()` must be fork-mode (`kill` + `create(from_snapshot=golden)`,
≈18 s) — the pool's `mode="fork"`. Whether `revert()` is gated by plan or by
machine kind is unknown; the docs describe it as available on both. Ask Solari
(Discord) with the exact 409 body. **Do not call `revert()` on a running
machine you care about until that is answered: on this account it destroyed the
machine.** The revert-latency row for Chart 2 stays empty until a plan where
`revert()` is allowed.

## Spike 1 — revert latency and state fidelity

**Question.** How long is `revert()` wall-clock (API call → guest `health().ready`
→ two identical screenshot hashes), and does the screen come back to the
post-snapshot state?

**Command.** `python spikes/spike_01_revert_latency.py --iterations 20`

| metric | value | unit |
| --- | --- | --- |
| snapshot_s | not yet measured | s |
| revert_api_p50 (HTTP call only) | not yet measured | s |
| revert_wall_p50 | not yet measured | s |
| revert_wall_p95 | not yet measured | s |
| revert_wall_p99 | not yet measured | s |
| state_back_rate (post-revert hash ∈ post-snapshot hash set) | not yet measured | fraction |
| screen_stable_rate (stable within 15 s) | not yet measured | fraction |
| marker_text_back (clipboard read) | not yet measured | bool |

**Decision rule.**
- `revert_wall_p50` ≤ 10 s → `reset()` uses `revert()` on a warm worker (plan default); Chart 2 headline is this number.
- 10–30 s → still revert, but the pool pre-reverts idle workers so the agent never waits.
- > 30 s or `state_back_rate` < 0.95 → fall back to `create_desktop(from_snapshot=...)` per episode (spike 2 number) and re-examine what the snapshot contains.

## Spike 2 — fork independence

**Question.** Do two desktops created with `from_snapshot` share nothing (separate disks), and how long until a fork is `ready`?

**Command.** `python spikes/spike_02_fork_independence.py --forks 2`

| metric | value | unit |
| --- | --- | --- |
| fork_count (requested 2) | not yet measured | count |
| fork_ready_s (fork 0) | not yet measured | s |
| fork_ready_s (fork 1) | not yet measured | s |
| forks_same_start | not yet measured | bool |
| fork_independent | not yet measured | bool |

**Decision rule.**
- `fork_independent` must be true; if false, the search/branching rung of the plan is off the table and every episode runs on its own reverted worker.
- `fork_ready_s` is the "cold reset" number on Chart 2 and the cost input for `cost_per_1k_resets("from_snapshot", ...)`.
- A `429` on the second fork on Starter means the base was still alive; the script kills it first — if it still happens, the cap counts something else (report it).

## Spike 3 — recording with snapshots

**Question.** Does `record=True` work with `create_desktop(from_snapshot=...)`? With plain create + `revert()`? Does `record.start()/stop()` work in each case and does `recordingUrl` resolve?

**Command.** `python spikes/spike_03_record_with_snapshot.py`

| step | ok | exact error / detail |
| --- | --- | --- |
| A.create(record=True) | not yet measured | |
| A.record.start / stop / recordingUrl | not yet measured | |
| A.revert then record.start / stop | not yet measured | |
| B.create(from_snapshot, record=True) | not yet measured | |
| B.record.start / stop / recordingUrl | not yet measured | |
| C.create(from_snapshot) then record.start / stop | not yet measured | |

**Decision rule.**
- B works → episode videos come from Solari for free; the recorder's `episode.mp4` is optional.
- Only A works → record on the golden worker only (demo videos); episodes use the `shots/*.png` → ffmpeg path from contracts §10.
- Nothing works → ffmpeg path only. Either way nothing in the plan blocks on this.

## Spike 4 — does memory survive revert?

**Question.** Is a snapshot memory + disk (processes, windows, tmpfs come back) or disk only?

**Command.** `python spikes/spike_04_memory_survives_revert.py`

| check | before snapshot | after kill | after revert |
| --- | --- | --- | --- |
| process `sleep 3600` | not yet measured | not yet measured | not yet measured |
| window (xdotool search mousepad) | not yet measured | not yet measured | not yet measured |
| tmpfs file /dev/shm/forkloop/mem.txt | not yet measured | not yet measured | not yet measured |
| streamUrl WebSocket connect | not yet measured | – | not yet measured |
| memory_restored (all three) | | | not yet measured |

**Decision rule.**
- `memory_restored` true → golden snapshot is taken **live**: Chrome open on the portal, MariaDB warm, logged in. `reset()` = revert + seed + navigate.
- false → golden snapshot is taken with apps **stopped**; `reset()` must start MariaDB/Apache/portal/Chrome after revert, and the readiness gate (contracts §11 step 3) becomes the main cost. Spike 6 becomes moot (DB is closed at snapshot time).
- stream dead after revert → the demo viewer must reconnect the VNC socket after every reset (harness detail, not a plan change).

## Spike 5 — action round-trip

**Question.** Latency of `screenshot()` and `mouse.click()`; bounds steps/s per VM.

**Command.** `python spikes/spike_05_action_roundtrip.py --iterations 50`

| call | p50 | p95 |
| --- | --- | --- |
| screenshot_s | not yet measured | not yet measured |
| click_s | not yet measured | not yet measured |
| loop_s (shot → click → shot) | not yet measured | not yet measured |
| png_bytes_mean | not yet measured | |

**Decision rule.**
- `loop_s` p50 < 1 s → env step overhead is negligible next to model latency; no batching work.
- 1–3 s → take the after-screenshot lazily (only when the policy asks) and drop the before/after pair to a single frame per step.
- > 3 s → open a ticket with Solari before scaling to 10 concurrent workers; this number × steps × episodes is the VM-hour bill.

## Spike 6 — DB consistency under snapshot

**Question.** Is MariaDB clean after reverting to a snapshot taken under write load, with and without `FLUSH TABLES WITH READ LOCK`?

**Command.** `python spikes/spike_06_db_consistency.py` (installs mariadb-server in the VM; ~5–10 min)

| mode | db_answer_s | rows after revert (≈ at snapshot) | mysqlcheck_ok | recovery_log_lines |
| --- | --- | --- | --- | --- |
| hot (writer running) | not yet measured | not yet measured | not yet measured | not yet measured |
| locked (FLUSH TABLES WITH READ LOCK) | not yet measured | not yet measured | not yet measured | not yet measured |
| lock_needed | | | not yet measured | |

**Decision rule.**
- both OK → golden snapshot taken live, no lock dance.
- only locked OK → the golden build script wraps `snapshot()` in a read lock (and the same for any mid-run re-snapshot).
- neither OK → snapshot with MariaDB stopped (`service mariadb stop` → snapshot → start on reset), which also depends on spike 4.

---

## Verified so far (no key needed) vs. needs the key

**Verified from `docs.getsolari.com` and the solari-sandbox 0.2.0 source** (`solari_sandbox/sandbox_client.py`, `solari_core/desktop.py`, `handle.py`, `errors.py`):

- Desktops that support `from_snapshot` are created with `SandboxClient(api_key=..., base_url="https://api.getsolari.com").create_desktop(template="default", resolution="1280x720", cpu=2, mem_mb=4096, timeout_ms=..., record=..., from_snapshot=..., metadata=..., lifecycle={"onTimeout": "kill"})` — the unified `POST /sandboxes` route with `kind:"desktop"`. `DesktopClient.create` has **no** `from_snapshot` parameter.
- Handle surface: `await d.connect()`, `await d.health()` → `.ready`, `await d.snapshot(name)` → snapshot id (`POST /sandboxes/:id/snapshots`), `await d.revert(snapshot_id)` (`POST /sandboxes/:id/revert`, same machine id), `await d.commands.run(cmd, args=[...])` → `.exitCode/.stdout/.stderr` (argv, **not** a shell — spikes use `sh -c`), `await d.screenshot(format="png")` → bytes, `d.mouse.click(x, y)`, `d.keyboard.type(text)`, `d.files.write(path, data)`, `d.files.read_text(path)`, `d.record.start()/stop()`, `d.streamUrl`, `d.recordingUrl`, `await d.kill()` (destroys; `close()` only drops the channel), `d.process.start(cmd, args=)`, `d.open(name)`.
- Client surface: `list_snapshots()`, `delete_snapshot(id)` (refused while a child VM is alive), `list_all(kind="desktop", metadata=...)` (async generator), `kill(id)` (idempotent).
- Errors: `PlanError` (402 `FeatureRequiresPlan` — desktops need a paid plan), `ConcurrencyLimitError` (429), `NoCapacityError` (503).
- Transport: default per-call timeout is 300 s (spikes set 30 s); `reconnect()` is a no-op while the channel still reports connected, so the spikes re-attach after revert with `close()` + `connect()`.
- Plan caps and prices (Sept 2026): Free 1 concurrent / $3 credits; Starter $20/mo, $20 credits, 2 concurrent VMs (20 browsers); Pro $200/mo, 10 concurrent. 2vCPU/4GB is $0.114/h on Starter + $0.02/h screen = $0.134/h → ≈ 149 VM-hours per $20. See `docs/cost.md`.
- OpenEMR 8.3.0 is the pinned version; the payer portal is FastAPI + SQLite on :8080 inside the VM.

**Needs the key (everything measured above).** In particular these are *assumptions the scripts make* that only a run can confirm:

- Whether the control WebSocket survives `revert()` (scripts assume it does not and redial).
- Whether `template="default"` may be sent together with `from_snapshot` (the contract says yes; if the gateway rejects the pair, drop `template` in `_common.create_desktop`).
- Whether `commands.run` runs as root (spike 6 falls back to `sudo -n`).
- Whether `xdotool` and `xfconf-query` exist in the `default` template (spike 4 window check; spike 1 caret-blink suppression is best effort either way).
- Whether `streamUrl` needs extra auth headers for a bare WebSocket connect (spike 4 tries without).
