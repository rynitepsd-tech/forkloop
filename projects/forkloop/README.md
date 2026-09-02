# Forkloop

**Snapshot-native training worlds for vision-only GUI agents on Solari desktops.**
`reset()` is one `revert()` call. `fork()` is one `create(from_snapshot=...)`.

> Gym-Anything showed environment *creation* can be automated. Forkloop shows the environment *runtime* can be a single API call: snapshot once, reset and fork forever.

```
VISION-ONLY 4B POLICY · claims-ops-v1 (payer portal + OpenEMR)
Held-out success:          not yet measured
Verified trajectories:     0 (collection not started)
LLM-graded rewards:        0 — by construction, see forkloop/oracle.py
Unrelated records edited:  checked on every episode (COLLATERAL_EDIT reason code)
Median reset:              19.1 s sandbox (n=10, 0 fail) / 25.0 s desktop incl. screen stages (n=10, 1 fail, disk-full); revert() refused on this account
Solari compute:            ≈ $1.50 so far (sandbox + desktop builds, ~40 resets, spikes) — docs/cost.md
```

Chart 2 has two real bars (`bench/chart2_solari.png`: fork-mode resets on Solari sandboxes and desktops, n=10 each; revert(), local and cold bars unmeasured). Chart 1 does not exist yet; `train/examples/` holds *synthetic placeholders* so the plotting pipeline can be checked, and their titles say so. Everything that needs a Solari key, a GPU, or a teacher-model budget is built and tested offline but has not been run for real. `docs/limitations.md` is the honest list.

## Status (2026-09-01)

| Piece | State |
| --- | --- |
| Core library (`forkloop/`): backends, env, pool, reset, oracle, recorder, exporters, search, metrics, CLI | built, 158 offline tests green |
| `claims-ops-v1` world: payer portal (FastAPI + SQLite), OpenEMR 8.3.0 install + seed layer, 3 task families + held-out compositions | **golden desktop snapshot built and verified** (`snap_dl4e05ciyt1p`: OpenEMR + portal + Chrome logged in, locked to localhost); a scripted GUI episode through the real agent channel scored 1.0 and a decoy-number control was rejected (`docs/demo_episode/`) |
| `toy-counter` world | built; runs the whole loop (revert, seed, oracle, search, export) in seconds offline |
| Teacher policy (Anthropic computer-use toolset) and student policy (vLLM, Fara/Qwen/UI-TARS output formats) | built; not run against live endpoints (**needs an `ANTHROPIC_API_KEY`** for the teacher; none on the build machine) |
| Training ladder (`train/`): SFT export, LoRA, held-out eval with Wilson CIs, plots, bake-off | built; no GPU run yet |
| Reset benchmark + local docker-compose baseline + cost model | **fork-mode measured on Solari**: p50 19.1 s, p95 21.8 s, 0/10 failures; revert bar blocked (409 on this account); local and cold bars not run |
| Day-1 spikes | run on Free then Starter: `revert()` is 409 on sandboxes *and* desktops (fork mode is the reset); fork restores RAM + windows; screenshot 0.13 s, click 0.19 s, observe-act-observe 0.45 s p50; snapshot 14–22 s — `docs/spikes.md` |

## Run it

```bash
cd projects/forkloop
python3.11 -m venv venv && . venv/bin/activate
pip install -e ".[dev,world]"

# 1. everything offline, in seconds (no key needed)
pytest
forkloop run --backend fake --world toy-counter --family reach_target --policy random --seed 3
forkloop reset-bench --backend fake --world toy-counter --methods revert fork --trials 10 --fake-latency 0.2

# 2. first hour with a key
export SOLARI_API_KEY=slr_live_...
python spikes/spike_01_revert_latency.py           # p50/p95/p99 of revert() → healthy → stable screen
bash spikes/run_all.sh

# 3. build the world once, then reset forever
forkloop build-world --world claims-ops-v1          # prints FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1=snap_...
export FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1=snap_...
forkloop reset-bench --world claims-ops-v1 --methods revert fork --trials 50

# 4. teacher data with snapshot-native best-of-N, then the training ladder
export ANTHROPIC_API_KEY=...
forkloop collect --world claims-ops-v1 --policy teacher --seeds 0-99 --best-of 2 --search-mode revert
forkloop metrics --run runs/<run_id>
forkloop export --run runs/<run_id> --format sft --out sft.jsonl
python -m train.train_lora --sft sft.jsonl --model microsoft/Fara1.5-4B --output-dir ckpt/r1   # on a GPU box
python -m train.eval --world claims-ops-v1 --split heldout_seeds --policy student --student-url http://gpu:8000/v1
python -m train.plot chart1 --curve curve.json --out chart1.png
```

## How it works

```
 controller (your machine)                       Solari desktop VM (one snapshot)
 ┌──────────────────────────────┐                ┌───────────────────────────────┐
 │ forkloop.env.Env             │  agent channel │  Chrome ── payer portal :8080 │
 │   reset(seed) ───────────────┼──screenshot──▶ │         └─ OpenEMR      :80   │
 │   step(action) ◀─────────────┼──click/type─── │  MariaDB, SQLite, documents,  │
 │ policy (teacher | student)   │                │  browser profile, window layout│
 │                              │ controller ch. │                               │
 │ seed.py  ───── sql/files ────┼──exec/files──▶ │  everything inside revert()   │
 │ oracle.py ◀─── row hashes ───┼──exec────────  │                               │
 │ pool.py  ───── revert/fork ──┼──REST────────▶ │  snapshot ─▶ revert / fork    │
 └──────────────────────────────┘                └───────────────────────────────┘
```

