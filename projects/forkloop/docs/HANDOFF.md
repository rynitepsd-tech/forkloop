# Handoff — Forkloop, as of 2026-09-02 (late evening)

Read this first, then `CLAUDE.md`. The deep references are `docs/contracts.md`
(every interface), `system.md` (every module), `docs/spikes.md` (every real
measurement), `docs/limitations.md` (everything unproven or broken).

**One line:** a snapshot-native training loop for vision-only GUI agents on
Solari desktops — build a payer-portal + OpenEMR world once, snapshot it, and
every episode reset is one API call. The library, the world, the oracle and the
training scripts are built and tested; the world runs for real on Solari; the teacher verifies 8/12 family-3 episodes; the student has never run.

Last commit on `main`: `6283430`. **Uncommitted in the working tree** (late 2026-09-02): priced metrics (`forkloop/metrics.py`, `tests/test_metrics_cost.py`), pool orphan-reaping on 429 and `collect --reset-retries`, the teacher pre-flight, cache-token accounting in `teacher.py`, wider episode diagnostics (`worlds/claims_ops_v1/world.py`), `scripts/episode_table.py`, `docs/solari-repro.md`, and the doc updates below. `runs/` is git-ignored; the trajectories live only on this Mac. Repo: `rynitepsd-tech/forkloop`
(fork of `solari-sdk/solari-cookbook`), cloned at `~/Desktop/Solari/repo`,
project under `projects/forkloop/`.

---

## First five minutes

**The `venv` symlink is dead.** It points into the previous session's
scratchpad. Recreate it before anything else:

```bash
cd ~/Desktop/Solari/repo/projects/forkloop
rm -f venv && python3.11 -m venv venv && ./venv/bin/pip install -q -e ".[dev,world,teacher]"
```

Credentials and snapshot ids live outside the repo in `~/.config/forkloop/env`
(mode 600, never commit it). It exports `SOLARI_API_KEY`, `SOLARI_PLAN=starter`,
`FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1` (desktop golden) and
`FORKLOOP_GOLDEN_SANDBOX_CLAIMS_OPS_V1` (headless golden).

```bash
source ~/.config/forkloop/env
export PYTHONPATH=. FORKLOOP_CONCURRENCY=2
PYTHONPATH=. ./venv/bin/pytest -q          # 158 offline tests, ~2 min, no key needed
./venv/bin/python -m forkloop.cli reap     # kill anything left running on the account
```

The Solari account is **Starter** with a hard $30 cap: two concurrent machines,
desktops allowed. About $0.55 plus snapshot storage has been spent. Four
snapshots exist; `v5` is the live golden desktop and the other three are its
ancestors, which cannot be deleted while it exists.

---

## What is true today

**Built and green offline.** Core library (backends, env, pool, reset, oracle,
recorder, exporters, search, metrics, CLI), the `claims-ops-v1` world (portal +
OpenEMR 8.3.0 + three task families + held-out compositions), the `toy-counter`
world, teacher and student policies, and the training ladder. 158 tests, all
offline, no key required.

**Verified on real Solari.** The golden desktop world boots with OpenEMR and the
portal healthy, Chrome logged into the portal and locked to localhost with
popups disabled. A scripted click-and-type episode drove the real agent channel
end to end and the oracle scored it 1.0; the same script with a decoy
authorization number was rejected as `WRONG_VALUE`. Screenshots and both
verdicts are in `docs/demo_episode/`.

**Measured.** Numbers and method in `docs/spikes.md`; raw rows in `bench/`.

| Measurement | Result |
| --- | --- |
| `revert()` | 409 "Not revertable" on sandboxes **and** desktops |
| Reset via fork, desktop, n=10 | p50 25.0 s, p95 26.6 s, 1 failure (disk-full, since fixed) |
| Reset via fork, sandbox, n=10 | p50 19.1 s, p95 21.8 s, 0 failures |
| Observe-act-observe loop | 0.45 s p50 → about 2.2 agent steps per second |
| Fork fidelity | restores RAM, processes and windows; kernel uptime continues |
| Parallel forks | two desktops from one snapshot, ~31 s each |
| Session recording | works on forked desktops in-VM; `recordingUrl` never populates |

