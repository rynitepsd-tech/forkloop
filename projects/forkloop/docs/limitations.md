# Limitations and what is not yet proven

This file is deliberately blunt. Every claim in the README that is not backed
by a number in `docs/spikes.md`, `bench/reset_summary.json`, or a run
directory should be read as *design intent*, not result.

## Measured on 2026-09-01 (Free-plan key, headless sandboxes)

- **Desktops are plan-gated**: `create_desktop` → 402. Every number below is
  from headless `base` sandboxes (same snapshot API, no screen). The agent
  channel (screenshots, mouse, keyboard) remains unexercised.
- **`revert()` is not available on this account**: 409 `Not revertable` on a
  running sandbox *and* on a paused one, for the newest snapshot and an older
  one. A failed revert on a *running* sandbox left it `Not found` — treat
  `revert()` as destructive until Solari confirms the semantics. The pool's
  `fork` mode (`kill` + `create(from_snapshot=golden)`) is the working reset
  on this plan: ≈ 18 s to first command. `snapshot()` takes 14–20 s and stores
  a full ≈ 6 GB disk image. Fresh create from template: 0.6 s.
- The `base` sandbox is Debian 12 (not Ubuntu), root, systemd as PID 1, with
  a 4 GB disk. The world build requests `disk_gb: 10` but the block device
  stayed 4 GB (the request is ignored on this plan or needs a larger
  template); after the OpenEMR install the golden image is ~89% full.
  PHP 8.3 comes from packages.sury.org.
- **Golden world built and verified headless** (`docs/spikes.md`): the
  browser profile / window layout half of the world (`browser_setup.sh`) has
  still never run — it needs a desktop.

## Measured on 2026-09-02 (Starter, desktops)

- `revert()` is refused on desktops too (409). Fork mode is the reset on this
  account: ~17–20 s restore + ~4 s seed/health/baseline/screen ≈ 21 s.
- The desktop's 4 GB disk cannot be enlarged; the build purges VS Code and
  LibreOffice to fit OpenEMR (golden image ends ~95% full — episodes have
  ~200 MB of headroom; the uploads and documents they write are small).
- The OpenEMR PHP session does not survive into the snapshot; the OpenEMR
  family starts on the login page and the instruction carries `admin / pass`.
  The portal's 30-day signed cookie does survive.
- The SDK presses a key list sequentially; chords must be a single
  `"ctrl+a"` string (backend does this). Keyboard focus after a fork is not
  guaranteed to be in Chrome: navigation clicks the omnibox first.
- **Family 1 was never blocked server-side (re-measured 2026-09-02).** The
  "only Administrator" provider list had two causes, both now understood:
  (1) Chrome's renderer crashed on every authenticated OpenEMR page ("Aw,
  Snap! Error code: 5") because the Solari `default` desktop has no working
  GPU process (`Failed to send GpuControl.CreateCommandBuffer` in
  `chrome.log`); `--disable-gpu` fixes it; `browser_setup.sh` now starts Chrome with it and the
  `before_episode` stage relaunches Chrome when a golden lacks it (≈ 8 s, only
  on pre-v6 goldens).
  (2) OpenEMR's calendar narrows the Providers box to the logged-in user's
  `pc_username` session value; the seeded providers are all there and one
  click on "All Users" (≈ (63, 567) at 1280×720) shows seven provider
  columns. Verified with an authenticated curl fetch (all seven `<option>`s,
  the provider dropdown of `add_edit_event.php` lists them too) and with
  screenshots through the agent channel. A reschedule has still not been
  driven end to end through the GUI.
- **`snapshot()` is refused on forked machines.** A desktop created with
  `from_snapshot` returns 409 `Not snapshottable` (three attempts, state
  `running`); a fresh desktop snapshots fine. So golden images can only be
  produced on the original build machine (hence the from-scratch rebuild for
  the `--disable-gpu` fix), and `env.checkpoint()` — the branch point of
  `best_of_n` in both `revert` and `fork` modes — cannot run on this account.
  `collect --best-of 1` is the only mode that works today.
- Attachment upload through the GTK file chooser has not been exercised.
- An OpenEMR page reload (F5) on the tabs UI drops the session; agents that
  reload will have to log in again (credentials are in the instruction).

## Not yet measured (needs more time or a key)

- **Spikes 1–6 have not run** (they need a desktop). `docs/spikes.md` holds
  the questions, commands, and empty tables. Revert latency on a desktop,
  parallel fork independence (needs 2 concurrent machines), memory/window
  survival, `record=True` with `from_snapshot`, and MariaDB consistency after
  a live snapshot are still *unverified*.
  The reviewer's claim that recording is rejected with snapshot-restored
  machines is likewise untested; forkloop never depends on native recording.
