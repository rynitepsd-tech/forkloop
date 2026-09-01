# system.md — how Forkloop is built, module by module

This document explains every part of the product as it exists today. It is
descriptive: if the code and this file disagree, the code is the truth and
this file has a bug. The interface *spec* (what the parts promise each other)
is `docs/contracts.md`; the plan that motivated all of it is
`../../forkloop-plan.md` on the author's machine and is summarised in the
README.

Contents

1. Purpose and thesis
2. Repository layout
3. The two channels
4. Core library (`forkloop/`)
   4.1 actions · 4.2 types · 4.3 backends (base, fake, solari) · 4.4 dbaccess ·
   4.5 oracle · 4.6 tasks · 4.7 world · 4.8 seed · 4.9 observe · 4.10 reset ·
   4.11 pool · 4.12 env · 4.13 search · 4.14 trajectories · 4.15 exporters ·
   4.16 metrics · 4.17 policies · 4.18 bench · 4.19 util · 4.20 cli
5. Worlds
   5.1 toy-counter · 5.2 claims-ops-v1 (portal, OpenEMR layer, task families,
   build scripts)
6. Training ladder (`train/`)
7. Spikes (`spikes/`)
8. Cookbook example
9. Tests
10. Data flow of one episode, end to end
11. What runs where (offline vs Solari vs GPU)
12. Verified external facts and where they came from

---

## 1. Purpose and thesis

Computer-use agents are trained inside environments that must be reset to a
known state thousands of times. Existing frameworks (Gym-Anything, CUA-Gym,
OSWorld) orchestrate Docker or QEMU with per-application reset scripts.
Solari desktops expose `snapshot()`, `revert(id)` (rewind the same machine)
and `create(from_snapshot=id)` (independent copies). Forkloop's thesis: with
those three calls the environment *runtime* becomes trivial — build the world
once, snapshot it, then every `reset()` is one `revert()` and every search
branch is one fork — and the hard part becomes the *world* and the *oracle*.

The headline artifact the plan calls for is a learning curve: a vision-only
~4B policy, fine-tuned on oracle-verified teacher trajectories, improving on
held-out episodes of a cross-application claims workflow (payer portal +
OpenEMR), with every reward decided by deterministic SQL and every reset done
by `revert()`. This repository contains everything needed to produce that
artifact; the measurements themselves have not been taken yet (README
"Status", `docs/limitations.md`).

## 2. Repository layout

```
projects/forkloop/
  pyproject.toml            package "forkloop", extras: dev, world, teacher, student, train, plots, gym
  README.md · CLAUDE.md · system.md (this file)
  forkloop/                 the library (§4)
    backends/{base,fake,solari}.py
    policies/{base,scripted,teacher,student,action_parse}.py
    exporters/{jsonl,sft_pairs,osworld}.py
    bench/{reset_benchmark,cost_model}.py + bench/local_baseline/ (docker-compose baseline)
    util/{sql,minipdf}.py
    actions.py types.py tasks.py oracle.py dbaccess.py world.py seed.py observe.py
    reset.py pool.py env.py search.py trajectories.py metrics.py cli.py
  worlds/
    toy_counter/{world.yaml,world.py}
    claims_ops_v1/{world.yaml,world.py,seed_world.py,build.sh,browser_setup.sh}
    claims_ops_v1/portal/     FastAPI app, schema, base data, templates, css
    claims_ops_v1/openemr/    install.sh, shim_schema.sql, base_data, openemr_sql helpers
    claims_ops_v1/tasks/      common.py + one module per family
  train/                    make_sft, train_lora, eval, plot, bakeoff, wilson, README, examples/
  spikes/                   _common.py, spike_01..06, run_all.sh
  tests/                    158 offline tests
  docs/                     contracts, spikes, cost, limitations, buildlog
examples/desktop-snapshot-revert-py/   the cookbook example (outside the project dir)
```

## 3. The two channels

