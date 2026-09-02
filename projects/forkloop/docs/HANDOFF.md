# Handoff — Forkloop, as of 2026-09-02

Read this first, then `CLAUDE.md`. The deep references are `docs/contracts.md`
(every interface), `system.md` (every module), `docs/spikes.md` (every real
measurement), `docs/limitations.md` (everything unproven or broken).

**One line:** a snapshot-native training loop for vision-only GUI agents on
Solari desktops — build a payer-portal + OpenEMR world once, snapshot it, and
every episode reset is one API call. The library, the world, the oracle and the
training scripts are built and tested; the world runs for real on Solari; the
teacher and student have never run.

Last commit on `main`: `60c680f`. Repo: `rynitepsd-tech/forkloop`
(fork of `solari-sdk/solari-cookbook`), cloned at `~/Desktop/Solari/repo`,
project under `projects/forkloop/`.

---

## First five minutes

**The `venv` symlink is dead.** It points into the previous session's
scratchpad. Recreate it before anything else:

```bash
cd ~/Desktop/Solari/repo/projects/forkloop
rm -f venv && python3.11 -m venv venv && ./venv/bin/pip install -q -e ".[dev,world]"
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

**No Anthropic key on this machine.** The teacher policy is written against the
GA computer-use toolset and is the single biggest unblock: it turns the working
world into verified trajectories, which is the whole point of the project. Ask
the user for `ANTHROPIC_API_KEY`, or run `ant auth login` if the CLI is
installed.

**Family 1 cannot be completed through the GUI.** OpenEMR's calendar shows only
"Administrator" in its provider list, so the seeded appointment is invisible.
Ruled out already: `authorized`, `calendar`, `active`, `facility_id`, `uuid`,
`password` sentinel and `cal_ui` all match the admin row, and
`getProviderInfo()`'s own SQL returns all seven providers when run directly.
The filter is therefore in the calendar's rendering layer. Leading hypothesis is
a missing `users_facility` row per provider; the next probe is the PostCalendar
template and the JS that fills that listbox. Families 2 and 3 are unaffected.

**No GPU.** The student bake-off and LoRA training need a rented card.

---

## Next steps, in order

1. **Teacher trajectories on family 3.** The world, oracle and recorder are
   ready; this produces the rung-1 result and the first real data.
   ```bash
   ./venv/bin/python -m forkloop.cli collect --world claims-ops-v1 --policy teacher \
     --families resolve_denial --seeds 0-19 --pool-mode fork --search-mode fork \
     --best-of 2 --concurrency 2
   ```
   Pass `--pool-mode fork` explicitly. The pool now detects a refused `revert()`
   and switches itself to fork mode automatically, but the first worker still
   pays one wasted revert attempt (about 30 s) before it does. Search branching
   costs a machine slot per branch, and Starter allows two, so `--best-of 2` at
   `--concurrency 1` is the safe combination if branches start colliding.
2. **Unblock family 1** with the `users_facility` hypothesis, then drive one
   reschedule through the GUI the way `scripts/gui_episode.py` does the appeal.
3. **Ask Solari about `revert()`**, quoting the 409 body in `docs/spikes.md`.
   If they enable it, the revert bar of Chart 2 and the plan's headline reset
   number follow from one benchmark run.
4. **Check snapshot storage pricing** in the console. Four snapshots of about
   8 GB each are on the account and the price is not published.
5. Optional and cheap: the local docker-compose baseline in
   `forkloop/bench/local_baseline/` adds a third bar to Chart 2 on any machine
   with Docker.

---

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
- **The desktop disk is 4 GB and `disk_gb` is ignored.** The build purges VS Code
  and LibreOffice and trims caches; a full disk breaks OpenEMR with
  "table 'log' is full", which is exactly the one benchmark failure.
- **OpenEMR 8.3 refuses to run CLI scripts as root** (`RootCliGuard`); the
  installer and the uuid backfill run as `www-data`.
- **`tr ... | head -c` under `pipefail` exits 141.** Cost an entire build.
- **The OpenEMR session is not in the snapshot.** That family starts on the
  login page and the instruction carries `admin / pass`. A page reload also
  drops the session.
- **`forkloop build-world --attach <id>`** resumes a failed build on the same
  machine instead of paying for a fresh one. The build scripts are idempotent.

---

## House rules worth repeating

Two channels stay separate: the policy sees screenshots and emits actions,
nothing else. Expected values never enter the VM. `docs/contracts.md` is the
spec and changes with the code in the same commit. Nothing that has not been
measured gets stated as a result — unmeasured things stay "not yet measured" in
the README, and fake-backend timings are labelled as simulator numbers.
