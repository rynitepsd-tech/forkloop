# The second world: kanboard-v1

A different real application — **Kanboard**, the open-source kanban board — plugged into Forkloop
through nothing but the public world interface (`world.yaml` + a `World` subclass + a pure generator,
[contracts.md](contracts.md) §4–§6), run on the Docker backend, and taken through the real
record → failures → repair → dataset → inspect loop ([correction.md](correction.md)). No file of the
engine, the backend or the oracle was changed for it; the one backend convenience it would like is
proposed below as a diff ("Backend hook").

Everything here was measured on `forkloop-dev` (Lambda, 26 vCPU, Docker 28.3.1) on 2026-09-29 UTC unless
marked otherwise. Raw files are named in each section (paths relative to `projects/forkloop` on that host).

## Quick start

```bash
cd projects/forkloop
# 1. Golden image (≈1 min with the claims-ops-v1 desktop layer already in the BuildKit cache; 1.26 GB)
worlds/kanboard_v1/docker/build_image.sh --version 1          # -> forkloop/kanboard-v1:1
# 2. Point the Docker backend at it (until the world-aware hook below lands)
export FORKLOOP_DOCKER_IMAGE=forkloop/kanboard-v1:1 FORKLOOP_DOCKER_SNAPSHOT_DB=none
# 3. Qualify: timed resets + scripted UI controls (no model calls)
venv/bin/python -m worlds.kanboard_v1.qualify --out runs/kanboard-qualify
# 4. The correction loop (OpenAI: export OPENAI_API_KEY and a session ledger)
forkloop ledger runs/loop-kanboard/session-ledger.sqlite --create --uncapped openai --authorization "..."
export FORKLOOP_SESSION_LEDGER=$PWD/runs/loop-kanboard/session-ledger.sqlite
forkloop record   --config configs/loop-kanboard.yaml --role student --families move_and_assign,due_and_comment \
                  --split train --seeds 1-3 --experiment E
forkloop failures --config configs/loop-kanboard.yaml --experiment E
forkloop repair   --config configs/loop-kanboard.yaml --experiment E
forkloop dataset  --config configs/loop-kanboard.yaml --experiment E --out runs/loop-kanboard/datasets/E
forkloop inspect  --config configs/loop-kanboard.yaml --experiment E --out runs/loop-kanboard/E.html
# re-running repairs of the same failures with another teacher needs a new repair experiment id (the runner
# skips an attempt already repaired in that experiment and mode, whatever the teacher):
forkloop repair   --config configs/loop-kanboard.yaml --experiment E2 --source-experiment E
```

## The application

| | |
| --- | --- |
| Application | Kanboard **v1.2.54** (GitHub release 2026-08-29, the latest on 2026-09-29) |
| Licence | MIT (`github.com/kanboard/kanboard/blob/main/LICENSE`) |
| Source of the code | the official image `kanboard/kanboard:v1.2.54@sha256:8df6c4339134b6c196da9a262daa42ab8ce395f528a48d4a3dd7038c296f34c1` (Docker Hub); only its `/var/www/app` tree is copied, unmodified, plus our `config.php` |
| Runtime | Ubuntu 22.04's PHP 8.1 (Kanboard requires ≥ 8.1.0) under php-fpm, nginx with the official image's server block |
| Storage | SQLite, `/var/www/app/data/db.sqlite`, rollback journal (see "Kanboard facts") |
| Desktop | the claims-ops-v1 desktop: Xvfb `:0` 1280x720x24, XFCE, Google Chrome with the same flags and profile path, user `desktop` |
| Agent channel | the claims-ops-v1 in-container agent (`worlds/claims_ops_v1/docker/agent.py`, copied into the image): screenshots via XGetImage, xdotool input |

**Image recipe** (`worlds/kanboard_v1/docker/`, [Dockerfile](../worlds/kanboard_v1/docker/Dockerfile)).
The `desktop` stage is instruction-for-instruction the claims-ops-v1 one, so BuildKit reuses that layer
on a host that built the claims image (the image does not depend on the claims image itself, which another
agent keeps changing). `install_world.py` runs Kanboard's own migrations (`php cli db:migrate`, schema 128),
then writes the base population, the world's settings, a read-only audit view and a `forkloop_meta` row
holding `base_data.base_sha256()`. `build_image.sh` then boots the base image, logs Chrome in through the
real login form (`bake.sh`, keyboard only), quits Chrome cleanly and commits the golden image. Measured:
build phase 1 57 s with the desktop layer cached (1 s when nothing changed), bake 16–17 s, golden image
1.26 GB, container start to `/run/forkloop/ready` 3.2–3.6 s.