Every interaction with a machine is on one of two channels and the code keeps
them apart by construction:

| | Agent channel | Controller channel |
| --- | --- | --- |
| Who | the policy being evaluated (teacher or student) | forkloop on the researcher's machine |
| In | `Observation`: screenshot PNG, instruction, step index, last-k actions, screen size | DB rows, exec output, health, snapshot ids |
| Out | one `Action` per step (click/type/key/scroll/drag/wait/done) | SQL scripts, files, commands, snapshot/revert/kill |
| Implemented by | `Machine.screenshot/click/type_text/press/...` | `Machine.exec/read_file/write_file/snapshot/revert/kill` |

The env's `info` dict deliberately exposes only `TaskInstance.public_info`
(task id, family, seed, split, world, budget). The manifest with `expected`
and the oracle spec is written to the controller's run directory, never to
the VM.

## 4. Core library

### 4.1 `actions.py`

`Action` is a frozen dataclass with a `type` and optional fields. Ten types:
`click`, `double_click`, `right_click`, `move`, `scroll`, `drag`, `type`,
`key`, `wait`, `done`. Three input forms are accepted by `Action.parse`:

- dict (the canonical JSON in contracts §3, with tolerant aliases such as
  `left_click`, `coordinate: [x, y]`, `start`/`end`);
- JSON string;
- compact text: `click(640, 360)`, `type("hi")`, `key("ctrl+l")`,
  `scroll(640, 360, "down", 3)`, `wait(1)`, `done()`.

`validate(width, height)` bounds-checks coordinates, caps typed text at 2000
chars, waits at 30 s, scroll amounts at 50. Key names are normalised to
xdotool names through `KEY_ALIASES` (`enter`→`Return`, `cmd`→`super`, ...).
`to_compact()` is the canonical string used in history, SFT targets and
logs; `to_dict()` is the canonical JSON.

### 4.2 `types.py`

Small shared records: `ExecResult`, `SnapshotInfo`, `MachineInfo`,
`Observation`, `StageTiming`, `ResetReport`. `Observation.to_dict()` omits
the image unless asked.

### 4.3 backends

`backends/base.py` defines the `Machine` and `Backend` protocols
(contracts §2), the error hierarchy (`ConcurrencyError` = plan cap /
HTTP 429, `PlanGateError` = HTTP 402, `CapacityError` = HTTP 503) and
`apply_action(machine, action)`, the single place that maps an `Action` onto
the agent-channel calls.

`backends/fake.py` — an in-process simulator with *real* snapshot semantics.
Each machine is a directory; `snapshot()` copies it, `revert()` restores it,
`create(from_snapshot=...)` clones it. `exec` runs the command locally with
every absolute VM path (`/var/...`, `/etc/...`, `/home/...`, `/tmp/...`,
`/opt/...`) rewritten under the machine root, so the same `python3`/`sqlite3`
seeding and hashing commands run unchanged. A world may attach a `GuiSim`
(`render(root, size) -> PNG`, `apply(root, action, size)`); without one,
screenshots are a labelled blank frame. Optional injected latencies let the
pool and benchmark be exercised offline; `concurrency_cap` reproduces the
plan limit. It exists so the *entire* loop is testable without a key, and it
is the reason the test suite runs in about two minutes.

`backends/solari.py` — the real thing. `SolariBackend` wraps
`solari_sandbox.SandboxClient` and creates desktops through
`create_desktop(...)` — the unified `POST /sandboxes` route with
`kind: "desktop"` — because that is the only desktop constructor in the SDK
that accepts `from_snapshot`. Every machine is tagged
`metadata={"forkloop": "1", "run_id": ...}` so orphans can be found and
killed. `SolariMachine` maps the protocol one-to-one onto the SDK's `Desktop`
handle (table in contracts §2). Two behaviours worth knowing:

- `revert()` calls the SDK, then `reconnect()`s the control channel and polls
  `health()` until `ready`, because the guest accepts only one control
  connection for a brief window after a restore (comment in
  `solari_core/transport.py`).
