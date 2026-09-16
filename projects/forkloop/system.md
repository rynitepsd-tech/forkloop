# system.md — how Forkloop is built, module by module

This document explains every part of the product as it exists today. It is
descriptive: if the code and this file disagree, the code is the truth and
this file has a bug. The interface *spec* (what the parts promise each other)
is `docs/contracts.md`. The supported user paths are `forkloop demo`, setup
diagnostics with `forkloop doctor`, matched evaluation with `forkloop compare`,
and text/HTML evidence inspection with `report` and `compare-report`. Other
families, search and training remain research paths. See the README and
[dated verification record](docs/product-handoff.md) for exercised paths and limits.

Contents

1. Purpose, thesis, and current evaluation status
2. Repository layout
3. The two channels
4. Core library (`forkloop/`)
   4.1 actions · 4.2 types · 4.3 backends (base, fake, solari) · 4.4 dbaccess ·
   4.5 oracle · 4.6 tasks · 4.7 world · 4.8 seed · 4.9 observe · 4.10 reset ·
   4.11 pool · 4.12 env · 4.13 search · 4.14 trajectories · 4.15 exporters ·
   4.16 metrics · 4.17 policies · 4.18 bench · 4.19 util · 4.20 cli · 4.21 report
5. Worlds
   5.1 toy-counter · 5.2 claims-ops-v1 (portal, OpenEMR layer, task families,
   build scripts)
6. Training ladder (`train/`), frozen evaluation (6.1) and magnification diagnostic (6.2)
7. Spikes (`spikes/`)
8. Cookbook example
9. Tests
10. Data flow of one episode, end to end
11. What runs where (offline vs Solari vs GPU)
12. Verified external facts and where they came from

---

## 1. Purpose and thesis