**Two channels, strictly separated.** The policy only ever sees a screenshot and the instruction and only ever emits mouse/keyboard actions (`forkloop/actions.py`). Seeding, health checks, DB reads for the oracle, and snapshot control go over the controller channel (`exec`, `files`, gateway REST). Expected values never enter the VM.

**Reset protocol** (`forkloop/reset.py`, every stage timed): restore (`revert(golden)` or `create(from_snapshot=golden)`) → seed (per-episode SQL + files in one transaction per DB) → health (HTTP 200 on both apps, `SELECT 1`, row counts) → baseline (per-row MD5 of every checksummed table + append-only watermarks) → initial screen (`ctrl+l`, URL, `Return`) → two identical consecutive screenshots.

**Oracle** (`forkloop/oracle.py`): effects and invariants are plain SQL checks plus three structural kinds — `baseline_checksum` (no row outside the allow-list changed), `ui_path_only` (every changed row has an audit-log row written after seeding), `forbidden_screens` (portal `page_views` never hit `/admin`). Reward is 1.0 only if everything passes; `milestones` is the fraction of effects passed and is for analysis only; `reason_code` is the first failure (`WRONG_RECORD`, `DUPLICATE_SIDE_EFFECT`, `COLLATERAL_EDIT`, `DIRECT_DB_WRITE`, ...). No LLM anywhere in the reward path.

**Search** (`forkloop/search.py`): at steps where the teacher reports low confidence (or a random 20%), the env takes a snapshot, tries N candidate actions — by reverting the same machine (width 1, any plan) or forking with `from_snapshot` (bounded by the plan cap) — rolls each branch to the end, verifies, and adopts the winning branch into the main trajectory. Every branch stays on disk with its own verdict.

**Recorder** (`forkloop/trajectories.py`): per step, before/after PNG, the action, latency, tokens, the branch it came from; per episode the manifest, the reset timings, the verdict. MP4s are rendered with ffmpeg afterwards. Exporters produce episode JSONL, per-step SFT pairs, and OSWorld-style task JSON.

## The world: claims-ops-v1

Two web apps on one desktop so a web-trained 4B policy is in-distribution:

- **Payer portal** (`worlds/claims_ops_v1/portal/`, ours): login, claims list with filters, claim detail with denial code/reason, appeal form (reason, authorization number, narrative, optional attachment), resubmit form, inbox, eligibility check. Server-rendered, no JavaScript on any task path, no animations, fixed 1280-wide layout, 44 px buttons, 40 px rows. Every write inserts an `audit_log` row in the same transaction; every request inserts a `page_views` row.
- **OpenEMR 8.3.0** (`worlds/claims_ops_v1/openemr/`, real): native LAMP install script, base population of 6 providers / 40 patients / 79 appointments, and a SQLite *shim* of the ten tables forkloop touches so seeding SQL and oracle queries are tested offline. Both apps describe the **same** people: the portal derives its patients and providers from the OpenEMR base data.

Task families (`worlds/claims_ops_v1/tasks/`), each a pure function of `(family, seed, split)`:

1. `reschedule_constrained` — move patient X's appointment with Dr Y to the next Tuesday afternoon; provider must not change.
2. `update_insurance_reconcile` — insurance changed; update OpenEMR, then resubmit claim C-#### in the portal with the corrected member ID. Sometimes OpenEMR is already updated (one-system variant).
3. `resolve_denial` — CO-197 denial; find the authorization number in the patient's OpenEMR PDF (which document, which page, with decoy numbers on the page) and file exactly one appeal.

Randomised: names, DOBs, providers, dates, member IDs, denial wording, amounts, document/page placement, distractor count and similarity (same surname, adjacent claim numbers, near-miss member IDs), inbox noise, one-system vs both, partial starting state. Held-out seeds use disjoint surname pools; held-out compositions chain "update insurance, resubmit one denial, appeal another, leave a third alone".

## Results

None yet. Tables for the bake-off, per-checkpoint metrics with CIs, and reason-code breakdown will land here as `train/eval.py` and `train/bakeoff.py` produce them.

## Cost

See `docs/cost.md` for the verified price table, formulas, the all-in estimate ($80–350), and the actual-spend ledger (currently empty).

## Limitations and what didn't work

`docs/limitations.md`. Short version: nothing has been measured on Solari yet; the OpenEMR audit tripwire is patient-keyed and coarser than the portal's; checksums are scoped to listed tables; attachment tasks are hard for small models and are a difficulty knob.

## Build log

`docs/buildlog.md`.

## Layout

```
forkloop/            library (see system.md for every module)
worlds/claims_ops_v1 portal app, OpenEMR layer, task families, build.sh, world.yaml
worlds/toy_counter   offline world that exercises the full loop
train/               make_sft, train_lora, eval, plot, bakeoff, wilson
spikes/              six day-1 measurements against Solari
tests/               158 offline tests (portal, OpenEMR layer, core loop, claims-ops oracle, parsers, training, cost)
docs/                contracts.md (the interface spec), spikes.md, cost.md, limitations.md, buildlog.md
```

## Credits

Solari (desktops, snapshots, the cookbook this lives in), OpenEMR, Microsoft's Fara 1.5 and Qwen 3.5 (student candidates), Gym-Anything / CUA-World (prior work and the expectation anchor for small-model baselines on OpenEMR), and the two cookbook forks that verified `fromSnapshot` byte-identical filesystems and the plan concurrency caps.

MIT.