- `scroll` is emulated with `Page_Down`/`Page_Up`/arrow keys after a
  `mouse.move`, because the SDK's `mouse.scroll` takes a button code and only
  names left/middle/right.

`PLAN_CAPS` (free 1, starter 2, pro 10) seeds `concurrency_cap`; override
with `FORKLOOP_CONCURRENCY`.

### 4.4 `dbaccess.py`

The controller never opens a network connection to a database. `DbAccess`
runs CLIs inside the machine over `exec` and parses the output:

- SQLite: a `python3 -c` one-liner (stdlib `sqlite3`) for queries (JSON
  rows), scripts (one transaction, statement splitter that honours quotes
  and `--` comments, rollback on error) and per-row hashing.
- MySQL/MariaDB: `mysql --batch --raw` with the password read from
  `/etc/forkloop/openemr.pw` *inside* the VM; scripts are wrapped in
  `START TRANSACTION ... COMMIT`; row hashes are computed in SQL with
  `MD5(CONCAT_WS(...))` across all listed tables in one `UNION ALL` query
  after a single `information_schema` lookup, so a baseline costs two
  round-trips regardless of table count.

Parameters use `?` placeholders substituted client-side by
`util/sql.substitute`, which is quote-aware. `quote()` refuses backslashes
because MariaDB and SQLite disagree on them.

### 4.5 `oracle.py`

`Check` (id, kind, db, sql, params, equals, op, reason_code, allow,
exempt_tables), `OracleSpec` (effects, invariants), `Verdict` (reward,
milestones, reason_code, failed, details). `Oracle.evaluate` runs every
check (so `failed` is complete), sets `reason_code` to the first failure,
`reward = 1.0` iff nothing failed, `milestones` = fraction of effects passed.

Kinds:

- `query` / `count`: first column of the first row compared with `equals`
  using `op` (`eq`, `ne`, `in`, `ge`, `le`, `contains`) after light
  normalisation (numeric strings ≡ ints).
- `baseline_checksum`: `Baseline.capture` records `{table: {pk: md5(row)}}`
  for every table in `oracle.checksum_tables` and the max primary key of each
  append-only table (`watermark_tables`). `diff_baseline` reports
  added/changed/deleted rows; anything not in the check's `allow` map and not
  in an exempt table fails with `COLLATERAL_EDIT`.
- `ui_path_only`: for every non-exempt changed row there must be an audit row
  written after the watermark. The audit table per db comes from
  `oracle.audit` in `world.yaml`; `audit_entity_names` maps table →
  entity value; `audit_id_lookup` maps table → a column on the changed row
  whose value is the audit key (OpenEMR's `log` keys by `patient_id`, so
  `insurance_data` changes are looked up through `pid`). With `loose: true`
  a match on the id column *or* a `comments LIKE %id%` also counts.
- `forbidden_screens`: page-view rows after the watermark whose path starts
  with any of `world.forbidden_paths`.

`REASON_CODES` is the closed vocabulary from contracts §6; `OracleSpec.validate`
rejects unknown codes, duplicate ids and malformed checks at generation time.

### 4.6 `tasks.py`

`TaskInstance` (world, family, seed, split, task_id, instruction,
initial_screen, seeding, expected, oracle, budget, difficulty) and `Seeding`
(portal_sql, openemr_sql, files, post_commands, extra_sql). `SeedFile` carries
base64 content plus a VM path and mode. `to_dict/from_dict/to_json`
round-trip exactly; `public_info` is the agent-visible subset.

### 4.7 `world.py`