Chart 2 has two real bars (`bench/chart2_solari.png`). Chart 1 does not exist:
it needs the teacher and a student.

---

## What is blocked

**Rung 1 has volume: 8/12 family-3 episodes verified** (66.7 % [39.1, 86.2]; `runs/teacher-pilot4`, `runs/teacher-f3-s0-9`, `runs/teacher-f3-s1-9`; `scripts/episode_table.py` prints the per-episode table, `runs/exports/` holds the SFT pairs — 573 examples from the 8 verified episodes). $2.22 per episode, $4.00 per verified episode, all but a cent of it Opus. **Every failure is the 60-action budget, and three of the four were spent recovering from 2–3 Chrome tab crashes** (`docs/spikes.md`). The crashes, not the policy, are the next thing to fix: they happen with `--disable-gpu` in place, `chrome.log` shows `Network service crashed`, and the guest kernel logged RCU stalls mid-episode — suspect memory pressure on the 4 GB desktop or vCPU starvation on the host. Try one collect at `mem_mb: 8192` (world.yaml `resources`, 4 vCPU/8 GB is $0.248/h) and compare crash counts; if that fixes it the 4 GB price is not the real price.

**The teacher completed family 3 end to end** (pilot 3, seed 1, `runs/teacher-pilot3`): right authorization number, letter downloaded, appeal filed with attachment via the GTK chooser; every effect check passed. It scored 0 only because OpenEMR rewrites `uuid` on rows it displays and the checksum oracle counted that as collateral — fixed with `oracle.ignore_columns` in `world.yaml` (`docs/contracts.md` §6). Seeds 1 and 3 then scored 1.0 (pilot 4).

**The teacher runs.** First real teacher episodes are in `runs/teacher-pilot*`
(see `docs/spikes.md`): the policy logs into OpenEMR, searches the right
patient and recovers from crashes on its own; what it cannot survive is the
OpenEMR chart tab dying with "Aw, Snap! Error code: 5" (renderer SIGTRAP),
which happened on the pilot machine but not on probe forks. Episodes now keep
`diagnostics/chrome.log` and `dmesg.log`; read those from the next crashing
episode before changing anything else. Cost: ≈ 100k input tokens per 60-step
episode at `claude-opus-5` (≈ $0.57).

**The teacher is unblocked on the Anthropic side.** `ANTHROPIC_API_KEY` and
`ANTHROPIC_WORKSPACE_ID` are both in `~/.config/forkloop/env` (the key is
identity-linked, so the workspace header is mandatory; `teacher.py` sends it)
and a `claude-opus-5` smoke call with the computer toolset succeeded. The first
pilot (`collect … --run-id teacher-pilot`) never got a Solari machine: the
account answered 429 `Too many concurrent sessions` with zero sessions listed
(see `docs/spikes.md`). Re-run the pilot once Solari creates machines again.

**Family 1 is not blocked any more** (see `docs/limitations.md`): the
"only Administrator" list was Chrome's renderer crashing on every
authenticated OpenEMR page (no GPU process on the template — fixed with
`--disable-gpu`: in `browser_setup.sh` for future goldens and in the
`before_episode` relaunch hook for v5) plus OpenEMR's per-user provider
filter, which one click on "All Users" widens. A reschedule
has still not been driven through the GUI.

**Forks cannot be snapshotted.** `snapshot()` on a `from_snapshot` desktop is
409 `Not snapshottable`; fresh desktops snapshot fine. Golden images are
therefore rebuilt from scratch (`forkloop build-world`, ~10 min, resumable
with `--attach`), and `best_of_n` search — which checkpoints a forked worker —
cannot run on this account in either mode. Use `--best-of 1`.

**No GPU.** The student bake-off and LoRA training need a rented card.

---

## Next steps, in order

1. **Chrome tab crashes** (see "What is blocked"): run seeds 10–14 once at 8 GB
   and once at 4 GB, compare the `crashes`/`netsvc` columns of
   `scripts/episode_table.py`, and read the new `sys.txt`/`dmesg.log` stall
   lines against `t_wall`. Whichever wins becomes the world default.
