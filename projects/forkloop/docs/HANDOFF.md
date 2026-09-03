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

**Rung 1 has volume: 11/17 family-3 episodes verified** (64.7 % [41.3, 82.7]; `runs/teacher-pilot4`, `runs/teacher-f3-s0-9`, `runs/teacher-f3-s1-9`, `runs/teacher-f3-s10-14-8gb`; `scripts/episode_table.py` prints the per-episode table, `runs/exports/` holds the SFT pairs — 746 examples from the 11 verified episodes). $2.22 per episode, $4.00 per verified episode, all but a cent of it Opus. **Every failure is the 60-action budget, and three of the four were spent recovering from 2–3 Chrome tab crashes** (`docs/spikes.md`). The crashes, not the policy, are the next thing to fix: they happen with `--disable-gpu` in place, `chrome.log` shows `Network service crashed`, and the guest kernel logged RCU stalls mid-episode — suspect memory pressure on the 4 GB desktop or vCPU starvation on the host. `cpu`/`mem_mb` are ignored on forks (measured: a `--mem-mb 8192` collect ran at 4031 MB) and `free -m` shows 2.8 GB available at episode end, so memory is not the lead. Use `scripts/chrome_crash_probe.py` (replays a verified trajectory on fresh forks, counts crashpad reports, no Opus) to compare Chrome flag sets via `FORKLOOP_CHROME_FLAGS` / `FORKLOOP_CHROME_DROP`; the winning set goes into `chrome_base_flags` and `browser_setup.sh`.

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

1. **Chrome tab crashes** (see "What is blocked"): re-run
   `scripts/chrome_crash_probe.py --stress 6` for the baseline and
   `FORKLOOP_CHROME_FLAGS="--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader"`
   once forks stop dying (2026-09-03 02:30–03:00 UTC five of eight probe forks
   vanished on host `i-00cac13223691ff7d`). Result so far (`docs/spikes.md`):
   swiftshader 5/6 completed replays verified vs baseline 2/3, about one
   crash report per replay under both — but forks *died* 5/8 under baseline
   vs 1/7 under swiftshader (last pairs interleaved on one host: 1/2 vs 0/2).
   Suggestive, not proven. Next: run seeds 15–19 with
   `FORKLOOP_CHROME_FLAGS="--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader"`
   and compare `scripts/episode_table.py` crash columns and machine deaths
   with seeds 0–14; adopt in `chrome_base_flags`/`browser_setup.sh` if it
   holds. Ask Solari about the stalls/deaths regardless (ids and times in
   `docs/solari-repro.md`).
   **Cheaper teacher candidate: OpenAI GPT-5.6 Luna** ($0.20/$1.20 per 1M vs
   Opus $5/$25; vision + computer-use listed as supported; weak long-context
   recall, slow at xhigh). The OpenAI-compatible student path is wired for it —
   put `OPENAI_API_KEY` in `~/.config/forkloop/env`, then
   `collect --policy student --student-url https://api.openai.com/v1 --model gpt-5.6-luna --effort high --seeds 0-2 --pool-mode fork`
   (text actions, no native tool; `metrics` prices it). Under $1 for three seeds.
   **Status 2026-09-03 (`docs/spikes.md`):** grounds well, 1.5–2.4 s per call,
   ≈ $0.03 per episode; 0/6 with the plain compact prompt at any effort
   (loops on an already-focused field), 0/3 with the hosted prompt
   (`forkloop/policies/prompts/hosted_gui_agent.md`, `--system-prompt-file`,
   `--history-k 16`) but no loops and the right auth number read on one seed —
   it runs out of the 60-action budget. Third run = `--prev-shot`
   (previous screenshot too) + `--max-steps 120 --max-seconds 900`
   (`run.json` records `budget_override`) **verified seed 1 at 1.0 for
   $0.045** (`runs/luna-high-v3-s0-2`; Opus: $1.83 on the same seed). Seed 0
   lost 70 actions to a read-only address bar in a Chrome popup window and a
   decoy letter; prompt v4 (popup + decoy guidance, alternating-loop warning)
   **verified 2/3** (`runs/luna-high-v4-s0-2`: seeds 1 and 2 for $0.04–0.08
   each; seed 0 with four decoys still fails). Volume run on seeds 3–19 with
   the v4 configuration is `runs/luna-high-v4-s3-19`; compare its success
   rate and per-seed outcomes with Opus's seeds 3–14 via
   `scripts/episode_table.py`. **Result: Luna v4 11/20 (55 %) at $0.15 per
   verified vs Opus 9/15 (60 %) at $3.55; 8 vs 9 on shared seeds**
   (`scripts/compare_teachers.py`). Luna is the volume teacher from here;
   Opus stays the reference for audits and hard families. v5 prompt
   (`hosted_gui_agent_v5.md`: one tab per app, exact transcription, keep
   the number in reasoning) + `detail: high` images is being retried on the
   seeds v4 failed (`runs/luna-high-v5-retry`): **7/9**, so Luna covers
   18/20 seeds within two attempts. Clean v5 pass on seeds 0–19 =
   `runs/luna-high-v5-s0-19` (single attempt, the fair number). Next: Luna
   on families 1–2, then seeds 20–99 (≈ $8) for the SFT set; keep Opus for
   audits.
   Cheaper lever already in: the teacher caches its prompt (moving breakpoint
   + pruning hysteresis), which should cut Opus input cost by more than half;
   check `cache_read` in the next run's `metrics`.
2. **More family-3 seeds and the other families** (the command below; seeds
   0–14 are done; the 429/orphan failure mode is handled by the pool now):
   ```bash
   ./venv/bin/python -m forkloop.cli collect --world claims-ops-v1 --policy teacher \
     --families resolve_denial --seeds 15-24 --best-of 1 --pool-mode fork --concurrency 2
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
- **The control WebSocket drops mid-episode now and then** (close code 1000/1006;
  the SDK then raises `ConnectionError: Not connected`). `SolariMachine` re-dials
  and retries the operation once (`_call`, `reconnects` counter); a drop that
  does not recover within 30 s surfaces as `BackendError`.
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
