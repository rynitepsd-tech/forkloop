# Solari desktops: what we measured

Dated observations from running Forkloop on Solari desktops (Starter plan, 2 vCPU / 4 GB,
1280 × 720) between 2026-09-01 and 2026-09-24. Each line says when it was seen and links the
evidence. None of this is a statement about how Solari works in general or today: platforms
change, and several of these already have.

## Reset: snapshot restore

| Date | Observation | Evidence |
| --- | --- | --- |
| 2026-09-01 | `revert()` returned 409 `Not revertable` everywhere, and a failed revert on a running machine destroyed it. Fixed by Solari on 2026-09-03. | [spikes.md](spikes.md), [buildlog.md](buildlog.md) |
| 2026-09-03 | `revert()` works and keeps the machine id; desktop state came back 3/3. Restores were **bimodal**: about 22 s or 70–160 s (host side, not client backoff); `revert(golden)` p50 100.9 s, fork p50 92.0 s. | [spikes.md](spikes.md) |
| 2026-09-03 | `revert()` needs a running machine: on a paused sandbox it returns a clear 409 ("revert needs a running sandbox — resume first") and the machine survives. | [spikes.md](spikes.md) |
| 2026-09-03 | After a revert the old control connection is dead; the guest briefly accepts one new connection. Reconnect, then poll `health()`. `reconnect()` is a no-op while the channel still reports connected, so close and connect. | [spikes.md](spikes.md), [upstream PR #82](https://github.com/solari-sdk/solari-cookbook/pull/82) |
| 2026-09-15 to 09-24 | No longer bimodal in our runs. **Fork restore p50 34.8 s** (p10 28.9, p90 56.2, max 65.8) over 81 live resets; whole reset (restore, seeding, health, baseline, initial screen) p50 47.9 s, p90 68.5 s. Revert, 11 resets on 09-15: restore p50 39.5 s, max 80.9 s. | [`scripts/reset_times.py`](../scripts/reset_times.py) over the local `runs/**/reset.json` |
| 2026-09-23 | `revert()` on a fresh desktop restored in 21 s with a killed Mousepad back under the same PID and its unsaved text intact. | [upstream PR #82](https://github.com/solari-sdk/solari-cookbook/pull/82) |

## Lifetime and billing

| Date | Observation | Evidence |
| --- | --- | --- |
| 2026-09-15 | Two desktops were still running about 10 hours after creation. | [issue #79](https://github.com/solari-sdk/solari-cookbook/issues/79) |
| 2026-09-23 | A desktop's 5-minute idle `expiresAt` **renewed itself** three times with no client activity (list calls only, which do not move it). Forkloop therefore never relies on the idle timeout; it kills machines at its own deadline and runs a separate reaper. | [solari-lifetime-probe.md](solari-lifetime-probe.md) |
| 2026-09-23 | `solari-sandbox` 0.2.2 (PyPI, 2026-09-23) is broken: `create_desktop` starts a billable desktop, then raises (`SessionHooks` has no `park`) before returning a handle. Still the latest release on 2026-09-24. Pin 0.2.1. | [issue #81](https://github.com/solari-sdk/solari-cookbook/issues/81) |

## Desktop control (SDK 0.2.1)

| Date | Observation | Evidence |
| --- | --- | --- |
| 2026-09-23 | `keyboard.hotkey("ctrl", "a")` typed a plain `a`. A chord as one string, `press("ctrl+a")`, works. | [upstream PR #82](https://github.com/solari-sdk/solari-cookbook/pull/82) |
| 2026-09-23 | `process.list()` returned processes with empty names; `app.open()` returns the app's own PID, so match on that. | [upstream PR #82](https://github.com/solari-sdk/solari-cookbook/pull/82) |
| 2026-09-23 | `recordingUrl` works on `create_desktop` with 0.2.1: the URL is returned at create, `record.stop()` uploads, and the URL answers 200. (It was always `None` in our runs on 2026-09-02 and 09-03.) | [spikes.md](spikes.md) for the old behaviour; local `runs/image-detail-live-20260923/recording-repro-run.txt` |

## Machine shape and capacity

| Date | Observation | Evidence |
| --- | --- | --- |
| 2026-09-01 to 09-03 | `disk_gb` is not honoured: every machine had a 3.9 GB root disk. `cpu` and `mem_mb` are ignored on `from_snapshot` creates (forks came up at 2 vCPU / 4031 MB when 4 / 8192 was requested). A fork with a full disk breaks MariaDB, so the golden image is kept lean. | [spikes.md](spikes.md) |
| 2026-09-03 | One 503 on `revert(golden)` from a fork: "no desktop host has capacity … could not restore this snapshot in time". The machine survived. The maintainer explained that a desktop create returns 503 when the metal pool is scaled to zero, which looks like an entitlement refusal, and said the message would be fixed. | [spikes.md](spikes.md), [maintainer on PR #20](https://github.com/solari-sdk/solari-cookbook/pull/20) |
| 2026-09-03 | Five of eight probe forks vanished mid-episode (`Not connected`, then HTTP 404 on re-dial); every one we could identify ran on the same host, and restores in the same window took 74–221 s. | [spikes.md](spikes.md) |
| 2026-09-24 | From 06:12 UTC every create returned **HTTP 500** with a plain-text "Internal Server Error": desktops from a snapshot, plain desktops and headless sandboxes alike. List calls kept working and nothing was left running. Even an empty request body got the 500 (a validation error would be 4xx), so it fails before the request is read; the account had a $40 balance. Still failing at 09:25 UTC. | local `runs/session-20260924/solari-outage.log` |

## What Forkloop does about it

- Kills every machine at an enforced deadline (`FORKLOOP_SOLARI_MAX_LIFETIME_MIN`), runs a
  separate `forkloop reap --older-than-min` loop, and books each machine's worst case in a
  session ledger before creating it ([solari-lifetime-probe.md](solari-lifetime-probe.md)).
- Uses fork resets (a fresh desktop per cell) for experiments, so no machine outlives one cell.
- Checks after every reset that the world is usable, not only equivalent across arms: the
  portal must be logged in, and the task's seeded records must exist
  ([live-image-detail-comparison.md](live-image-detail-comparison.md)).