`WorldConfig.load(world.yaml)` → `World` (or the subclass named in
`module:`). The registry scans `worlds/*/world.yaml`; `load_world(name)`
matches on the public `name:` or the directory name. `World` provides:
`generate` (imports `seed_module.generate`, validates the oracle spec),
`databases(machine)` (builds `DbAccess` objects; on the fake backend a
`mysql` entry with `shim_path` becomes SQLite), `oracle_context`,
`checksum_tables/watermark_tables/primary_keys`, and the hooks
`build`, `health` (DB pings, HTTP health when the machine has the `http`
capability), `open_initial_screen` (ctrl+l, URL, Return), `before_episode`,
`gui_factory`.

### 4.8 `seed.py`

`apply_seeding(machine, dbs, seeding)`: write files, run `portal_sql`,
`openemr_sql`, then `extra_sql` per named db, then `post_commands` (argv
lists). Returns a `SeedReport` with counts and timing. Fails loudly on any
non-zero exit.

### 4.9 `observe.py`

`png_hash`, `image_size`, `resize_png(max_side) -> (png, scale)`, and
`wait_stable(machine, timeout, interval, required=2)`: the first screenshot
that repeats consecutively, or `ScreenNotStable`.

### 4.10 `reset.py`

`ResetController.reset(worker, task)` executes the fixed protocol:
`restore` → `seed` → `before_episode` → `health` → `baseline` →
`initial_screen` → `stable_screen`. Each stage is a `StageTiming` in a
`ResetReport`; a failure raises `ResetError` carrying the partial report and
the env marks the worker unhealthy so the pool replaces its machine. The
report is what the reset benchmark measures.

### 4.11 `pool.py`

`WorkerPool(backend, world, size, mode, golden_snapshot, run_id, ...)`.
`size` is clamped to the backend's concurrency cap. Two modes:

- `revert`: long-lived machines; `Worker.restore()` is `revert(golden)` on
  the same id (or a fresh `create(from_snapshot=golden)` if the machine died).
- `fork`: `restore()` kills the old machine and creates a new one from the
  golden snapshot.

If no golden snapshot is configured, the first worker builds the world
(`world.build`) under a lock and snapshots it; a second worker that raced on
the same miss reverts (or re-forks) to the snapshot the first one built. On
Solari this implicit build is refused — you run `forkloop build-world`
explicitly because it takes minutes. `create` retries 429/503 with backoff;
`reap_orphans` kills `forkloop=1` machines from other runs.

### 4.12 `env.py`

`Env(world, backend, family, split, pool, recorder, history_k, settle_s,
stable_after_action, max_invalid, ...)`.

- `reset(seed, family=None, task=None)` ends any previous episode (releasing
  its worker), generates the task, acquires a worker, runs the reset
  protocol, opens an `EpisodeRecorder`, and returns `(Observation, info)`.
- `step(action, meta=None)` parses/validates the action (invalid ones count
  against `max_invalid` and never touch the machine), applies it, waits
  `settle_s` (or for a stable screen), takes the after-screenshot, records
  the step, and decides termination: `done` action, `max_steps`,
  `max_seconds`, or the invalid-action limit. On termination it runs the
  oracle and returns its reward; otherwise reward is 0.0.
- `verify()` is idempotent and fixes up reason codes for truncation.
- `checkpoint()`/`restore(cp)` snapshot and revert the machine *and* the
  env's own state (step, history, invalid count, clock) for search.
- `run_episode(env, policy, seed)` is the plain loop.

### 4.13 `search.py`

`best_of_n(env, policy, n, seed, branch_prob, confidence_threshold,
max_branch_points, mode)`. At an uncertain step: checkpoint, gather `n`
candidates (`policy.propose` if available, else repeated `act`), dedupe,
then either

- `revert` mode: for each candidate, `env.restore(cp)`, roll out to the end
  with the child recorder, verify, snapshot the end state; or
- `fork` mode: for each candidate, create a machine from the checkpoint
  snapshot (bounded by `concurrency_cap - 1`), attach a sub-env directly to
  the already-seeded state, roll out in parallel.

The best verdict (reward, then milestones) wins; the main recorder adopts the
winning branch's steps (copying screenshots) and finishes with its verdict;
in revert mode the machine is reverted to the winner's end snapshot.
`SearchStats` counts branch points, branches, wins, snapshots, reverts, forks.