2. **More family-3 seeds and the other families** (the command below; seeds
   0–9 are done; the 429/orphan failure mode is handled by the pool now):
   ```bash
   ./venv/bin/python -m forkloop.cli collect --world claims-ops-v1 --policy teacher \
     --families resolve_denial --seeds 10-19 --best-of 1 --pool-mode fork --concurrency 2
   ```
   `--best-of` > 1 cannot work on this account (forks are not snapshottable).
   Budget: about $2.20 of Opus per episode; Solari is negligible.
3. **Rebuild golden v6** when Solari is stable (see "What is true today"), then **drive one family-1 reschedule through the GUI** the way
   `scripts/gui_episode.py` does the appeal (login → Calendar → "All Users" →
   open the event → change date/time → Save), then let the teacher loose on
   family 1 too.
4. **Solari support email was sent on 2026-09-02** (the text is
   `docs/solari-message.md`; `docs/solari-repro.md` maps each item to the
   script that reproduces it, for the reply). If Solari enables `revert()` or
   fork snapshots, the Chart 2 revert bar and best-of-N search follow from one
   benchmark run each. New for the thread: mid-episode RCU stalls in the guest.
5. **Check snapshot storage pricing** in the console; five snapshots of
   6–8 GB are on the account now (v6 plus the v5 lineage; the lineage can be
   deleted once v6 is verified, newest first).
6. Optional and cheap: the local docker-compose baseline in
   `forkloop/bench/local_baseline/` adds a third bar to Chart 2.

## Gotchas that cost real time yesterday

These are all fixed in the scripts, but they will bite again if you touch the
same areas.

- **`revert()` destroyed a running machine** on this account, twice. Treat it as
  destructive until Solari says otherwise. The pool now catches a refused revert,
  logs `revert_unsupported_fell_back_to_fork`, replaces the machine and switches
  to fork mode for the rest of the run (verified live), so a wrong `--pool-mode`
  costs time rather than the run.
- **Chrome will not run as root.** The desktop session user is `desktop` on Xvfb
  `:0`; launch through
  `runuser -u desktop -- env DISPLAY=:0 HOME=/home/desktop XDG_RUNTIME_DIR=/run/desktop`.
- **Key chords must be one string.** The SDK presses a list sequentially, so
  `["ctrl", "a"]` types the letter a. The backend now joins them into `"ctrl+a"`.
- **Keyboard focus is not guaranteed after a fork.** Navigation clicks the
  omnibox at (640, 90) before typing a URL.
- **A fresh venv needs the `teacher` extra** or every model call fails with
  `ModuleNotFoundError: anthropic` while the desktops keep billing; `collect`
  now refuses to start without it. **A killed `collect` leaves its machines
  running** (SIGTERM skips `pool.close()`): run `forkloop reap` before the
  next run, or let the pool's 429 handler reap them.
- **The desktop disk is 4 GB and `disk_gb` is ignored.** The build purges VS Code
  and LibreOffice and trims caches; a full disk breaks OpenEMR with
  "table 'log' is full", which is exactly the one benchmark failure.
- **OpenEMR 8.3 refuses to run CLI scripts as root** (`RootCliGuard`); the
  installer and the uuid backfill run as `www-data`.
- **`tr ... | head -c` under `pipefail` exits 141.** Cost an entire build.
- **The OpenEMR session is not in the snapshot.** That family starts on the
  login page and the instruction carries `admin / pass`.
- **Chrome needs `--disable-gpu` on this template**, and the calendar shows
  one provider column until "All Users" is clicked. Both were misread as a
  data bug for a whole session.
- **`apt-get update` fails on the template** until the VS Code apt source is
  removed (`build.sh` does it now).
- **`forkloop build-world --attach <id>`** resumes a failed build on the same
  machine instead of paying for a fresh one. The build scripts are idempotent.

---

## House rules worth repeating

Two channels stay separate: the policy sees screenshots and emits actions,
nothing else. Expected values never enter the VM. `docs/contracts.md` is the
spec and changes with the code in the same commit. Nothing that has not been
measured gets stated as a result — unmeasured things stay "not yet measured" in
the README, and fake-backend timings are labelled as simulator numbers.