Computer-use agents need repeatable task state. Prior work includes
[Gym-Anything / CUA-World](https://arxiv.org/abs/2604.06126) and
[OSWorld](https://github.com/xlang-ai/OSWorld); Forkloop does not claim to
invent software environments or execution-based evaluation.
[Solari](https://getsolari.com) desktops provide `snapshot()`, `revert(id)`
and `create(from_snapshot=id)`. These simplify the restore stage, not the
whole runtime: reset also seeds, prepares the world, checks health, captures
baselines and opens a stable initial screen (§4.10). The first allocation
forks/builds a golden; healthy reused workers normally revert.

The original research plan called for a learning curve: a vision-only
~4B policy, fine-tuned on oracle-verified teacher trajectories, improving on
held-out episodes of a cross-application claims workflow (payer portal +
OpenEMR), with every reward decided by deterministic SQL and task state reset
through the snapshot-backed pipeline. This repository contains the training
and evaluation components, but not the intended positive learning-curve evidence.
Historical base/SFT-v1/SFT-v2 development runs each scored 0/30.
The v3 adapter has now been trained and evaluated on a larger frozen set of
saved observations. It improves exact reading and immediate action selection
there; complete-workflow success was measured at **0/2 for both base and v3**
in the paired development evaluation. No positive learning curve is established.

### Current product — September 15 recovery

New Solari allocations are paused by `require_solari_lifetime_bound`, including
the historical standalone spike allocators. The guard is independent of ledger
state and pricing acknowledgments. Doctor reports the capability hold without
advertising a fictitious per-create upper bound; offline workflows and cleanup
remain available. The source release's report regressions construct their own
offline evidence and no longer require private historical screenshots.

The product now accepts two built-in or trusted custom policies through a
versioned configuration. `policy_config.py` resolves identities, prompt content
and constructor options; `comparison.py` records the full best-of-one plan before
allocation, alternates policy order by seed, and compares recorded task/reset
state. Every planned cell remains visible. Setup, provider and interrupted
attempts are not silently counted as model failures; incomplete comparisons
cannot nominate a leader. `comparison_html.py` presents those denominators,
changed settings, equivalence checks and links to episode evidence.

`forkloop compare-report --format html --bundle DIRECTORY --crop-top PIXELS`
regenerates a new HTML-only sharing directory. It excludes raw controller JSON,
logs and original screenshot files. Every exported episode is freshly rendered
with the selected crop, not copied from potentially uncropped prior HTML.
Screenshot pixels and free text still require human review; this is not a secret
scanner or anonymization guarantee.

The interrupted September 15 memory experiment retained one comparable pair
out of four planned, with A and B each 0/2 scored and one additional A attempt
excluded after a backend action error. Navigation retained two comparable pairs:
two compact-prompt failures and two workflow-prompt successes. Original plans
and interrupted records are unchanged. This is incomplete development evidence,
not a winning-policy or held-out reliability claim.

Recovery found two session desktops still reported running about ten hours
after creation; both were explicitly killed, and a read-only inventory confirmed
no remaining active session machines. The five-hour reservation assumption
therefore cannot be treated as a verified provider lifetime cap. Priced elapsed
time is not an invoice, and confirmed cleanup does not erase uncertain charges.

### Current product — offline evidence integration, September 10 session

`forkloop report PATH --format html --out FILE.html` now generates a
self-contained, script-free report from the canonical retained artifacts.
The worked example exposes `WRONG_VALUE`, the expected/persisted authorization,
all declared check results, scoped exemptions and six preserved screenshots;
142 unavailable references remain explicitly unavailable. No live query or
model call is made. Default text reporting and run filters remain supported.

Five controller-constructed offline scenarios exercise the real portal HTTP
routes, reset/recording and oracle with SQLite stand-ins: correct appeal accepted,
wrong authorization rejected, wrong-record edit rejected, duplicate rejected,
and abrupt interruption left unscored. They do not measure policy navigation.
The exact-one invariant remains exact-one; count shortfalls now carry
`NOT_DONE` instead of a misleading duplicate code. Incomplete invariant evidence
can no longer become a clean side-effect summary.

The retained model results below are unchanged. The next product evaluation is
a human reviewing the report against a concrete policy-comparison decision,
not another paid learning experiment. No external usage or demand is established.

### Current state — September 7, 2026, after the live paired comparison and the stopped magnification diagnostic

Three evaluation sessions ran on September 6–7 against the same frozen weights;
the results of record, in order, are
[frozen-v3-evaluation-results.md](docs/frozen-v3-evaluation-results.md)
(saved observations plus an incomplete live attempt),
[live-paired-v3-results.md](docs/live-paired-v3-results.md)
(the completed live paired comparison), and
[document-magnification-results.md](docs/document-magnification-results.md)
(a diagnostic stopped before model scoring). The
[training handoff](docs/lambda-v3-handoff.md)
records how the weights were produced; the
[evaluation readiness handoff](docs/evaluation-readiness-handoff.md)
records the frozen package. Their run proposals are historical.

**Preserved weights.** The main adapter completed **411/440 planned optimizer
steps, or 1.869169 epochs**, on 25 teacher episodes (1,758 examples). It is
not a completed two-epoch run and was never resumed. Base:
`microsoft/Fara1.5-4B`, revision `776a33ae5b2ad503796a97ae20fdc66f61d2feea`;
adapter SHA-256 `97b1f2d4581332ce679f555834c7ac842c54d585fe22799c702bcb64b3a5018d`.

**Saved observations (frozen v3 evaluation).** Both models completed all 40
frozen cases: 80 requests, 40 matched pairs. Teacher-reached states from 20
development episodes, not independent navigation.

| Metric | Base | Frozen v3 adapter |
| --- | ---: | ---: |
| Exact authorization emitted in a recognized structured field | 8/20 | 14/20 |
| Exact authorization selected for typing | 2/20 | 14/20 |
| Exact mapped runtime type action | 2/20 | 14/20 |
| Navigation agreement with the teacher's recorded action | 8/20 | 13/20 |
| Incorrect runtime typing on authorization states | 3/20 | 6/20 |

The adapter typed on all 20 authorization states (base on five); its six
errors are single-glyph confusions or dropped characters (seeds 102, 105, 109,
116, 121, 138). Fixed mapped actions do not prove persisted form entry.

**Live paired comparison (September 6, 22:00–23:00 UTC).** After
`SolariMachine._ready` was repaired to redial the control channel after every
transport error inside one monotonic deadline, and `lambda_development_eval.py`
became a paired sequencer on one reverted golden machine, all four planned
cells completed: 2/2 matched pairs on development seeds 200 and 201, zero
missing, zero recovery used. Neither model succeeded (0/2 each). The adapter
submitted the seed-200 appeal — correct patient, document, appeal
form, reason and one submitted appeal — but failed verification by transcribing
`AUTH-3614538` for `AUTH-36G14538` from the PDF at 75% viewer zoom; five later
round-trips repeated the same value. On seed 201 it found the correct patient,
rejected the decoy letter, then an in-VM Chrome renderer crash ("Aw, Snap!",
error code 5) logged it out and it issued 67 consecutive waits. Base never got
past the OpenEMR login/menu bar on either seed. Reset equivalence held for
both pairs (identical task fingerprints, 14 checksummed tables, watermarks).

The portable worked example retains this seed-200 episode's 74 recorded steps
and **6/148 referenced screenshots**. It is selected live failure evidence, not
a full visual replay. Missing screenshots are not reconstructed. All observed
checks passing except the value check does not imply global application safety:
checksums cover configured tables and OpenEMR audit evidence is a coarse tripwire.

**Magnification diagnostic (September 7, 01:10–02:15 UTC) — stopped before
scoring.** The plan was to re-present the 20 saved authorization cases to the
frozen adapter with the source PDF at the viewer's default 70% fit versus 150%
set through the viewer's own zoom readout, on states reconstructed by the
controller from recorded provenance. The protocol was frozen
(`runs/magnification-20260906/protocol.md`), the geometry fixed on neutral
seed 107 (measured text row height 7 px → 16 px, authorization line fully in
the pane), and seed 101 prepared. Then Chrome's renderer crashed on OpenEMR
page loads 13 times during controller navigation — login, finder, chart and
Documents alike, typically 17–30 s after a login; a fresh Chrome process
crashed just the same — exceeding the protocol's 12-crash session limit with
1/20 scored seeds prepared. No model request was made, no GPU was launched,
and the hypothesis remains untested (0/0 matched pairs, not zero successes).
No agent, reset-pipeline or product code was changed; the crash handling that
was added lives only in the controller preparation harness and is recorded
per attempt. Historical crash frequency for comparison: 2 of 45 teacher
episodes on 2026-09-04.

Scripts introduced in that historical session: `scripts/magnification_common.py`
(label-free PDF-toolbar detection, text-size measurement, zoom/page/positioning
action sequences), `scripts/magnification_observations.py` (session-owned
golden fork/attach, per-seed reconstruction and captures, exercised end to end
on seeds 107 and 101), `scripts/build_magnification_package.py` and
`scripts/compare_magnification.py` (scaffolding validated offline only: the
builder produced a `verify_dataset`-clean package from the one prepared seed,
the comparator was checked on synthetic rows and rejects pairs whose server
telemetry is absent; neither has processed real model output).

**Resources and closure.** Every session's GPU instance is provider-confirmed
terminated (`9c39b779…`, `bc9308f2…`); the magnification session launched
none. Lambda reservations retained pending invoice: $12.4362 and $11.844 (the
conservative compute estimates are $2.03 and $2.94). Solari cumulative
accounted upper is **$8.308**, as reported by the September 7 magnification
receipt; this is pending reservation accounting, not a compute invoice. It was
below the $10 ceiling and above the former
$8 threshold under explicit authorization. No session-owned Solari machine
remains; the golden snapshot and the two pre-existing Lambda
filesystems are preserved. OpenAI and other paid services: $0 in all three
evaluation sessions. Former instance IPs are historical, not reusable hosts.

**Separately authorized research next step.** Run a short disposable check of OpenEMR page-load stability on
a fresh fork before any further Solari-based work; if the renderer crash rate
is back at the September 4 level, rerun the magnification preparation and then
the GPU scoring step; if not, the renderer-crash problem seen on seed 201 and
throughout the magnification session must be characterised on its own. The
adapter, frozen inputs and all artifacts remain preserved; seeds 100500–100529
remain sealed. Another paid run needs fresh authorization.

## 2. Repository layout

```
projects/forkloop/
  pyproject.toml            package "forkloop", extras: dev, world, teacher, student, train (torch, torchvision, transformers, peft), plots, gym
  README.md · CLAUDE.md · system.md (this file)
  forkloop/                 the library (§4)
    backends/{base,fake,solari}.py
    policies/{base,scripted,teacher,student,action_parse}.py + policies/prompts/ (hosted_gui_agent*.md for the
                              teacher path, fara_no_user_v1.md for base Fara: identity + tools kept via placeholders, no critical points)
    exporters/{jsonl,sft_pairs,osworld}.py
    bench/{reset_benchmark,cost_model}.py + bench/local_baseline/ (docker-compose baseline)
    util/{sql,minipdf}.py
    actions.py types.py tasks.py oracle.py dbaccess.py world.py seed.py observe.py
    reset.py pool.py env.py search.py trajectories.py metrics.py report.py fixed_metrics.py spending.py cli.py
    policies/observation.py · exporters/observations.py  shared v3 multimodal observation contract
  worlds/
    toy_counter/{world.yaml,world.py}
    claims_ops_v1/{world.yaml,world.py,seed_world.py,build.sh,browser_setup.sh}
    claims_ops_v1/portal/     FastAPI app, schema, base data, templates, css
    claims_ops_v1/openemr/    install.sh, shim_schema.sql, base_data, openemr_sql helpers
    claims_ops_v1/tasks/      common.py + one module per family
  train/                    make_sft, train_lora, eval, plot, bakeoff, wilson, box_setup.sh (one-command GPU box setup), README, examples/
  scripts/                  inspect_episode (wrapper over forkloop.report), episode_table, compare_teachers, audit_probe (replay + OpenEMR log dump),
                            chrome_crash_probe, solari_verify_fork (re-check revert/snapshot/disk on a golden fork), gui_episode,
                            student_click_check (one fork, one student click, the four coordinate-space values + a crosshair PNG),
                            classify_failures (a run's failed episodes into the bake-off classes: invalid/parse, wrong-record,
                            transcription, decoy, budget-sane, budget-looping),
                            milestone_staircase (per-run percentage of episodes reaching each UI rung from
                            verdict.details.ui_milestones, plus the trajectory rungs login_page / auth_typed; --png draws
                            several runs side by side, e.g. docs/images/staircase-f3-ladder.png)
  spikes/                   _common.py, spike_00..06, run_all.sh
                            frozen evaluation: build_saved_evaluation, stage_evaluation_package, evaluation_contract,
                            run_inference_only, saved_fixed_eval, compare_saved_evaluation, lambda_serve,
                            lambda_development_eval, lambda_score_development, compare_live_evaluation,
                            gpu_inference_watchdog, start_live_guard, evaluation_watchdog,
                            lambda_provision, live_paired_readiness (see §6.1);
                            magnification diagnostic: magnification_common, magnification_observations,
                            build_magnification_package, compare_magnification (see §6.2)
  tests/                    offline contract/regression tests and portable GPU-environment checks (see §9);
                            conftest.py scrubs FORKLOOP_GOLDEN_* so fake tests cannot use a live golden
  docs/                     worked-example/ (one recorded live episode + its report; the README's entry point), contracts,
                            HANDOFF and later handoffs (research diary), spikes (results ledger incl. the SFT ladder table),
                            solari-repro, solari-message, cost, limitations, buildlog, student-2026-09-05/06 (student ledgers),
                            images/ (charts), lambda-v3-handoff, evaluation-readiness-handoff, frozen-v3-evaluation-results,
                            live-paired-v3-results, document-magnification-results
examples/desktop-snapshot-revert-py/   the cookbook example (outside the project dir)
```

## 3. The two channels

Every interaction with a machine is on one of two channels and the code keeps
them apart by construction:

| | Agent channel | Controller channel |
| --- | --- | --- |
| Who | the policy being evaluated (teacher or student) | forkloop on the researcher's machine |
| In | `Observation`: current and preceding screenshot PNGs, instruction, step index, last-k actions, screen size | DB rows, exec output, health, snapshot ids |
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
  `solari_core/transport.py`). The guest can take the slow restore mode
  (70–160 s) to answer, so this wait has its own window,
  `revert_ready_timeout_s` (240 s; the general `ready_timeout_s` is 90 s),
  and raises `RevertTimeoutError` — a `BackendError` subclass the pool treats
  as "replace this machine", not "revert is unsupported".
- `scroll` is emulated with `Page_Down`/`Page_Up`/arrow keys after a
  `mouse.move`, because the SDK's `mouse.scroll` takes a button code and only
  names left/middle/right.

`PLAN_CAPS` (free 1, starter 2, pro 10) seeds `concurrency_cap`; override
with `FORKLOOP_CONCURRENCY`. `kind="sandbox"` (env `FORKLOOP_SOLARI_KIND`)
creates headless sandboxes through `SandboxClient.create`: the controller
channel is identical, `capabilities` lacks `gui`, and the env skips the
screen stages — this is how the world was built and verified on a Free-plan
key. `attach(id)` re-attaches to a running machine (`build-world --attach`).

Measured behaviour on the account (Starter; history in `docs/spikes.md`):
until 2026-09-02 `revert()` returned 409 `Not revertable` and destroyed a
running machine, and `snapshot()` was refused on any `from_snapshot`
machine, so every run used `fork` mode with `--best-of 1`. Re-verified
2026-09-03 evening after Solari support's fix: `revert()` works (desktop
21.5 s p50 end to end, state restored; a refused revert leaves the machine
alive) and `snapshot()` works on forks (20.8 s). `reset-bench` on the
golden the same night: `revert(golden)` 10/10 on one machine id, full reset
p50 100.9 s; `create(from_snapshot)` 10/10, p50 92.0 s — so revert is the
pool's reset mode (`--pool-mode revert` is the CLI default; fork stays the
fallback). Historical best-of-N runs preceded the v3 isolation repair and do
not validate the current search implementation. Still ignored in those measurements: `disk_gb`
(3.9 GB disk), `cpu`/`mem_mb` on forks (2 vCPU / 4 GB); `recordingUrl` never
populates. Restores are bimodal — ≈ 22 s or 70–160 s (max 353 s) — and the
two modes are identical for revert and fork, so the slow half is the host
restoring the 8.5 GB snapshot, not the client's 429 backoff (the earlier
hypothesis). One `revert(golden)` returned 503 "could not restore this
snapshot in time" and left the machine alive; ~2–5 % of forks die
mid-episode (control channel closed 1000).

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
For exact-count checks configured as `DUPLICATE_SIDE_EFFECT`, a shortfall is
now classified `NOT_DONE`; the check still fails and exact-one acceptance is
unchanged. Excess counts remain duplicates. Historical verdicts are not rewritten.