### 4.14 `trajectories.py`

`Recorder(root, run_id, meta)` writes `run.json` (backend, world, policy, git
sha). `EpisodeRecorder` writes `manifest.json` (the full task + world version
+ episode id), `reset.json`, `steps.jsonl` (one `StepRecord` per line with
before/after shot paths, action, raw text, validity, latency, tokens,
milestones, note, search tag, error), `verdict.json` (+ wall seconds, step
counts, end reason), `shots/NNN_before.png` / `NNN_after.png`, and optionally
`episode.mp4` via ffmpeg. `fork(label)` makes a child recorder under
`branches/<label>/`; `adopt(child, from_step)` replaces the parent's tail.
`iter_episode_dirs` / `load_episode` are the read side used by exporters,
metrics and `train/`.

### 4.15 exporters

`export_jsonl` (episode-level, optional steps, optional success filter),
`export_sft_pairs` (one record per valid step of every reward-1.0 episode:
image path, instruction, last-k history, compact target; supports
`limit_episodes` for the 25/50/100/200 checkpoints and excludes held-out
splits by default), `export_osworld` (one OSWorld-style task JSON per task,
expected values omitted unless asked).

### 4.16 `metrics.py`

`wilson(k, n)` and `summarize_run(run_dir)` → success rate, milestone score,
median steps/wall/reset, cost per success (VM hours × hourly rate + tokens ×
prices), invalid-action rate, wrong-record / duplicate / collateral rates,
reason-code histogram, per-family and per-split breakdowns, all rates with
95% Wilson intervals. `format_table` prints it.

### 4.17 policies

- `base.py`: `Policy` protocol (`name`, `async act(obs) -> (Action|None,
  meta)`), optional `propose(obs, n)`; `propose_or_repeat` helper.
- `scripted.py`: `ScriptedPolicy` (replay), `CallbackPolicy`, `RandomPolicy`
  (the floor for any curve).
- `teacher.py`: `TeacherPolicy` — Anthropic API, model `claude-opus-5` by
  default, the GA `computer_toolset_20260801` (no beta header), adaptive
  thinking, cached system prompt, server-side refusal fallbacks
  (`fallbacks="default"`). The model may batch several actions per turn;
  the policy queues them and hands the env one per `act()`. `screenshot`,
  `zoom` and `cursor_position` members are answered locally from the current
  observation and never consume an env step. After each executed batch the
  policy appends a fresh screenshot. Image history is pruned to the last 8
  screenshots. The prompt asks for a `confidence: 0.NN` line, which is
  parsed into `meta["confidence"]` for search. Coordinates are rescaled when
  the screenshot was downscaled for the API.
- `student.py`: `StudentPolicy` — any OpenAI-compatible chat endpoint via
  `httpx` (vLLM serving Fara 1.5, Qwen 3.5-VL, UI-TARS). Prompt styles
  `compact`, `json`, `fara`; Fara's coordinates are in a fixed 1000×1000
  space and are rescaled accordingly (`coord_space`). `propose(obs, n)` uses
  the server's `n`. Network and parse failures return `(None, meta)`.
- `action_parse.py`: parsers for compact text, JSON (fenced or not) and Fara
  `<tool_call>` blocks, `scale_coords`, `parse_tool_calls`; never raise.

### 4.18 bench

- `reset_benchmark.py`: runs `ResetController.reset` N times per method
  (`revert`, `fork`, `cold` = create + full world build), appends JSONL with
  stage timings, and summarises p50/p95/p99, failure rate, restore-stage p50,
  cost per 1k resets (from `cost_model`) and a "state restored" column. The
  fake backend is allowed with an explicit warning that the numbers are not
  Solari numbers.
- `cost_model.py`: verified Solari prices (Sept 2026), `vm_hour_cost`,
  `cost_per_1k_resets`, `episode_cost`, `budget_table`.