Why nginx + php-fpm and not PHP's built-in server: the first image served Kanboard with `php -S` and 4
workers; 3 of the first 5 resets then timed out in `stable_screen` because the page never finished
loading (tab spinner). The server log showed a worker holding two accepted connections with no request: Chrome's
speculative preconnect sockets block the single-threaded workers. With nginx + php-fpm (and Chrome's
`NetworkPredictionOptions: 2`) 12/12 and then 10/10 resets passed.

## The world package

| File | Role |
| --- | --- |
| [world.yaml](../worlds/kanboard_v1/world.yaml) | the database, checksummed tables, ignored/volatile columns, the audit trail, families, budget |
| [world.py](../worlds/kanboard_v1/world.py) | `KanboardWorld`: health, feasibility, initial screen, analysis-only UI milestones, diagnostics, fake/Docker build |
| [base_data.py](../worlds/kanboard_v1/base_data.py) | the fixed synthetic population (one source for the image build and the generators) |
| [tasks/](../worlds/kanboard_v1/tasks/) | `generate(family, seed, split)` and the two families |
| [kanboard_schema.sql](../worlds/kanboard_v1/kanboard_schema.sql) | Kanboard 1.2.54's real SQLite schema, dumped from the image: the offline test stand-in |
| [qualify.py](../worlds/kanboard_v1/qualify.py) | live qualification: timed resets, scripted UI controls |
| [docker/](../worlds/kanboard_v1/docker/) | Dockerfile, entrypoint, bake, build script, nginx/php-fpm/Kanboard config, Chrome policy |
| [tests/test_kanboard_world.py](../tests/test_kanboard_world.py) | 25 offline tests (generators, base data, oracle on the stand-in) |
| [configs/loop-kanboard.yaml](../configs/loop-kanboard.yaml) | the correction-loop project |