Kinds:

- `query` / `count`: first column of the first row compared with `equals`
  using `op` (`eq`, `ne`, `in`, `ge`, `le`, `contains`) after light
  normalisation (numeric strings ≡ ints).
- `preserve_fields`: capture selected query rows at reset and require the same rows/fields at verification, excluding only explicit `mutable_fields` and configured bookkeeping columns. Family 1 protects the target appointment beyond its allowed date/time changes; family 2 protects insurance and claim rows beyond requested changes.
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
  a match on the id column, or a `comments` text that contains the audit id
  *or* names both the changed table and the row's primary key, also counts;
  the comments are matched **after base64-decoding** (OpenEMR 8.3 stores
  `log.comments` base64-encoded — measured 2026-09-04 by replaying a false
  `DIRECT_DB_WRITE` episode, `runs/probe-audit-s7`), on the write rows only
  (`event` not `*-select` / `http-request*`). When a change goes unmatched the
  check's details carry `audit_rows_after_watermark` (up to 20 newest audit
  rows per db) so the verdict alone explains a false negative — OpenEMR's `EventAuditLogger::auditSQLEvent` writes `patient_id`
  from the *session's* active chart (0 when an appointment is edited from
  the Finder with no chart open) and stores the statement with its bound
  values in `comments`, which made a correct calendar save score
  `DIRECT_DB_WRITE` (family 1 seed 2, 2026-09-03). The live comment format
  of a scheduling update has not been observed yet.
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
`gui_factory`, and `ui_milestones(dbs, baseline, task)` (2026-09-05: the
staircase rungs read from the audit trails after an episode; the base class
returns None, `Env.verify` stores a non-None answer under
`verdict.details["ui_milestones"]`, never in the reward; the claims-ops
implementation is in §5.2). `ClaimsOpsWorld.before_episode` clears the downloads dir and
runs `ensure_chrome_gpu_flag`: on goldens whose Chrome lacks `--disable-gpu` it
kills Chrome, **waits until the old processes are gone**, removes the profile's
`Singleton*` files, launches with `chrome_base_flags`, **verifies** a
`forkloop-chrome` Chrome is running (one retry) and raises otherwise, so the
reset stage fails instead of starting a browser-less episode (2026-09-04: a
fixed sleep let the new Chrome attach to the dying one and exit with it).

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

`WorkerPool(backend, world, size, mode, golden_snapshot, run_id, ...,
reap_orphans_enabled=True)`. `size` is clamped to the backend's concurrency
cap. Two modes:

- `revert`: long-lived machines; `Worker.restore()` is `revert(golden)` on
  the same id (or a fresh `create(from_snapshot=golden)` if the machine died).
  A revert that times out (`RevertTimeoutError`) or gets a 503
  (`CapacityError`) replaces that one machine with a fresh fork and keeps
  revert mode (`revert_failed_replaced_machine`); so does any other error
  that is not a refusal (a bare `ConnectionError` after the controller Mac
  slept, 2026-09-04). Only a real refusal — `_is_revert_refusal`: a
  `BackendError` saying 409 / "Not revertable" / "needs a running" — switches
  the whole pool to fork mode (`revert_unsupported_fell_back_to_fork`, at
  most once per run); with `fallback_to_fork=False` every error raises.
- `fork`: `restore()` kills the old machine and creates a new one from the
  golden snapshot.

If no golden snapshot is configured, the first worker builds the world
(`world.build`) under a lock and snapshots it; a second worker that raced on
the same miss reverts (or re-forks) to the snapshot the first one built. On
the fake backend the world's `golden_snapshot_env` is ignored (its snapshots
are directories of this process; a Solari id left in the environment made
every offline reset fail with "unknown snapshot" until 2026-09-07). On
Solari this implicit build is refused — you run `forkloop build-world`
explicitly because it takes minutes. `create` retries 429/503/timeouts
(240 s per call) with backoff — capped at 15 s for 429s
(`concurrency_backoff_max_s`), because a 1→60 s doubling turned Solari's
slot-release lag after a kill into 130–240 s restores — and on a 429 it
first re-lists and kills orphans. `reap_orphans` filters on `forkloop=1` and
**this pool's `run_id`**, checks the returned metadata again, and excludes
worker-owned machines; it can clean leaks from an ambiguous create without
reaping another run. Branch pools also set `reap_orphans_enabled=False`.
The CLI `forkloop reap` instead uses the caller's session ledger by default;
account-wide Forkloop cleanup requires explicit `--all-sessions`.
Every event (`create_retry`, `reaped`,
`restored` with seconds, `golden_built`, `revert_failed_replaced_machine`,
`revert_unsupported_fell_back_to_fork` with the error text) is kept in
`events` and echoed to stderr as `[pool HH:MM:SS] …` unless
`FORKLOOP_POOL_LOG=0`, so a collect log can tell a slow restore from a
retried create. Runs through 2026-09-03 used `fork` mode; since the
2026-09-03 night benchmark they run `revert` mode (the v8 family-1 run held
it for all 16 attempts through one 503).

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
- `verify()` is idempotent and fixes up reason codes for truncation. After
  the oracle it asks the world for `ui_milestones(dbs, baseline, task)` and
  stores the answer under `verdict.details["ui_milestones"]` (analysis only,
  never in the reward; the base `World` returns None; errors are recorded,
  not raised). Added 2026-09-05 for the student staircase.
- `checkpoint()`/`restore(cp)` snapshot and revert the machine *and* the
  env's own state (step, charged-action count, history, invalid count, elapsed execution clock and both screenshots) for search. Waits advance observation history without consuming charged actions. `act_with_deadline` bounds policy calls by remaining trajectory time; `step` verifies expiry before applying another action.
- `run_episode(env, policy, seed)` is the plain loop.

### 4.13 `search.py`

`best_of_n(env, policy, n, seed, branch_prob, confidence_threshold,
max_branch_points, mode)`. At an uncertain step: checkpoint, gather `n`
candidates from the explicit policy checkpoint **before** the initial `act`, with a separate post-choice state attached to each candidate, dedupe,
then either

- `revert` mode: for each candidate, `env.restore(cp)`, roll out to the end
  with the child recorder, verify, snapshot the end state; or