- `local_baseline/`: docker-compose (OpenEMR `8.3.0-2026-08-30`, MariaDB
  10.11, the portal), `snapshot.sh` / `restore.sh` / `bench_local.sh` that
  restore the *same* state (DB dump, uploads, documents, browser profile) and
  time it to "both apps healthy", for a fair Chart 2 bar.

### 4.19 util

`sql.py` (`quote`, `substitute`, `ident`), `minipdf.py` (a dependency-free
PDF writer used for the synthetic authorization letters Chrome renders in
OpenEMR).

### 4.20 `cli.py`

`forkloop worlds | task | build-world | run | collect | export | metrics |
reset-bench | reap`. `--backend fake|solari` (env `FORKLOOP_BACKEND`),
`--policy scripted|random|teacher|student`, `--best-of N --search-mode
revert|fork`, `--seeds 0-99,200`, `--concurrency`.

## 5. Worlds

### 5.1 toy-counter

Two on-screen counters with ± buttons and a note box, state in a SQLite file
under the machine root, a `GuiSim` that renders the screen with Pillow and
applies clicks/typing. Tasks: "set counter A to N, don't touch B". Its
oracle uses every check kind (query, baseline checksum with an allow-list,
`ui_path_only` against its own audit table). It is the world that lets
`tests/test_core_toy.py` exercise revert, fork, seeding, budgets, invalid
actions, best-of-N adoption, exporters and metrics in seconds.

### 5.2 claims-ops-v1

`world.yaml` declares paths, the two databases (portal SQLite; OpenEMR
MariaDB with a `shim_path` for the fake backend), app health URLs, the three
families, the budget, forbidden paths and the whole oracle configuration
(checksummed tables, watermark tables, primary keys, exempt tables, audit
tables, entity names, audit-id lookups, page views).

`world.py::ClaimsOpsWorld`:

- `build(machine)` on Solari uploads `worlds/` and `forkloop/` to
  `/opt/forkloop`, installs the portal systemd unit, runs `build.sh` with
  sudo, checks health, and takes the golden snapshot. On the fake backend it
  initialises the portal SQLite, loads `openemr/shim_schema.sql` and the
  OpenEMR base SQL into the shim, and snapshots.
- `health` adds row-count checks for both patient tables.
- `open_initial_screen` uses only agent-channel keys.

`build.sh` (runs as root inside the VM): apt packages, a venv for the portal,
`portal.db init` + `seed-base`, the `forkloop-portal` systemd service on
:8080 with a health wait, `openemr/install.sh --with-demo-data` (native LAMP,
OpenEMR 8.3.0, password written to `/etc/forkloop/openemr.pw`), the OpenEMR
base population via `mysql`, document directory permissions, and
`browser_setup.sh` as the desktop user (Chrome with fixed geometry and no
first-run UI, logs into both apps with xdotool, lands on `/claims`).

**Portal** (`portal/`): `app.py` (`create_app(db_path, uploads_dir, secret)`,
routes per contracts §7 plus `/patients`; signed-cookie sessions; a
middleware writing `page_views`; audit rows in the same transaction as each
write; `PORTAL_FIXED_NOW` for deterministic timestamps), `db.py` (schema,
`init_db`, `seed_base`, pbkdf2 passwords, CLI), `base_data.py` (1 user, 6
providers and 40 patients *derived from the OpenEMR base data*, 120 claims
across all statuses, 8 messages, 8 appeals, 8 resubmissions — deterministic,
written to `base_data.json`), `templates/` + `static/style.css` (no JS on
task paths, fixed 1280 px layout, measured element sizes).

