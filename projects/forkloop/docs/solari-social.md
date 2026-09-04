# Solari bug-to-fix story — X post and Discord reports (drafts, 2026-09-03)

Drafts only; nothing has been posted. Every number is measured (`docs/spikes.md`). Handles: Solari's X account and
its founders' handles could not be verified from getsolari.com, docs.getsolari.com or the `solari-sdk` GitHub org
(no social links, no public members) — **fill in the two handles before posting.**

## X post

Attach: `bench/restore_bimodality_0903.png` (the restore-time histogram: revert vs fork, 110 resets) — or
`bench/chart2_solari_0903.png` for the p50/p95/p99 bars.

> Reported a 409 on `revert()` for desktop snapshots to @<SOLARI_HANDLE> on Tuesday. Fixed within a day.
>
> Before: 409 "Not revertable" on every machine, and a failed revert destroyed the VM.
> After: revert to a snapshot of a running desktop in 21.5 s p50 (state restored 3/3), and 10/10 reverts to our
> 8.5 GB golden image on one machine id.
>
> That golden is an Ubuntu desktop + OpenEMR + a payer portal; every RL episode now resets with one API call.
> GPT-5.6 Luna verifies 94/100 tasks on it at $0.061 per verified trajectory, checked by a SQL oracle, not an LLM.
>
> Chart: restore time is bimodal — ~22 s or 70–160 s — and the same for revert() and forks, so it's host-side,
> not our client. Precise reports get precise fixes. Thanks @<FOUNDER_HANDLE> and team.

(≈ 900 characters; trim the third paragraph if the account is not on Premium.)

Alt text for the image: "Histogram of Solari snapshot restore times on 2026-09-03: 110 resets, two modes, about
22 seconds or 70 to 160 seconds, for revert() and create(from_snapshot) alike."

## Discord — three separate, reproducible reports (#support)

Each stands alone; post them as three messages so they can be tracked separately.

### Report A — `recordingUrl` never populates on `/sandboxes` desktops

> **recordingUrl stays null although the in-VM mp4 is written** (Starter, `solari-sandbox` 0.2.0, re-measured
> 2026-09-03 after the revert fix).
> Repro (`projects/forkloop/spikes/spike_03_recording_snapshot.py` in our cookbook fork):
> 1. `create_desktop()` (plain) → `record.start()` → type a few keys → `record.stop()` → `{'path':
> '/tmp/solari-rec-1788481557421.mp4', 'sizeBytes': 140722}`, `recordingUrl = None`.
> 2. Same on a `create_desktop(from_snapshot=…, record=True)` desktop → mp4 125 822 bytes, `recordingUrl = None`.
> 3. Same on a `from_snapshot` desktop created without `record` → mp4 145 352 bytes, `recordingUrl = None`.
> New: on desktop 1, after a `revert()`, `record.start()` succeeded but `record.stop()` timed out after 30 000 ms.
> Question: is the presigned upload expected for `POST /sandboxes` with `kind: "desktop"`, or only for the legacy
> desktops route? We pull the file with `files.read` meanwhile.

### Report B — `disk_gb` (and `cpu` / `mem_mb` on forks) are ignored

> **Every machine has a 3.9 GB root disk regardless of `disk_gb`; forks also ignore `cpu`/`mem_mb`** (Starter,
> 2026-09-03).
> - `create(template="base", disk_gb=10)` → `df -h /` = `/dev/root 3.9G 1.5G 2.2G 42%`.
> - Fork of our golden desktop (`snap_dl4e90g095y2`, requested `disk_gb=10`) → `3.9G 3.2G 535M 86%`.
> - Fork requested with `cpu=4, mem_mb=8192` → `nproc` = 2, `free -m` total = 4031 MB.
> Consequence: OpenEMR + MariaDB + Chrome fit only after purging VS Code and LibreOffice from the template, and a
> fork that fills the disk breaks MariaDB ("table 'log' is full").
> Repro: `forkloop build-world --disk-gb 10` (logs `df -h`), and `forkloop collect --cpu 4 --mem-mb 8192` (writes
> `sys.txt` per episode). Is there a template or plan where these are honoured, and does a golden built there keep
> its shape when forked?

### Report C — slot-release lag and bimodal restores on Starter

> **Two related capacity observations** (Starter cap = 2, 2026-09-02/03):
> 1. `POST /sandboxes` returns 429 `Too many concurrent sessions` while `list_all(kind="desktop")` and
> `list_all(kind="sandbox")` both return zero sessions — seen after machines lost their control channel (close
> code 1000) on 2026-09-02 13:50–14:30 UTC, and as 9 consecutive 429s at the start of a run on 2026-09-02 evening
> until a machine an aborted process had left behind was killed by hand. In a 90-episode run on 2026-09-03 the
> pool logged one 429 and two create calls that hung past 240 s.
> 2. Restore time from an 8.5 GB snapshot is bimodal and the modes are identical for `revert()` and
> `create(from_snapshot)`: 7/20 at ≈ 22 s, 13/20 at 70–160 s tonight (n=10 each, one machine, back to back); 90
> forks earlier today: 33 under 30 s, 50 over 60 s, max 353 s. One `revert(golden)` on a fork returned 503 "no
> desktop host has capacity right now … could not restore this snapshot in time" (machine survived).
> Repro: `forkloop reset-bench --methods revert fork --trials 10 --no-fallback` (rows in
> `bench/reset_results_desktop_0903.jsonl`), `forkloop reap --dry-run` (lists sessions next to a failing create).
> Questions: how long after `kill()` does a session count against the cap, is there a `retry-after` we should
> honour on the 429/503, and is the slow restore mode snapshot locality (anything we can do to stay warm)?