- `fork` mode: for each candidate, create a machine from the checkpoint
  snapshot (bounded by `concurrency_cap - 1`) in a private one-worker pool
  with orphan reaping off, attach a sub-env directly to the already-seeded
  state, roll out, then close both the sub-env and its pool (the branch's
  fork must not outlive the branch).

`BranchablePolicy` declares mutable decision fields; branch clones deep-copy those fields and share network clients and monotonic usage counters deliberately. Unsupported policies fail before resource creation. Fork sub-environments inherit the parent's budget overrides, invalid-action limit, stability settings and history. Candidate generation obeys remaining trajectory time. Failed branches finish sibling cleanup before propagating errors. In fork mode the parent VM remains at the branch point: adoption is terminal recorded output, not a live VM transfer. The revised isolation path is fake-backend tested, not revalidated through paid search.

The best verdict (reward, then milestones) wins; the main recorder adopts the
winning branch's steps (copying screenshots) and finishes with its verdict;
in revert mode the machine is reverted to the winner's end snapshot. The
checkpoint and branch-end snapshots are then deleted best-effort (each is a
full disk image on the account); `SearchStats` counts branch points,
branches, wins, snapshots, reverts, forks, `snapshots_deleted` and records
`snapshot_delete_errors`. First real run 2026-09-03: `collect --best-of 2
--search-mode fork` verified 3/3 family-3 seeds through real branch points
(`runs/luna-v5-f3-bo2-smoke`, $0.087 per verified). The checkpoint is taken
before the candidates are deduplicated, so the no-branch path (both candidates
identical) deletes it too — 6/16 leaked silently there on 2026-09-04
(`runs/luna-v10-bo2-hard`, where the other 10 deletes succeeded with no
`snapshot_delete_errors`); that run recovered 2/10 double-failed family-1
seeds at $0.53 each, and every loss had both candidates fail the same check.

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
metrics and `train/`. **Attempts:** `collect --retry-failed N` re-runs a
seed on a fresh reset, so one run may hold several episodes of one task;
each manifest carries `attempt` (via `Env(record_extra=…)`), and
`select_attempts(run_dir)` marks exactly one per `(family, seed)` as
`selected` — the shortest verified, else the last — and the rest
`superseded`. `iter_episode_dirs()` skips superseded attempts unless
`include_superseded=True`, so every reader sees one trajectory per seed.
`Recorder.update_meta()` merges `retry_failed` / `n_attempts` / `attempts`
into `run.json` after every pass; `collect_summary.json` has one row per
seed with its attempt list (contracts §10).

### 4.15 exporters

`export_jsonl` (episode-level, optional steps, optional success filter),
`export_sft_pairs` (one record per valid step of every reward-1.0 episode:
image path, instruction, last-k history, compact target; supports
`limit_episodes` for the 25/50/100/200 checkpoints and excludes held-out
splits by default), `export_osworld` (one OSWorld-style task JSON per task,
expected values omitted unless asked).

### 4.16 `metrics.py`

`wilson(k, n)` and `summarize_run(run_dir)` report success, milestones and invalid actions with Wilson intervals. Secondary failures come from the complete verdict, including collateral/safety checks. Selected-attempt rates are separate from all-attempt token and timing totals.

`accounting.json` records experiment-wide policy usage even on failure and after branch adoption. Usage is cumulative: maximum over repeated step counters, never their sum; accounting includes losing branches and discarded output. Legacy runs without this file may still omit failed calls and are flagged. Model response token usage is authoritative usage; multiplying it by a price is a cost calculation, not an invoice.

Summary VM costs estimate execution plus recorded reset/setup and fork lifetimes. Unknown idle/storage/failed setup costs remain explicitly incomplete. `spending.py` provides the independent SQLite reservation ledger used before every authorized paid request/create: process-safe atomic reservations, service-specific stop/ceiling limits, no refunds for uncertain calls, and persistent resource IDs. OpenAI Luna calls have explicit token caps and zero HTTP retries. Solari creates reserve the full Starter maximum lifetime plus setup, request a 30-minute kill timeout, and retain invoice-pending reservations after confirmed cleanup. September pricing expires October 1. This guard does not authorize other paid paths such as Anthropic or GPU rental.

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
  `nav_macro=True` (`collect --nav-macro`) expands Fara's `visit_url` into
  click(omnibox), key(ctrl+a), type(url), key(Return) over four env steps (one
  model call; `meta["macro"]` marks the queued steps) and `history_back` into
  alt+Left — measured need 2026-09-04: base Fara 1.5 emits `visit_url` on 10 of
  its first 27 steps and the invalid-action limit ends the episode otherwise.
  The same class is the **hosted teacher path**: `--student-url
  https://api.openai.com/v1 --model gpt-5.6-luna` switches on
  `reasoning_effort`, `detail: high` images, a 300 s timeout and 4096 output
  tokens; `--system-prompt-file` replaces the compact prompt with one of
  `policies/prompts/hosted_gui_agent{,_v5,_v6,_v7,_v8,_v9,_v10}.md`, `--history-k
  16` and `--prev-shot` give it the last actions as text and the previous
  screenshot (the env keeps at least that many actions since 2026-09-04; before,
  `collect` left the env at its default of 8 and `--history-k 16` showed 8),
  `--history-notes` puts the model's own reasoning line next to each previous
  action — optional additional memory across turns alongside paired screenshots and compact actions
  (`note_from_reply`; measured need in `docs/spikes.md` 2026-09-04) —, `--instruction-note`
  appends a policy-side text to every instruction the model sees (the world and
  the manifests are untouched; used by the 2026-09-05 login probes,
  `docs/student-2026-09-06.md`), a `--system-prompt-file` may keep Fara's trained
  identity and `computer_use` schema through the `{fara_identity}` / `{fara_tools}`
  placeholders (`prompts/fara_no_user_v1.md`: the critical-points text replaced
  by a no-user rule, the v5 world conventions appended), every one of these knobs
  is recorded in `run.json` under `policy_options`; the module-level helpers `fara_allowed_actions(nav_macro)` and
  `format_prompt_override(text, coord_size, allowed)` do the tool-enum and placeholder work so `train/train_lora.py`
  renders system text at training time. That earlier single-screen comparison did not prove full input parity. The v3 repair shares `policies/observation.py`: task/history text, then labeled previous/current images; step zero has only current. Historical pointer coordinates are converted from desktop pixels to the configured model space. Rendering is pure; `observe()` advances on every action including queued macro actions. Complete message/image tests and the real cached HF processor now verify the inference prefix, all images and target masking, and a history-based loop warning (three near-identical pointer
  actions, three waits, an alternating pair, or five consecutive scrolls in
  one direction whatever their coordinates) appends a "do not repeat" line. The model's reasoning precedes the action inside `raw_action`
  (`policy_note` stays empty), which is what `scripts/inspect_episode.py`
  prints. Luna v5 is the volume teacher (family 3: 94/100 at $0.061 per
  verified); v7 carries the family-1/2 rules learned on 2026-09-03 (family 1
  7/10, family-2 two-system seeds 6/6); v8 adds a fixed CURRENT-date rule
  and `done()` right after the provider-warning OK (family 1 7/10 again,
  shorter episodes, every failure a one-week drift after the blank
  "Available Appointments Calendar" modal); v9 names that modal and drops
  v8's dashboard-first hint — not yet run.
- `action_parse.py`: parsers for compact text, JSON (fenced or not) and Fara
  `<tool_call>` blocks, `scale_coords`, `parse_tool_calls`; never raise.

### 4.18 bench

- `reset_benchmark.py`: runs `ResetController.reset` N times per method
  (`revert`, `fork`, `cold` = create + full world build), appends JSONL with
  stage timings, and summarises p50/p95/p99, failure rate, restore-stage p50,
  cost per 1k resets (from `cost_model`) and a "state restored" column.
  `--no-fallback` makes a refused revert a failed trial instead of a silent
  switch of the pool to fork mode, and the revert pool is warmed first so
  every trial is a revert rather than the initial fork. The fake backend is
  allowed with an explicit warning that the numbers are not Solari numbers.
- `cost_model.py`: verified Solari prices (Sept 2026), `vm_hour_cost`,
  `cost_per_1k_resets`, `episode_cost`, `snapshot_storage_cost` (first
  10 GB free, then $0.05 per GB-month, from the pricing page 2026-09-04),
  `budget_table`.