**Base population** (ids ≥ 100000; Kanboard's own `admin` keeps id 1): the world's account
`jordan.ellis` (project manager everywhere; the only account the browser uses), twelve team members in
near-twin pairs (Priya Raman / Priya Rao, Marcus Chen / Marcus Cheng, Dana / Diana Okafor, Luis / Lucia
Ortega, Sam / Sami Whitfield, Elena Petrova / Elena Petrov), four projects (Website Relaunch, Mobile App,
Customer Onboarding, Data Platform) with **the same five columns** (Backlog, Ready, In progress, Review,
Done), five tasks each and a few comments. All names, emails (`@example.test`) and passwords are synthetic.
Settings pinned: UTC, board auto-refresh off (Kanboard polls the board every 10 s by default and
re-renders it under the agent), US date format (Kanboard's default).

## Task families

Seeds: `train` 0–99999, `heldout_seeds` 100000–199999, with disjoint title pools and due-date windows
(2026-10-05..2026-12-18 vs 2027-01-11..2027-03-26). Each episode inserts 4–6 tasks (and, for
`due_and_comment`, two comments) with ids in a per-seed block from 500000, **shuffled**, so a card's
`#id` says nothing about its role. Every `random.Random` is seeded from `"kanboard-v1:{family}:{split}:{seed}"`;
the tests check byte-identical output across calls and in a fresh interpreter with another hash seed.

**`move_and_assign`** — *In the Kanboard project "Customer Onboarding", move the task "Schedule user
interviews" to the "In progress" column and assign it to Luis Ortega.* Distractors: a near-twin title in
the same column ("Schedule user surveys"), the **same title in another project** in the same-named column,
the assignee's near-twin in the assignee list, 1–3 noise tasks; in 40 % of episodes
(`difficulty.start_on_other_project`) the episode opens on the other project's board, where the same-titled
task is the first match on screen. ≈ 4–8 GUI actions (drag the card or use the task's edit form).

**`due_and_comment`** — *In the Kanboard project "Mobile App", set the due date of the task "Verify backup
restore" to 2027-01-27 and add the comment "QA found two regressions; ping the owner tomorrow." to it.*
Distractors: a near-twin title (sometimes with its own due date), the same title in another project (with
its own comment), an existing comment on the target; in 50 % (`difficulty.has_due_date`) the target already
has a due date to replace. ≈ 8–12 GUI actions.

### The oracle (`OracleSpec`)

| Check | Kind | Reason | `move_and_assign` | `due_and_comment` |
| --- | --- | --- | --- | --- |
| effects | query / count | NOT_DONE, WRONG_VALUE, DUPLICATE_SIDE_EFFECT | `moved` (column changed), `assigned` (owner changed), `column` = target, `assignee` = target | `due_changed`, `due_date` = target day (UTC), `one_comment` (exactly one new comment on the target), `comment_text` (trimmed, exact) |
| `target_other_fields` | preserve_fields | COLLATERAL_EDIT | every column of the target except `column_id`, `owner_id` and bookkeeping | every column except `date_due` and bookkeeping |
| `no_comment_elsewhere` | count | WRONG_RECORD | — | no new comment on any other task |
| `untouched_1`, `untouched_2` | preserve_fields | WRONG_RECORD | the near-twin task and the same-titled task in the other project, every column | same |
| `existing_comments_unchanged` | preserve_fields | COLLATERAL_EDIT | every comment that existed at reset | same |
| `no_collateral` | baseline_checksum | COLLATERAL_EDIT | 15 tables, only the target task row may change (a new comment anywhere fails) | same, with `comments` exempt (covered by the three comment checks) |
| `ui_path` | ui_path_only | DIRECT_DB_WRITE | every changed task row, and every row keyed by a task (comments, subtasks, tags, links, files), has a Kanboard activity-stream event for that task after seeding | same |

"Bookkeeping" is `date_modification` and `date_moved`. The audit trail is Kanboard's own
`project_activities` (written only by its event handlers: `task.move.column`, `task.assignee_change`,
`task.update`, `comment.create`, ...), exposed through a read-only view
`forkloop_task_audit(id, entity='task', entity_id=task_id, ...)` created at image build, so the oracle's
existing strict `entity`/`entity_id` join works unchanged (no `loose` matching).

**What it can verify:** the persisted outcome in the 15 checksummed tables (tasks, comments, subtasks, tags,
links, files, projects, columns, swimlanes, memberships, categories, users, automatic actions); that the
right record changed and the look-alikes did not, field by field; exact counts of new comments; that every
change went through Kanboard's application code (the activity event), not SQL.

**What it cannot verify:** which *screens* were visited (Kanboard keeps no request log in its database;
`forbidden_paths` is empty and no `forbidden_screens` check is emitted — nginx's access log exists in the
container for diagnostics only); `position` (card order: Kanboard renumbers every card of both columns on
each move) and `projects.last_modified` are ignored columns, so a pure reordering of cards or columns is
invisible; settings, sessions, notifications, `last_logins` and the daily statistics tables are not
checksummed; the activity join proves *an* event for the task, not that the event wrote exactly those
fields (the preserve checks do that part); the `ui_path` join does not cover project-level rows (a column
rename would be reported as COLLATERAL_EDIT by `no_collateral` first).

**Kanboard facts the oracle depends on** (measured on the running app, 2026-09-29): a drag to another
column updates the card's `column_id`, `position`, `date_moved`, `date_modification`, renumbers the
`position` of the other open cards in both columns, bumps `projects.last_modified` and writes one
`task.move.column` activity and one `transitions` row; saving the edit form with a new assignee changes
only `owner_id` and `date_modification` and writes `task.assignee_change`; saving a due date changes only
`date_due` and `date_modification` (`task.update`) — the base and episode rows are seeded with `time_spent`
and `time_estimated` 0, not NULL, so the form's number normalisation does not rewrite them; adding a comment
writes the row (id = max + 1) and `comment.create`. A date typed without a time keeps the wall-clock time
of the save (`date_due` is volatile for checkpoint digests), and **an ISO date typed over an existing due
date is replaced by the date picker's previous value when the field loses focus** (the typed text must be in
the displayed `m/d/Y H:i` format) — a real trap the student meets. Kanboard's default is SQLite WAL with
`wal_autocheckpoint = 0`; the world sets `DB_WAL_MODE false` and converts the file to a rollback journal
at build, so the whole application state is one file and the controller's `sqlite3` (root) never leaves a
WAL/SHM file the php-fpm workers cannot open (the workers run as root for the same reason).

### Health, feasibility, initial screen

- **health**: SQLite ping, `GET /healthcheck.php` (Kanboard's own, queries the database) = 200, the
  base-population counts, and `forkloop_meta.base_sha256 == base_data.base_sha256()` — an image built from
  other base data than the generators assume fails every reset (unscored) instead of producing wrong tasks.
- **feasibility** (after seeding, before the baseline): target, near-twin and same-title tasks exist with
  the generated titles/projects, the title is unique in the target project, the instruction names them;
  the target column belongs to the project; the assignee is a project member (so the assignee list offers
  them) and the instruction names them; for comments, the comment floor equals the current maximum id.
- **initial screen**: through the agent channel only — click the omnibox, type the board URL; if Kanboard
  shows its login page (a lost session), log in as the world's account and navigate again; the reset fails
  unless the window title is exactly `<project name> - Google Chrome`. The golden image carries a persistent
  session cookie and the matching `sessions` row (`SESSION_DURATION` 10 years) and boots straight into
  "Dashboard for Jordan Ellis" (checked by `build_image.sh` phase 3); the login branch is the fallback.

## Live qualification (no model calls)

`python -m worlds.kanboard_v1.qualify --out runs/kanboard-qualify-20260929 --resets 10`
(`runs/kanboard-qualify-20260929/qualify.json`, episodes with screenshots under `episodes/episodes/`):

| | |
| --- | --- |
| resets (fresh container each) | 10/10 ok, wall median 8.8 s (8.3–9.2): restore 5.7, seed 0.14, health 0.38, feasibility 0.25, baseline 0.36, initial screen 1.5, stable screen 0.53 |
| earlier loop | 12/12 ok, 8.6–9.7 s |
| correct `due_and_comment` UI path (task page → Edit → due date → Save → Add a comment → Save) | reward **1.0**, OK |
| the same actions on the near-twin task | 0.0 NOT_DONE; `untouched_1`, `no_comment_elsewhere` (WRONG_RECORD) and `no_collateral` also failed |
| the right values written by SQL instead | 0.0 **DIRECT_DB_WRITE** (`ui_path` alone failed) |

One boot out of roughly the first twenty timed out because XFCE never mapped its panel (no
`_NET_WORKAREA` after 20 s); the entrypoint now restarts the session once in that case. That reset was
unscored, as designed.

## The correction loop on kanboard-v1 (live, 2026-09-29)

Store `runs/loop-kanboard/forkloop.sqlite` (copied to the Mac under the same path), session ledger
`runs/loop-kanboard/session-ledger.sqlite` (OpenAI uncapped, fully accounted). Project
[configs/loop-kanboard.yaml](../configs/loop-kanboard.yaml): Docker backend, replay checkpoints (every 4
steps, before every `type`, before `Return`), history 12, task budget 40 steps / 900 s, both policies the
built-in `student` policy with `agent_memory_v2.md` and explicit memory.

### Students

The first student was too weak to fail in an interesting way, the second too strong; the third is the
configuration in the file. Every attempt is kept in the store; the policy identity (all options) of each is
recorded there.

| Experiment | Student (gpt-6-luna, reasoning `low`) | Attempts | Verified | Failures by reason | Steps of verified |
| --- | --- | --- | --- | --- | --- |
| `kb-loop-20260929` (pilot) | 1280x720, `image_detail: low` | 6 | 0 | NOT_DONE 6 (all at the 40-step budget) | — |
| `kb-loop2-20260929` | 1280x720, `image_detail: high` | 12 | 11 | WRONG_RECORD 1 | 6–16 |
| `kb-loop3-20260929` (**main**) | downscaled to 640x360, `image_detail: high` | 12 | 6 | NOT_DONE 5, WRONG_RECORD 1 | 7–38 |

Seeds 1–3 (pilot) and 1–6 (the others) of both families, `train` split. 30 attempts, 30 scored, 0 unscored,
0 infrastructure errors. What the failures were (read from the trajectories): with low detail every click
landed in the downscaled image's coordinate frame (`click(152, 112)` for the card at (390, 275): 0.4 × both
coordinates, the 512/1280 downscale), so the pilot never left the start board. The two WRONG_RECORDs are the
designed trap: the episode opened on the *other* project's board (`start_on_other_project`) and the student
edited the same-titled task there. Four of the five NOT_DONEs are `due_and_comment` date-entry loops: in
three the target already had a due date, the student typed the ISO date over it, Kanboard's picker put the
old value back (see "Kanboard facts"), and it kept retyping (ISO, then `20/10/2026`, ...) until the budget
ran out; in the fourth it typed a two-digit year (`12/16/26 04:31`) into the empty field and then kept
appending attempts without selecting the field's text. The fifth is the other-project trap ending in `done()`.

### Failures → restart points → repairs

`forkloop failures` (controller-side) chose, per failed attempt, one evidence-based point plus step 0:
`damage` (the last clean checkpoint before the first checkpoint whose verifier status was `damaged` —
`untouched_2`/`no_collateral` failing after the wrong task was saved), `stall` (repeated actions) or
`latest`. Teacher: gpt-5.6-luna, full resolution, `image_detail: high`, `feedback: none` (it gets only the
instruction, screenshots, the history and the checkpoint's memory).

| Repair experiment | Teacher effort, k | Failed attempt (task) | Restart point: branches | Result |
| --- | --- | --- | --- | --- |
| `kb-loop2-20260929` | medium, 3 | move_and_assign 3 (WRONG_RECORD) | step 4 damage: 3 NOT_DONE · step 0: 2 NOT_DONE, 1 WRONG_RECORD | unrepaired |
| `kb-loop2r-20260929` | high, 2 | same attempt | step 4 damage: 2 NOT_DONE · step 0: 1 NOT_DONE, 1 WRONG_RECORD | unrepaired |
| `kb-loop3-20260929` | high, 2 | move_and_assign 1 (WRONG_RECORD) | step 0 damage: **2 OK** | verified |
| | | move_and_assign 3 (NOT_DONE) | step 4 damage: 1 WRONG_RECORD, 1 NOT_DONE · step 0: 1 NOT_DONE, 1 WRONG_RECORD | unrepaired |
| | | due_and_comment 1 (NOT_DONE) | step 39 latest: 2 NOT_DONE · step 0: **2 OK** | verified |
| | | due_and_comment 3 (NOT_DONE) | **step 4 stall: 2 OK** (replayed 4 steps) | verified |
| | | due_and_comment 5 (NOT_DONE) | step 39 latest: 2 NOT_DONE · step 0: **2 OK** | verified |
| | | due_and_comment 6 (NOT_DONE) | **step 36 latest: 1 OK**, 1 NOT_DONE (replayed 36 steps) | verified |

Totals: 8 repairs, **5 verified** (all in the main experiment: 5 of its 6 failures), 3 unrepaired;
28 branches, all finished and scored (0 restore-failed, 0 infrastructure): 9 OK, 15 NOT_DONE,
4 WRONG_RECORD. **Three verified branches continue a real failed attempt from a mid-episode checkpoint**
restored by replay (step 4 ×2, step 36 ×1); six are restarts from step 0. `branch_max_steps: 30` let a
branch act up to 30 times after its checkpoint (without it a `latest` point at step 39 of a 40-step budget
leaves one action).

What the unrepaired and failed branches show: the other-project trap also beat the teacher — 0/14
branches on move_and_assign seed 3, at both efforts (the step-4 `damage` point restores the wrong task's
edit form with the assignee already chosen; the teacher saved it); the teacher did escape it on seed 1
(2/2 from step 0). From `latest` points deep in the date-entry loops the teacher verified 1 of 6 branches
(0/4 at step 39, where it kept fighting the picker for 30 actions; 1/2 at step 36, where it selected the
field's text, typed the ISO date into the now-invalid field, saved and commented in 9 actions), while
restarts from step 0 of the step-39 tasks verified 4/4 and the step-4 `stall` point 2/2 — the restart-point
heuristic's yield differs by reason, which is why [correction.md](correction.md) reports it per experiment.

### Restore fidelity

29 restores for 28 branches: 13 `reset` (step 0) and 16 `replay` (4, 36 or 39 recorded actions re-executed
through the agent channel, 303 steps in total). **Tables equal in 29/29.** Screen distance 0.0 in 28/29;
one 39-step replay ended on a different screen (distance 0.274, tables equal), was marked failed and retried
(`restore_retries`), and the retry matched (0.0). Restore time median 12.2 s (4-step replays 12–15 s,
36/39-step replays 35–46 s; each includes a full reset, ≈ 9 s).

### Dataset and report

`forkloop dataset --out runs/loop-kanboard/datasets/kanboard-corrections-20260929` →
**`ds-a504e6fca3fb`**: 136 action records from the 9 verified branches (42 `correction_suffix` — the
teacher's steps after a mid-episode checkpoint, with the student's prefix as history — and 94
`restart_demo`), 10 preference pairs (verified vs failed first step at the same checkpoint, or vs the failed
attempt's own step), 8 controller-only diagnostics (one per repair), 98 images; split `train` only. Audit:
`memory_provenance_ok` 136/136, `memory_provenance_bad` 0, `hidden_in_text_input` 0 (this world has no
hidden values: the instruction states the date, comment, column and assignee). Report:
`runs/loop-kanboard/kanboard-loop-20260929.html` (`forkloop inspect`, all 30 attempts, checkpoints, branches).

### Spend

OpenAI, from the session ledger: **$0.6300** actual (1,101 calls, 0 pending), equal to the store's
`model_tokens` charges (3,505,306 tokens): students $0.0625 (pilot) + $0.0599 + $0.0866; teachers $0.0402
(medium) + $0.0286 + $0.3522. Docker on `forkloop-dev` only; no cloud resources were created. Wall time:
attempts 2,752 s, branches 2,629 s, restores 536 s, checkpoints 193 s (store `charges`).


## Backend hook (proposed diff, not applied)

The Docker backend already serves this world unchanged when the environment names the image. To let a
world carry its own image and snapshot settings (so a shell with `FORKLOOP_DOCKER_IMAGE` exported for one
world cannot silently start another world's image — here the health check would catch it, via the missing
`forkloop_meta` row), `DockerBackend.for_world` could read an optional `docker:` block of `world.yaml`
(already present in kanboard-v1's; `WorldConfig` keeps unknown keys in `extra`):

```diff
--- a/projects/forkloop/forkloop/backends/docker.py
+++ b/projects/forkloop/forkloop/backends/docker.py
@@ class DockerBackend:
     @classmethod
     def for_world(cls, world: Any, **kw: Any) -> "DockerBackend":
-        b = cls(**kw)
+        # world.yaml may declare `docker: {image: ..., snapshot_db: flush|stop|none}`; an explicit argument
+        # or the FORKLOOP_DOCKER_* environment still wins.
+        cfg = dict((getattr(getattr(world, "config", None), "extra", None) or {}).get("docker") or {})
+        if cfg.get("image") and not kw.get("image") and not os.environ.get("FORKLOOP_DOCKER_IMAGE"):
+            kw["image"] = cfg["image"]
+        if cfg.get("snapshot_db") and not kw.get("snapshot_db") and not os.environ.get("FORKLOOP_DOCKER_SNAPSHOT_DB"):
+            kw["snapshot_db"] = cfg["snapshot_db"]
+        b = cls(**kw)
```

Two related observations for the backend owner: `snapshot_db: flush` (the default) runs
`mariadb -e 'FLUSH TABLES'`, which fails in an image without MariaDB, so kanboard-v1 needs `none` (a paused
commit of an SQLite rollback-journal database is crash-consistent) — only relevant to the `snapshot`
checkpoint strategy, which this world's loop does not use; and `_running_worlds()` counts every
`forkloop.world_container=1` container on the daemon, so two agents' pools share one concurrency cap
(this work ran with `FORKLOOP_DOCKER_CONCURRENCY=24`); filtering by `forkloop_owner` would separate them.

### Observations for the engine owners (no change made)

- `forkloop repair` bounds branches with the shared semaphore sized by `--concurrency` or the project's
  top-level `concurrency`; `repair.concurrency` in the YAML is only used when `repair_attempt` is called
  without a semaphore, so it had no effect here (the config now says 3, what ran; the recorded
  `kb-loop3-20260929` repair configs say 6).
- `run_repairs` skips an attempt that already has a finished repair in the same experiment and mode, so a
  second teacher needs a new `--experiment` with `--source-experiment` (how `kb-loop2r-20260929` was made).
- A `latest` restart point of an attempt that used its whole step budget leaves the branch no budget unless
  `repair.branch_max_steps` is set.

## Limitations

- Docker only: `build()` refuses Solari (no golden desktop snapshot was built for this world).
- The agent prompt is `agent_memory_v2.md`, written for claims-ops-v1: its OpenEMR/portal hints do not
  apply here and it says nothing about Kanboard. Both student and teacher use it unchanged.
- Screen visits are not verifiable (above); `position` and `last_modified` are ignored.
- Two families, two splits; no held-out compositions and no final-test pool for this world.
- The golden image's Kanboard session and remember-me token are dated by the bake; the session lasts 10
  years, the remember-me token 60 days (`RememberMeSessionModel::EXPIRATION`), and `open_initial_screen` logs in again if both are gone.