**OpenEMR layer** (`openemr/`): `install.sh` (unattended 8.3.0 install with
pinned tarball and sha256, `InstallerAuto.php` with the verified arguments,
`OPENEMR_ENABLE_INSTALLER_AUTO=1`, socket-auth root path, health curl),
`shim_schema.sql` (the ten touched tables with real 8.3.0 column names,
calendar categories, document category tree), `base_data.py` (6 provider
users, 4 insurers, 40 patients with primary insurance, 79 appointments over
six weeks from Monday 2026-09-07, log rows; `render_base_sql`),
`openemr_sql.py` (portable INSERT/UPDATE builders for every touched table
plus `assert_portable`), `docs_paths.py`, `providers.json`.

**Task families** (`tasks/`): `common.py` holds the merged `BaseData` view,
split-disjoint surname pools, payer/plan table, denial codes with several
wordings each, `Person` (one synthetic patient inserted into *both* apps with
aligned ids), `Claim`, near-miss member-id generation, inbox noise, and the
authorization-letter PDF builder (real number on a chosen page, decoy
numbers around it). Each family module's `generate(family, seed, split)`
returns a `TaskInstance` with seeding SQL for both databases, files for
OpenEMR documents, controller-only `expected`, and an oracle spec using the
shared reason-code vocabulary. `seed_world.py` dispatches by family and
builds the held-out compositions (seeds ≥ 200000) that chain families 2 and
3 on one patient with a third denial that must be left alone.

Per-episode ids live in a block of 1000 starting at `500000 + seed*1000`, so
episodes never collide with each other or with the base data (100001+).

## 6. Training ladder (`train/`)

`make_sft.py` (verified episodes → per-step records, `--limit` for
checkpoints, split exclusion), `train_lora.py` (transformers + peft LoRA for
Qwen3.5-based VLMs including Fara 1.5; prompt-token masking; `--smoke`;
imports without torch; rewrites targets into the chosen prompt style and
coordinate space), `eval.py` (held-out episodes through the real `Env`, N
sampling seeds, Wilson CIs, optional best-of-N, `eval_summary.json`),
`plot.py` (`chart1` learning curve, `chart2` reset benchmark, `--demo`
synthetic placeholders), `bakeoff.py` (base success, action-format validity,
tokens/step, VRAM, LoRA smoke → markdown table), `wilson.py`, `README.md`
(rungs 1, 2, 2.5, 3 with commands and GPU guidance).

## 7. Spikes (`spikes/`)

Six standalone scripts answering the plan's day-1 questions against real
Solari: revert latency (20×, health + stable screenshot), fork independence,
`record=True` with `from_snapshot`, memory/process/window/stream survival
across revert, screenshot→click→screenshot latency, MariaDB consistency after
snapshot with and without a read lock. Each prints a table and appends a
JSON line to `spikes/results.jsonl`; `run_all.sh` runs them in order;
`docs/spikes.md` holds the empty result tables and decision rules.

## 8. Cookbook example

`examples/desktop-snapshot-revert-py/` — a self-contained cookbook-style
program: create a desktop via `create_desktop`, type into mousepad, snapshot,
type more, revert (timed) and screenshot, fork with `from_snapshot`, prove
independence, kill both. Comments sit on the lines where the gotchas bite.

## 9. Tests

158 tests, all offline, ~2 minutes:

| File | Covers |
| --- | --- |
| `test_portal.py` (22) | schema/base counts, login, filters, appeal with upload + sha256 + same-transaction audit (trigger-based proof), duplicate appeal allowed, resubmit, messages, eligibility, page_views, `/admin`, determinism |
| `test_openemr_layer.py` (15) | shim loads, base data deterministic and executes, every SQL helper executes on the shim, `quote`, portability |
| `test_core_toy.py` (11) | action parsing, registry, full episode + recorder + exporters + metrics, collateral and direct-DB verdicts, budget/invalid truncation, revert restores state, fork mode, best-of-N adoption, random policy, Wilson |
| `test_claims_ops_world.py` (8) | generator determinism and split disjointness, manifest round-trip, resolve_denial success and rejections (wrong number, duplicate, wrong claim, direct write, forbidden screen), update_insurance both-systems logic, reschedule oracle incl. provider change, concurrent golden build |
| `test_student_policy.py`, `test_train.py` (92) | every parser style, coordinate scaling, mocked vLLM transport, make_sft/limits/splits, Wilson, plots, train_lora import without torch, eval through the real env |
| `test_cost_model.py` (10) | verified prices, VM-hours per credit, monotone reset cost, budget rows |