- `local_baseline/`: docker-compose (OpenEMR `8.3.0-2026-08-30`, MariaDB
  10.11, the portal), `snapshot.sh` / `restore.sh` / `bench_local.sh` that
  restore the *same* state (DB dump, uploads, documents, browser profile) and
  time it to "both apps healthy", for a fair Chart 2 bar.

### 4.19 util

`sql.py` (`quote`, `substitute`, `ident`), `minipdf.py` (a dependency-free
PDF writer used for the synthetic authorization letters Chrome renders in
OpenEMR).

### 4.20 `cli.py`

`forkloop worlds | task | build-world | run | collect | export | metrics | report |
ledger | reset-bench | reap | demo | doctor | compare | compare-report`. `--backend fake|solari` (env `FORKLOOP_BACKEND`),
`--policy scripted|random|teacher|student`, `--best-of N --search-mode
revert|fork`, `--seeds 0-99,200`, `--concurrency`, `--pool-mode
revert|fork`, `--max-steps/--max-seconds` (recorded as `budget_override` in
`run.json`), `--reset-retries N` (re-queue a seed whose reset failed, after
`--reset-retry-wait-s`), `--retry-failed N` (after the pass, re-run every
seed below 1.0 up to N more times through the selected reset mode; §4.14), and the student
knobs `--student-url --system-prompt-file --history-k --history-notes --prev-shot
--image-detail --effort --nav-macro --instruction-note` (`--nav-macro` expands Fara's
`visit_url` / `history_back`; `--instruction-note` appends a policy-side text to every
instruction the model sees, §4.17). All of them are written to `run.json` under
`policy_options` (2026-09-05), since the world and the manifests do not change with
them. `_policy()` takes `family/seed/attempt` context so
tests can swap in attempt-aware policies. `reap --dry-run` lists only the
selected session's machines, unless `--all-sessions` is explicit. `reset-bench` hands the benchmark its own argv
(`argparse.REMAINDER` used to swallow the leading `--world`). `report PATH`
(§4.21) explains a recorded run or episode; add `--format html --out FILE`
to export HTML (default text unchanged, existing run filters preserved).
`ledger PATH --create --solari-usd N --openai-usd M` creates a session
reservation ledger (§4.16) for Solari creates and guarded OpenAI calls,
and prints its per-service summary
(without `--create` it only prints). `build-world` requests a 30-minute
kill-on-idle window, not a hard lifetime. A new golden build completed in the
prior September 15 session. Recovery found two later evaluation machines still
reported running after about ten hours; both were killed, and the invalid
reservation bound now blocks further Solari reservations in that ledger.

The student spending guard recognizes `api.openai.com` (including its absolute
DNS spelling with a trailing dot) and only `gpt-5.6-luna`; arbitrary compatible
endpoints, Anthropic and GPU rental are not covered. `compare` accepts trusted
custom Python factories through versioned config; direct `Env`/`run_episode`
integration remains available. See contracts §14–15 for comparison and recovery.
An empty `--policy scripted` run on fake claims-ops returns `NOT_DONE` and exit 1.

### 4.21 `report.py`

`episode_report(ep_dir, turns)` and `run_report(run_dir, all_attempts)` render
what a run left on disk — `run.json`, `manifest.json`, `verdict.json`,
`steps.jsonl`, `reset.json`, `accounting.json`, `baseline-digest.json`,
`shots/` — into text. `report_html.html_report` uses the same loader and
check explanations for static HTML; nothing is recomputed against a machine. The
report identifies backend, retained evidence and recording timestamps. Live
artifacts, fake simulations and labeled `constructed_control` scenarios must
not be conflated: reward 1 means the recorded checks passed on that backend,
not necessarily a policy navigating real applications. An episode report prints the
instruction, the manifest's controller-only `expected` block (for the reader;
it was never sent to the policy), every effect and invariant check as
`ok`/`FAIL` with expected/actual or the structural evidence (rows outside the
allow-list, unaudited rows with the newest audit rows, forbidden pages,
preserved-field before/after), a `side effects` line, the UI-milestone rungs,
the `type` steps whose text matches a value the oracle compared (with their
screenshot paths, or `(not preserved)`), the `done` step, reset stage timings,
token totals and the last N raw model turns. New evaluations classify a
`DUPLICATE_SIDE_EFFECT` exact-count shortfall as `NOT_DONE`; the report annotates
both new and legacy shortfall verdicts as "not a duplicate".
`side_effect_failures()` is the rule the run table's "side-effect failures"
column uses (`COLLATERAL_EDIT`, `DIRECT_DB_WRITE`, `FORBIDDEN_SCREEN`,
`WRONG_RECORD`, and `DUPLICATE_SIDE_EFFECT` only when the count exceeds the
requirement). A run report is one row per selected attempt plus success with
its Wilson interval and the reason-code histogram; `--failed` / `--all` append
episode reports. Both shapes of verdict (with and without per-check
`reason_code` and `ui_milestones`) render. `docs/worked-example/` is a copy of
one live episode with its report and interpretation; `scripts/inspect_episode.py`
is now a wrapper over this module.

HTML embeds decoded/re-encoded PNGs only from referenced paths below the
episode's `shots/` directory, rejecting traversal, symlinks and invalid images.
Text is escaped, selected identity fields are exposed, infrastructure references
are redacted and PNG metadata is stripped. The file has no scripts, external
assets or analytics. Native disclosure controls work with a keyboard.
HTML exports can use `--crop-top PIXELS` to omit top image rows from sharing copies,
with the crop disclosed in the report and each frame caption. Source PNGs remain
unchanged. The worked example uses 114 pixels to remove browser chrome containing
a session token. This is an explicit reviewed crop, not automatic redaction of
arbitrary screenshot contents.
Unknown origin/date/identity and missing frames/checks are shown as unavailable.
Run-report missing rewards are separate from measured failures and excluded
from the recorded-outcome denominator. Owners must still inspect free text
and screenshot pixels before sharing other recordings; this is not universal
secret detection or new evidence of live execution.

`metrics.summarize_run` tolerates a `run.json` whose `session_ledger` path does
not exist on this machine (copied run directories) and records
`session_spend: {"unavailable": path}` instead of raising.

`forkloop demo --out runs/offline-controls` produces
separate normal Recorder runs for known offline scenarios, marked `backend=fake`,
`evidence_kind=constructed_control` and an explanatory `evidence_note`.
The legitimate-success reference exercises the portal/controller and verifier,
not independent visual navigation; negative and missing-verdict cases show how
failure and absent outcome evidence differ. The README describes the recurring matched-policy
comparison job and a prospective first-user feedback session; neither is adoption
evidence. Interest, assisted use, independent use, repeat use and a changed research
decision require separate observations, none claimed here.

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
aligned ids; its OpenEMR insurance row carries the patient's sex and
address as subscriber fields because OpenEMR 8.3's insurance editor refuses
to save a policy without them — added 2026-09-03 without changing the RNG
draw order, so every existing seed's instruction, expected values and
patient row are byte-identical), `Claim`, near-miss member-id generation, inbox noise, and the
authorization-letter PDF builder (real number on a chosen page, decoy
numbers around it). Each family module's `generate(family, seed, split)`
returns a `TaskInstance` with seeding SQL for both databases, files for
OpenEMR documents, controller-only `expected`, and an oracle spec using the
shared reason-code vocabulary. Family 1's instruction says "the next
<weekday> <half> after its current date (the appointment is within the next
two weeks)" so the target is anchored to the appointment, not to today;
family 2 has a portal-only variant ("OpenEMR already reflects this") and a
two-system variant. Measured on 2026-09-03: family 3 94/100 seeds, family 1
3/10 and family 2 4/10 (portal-only 4/4, two-system 0/6) with the v6 prompt
before the v7 and seeding fixes. `seed_world.py` dispatches by family and
builds the held-out compositions (seeds ≥ 200000) that chain families 2 and
3 on one patient with a third denial that must be left alone.
`ui_milestones(dbs, baseline, task)` (2026-09-05) reads the two audit trails after
an episode and returns the staircase rungs, in order: `openemr_login` (a `log`
row `event LIKE 'login%'` with `success` 1 after the watermark; failed logins
counted in the evidence), `openemr_chart` (a `log` row keyed by the target
patient, or an audited request path under `patient_file`), `openemr_document`
(an `http-request` row whose base64-decoded path is a real document
`controller.php?document&retrieve/view` with a document id — not the
`/Documentation/` help pages nor the dashboard's patient-picture fetch, which the
first detector counted on the 2026-09-05 9B run; the Documents list page is
`evidence.openemr_documents_list`),
`portal_claim` / `portal_appeal_form` (`page_views` paths of the target claim),
`appeal_submitted` (an `appeals` row for the claim). `Env.verify` stores it under
`verdict.details["ui_milestones"]`; `scripts/milestone_staircase.py` aggregates a
run (plus the trajectory rungs `login_page`, the task's username typed, and
`auth_typed`, the expected authorization number typed anywhere).