- **The teacher has run on family 3 only** (8/12 verified over seeds 0–9 plus pilot 4, `runs/teacher-*`; every failure is the 60-action budget after Chrome tab crashes or PDF-viewer confusion — see `docs/spikes.md`);
  families 1 and 2 have not been driven by it yet. `forkloop metrics` now prices
  model tokens (`MODEL_PRICES_PER_M`, model read from `run.json`) on top of VM
  time: pilot 4 is $2.10 per verified episode, of which $2.08 is Opus tokens
  (≈ 250–530k input tokens per episode) and $0.015 is the desktop. Token cost
  is ~140× the VM cost, so the teacher's price, not Solari's, sets the cost of
  data; nothing in the harness uses prompt caching yet.
  The student needs a GPU box serving a model.
- **Chart 1 and Chart 2 do not exist.** `train/plot.py --demo` renders
  clearly labelled synthetic placeholders so the pipeline can be checked.
  No learning curve has been produced; no teacher trajectory has been
  collected; no student checkpoint exists.
- **Bake-off not run.** Which 4B model grounds best on this UI is an open
  question; `train/bakeoff.py` produces the table once endpoints exist.

## Known design limits

- **OpenEMR audit tripwire is coarse.** The portal writes an `audit_log` row
  keyed by the exact row it changed, so `ui_path_only` is precise there.
  OpenEMR's `log` table keys by patient, so for OpenEMR the check is
  "there is a log row for this patient after seeding". A direct DB write
  that also inserts a plausible log row would pass. Inside the VM the agent
  has no shell, so this is a tripwire, not the primary defence.
- **Checksums are scoped, not global.** `world.yaml` lists the tables that
  are checksummed (everything the UI can plausibly touch: patients, claims,
  appeals, resubmissions, insurance, appointments, documents, users). OpenEMR
  has ~300 other tables that are not diffed. A UI action that only touches
  an unlisted table is invisible to the oracle. Add tables to
  `oracle.checksum_tables` as you discover them.
- **Attachment tasks are hard for a 4B policy.** Uploading a file from
  OpenEMR to the portal requires a download plus a GTK file chooser. The
  attachment requirement is a difficulty knob (`require_attachment`) that
  is off for most training seeds and on for a minority of held-out seeds.
- **Scroll is emulated with keys.** The Solari SDK's `mouse.scroll` takes a
  button code, so the backend implements scroll as Page_Down/Page_Up
  (vertical) and arrow keys (horizontal). Pages that intercept those keys
  will behave differently from a real wheel.
- **`triple_click` and `left_mouse_down/up` are not in the action schema.**
  The teacher maps triple-click to double-click and rejects raw mouse
  down/up (drag is supported).
- **Fake backend is a simulator.** It proves the snapshot/revert/fork
  *semantics* (directory copies) and lets the full loop run offline, but it
  has no browser. Its screenshots are synthetic (toy world) or blank
  (claims-ops). Never quote its timings as Solari numbers; the benchmark
  labels them.
- **Concurrency is the bottleneck on Starter.** Two machines total means
  fork-mode search has width 2 at most and `collect` runs two episodes at a
  time. Depth-first search with `revert()` (width 1) is the default for
  that reason.
- **Synthetic data only.** Names, member IDs, NPIs, documents are generated.
  There is no PHI anywhere. Solari Starter/Pro are not HIPAA plans and
  nothing here should ever be pointed at real patient data.
- **Teacher cost is real money.** Each teacher step sends a screenshot;
  `keep_images` prunes history to the last 8 images, and the system prompt
  is cached, but a 40-step episode is still on the order of 100–300K input
  tokens at Opus pricing. Measure before scaling.
- **No GRPO.** Rung 3 is described in `train/README.md` but not
  implemented; the oracle already provides the reward signal it would need.

## Things that might simply be wrong

- The `SandboxClient.create_desktop(template=..., from_snapshot=...)`
  combination: the SDK accepts both; the gateway may reject `template` when
  `from_snapshot` is set. The backend omits `template` whenever
  `from_snapshot` is given for that reason.
- Whether the control WebSocket survives `revert()`: the backend reconnects
  and re-polls `health()` after every revert, so either answer works, but
  the reset timing includes that reconnect.
- Whether `commands.run` executes as root in the `default` desktop
  template. `build.sh` is invoked with `sudo` explicitly.