## 10. One episode, end to end

1. `forkloop collect --policy teacher --best-of 2` builds a `SolariBackend`, a
   `WorkerPool(mode=revert)` sized to the plan cap, and a `Recorder`.
2. `Env.reset(seed)` → `world.generate(family, seed, split)` (pure) →
   `pool.acquire()` → `ResetController.reset`: `revert(golden)` + reconnect +
   health poll; seeding SQL and PDF files over the controller channel;
   HTTP/DB health; baseline hashes and watermarks; ctrl+l/URL/Return; wait
   for two identical screenshots. `reset.json` records every stage.
3. The teacher receives the instruction and screenshot, writes a confidence
   line and one or more tool calls; the policy hands the env one `Action`.
4. `Env.step` applies it on the agent channel, waits, screenshots, records
   the step. If confidence is low, `best_of_n` checkpoints, tries the
   alternatives by reverting, and adopts the winner.
5. On `done` (or budget), the oracle recomputes hashes, runs the checks, and
   writes `verdict.json` with a reason code. The worker is released; the next
   `reset` reverts the same machine.
6. `forkloop metrics` summarises; `forkloop export --format sft` produces the
   per-step training set; `train/` takes it from there.

## 11. What runs where

| | Offline (this repo's tests) | Solari desktop | GPU box | Anthropic API |
| --- | --- | --- | --- | --- |
| Core loop, oracle, recorder, search | fake backend | real | — | — |
| Portal | in-process (TestClient) | systemd on :8080 | — | — |
| OpenEMR | SQLite shim of 10 tables | real 8.3.0 on :80 | — | — |
| Teacher | not run | drives the desktop | — | computer-use toolset |
| Student | mocked transport | drives the desktop | vLLM serves it | — |
| LoRA training | `--smoke` needs torch | — | yes | — |
| Reset benchmark | simulator numbers (labelled) | yes | — | — |

## 12. Verified external facts

| Fact | Source |
| --- | --- |
| `SandboxClient.create_desktop(from_snapshot=...)`, `Desktop.snapshot/revert`, `commands.run` argv semantics, `kill` vs `close`, error classes, the post-restore single-connection window | solari-sandbox / solari-core 0.2.0 source (installed from PyPI) |
| Plans: Free $0/1 concurrent, Starter $20/2, Pro $200/10; 2 vCPU/4 GB $0.114/h Starter + $0.02/h screen | docs.getsolari.com/pricing |
| Snapshots keep the machine running; revert keeps the id; from_snapshot makes independent copies; both VMs and sandboxes | docs.getsolari.com/snapshots |
| Templates: base / default / office / code; Image builder | docs.getsolari.com/templates |
| OpenEMR 8.3.0 released 2026-08-18, PHP 8.3+, MariaDB 10.6+; tarball asset and sha256; `InstallerAuto.php` args; `OPENEMR_ENABLE_INSTALLER_AUTO=1` | github.com/openemr/openemr v8_3_0 |
| Docker tag `openemr/openemr:8.3.0-2026-08-30` (no `7.0.3` tag exists) | hub.docker.com |
| Fara 1.5 (4B/9B/27B, Qwen3.5 base, `<tool_call>` format, 1000×1000 coordinate space) | huggingface.co/microsoft/Fara1.5-4B, github.com/microsoft/fara |
| Computer-use toolset `computer_toolset_20260801` GA, member names, batch semantics, result shapes | platform.claude.com computer-use docs |
| Gym-Anything / CUA-World includes OpenEMR; small models collapse on high complexity; 200-step budgets help on OpenEMR | arxiv 2604.06126 |