`resolve_denial_easy` (2026-09-04) is family 3 with the generator flag taken
from the family name: authorization number on page 1 of a one-page letter, no
distractor claims, `difficulty.variant == "easy"`; it seeds its RNG from the
base family name so patient, claim, number, decoys and document count are
those of `resolve_denial` for the same seed. It is the diagnostic rung of the
student bake-off, listed in `world.yaml` so `World.generate` accepts it, and
excluded from nothing automatically — pass `--families` explicitly. Measured
2026-09-04 (`docs/student-2026-09-05.md`): base `microsoft/Fara1.5-4B` scores
0/30 on family 3 seeds 200–229 as shipped (invalid-action rate 20.8 %, all
`visit_url`), 0/30 with `--nav-macro` (invalid 0 %; every episode stops at the
OpenEMR login), and 0/30 on `resolve_denial_easy`.

Per-episode ids live in a block of 1000 starting at `500000 + seed*1000`, so
episodes never collide with each other or with the base data (100001+).

## 6. Training ladder (`train/`) and frozen evaluation

`make_sft.py` turns verified episodes (`reward == 1.0`, selected attempts only) into one record per valid step,
sorted by `task_id` so `--limit 25/50/94` are nested prefixes. Since 2026-09-05/06 it also has
`--rerender-instructions <world>` (the instruction comes from the world's current generator for the same seed, the
`task_id` must match; 0 of 115 family-3 instructions differed, the 09-05 wording change was the policy-side note),
`--exclude-seeds '200-229,200000+'` (drops the held-out evaluation seeds whatever the split and refuses to write a
file that leaks one; the stats file records `episodes_filtered_seed`), and `--with-reasoning`, which tags the file
`recipe: v2-reasoning` and stores the teacher's reasoning line from `raw_action` (everything before the trailing
compact action line, untruncated) in each record; without it the recipe is `v1-actions-only`.

The v3 exporters reject missing, out-of-episode or noncontiguous screenshots and preserve the original source episodes. Records carry `schema_version: forkloop.observation.v3`, `image_roles`, `image_steps`, `screen_size`, and `history_coordinate_space: screen`. `recipe: v2-reasoning` denotes the unchanged **target** format, not the input schema. `data/sft_f3_25_v3.provenance.json` hashes the dataset, every source file and every image. All 25 first authorization-entry examples have the exact value visible in the preceding source screenshot, confirmed by local OCR; none has it in the current screenshot.

`train_lora.py` (transformers + peft LoRA r=16 on every attention/MLP projection of the language model; Qwen3.5-based
VLMs including Fara 1.5; imports without torch) renders each record as the chat the student is **served** with:
`--prompt-style fara --coord-space norm1000` rescales targets and history into Fara's 1000×1000 space, and
`--system-prompt-file`, `--instruction-note`, `--nav-macro` reproduce `StudentPolicy`'s system prompt (placeholders,
tool enum with `visit_url`) and the note appended to the instruction. The loader processes every image. The collator tokenizes the exact inference prefix separately from its assistant continuation, because joint tokenization can merge a boundary newline and change the final prompt token. Labels mask every prompt token; the assistant
turn is the tool call (v1) or the reasoning line then the tool call (v2), rendered by the Fara chat template as
`<think>\n\n</think>\n\n` + content, so the served model, whose generation prompt ends in `<think>\n`, learns to close
the think block and reply the way base Fara replies (prose, then `<tool_call>`; the parser handled 2,432 such
replies with 0 invalid actions). `--smoke` defaults to 4 examples / 2 steps but honours explicit `--limit` /
`--max-steps`, prints the rendered prompt tail, the decoded label tokens and per-example token counts (a 1280×720
screenshot is 880 image tokens; the fair prompt is ≈ 3.2–3.3k tokens per example; targets average 35 tokens in v1
and 60 in v2). The collate pads per-token extras (transformers 5's `mm_token_type_ids`) instead of concatenating
them, which is what made batch sizes above 1 crash. `train_summary.json` records every hyperparameter, the loss
curve, token statistics and peak VRAM; `train_log.jsonl` has one line per `--log-steps`.

**Historical single-image** throughput (`docs/spikes.md`, 2026-09-05/06): forward+backward with gradient checkpointing is
6.9 s per example on an RTX A6000 (19–21 GB at batch 1, 30–34 GB at batch 2) and 3.0 s on an H100 80 GB
(59 GB at batch 4); without checkpointing a single 3.3k-token example needs 74 GB. Attention is `sdpa` on both the
text and the vision tower (verified; flash-attn 2 is not installed, `eager` is 1.5× slower). A 25-episode rung
(1,758 records, 2 epochs, effective batch 8) is 440 optimiser steps: 5.5 h on the A6000, 2.3 h reported on the H100 (the separate rounded 3 s/example figure implies 2.93 h; neither predicts paired-image throughput).

**Measured paired-image v3 training:** the main H100 run completed 411/440 steps
and 1.869169 epochs in 22,852.34 seconds including final save (380.87 minutes),
with 55.60 seconds per optimizer step and 25.303 GB peak PyTorch allocation.
The original 380-minute wall cap was checked at optimizer boundaries. The
remaining 29 steps were not run; no result assumes they would fix the errors.
See the training handoff and its preserved `train_summary.json` for loss,
gradient, checkpoint, and environment receipts.

`train/box_setup.sh <commit>` sets a fresh Lambda-style box up in one command: python3.11 from apt (the image ships
3.10), the repo cloned on the local disk (tolerating run directories rsynced in first), a `venv` for training and a
separate `venv-vllm` (vLLM pins its own torch; its JIT needs `ninja` on PATH), `HF_HOME` on the persistent NFS mount
and a `~/forkloop-env.sh` to source. The runs referenced by the SFT records (≈ 1.9 GB of PNGs for the two family-3
teacher runs) travel with the repo by rsync; historical jobs rewrote paths with `sed`. For v3, relocate only JSON `images` fields with the handoff command and preserve the original provenance and hash; do not rewrite instructions or targets.

`eval.py` (held-out episodes through the real `Env`, N sampling seeds, Wilson CIs, optional best-of-N,
`eval_summary.json`) exists. The historical v1/v2 ladder rungs were evaluated with `forkloop collect` and the exact fair
flags of the base run (`--nav-macro`, `fara_no_user_v1.md`, the credentials note, 120 steps / 900 s, greedy,
retries off) so every row shares one serving stack: the checkpoint's merged model under vLLM on the GPU box,
reached from the Mac through an SSH tunnel (`--student-url http://127.0.0.1:8011/v1`, ≈ 0.8 s per call). Results
live in `docs/spikes.md` ("SFT ladder" table) and `docs/buildlog.md`: base 0/30, ckpt-25 v1 (actions-only, the
ablation row) 0/30 with the staircase flipped (0/30 logins, 19/30 appeals filed with an invented number), ckpt-25 v2
(reasoning targets) 0/30 with the OpenEMR path back (13/30 logins, 9/30 documents) but `auth_typed` still 0/30 and
13/30 invented numbers, 12 of them memorised training numbers. `plot.py` (`chart1` learning curve, `chart2` reset
benchmark, `--demo` synthetic placeholders), `bakeoff.py` (base success, action-format validity, tokens/step, VRAM,
LoRA smoke → markdown table), `wilson.py`, `README.md` (rungs 1, 2, 2.5, 3 with commands and GPU guidance).

### 6.1 Frozen v3 evaluation path

`build_saved_evaluation.py` constructs a deterministic, provenance-checked set
of teacher-reached observations. The executed package is
`runs/evaluation-readiness-20260906/saved-dev-v3`: 20 authorization and 20
navigation cases, with original adjacent screenshots and controller-only
labels. `stage_evaluation_package.py` packages source, frozen adapter, cases,
images and dependency receipts with a payload manifest. The final archive is
`runs/evaluation-readiness-20260906/inference-only-final.tar.gz`; earlier package
revisions are superseded. Input cases SHA-256:
`7b881466820d672bb6d1dd3d51a73c6af2693ff488c5bb79584fde43050ed1b9`.

`evaluation_contract.py` supplies frozen identities, dataset verification and
the audited policy. `run_inference_only.py` verifies the complete payload and
nine critical package versions, starts the pinned base then the same base with
the adapter, and terminates its own serving processes under a stage deadline.
`lambda_serve.py` uses Transformers/PEFT, bf16 and SDPA, binds loopback, serializes
generation and records base/source/adapter identity, request/prompt hashes,
image grids, token counts, raw output, latency and CUDA allocation. This is the
executed v3 serving path; historical vLLM timings are not its measurements.

`saved_fixed_eval.py` makes one greedy request per case. `fixed_metrics.py`
separates exact structured reading, selected typing and mapped runtime typing;
it retains wrong strings and character edits, parse errors, unsupported actions,
ambiguity and truncation. Navigation agreement uses the frozen action/tolerance
rule. `compare_saved_evaluation.py` requires matching input and model-serving
identities before aggregation and retains missing/unmatched cases. The full
40-pair result is in `runs/frozen-v3-eval-20260906/fixed-download/fixed-results/`.

On the Mac, `gpu_inference_watchdog.py` binds one explicitly authorized Lambda
instance and a lifetime-inclusive reservation/deadline to provider termination.
`start_live_guard.py` includes prior pending Solari ledgers and launches
`evaluation_watchdog.py`, which cleans only resources tagged to the new ledger.
`lambda_development_eval.py` requires a fresh ledger-bound heartbeat and matching
server identity at startup, checks remaining time before each seed, and reserves
the full operation cost through the backend before each allocation. It implements seeds
200–202, best-of-one, bounded model/action/time budgets, and stops a variant on
technical failure. `lambda_score_development.py` separates persisted authorization
from exact text typing and inspects actual safety assertions rather than treating
zero appeals as duplicate side effects. `compare_live_evaluation.py` requires
matching task/reset semantics for completed pairs.

The first live attempt stopped after base seed 201 failed readiness
(`runs/frozen-v3-eval-20260906/live-comparison-incomplete.json`). The repaired
path then completed the paired comparison: `SolariMachine._ready` redials the
control channel after every transport error inside one monotonic deadline,
`refresh_lifetime()` re-arms the 30-minute kill window before every cell,
`lambda_development_eval.py --plan trained:200,base:200,base:201,trained:201`
runs one `WorkerPool(mode='revert', fallback_to_fork=False)` machine reverted to
golden before each episode with a fresh `Env` and policy per cell, and
`compare_live_evaluation.py` checks task fingerprints and reset equivalence.
`lambda_provision.py` launches exactly one Lambda instance with a persisted
request, inventory reconciliation by unique name and a bounded capacity wait;
`live_paired_readiness.py` is the no-model revert-cycle diagnostic. Evidence:
`runs/live-paired-v3-20260906{,-trained,-base}`.

### 6.2 Document-magnification diagnostic (prepared, not scored)

`magnification_common.py` holds label-free screen geometry: the PDF viewer
toolbar band detector (60,60,60 band ≥40 rows with the readout box at x=720),
a text row-height measurement inside the PDF canvas, and the action sequences
for zoom-by-readout, page-by-field and fixed inner positioning.
`magnification_observations.py` reconstructs each saved authorization case on a
session-owned golden fork (or attaches to one by id), navigating from recorded
provenance only — pid, claim number, document name/page/hash, with the expected
value stripped before the harness runs — and captures the document at the
default fit zoom, at the magnified zoom, and the appeal form with the field
focused; every controller action, screenshot hash, DB identity check and
renderer crash is recorded. It has a calibration gate (one neutral seed,
operator decision file) and bounded, recorded controller-only retries.
`build_magnification_package.py` writes a `verify_dataset`-compatible package
with two cases per seed (case id = sha256 of episode:step:magnification-v1:
condition) and symmetric exclusions; `compare_magnification.py` pairs by seed,
requires identical text input, form image, model identity and server telemetry
(missing telemetry is a validity failure), and reports the frozen metrics with
corrected/broken/unchanged counts and the live-follow-up gate. The last two are
validated offline only. Protocol and evidence: `runs/magnification-20260906/`.

## 7. Spikes (`spikes/`)

Six standalone scripts answering the plan's day-1 questions against real
Solari: revert latency (20×, health + stable screenshot), fork independence,
`record=True` with `from_snapshot`, memory/process/window/stream survival
across revert, screenshot→click→screenshot latency, MariaDB consistency after
snapshot with and without a read lock. Each prints a table and appends a
JSON line to `spikes/results.jsonl`; `run_all.sh` runs them in order;
`docs/spikes.md` holds dated measurements and decision rules, including earlier
Free-plan failures and later Starter re-verification; it is not an empty plan.

## 8. Cookbook example

`examples/desktop-snapshot-revert-py/` — a self-contained cookbook-style
program: create a desktop via `create_desktop`, type into mousepad, snapshot,
type more, revert (timed) and screenshot, fork with `from_snapshot`, prove
independence, kill both. Comments sit on the lines where the gotchas bite.

## 9. Tests

The table below is the historical 211-test inventory, not a current full-suite
count. Additional v3 tests cover observation/processor parity, isolation,
reward preservation, spending, training progress, readiness deadlines and
frozen evaluation. `tests/conftest.py` deletes `FORKLOOP_GOLDEN_*` from the
environment so the fake backend never sees a real snapshot id.

The frozen inference session ran the required portable subset on the recorded
H100 environment: **17 passed**, covering `test_fixed_metrics.py`,
`test_saved_evaluation.py`, `test_solari_readiness_deadline.py`,
`test_evaluation_live_guards.py` and `test_lambda_serving_serialization.py`.
The last check, previously skipped on the Mac for missing Torch, passed there.
Two provider-watchdog tests passed on the Mac. No new full-suite count or
complete-workflow success is inferred from these checks.

| File | Covers |
| --- | --- |
| `test_portal.py` (22) | schema/base counts, login, filters, appeal with upload + sha256 + same-transaction audit (trigger-based proof), duplicate appeal allowed, resubmit, messages, eligibility, page_views, `/admin`, determinism |
| `test_openemr_layer.py` (15) | shim loads, base data deterministic and executes, every SQL helper executes on the shim, `quote`, portability |
| `test_core_toy.py` (23) | action parsing, registry, full episode + recorder + exporters + metrics, collateral and direct-DB verdicts, budget/invalid truncation, revert restores state, fork mode, best-of-N adoption, random policy, Wilson, orphan reaping (and that a branch pool never reaps its parent's machine), fork search leaves the main worker alive, a slow revert replaces the machine but keeps revert mode |
| `test_claims_ops_world.py` (15) | generator determinism and split disjointness, manifest round-trip, the `resolve_denial_easy` variant (page 1, no distractors, same patient/claim/number per seed, deterministic), resolve_denial success and rejections (wrong number, duplicate, wrong claim, direct write, forbidden screen), update_insurance both-systems logic, reschedule oracle incl. provider change, an OpenEMR-style audit row logged under patient 0 (accepted, plain and base64-encoded) vs one naming neither table nor pk (caught, with the audit rows kept in the verdict), the Chrome relaunch waits/verifies/raises, insurance rows carry subscriber sex/address, concurrent golden build, the UI milestone rungs from seeded audit rows (login success/failure, patient-keyed rows, base64 request paths, portal page views, the appeal) on a verified and on a failed episode without touching the reward, and `scripts/milestone_staircase.py` on a recorded run (with and without the rungs) |
| `test_collect_retry.py` (6) | `collect --retry-failed` on the fake backend: only unverified seeds re-run, retries stop once verified, `--retry-failed 0` is one pass, `select_attempts` prefers the shortest verified and ties to the earliest, exporters/metrics see one attempt per seed while cost counts all, `Recorder.update_meta` |
| `test_student_policy.py`, `test_train.py` (107) | every parser style, coordinate scaling, mocked vLLM transport, the Fara navigation macro (`visit_url` → four queued contract actions with one model call, `history_back` → alt+Left, tool enum advertises both, a new episode drops a half-finished macro) and `fara_call_name_args`, loop warnings incl. same-direction scroll loops, `--instruction-note` (appended policy-side, observation untouched, in `describe()`), a prompt file with `{fara_identity}` / `{fara_tools}` (identity and tool enum kept, critical points gone), make_sft/limits/splits, the reasoning line extracted from `raw_action` and the v2 reasoning-then-action target, Wilson, plots, train_lora import without torch, eval through the real env |
| `test_cost_model.py` (10) | verified prices, VM-hours per credit, monotone reset cost, snapshot storage free tier, budget rows |

## 10. One episode, end to end

1. `forkloop collect --policy teacher --best-of 1` builds a `SolariBackend`, a
   `WorkerPool(mode=revert)` sized to the plan cap, and a `Recorder`.
2. `Env.reset(seed)` → `world.generate(family, seed, split)` (pure) →
   `pool.acquire()` → `ResetController.reset`: restore the golden (first
   allocation forks/builds it; healthy reused workers revert and reconnect;
   fork mode replaces the machine). The completed paired v3 evaluation used
   one reused machine, not fork mode for every cell. Then seed SQL/files,
   `before_episode`, health, baseline hashes/watermarks, initial screen and
   stable-screen capture. Retries repeat this pipeline according to pool mode.
   `reset.json` records every stage; task-state equivalence is checked rather
   than assuming clock pixels or all VM bytes are identical.
3. The teacher receives the instruction and ordered previous/current screenshots
   (current-only at step zero), writes a confidence
   line and one or more tool calls; the policy hands the env one `Action`.
4. `Env.step` applies it on the agent channel, waits, screenshots, records
   the step. With separately enabled search, `best_of_n` can checkpoint, try
   alternatives and adopt the winner. The frozen evaluation used best-of-one;
   revised paid-search behavior is not currently validated.
5. On `done` (or budget), the oracle recomputes hashes, runs the checks, and
   writes `verdict.json` with a reason code. The worker is released; the next
   `reset` reverts the same machine or creates a clean fork according to mode.
6. `forkloop metrics` summarises; `forkloop export --format sft` produces the
   per-step training set; `train/` takes it from there.

## 11. What runs where

| | Offline (this repo's tests) | Solari desktop | GPU box | Anthropic API |
| --- | --- | --- | --- | --- |
| Core loop, oracle, recorder, search | fake backend; v3 isolation/clock/accounting contracts | current frozen run best-of-one; historical best-of-2 fork search 3/3 on 2026-09-03 predates the isolation repair | — | — |
| Portal | in-process (TestClient) | systemd on :8080 | — | — |
| OpenEMR | SQLite shim of 10 tables | **real 8.3.0 on :80, built and verified** (sandbox) | — | — |
| Teacher | not run | drives the desktop | — | computer-use toolset |
| Student | mocked transport and fixed-metric tests | live paired: 2/2 matched pairs on seeds 200/201, 0/2 success for both models; adapter reached submission on 200 with a one-character misread; magnification prep stopped by renderer crashes (§1) | v3: pinned Transformers/PEFT server on H100 PCIe over SSH; all 40 fixed observations per model completed, with results in §1. Historical v1/v2 used vLLM; older Mac probes used mlx-vlm. The evaluation GPU is now terminated. | — |
| LoRA training | import/contract tests; real smoke needs Torch/GPU | — | v3 H100: 411/440 steps, 1.869169 epochs, 380.87 minutes, 25.303 GB peak allocation (§6); adapters and receipts preserved locally. Older v1/v2 throughput is historical. | — |
| Reset benchmark | simulator numbers (labelled) | **revert and fork bars measured on the desktop golden** (n=10 each, p50 100.9 s / 92.0 s, 0 failures; earlier fork-only: sandbox 19.1 s, desktop 25.0 s) | — | — |

## 12. Verified external facts

| Fact | Source |
| --- | --- |
| `SandboxClient.create_desktop(from_snapshot=...)`, `Desktop.snapshot/revert`, `commands.run` argv semantics, `kill` vs `close`, error classes, the post-restore single-connection window | solari-sandbox / solari-core 0.2.0 source (installed from PyPI) |
| Plans: Free $0/1 concurrent, Starter $20/2, Pro $200/10; 2 vCPU/4 GB $0.114/h Starter + $0.02/h screen; snapshot storage first 10 GB free, then $0.05 per GB-month | docs.getsolari.com/pricing (storage line read 2026-09-04) |
| Snapshots keep the machine running; revert keeps the id; from_snapshot makes independent copies; both VMs and sandboxes | docs.getsolari.com/snapshots; re-verified live 2026-09-03 (`docs/spikes.md`): desktop revert 21.5 s p50 with state restored, fork snapshot 20.8 s; revert to the 8.5 GB golden 10/10 on one machine id, restores bimodal (≈ 22 s or 70–160 s) for revert and fork alike, one 503 that left the machine alive |
| OpenEMR's SQL audit log (`EventAuditLogger::auditSQLEvent`) takes `patient_id` from the session's active chart and stores the statement plus quoted bound values in `comments`, **base64-encoded** on this install (row `scheduling-update`, patient_id 0, decoded to the UPDATE with `'507000'` bound — `runs/probe-audit-s7`, 2026-09-04); `add_edit_event.php` itself logs nothing | github.com/openemr/openemr v8_3_0 `src/Common/Logging/EventAuditLogger.php`, `interface/main/calendar/add_edit_event.php` |
| OpenEMR 8.3 insurance editor requires subscriber sex, street, city, state and ZIP (client-side `required`); `state` is a `list_options` list (`state_data_type` 26) with two-letter ids, `sex` is Female/Male/UNK | observed live 2026-09-03 (screenshot in `runs/luna-v6-fam12-s0-9`, `list_options` dump in `runs/logs/audit_probe_f1s2.log`) |
| Templates: base / default / office / code; Image builder | docs.getsolari.com/templates |
| OpenEMR 8.3.0 released 2026-08-18, PHP 8.3+, MariaDB 10.6+; tarball asset and sha256; `InstallerAuto.php` args; `OPENEMR_ENABLE_INSTALLER_AUTO=1` | github.com/openemr/openemr v8_3_0 |
| Docker tag `openemr/openemr:8.3.0-2026-08-30` (no `7.0.3` tag exists) | hub.docker.com |
| Fara 1.5 (4B/9B/27B, Qwen3.5 base, `<tool_call>` format, 1000×1000 coordinate space) | huggingface.co/microsoft/Fara1.5-4B, github.com/microsoft/fara |
| Fara 1.5 4B's 1000×1000 space against the 1280×720 screen is correctly handled by `coord_space=norm1000`, `image_max_side=1280` (the rescaled click sat on the named nav link); the base model emits `visit_url` on ~10 of its first 30 steps whatever the tool enum says, types `admin` and `pass` into one login field, guesses passwords, and stops with `ask_user_question` when unsure | measured live 2026-09-04, `docs/student-2026-09-05.md`, `runs/fara15-4b-base-f3-s200-229*` |
| Told which word is the password (`--instruction-note`), base Fara 1.5 4B does the two-field login (30/30 episodes); without the critical-points prompt text it never calls `ask_user_question`; past the login it navigates OpenEMR by invented URLs and drops the session; OpenEMR 8.3 logs `login` rows with `success` 0/1 and `http-request-update` rows whose base64 `comments` are the request path; Chrome's post-login "Aw, Snap!" (error code 5) persists with `--disable-gpu` | measured live 2026-09-05, `docs/student-2026-09-06.md`, `runs/fara15-4b-fair-f3-s200-229` |
| mlx-vlm 0.6.17 (mlx 0.32.2) loads `microsoft/Fara1.5-4B` (`Qwen3_5ForConditionalGeneration`) from the bf16 safetensors without conversion, needs `jinja2` for the chat template, applies the template's `<think>` default (the model closes it at once; `enable_thinking=false` gives identical output), parses `<tool_call>` into `tool_calls` only when the request carries `tools`, and leaves `<\|im_end\|>` in `content` | measured 2026-09-04 (`runs/logs/mlx-server-fara15-4b.log`) |
| Computer-use toolset `computer_toolset_20260801` GA, member names, batch semantics, result shapes | platform.claude.com computer-use docs |
| Gym-Anything converts software into agent environments; its CUA-World collection motivates realistic long-horizon software tasks. This is related work, not a matched rate comparison to Forkloop. | [Gym-Anything / CUA-World](https://arxiv.org/abs/2604.06126) |
