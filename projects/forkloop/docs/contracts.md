# Forkloop contracts

This file is the single source of truth for every interface that crosses a
module boundary. Code that disagrees with this document is wrong; fix the code
or fix this document in the same commit.

Conventions: Python 3.11+, `from __future__ import annotations`, dataclasses
for records, plain dicts on the wire (JSON), snake_case everywhere except
where Solari's wire format forces camelCase.

---

## 1. Two channels

| Channel | Who | Surface | Never carries |
| --- | --- | --- | --- |
| **Agent** | the policy under evaluation | screenshot in, `Action` out (mouse/keyboard only) | shell, files, DB credentials, expected values, task metadata beyond the instruction |
| **Controller** | forkloop on the researcher's machine | `exec`, `files`, `snapshot`, `revert`, `create(from_snapshot)`, DB queries | anything that ends up inside a screenshot the agent sees |

Reward code and expected values live only on the controller. The VM contains
the world, never the answer key.

---

## 2. Backend protocol (`forkloop/backends/base.py`)

A backend owns machines. Both `SolariBackend` and `FakeBackend` implement it.

```python
class Machine(Protocol):
    id: str
    stream_url: str | None              # VNC url on Solari, None on fake
    # --- controller channel ---
    async def exec(self, cmd: str, args: list[str] = (), *, timeout_ms: int | None = None,
                   cwd: str | None = None, env: dict[str, str] | None = None) -> ExecResult
    async def read_file(self, path: str) -> bytes
    async def write_file(self, path: str, data: bytes | str, mode: int | None = None) -> None
    async def snapshot(self, name: str | None = None) -> str            # returns snapshot_id
    async def revert(self, snapshot_id: str) -> None
    async def kill(self) -> None
    # --- agent channel ---
    async def screenshot(self) -> bytes                                # PNG bytes
    async def display_size(self) -> tuple[int, int]
    async def click(self, x: int, y: int, *, button: str = "left") -> None
    async def double_click(self, x: int, y: int) -> None
    async def move(self, x: int, y: int) -> None
    async def scroll(self, x: int, y: int, *, direction: str, amount: int) -> None
    async def drag(self, x1: int, y1: int, x2: int, y2: int) -> None
    async def type_text(self, text: str) -> None
    async def press(self, keys: list[str]) -> None                      # chord, xdotool names

class Backend(Protocol):
    name: str                                                          # "solari" | "fake"
    concurrency_cap: int
    async def create(self, *, template: str | None = None, from_snapshot: str | None = None,
                     resolution: str = "1280x720", cpu: int = 2, mem_mb: int = 4096,
                     record: bool | None = None, metadata: dict[str, str] | None = None,
                     timeout_ms: int = 30 * 60_000) -> Machine
    async def list_snapshots(self) -> list[SnapshotInfo]
    async def delete_snapshot(self, snapshot_id: str) -> None
    async def list_machines(self, *, metadata: dict[str, str] | None = None) -> list[MachineInfo]
    async def kill_machine(self, machine_id: str) -> None
    async def close(self) -> None
```

`ExecResult(exit_code:int, stdout:str, stderr:str)`.
`SnapshotInfo(id, name, parent, size_bytes, created_at, kind, template)`.
`MachineInfo(id, state, metadata, created_at)`.

Solari mapping (verified against solari-sandbox 0.2.0 source):

| Contract | Solari call |
| --- | --- |
| `Backend.create(from_snapshot=...)` | `SandboxClient.create_desktop(template=, from_snapshot=, resolution=, cpu=, mem_mb=, record=, metadata=, timeout_ms=, lifecycle={"onTimeout":"kill"})` — the **unified** `POST /sandboxes` route with `kind:"desktop"`. `DesktopClient.create` does **not** accept `from_snapshot`. |
| `Machine.snapshot` | `Desktop.snapshot(name)` → `POST /sandboxes/:id/snapshots` |
| `Machine.revert` | `Desktop.revert(id)` → `POST /sandboxes/:id/revert` |
| `Machine.exec` | `Desktop.commands.run(cmd, args=...)` (argv, **not** shell-interpreted; use `sh -c` for pipes) |
| `Machine.screenshot` | `Desktop.screenshot(format="png")` |
| `Machine.click` | `Desktop.mouse.click(x, y, button=)` |
| `Machine.type_text` | `Desktop.keyboard.type(text)` |
| `Machine.press` | `Desktop.keyboard.press([...])` |
| `Machine.kill` | `Desktop.kill()` (`close()` only drops the local channel) |
| `Backend.list_snapshots` | `SandboxClient.list_snapshots()` |
| `Backend.list_machines` | `SandboxClient.list_all(metadata=..., kind="desktop")` |

Errors: `solari_core.errors.PlanError` (402), `ConcurrencyLimitError` (429),
`NoCapacityError` (503). The pool retries 429/503 with backoff; 402 is fatal
and surfaces the plan message.

Every machine forkloop creates carries `metadata={"forkloop": "1", "run_id": <run>}`
so `pool.reap_orphans()` can find and kill leftovers.

---

## 3. Action schema (`forkloop/actions.py`)

Normalized coordinate space is the **screenshot pixel space** at the world's
fixed resolution (1280x720 for claims-ops-v1). Policies that see resized
images must map back before emitting.

```json
{"type": "click",        "x": 640, "y": 360, "button": "left"}
{"type": "double_click", "x": 640, "y": 360}
{"type": "right_click",  "x": 640, "y": 360}
{"type": "move",         "x": 640, "y": 360}
{"type": "scroll",       "x": 640, "y": 360, "direction": "down", "amount": 3}
{"type": "drag",         "x": 10,  "y": 10,  "x2": 200, "y2": 200}
{"type": "type",         "text": "hello"}
{"type": "key",          "keys": ["ctrl", "l"]}
{"type": "wait",         "seconds": 1.0}
{"type": "done",         "success": true, "note": "optional"}
```

`Action.parse(obj_or_str)` accepts a dict, a JSON string, or a compact
text form (`click(640, 360)`, `type("hello")`, `key("ctrl+l")`, `scroll(640,360,"down",3)`,
`wait(1)`, `done()`), raising `InvalidAction` with a reason. `Action.to_dict()`
is the canonical JSON. Invalid actions are recorded, counted
(`invalid_action_rate`), and consume a step without touching the machine.

Key names are xdotool names: `Return`, `Tab`, `Escape`, `BackSpace`, `ctrl`,
`alt`, `shift`, `super`, `Page_Down`, `Home`, `End`, arrows `Up/Down/Left/Right`,
letters and digits as-is.

---

## 4. World protocol (`forkloop/world.py`)

A world is a directory `worlds/<name_with_underscores>/` (directory name is a valid Python package name; `name:` inside world.yaml keeps the hyphenated public name) containing `world.yaml`:

```yaml
name: claims-ops-v1
version: 1
resolution: 1280x720
template: default          # Solari template for the golden build
golden_snapshot_env: FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1   # env var holding snapshot id
paths:
  portal_db: /var/lib/forkloop/portal/portal.db
  portal_uploads: /var/lib/forkloop/portal/uploads
  openemr_docs: /var/www/openemr/sites/default/documents
  downloads: /home/user/Downloads
databases:
  portal:  {dialect: sqlite, path: /var/lib/forkloop/portal/portal.db}
  openemr: {dialect: mysql,  database: openemr, user: openemr, password_file: /etc/forkloop/openemr.pw}
apps:
  portal:  {url: "http://localhost:8080", health: "/healthz"}
  openemr: {url: "http://localhost/openemr", health: "/interface/login/login.php?site=default"}
families: [reschedule_constrained, update_insurance_reconcile, resolve_denial, resolve_denial_easy]
seed_module: worlds.claims_ops_v1.seed_world     # python module exposing generate(family, seed, split) -> TaskInstance
budget: {max_steps: 60, max_seconds: 600}
forbidden_paths: ["/admin", "/debug", "/api/"]
```

The world's `seed_world.generate(family: str, seed: int, split: str) -> TaskInstance`
is a **pure function** of `(family, seed, split)`. Same inputs, byte-identical
output. `split` ∈ `train | heldout_seeds | heldout_compositions` and only
changes the value ranges / composition rules, never the format.

---

## 5. TaskInstance / manifest

```python
@dataclass
class TaskInstance:
    world: str
    family: str
    seed: int
    split: str
    task_id: str                     # f"{family}-{split}-{seed:06d}"
    instruction: str                 # the ONLY text the agent sees
    initial_screen: dict             # {"app": "portal", "url": "http://localhost:8080/claims?status=DENIED"}
    seeding: Seeding                 # see below
    expected: dict                   # family-specific, controller-only
    oracle: OracleSpec               # see §6
    budget: dict                     # {"max_steps": 60, "max_seconds": 600}; wait() actions do not count toward max_steps
    difficulty: dict                 # knobs used, for analysis ({"distractors": 3, "require_attachment": false, ...})

@dataclass
class Seeding:
    portal_sql: str                  # executed by sqlite3 on the portal DB, inside a transaction
    openemr_sql: str                 # executed by mysql on the openemr DB (dialect: portable INSERT/UPDATE only)
    files: list[SeedFile]            # written via controller channel before the episode
    post_commands: list[list[str]]   # argv lists run after seeding (e.g. chown, cache clear)
    extra_sql: dict[str, str]        # {db_name: script} for worlds whose databases are not portal/openemr

@dataclass
class SeedFile:
    path: str                        # absolute VM path
    content_b64: str
    mode: int | None = None
```

`manifest.json` = `asdict(TaskInstance)` plus `{"world_version", "generated_at", "forkloop_version"}`.
It is stored on the controller under the episode directory and never written
into the VM.

**SQL portability rule.** `openemr_sql` must run unchanged on MariaDB (the VM)
and SQLite (the local test shim). Allowed: `INSERT INTO t (cols) VALUES (...)`,
`UPDATE t SET ... WHERE ...`, `DELETE FROM t WHERE ...`, string/number literals,
`NULL`. Not allowed: `ON DUPLICATE KEY`, `NOW()`, backticks, `LAST_INSERT_ID()`,
auto-increment reliance (always set explicit primary keys, choose ids ≥ 100000
so they never collide with demo data).

---

## 6. Oracle spec (`forkloop/oracle.py`)

```python
@dataclass
class Check:
    id: str                          # stable, e.g. "claim_status"
    kind: str                        # "query" | "baseline_checksum" | "ui_path_only" | "forbidden_screens" | "count"
    db: str | None = None            # "portal" | "openemr"
    sql: str | None = None           # parameterised with ? (sqlite) — the DbAccess layer rewrites for mysql
    params: list = field(default_factory=list)
    equals: Any = None               # expected scalar (first column of first row) for kind=query/count
    reason_code: str = "CHECK_FAILED"# emitted when this check fails
    allow: dict | None = None        # baseline_checksum only: {"portal.claims": ["C-1042"], "openemr.insurance_data": [100004]}
    exempt_tables: list[str] | None = None   # baseline_checksum: append-only tables ignored (audit_log, log, page_views, appeals, resubmissions)
    # Row hashes leave out `world.yaml` `oracle.ignore_columns` ({db: [column, ...]}): columns the app rewrites by
    # itself when a record is merely displayed (OpenEMR backfills `uuid` on first access). Without this, viewing a
    # patient was a COLLATERAL_EDIT (measured 2026-09-02).

@dataclass
class OracleSpec:
    effects: list[Check]
    invariants: list[Check]

@dataclass
class Verdict:
    reward: float                    # 1.0 iff all effects and all invariants pass, else 0.0
    milestones: float                # fraction of effects passed (analysis only)
    reason_code: str                 # "OK" or the first failed check's reason_code
    failed: list[str]                # ids of every failed check
    details: dict[str, Any]          # per-check {"expected", "actual", "passed"}
```

Evaluation order: effects in list order, then invariants in list order. The
first failure sets `reason_code`; all checks still run so `failed` is complete.

Standard reason codes (world generators must use these when they apply):
`WRONG_RECORD`, `NOT_DONE`, `WRONG_VALUE`, `DUPLICATE_SIDE_EFFECT`,
`COLLATERAL_EDIT`, `DIRECT_DB_WRITE`, `FORBIDDEN_SCREEN`, `MISSING_ATTACHMENT`,
`WRONG_ATTACHMENT`, `PROVIDER_CHANGED`, `WRONG_SLOT`, `BUDGET_EXCEEDED`,
`INVALID_ACTION_LIMIT`.

**Baseline checksum.** After seeding, the controller computes for every table
in both DBs `md5(row)` per primary key (`SELECT pk, <all cols> ORDER BY pk`).
After the episode it recomputes. A row counts as *changed* if its hash differs
or it was deleted; *added* rows in non-exempt tables also count. Changes whose
`table.pk` appears in `allow` are fine; anything else fails with
`COLLATERAL_EDIT`.

**ui_path_only.** For every non-exempt row change detected above, there must be
an `audit_log` row (portal) or `log` row (OpenEMR) for that entity written
after seeding. Missing audit → `DIRECT_DB_WRITE`. (Inside the VM only the UI
can write, so this is a tripwire, not the only defence.) OpenEMR's `log` is
matched loosely (`audit.loose`): the row's patient id, or a `comments` SQL that
names the changed table and primary key — matched after base64-decoding, because
OpenEMR 8.3 stores `log.comments` base64-encoded (measured 2026-09-04), on the
write rows only (`event` not `*-select` / `http-request*`). When a change goes unmatched the
check's `details` also carry `audit_rows_after_watermark` — up to 20 of the
newest audit rows per database (`pk`, `entity`, `entity_id`, first 300 chars of
`comments`) — so a false `DIRECT_DB_WRITE` can be diagnosed after the VM is gone.

**forbidden_screens.** Portal records every request path in `page_views`;
any row after seeding whose path starts with an entry in `world.forbidden_paths`
→ `FORBIDDEN_SCREEN`.

---

## 7. Payer portal (`worlds/claims_ops_v1/portal/`)

FastAPI + SQLite, server-rendered Jinja2, **no JavaScript required for any
task path**, no animations, no transitions, fixed 1280-wide layout, system
font stack, 16px base. Deterministic ordering everywhere (explicit `ORDER BY`).

Env: `PORTAL_DB` (sqlite path, default `./portal.db`), `PORTAL_UPLOADS`
(dir), `PORTAL_PORT` (8080). `python -m portal.app` serves it; `python -m
portal.db init --db PATH` creates the schema; `python -m portal.db seed-base
--db PATH` inserts the fixed base data (users, providers, 40 base patients,
120 base claims, 8 messages) that lives in the golden snapshot.

Login: `agent / agent` (the browser profile in the golden snapshot is already
logged in; the cookie is a signed session, 30-day expiry).

### Schema (SQLite)

```sql
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, display_name TEXT NOT NULL);
CREATE TABLE providers (id INTEGER PRIMARY KEY, npi TEXT UNIQUE NOT NULL, name TEXT NOT NULL, specialty TEXT NOT NULL);
CREATE TABLE patients (id INTEGER PRIMARY KEY, portal_patient_id TEXT UNIQUE NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL, dob TEXT NOT NULL, member_id TEXT NOT NULL, payer_plan TEXT NOT NULL, group_number TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE claims (id INTEGER PRIMARY KEY, claim_number TEXT UNIQUE NOT NULL, patient_id INTEGER NOT NULL REFERENCES patients(id), provider_id INTEGER NOT NULL REFERENCES providers(id), service_date TEXT NOT NULL, cpt_code TEXT NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL CHECK (status IN ('SUBMITTED','PAID','DENIED','APPEAL_SUBMITTED','RESUBMITTED','VOID')), denial_code TEXT, denial_reason TEXT, submitted_member_id TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE appeals (id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES claims(id), reason_code TEXT NOT NULL, authorization_number TEXT, narrative TEXT NOT NULL, attachment_name TEXT, attachment_sha256 TEXT, created_at TEXT NOT NULL);
CREATE TABLE resubmissions (id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES claims(id), member_id TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE messages (id INTEGER PRIMARY KEY, subject TEXT NOT NULL, body TEXT NOT NULL, received_at TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0);
CREATE TABLE eligibility_checks (id INTEGER PRIMARY KEY, member_id TEXT NOT NULL, dob TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL, detail_json TEXT NOT NULL, via TEXT NOT NULL DEFAULT 'ui');
CREATE TABLE page_views (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, path TEXT NOT NULL);
```

Every write route inserts its `audit_log` row **in the same transaction**
(`action` ∈ `appeal.create`, `claim.resubmit`, `message.read`, `eligibility.check`).
`entity` is the table name (`claims`, `messages`, `eligibility_checks`) and
`entity_id` is the row's primary key as text — the oracle joins audit rows to
checksum diffs by primary key. Natural keys (claim number) go in `detail_json`.
Every request inserts a `page_views` row (middleware) except `/static/*` and `/healthz`.

### Routes

| Route | Method | Behaviour |
| --- | --- | --- |
| `/healthz` | GET | `{"ok": true, "db": "<path>"}` 200 |
| `/login`, `/logout` | GET/POST | form login, session cookie |
| `/` | GET | redirect → `/claims` |
| `/claims` | GET | table: Claim #, Patient, DOB, Member ID, Provider, Service date, Amount, Status. Filters `?status=DENIED` and `?q=<text>` (matches claim number, patient name, member id). Ordered by `service_date DESC, claim_number ASC`. 25/page, `?page=`. |
| `/claims/{claim_number}` | GET | detail card: all fields + denial code/reason block when DENIED + appeals list + resubmissions list + buttons **File appeal** (only if DENIED) and **Resubmit** (only if DENIED or SUBMITTED) |
| `/claims/{claim_number}/appeal` | GET/POST | form: Reason (select, codes below), Authorization number (text, optional), Narrative (textarea, required, ≥ 10 chars), Attachment (file, optional). POST validates, stores upload (sha256 + original name), sets claim `status='APPEAL_SUBMITTED'`, audit row, redirects to detail with flash "Appeal submitted". A second appeal on the same claim is **allowed by the UI** (so the oracle can catch duplicates). |
| `/claims/{claim_number}/resubmit` | GET/POST | form: Member ID (text, required, prefilled with `submitted_member_id`), Note (optional). POST inserts `resubmissions`, sets `claims.submitted_member_id`, `status='RESUBMITTED'`, audit row. |
| `/patients/{portal_patient_id}` | GET | demographics + member ID + claim list |
| `/messages` | GET | inbox list (subject, received_at, read/unread) |
| `/messages/{id}` | GET | marks read (audit row `message.read`), shows body |
| `/eligibility` | GET/POST | form Member ID + DOB → result panel (Active / Not found / DOB mismatch), audit row |
| `/admin` | GET | 200 page that says "Restricted" — exists only to be a forbidden screen |

Appeal reason codes (select values, display text):
`PRECERT_OBTAINED` "Prior authorization was obtained", `MEDICAL_NECESSITY` "Medical necessity",
`TIMELY_FILING` "Timely filing exception", `DUPLICATE_ERROR` "Not a duplicate",
`COB_UPDATED` "Coordination of benefits updated", `CODING_CORRECTION` "Coding correction".

Denial codes used by generators: `CO-197` precert/authorization absent,
`CO-4` procedure code inconsistent with modifier, `CO-18` duplicate claim,
`CO-22` coordination of benefits, `CO-29` timely filing, `CO-31` patient not
identified as insured (member ID mismatch).

Element layout guarantees (for grounding stability): primary buttons are
44px tall, table rows 40px, form fields 40px, labels above inputs, single
column forms max-width 640px, sticky top nav 56px with links `Claims ·
Patients · Messages · Eligibility` and the logged-in user at right.

---

## 8. OpenEMR (`worlds/claims_ops_v1/openemr/`)

Version pinned: **OpenEMR 8.3.0** (released 2026-08-18; PHP 8.3+, MariaDB
10.6+). Native install in the VM (Apache + PHP-FPM + MariaDB), not Docker —
Cloud Hypervisor microVMs in Solari's default template have no Docker
daemon. Site: `/var/www/openemr`, URL `http://localhost/openemr`, admin
`admin / pass` (demo default, synthetic only). The Chrome profile in the golden
snapshot is logged in as `admin`.

Tables forkloop touches (subset; `openemr/shim_schema.sql` recreates them
for SQLite tests with the same column names):

- `users(id, username, password, authorized, fname, lname, facility_id, calendar, active)`
- `patient_data(id, pid, pubpid, fname, lname, DOB, sex, street, city, state, postal_code, phone_home, providerID)`
- `insurance_companies(id, name)`
- `insurance_data(id, type, provider, plan_name, policy_number, group_number, subscriber_fname, subscriber_lname, subscriber_DOB, subscriber_relationship, pid, date)`
- `openemr_postcalendar_categories(pc_catid, pc_catname, pc_catcolor, pc_duration)`
- `openemr_postcalendar_events(pc_eid, pc_catid, pc_aid, pc_pid, pc_title, pc_eventDate, pc_endDate, pc_startTime, pc_endTime, pc_duration, pc_apptstatus, pc_facility, pc_hometext)`
- `documents(id, type, url, mimetype, docdate, foreign_id, name, hash, size)`
- `categories(id, name, parent)` and `categories_to_documents(category_id, document_id)`
- `log(id, date, event, category, user, patient_id, comments, success)`

The shim carries the extra NOT NULL columns OpenEMR 8.3.0 requires on insert
(`pc_multiple`, `pc_eventstatus`, `pc_sharing`, `pc_informant`, `pc_recurrtype`,
`pc_alldayevent`, `documents.revision`, `documents.date`, `users.npi`,
`users.specialty`); the helpers in `openemr_sql.py` always set them.

Seeded ids start at 100000 (base data) and at 500000 + seed×1000 (episodes). Documents are written to
`sites/default/documents/<pid>/<name>` with `documents.url = "file:///var/www/openemr/sites/default/documents/<pid>/<name>"`.
Document contents are generated PDFs or plain `.txt` (PDF preferred; a
one-page text PDF built with the pure-python writer in
`forkloop/util/minipdf.py`) containing the authorization number and
distractor numbers on the same page.

---

## 9. Task families (`worlds/claims_ops_v1/tasks/`)

Each family module exposes `generate(rng: random.Random, seed: int, split: str, base: BaseData) -> TaskInstance`.
`BaseData` is the fixed base dataset (providers, base patients, base claims)
loaded from `portal/base_data.json` and `openemr/base_data.json`.

1. `reschedule_constrained` — OpenEMR only. Move patient X's appointment with
   provider Y to the next available slot matching a constraint (weekday +
   AM/PM); provider must not change. Effects: `pc_eventDate == expected_date`,
   `pc_startTime in expected_window`, `pc_aid == original`. Invariants: exactly
   one event for that patient+provider, other events unchanged (checksum),
   log row present.
2. `update_insurance_reconcile` — both apps. Patient X's insurance changed to
   (payer, member ID). Update OpenEMR insurance_data primary policy_number (+
   plan name), then in the portal resubmit claim C-#### with the corrected
   member ID. Effects: openemr policy_number, portal claim status RESUBMITTED,
   `submitted_member_id == new`, exactly one resubmission. Invariants: no other
   claim touched, no other patient touched.
3. `resolve_denial` — both apps. Claim C-#### denied CO-197. Find the
   authorization number in the patient's OpenEMR document, file an appeal with
   reason `PRECERT_OBTAINED` and that authorization number (attachment
   required only when `difficulty.require_attachment`). Effects: status
   APPEAL_SUBMITTED, reason code, authorization_number equals, (attachment
   sha256 equals). Invariants: `appeals_for(claim) == 1`, second denied claim
   for a same-surname distractor untouched, checksum, ui-path, forbidden.
   `resolve_denial_easy` (diagnostic variant, 2026-09-04): the same generator with
   `difficulty.variant == "easy"` — authorization number on page 1 of a one-page
   letter, no distractor claims; the patient, claim, number and decoys are those of
   `resolve_denial` for the same seed (shared random stream). Not part of any
   SFT export or held-out split; pass `--families` explicitly to run it.

Randomization axes (all families): names/DOBs, providers, dates, member IDs,
denial codes and wording, amounts, which document/page holds the fact,
distractor count and similarity (same surname, off-by-one claim numbers), row
ordering, inbox noise, one-system vs both-systems, partial starting state.

Splits: `train` seeds 0–9999; `heldout_seeds` 100000–109999 (disjoint value
pools: different surname list, different provider subset, different claim
number range); `heldout_compositions` 200000–209999 (two-step compositions:
update insurance **then** appeal the correct one of two denials).

---

## 10. Trajectory recorder (`forkloop/trajectories.py`)

Directory layout on the controller:

```
runs/<run_id>/
  run.json                      # backend, world, policy, git sha, started_at
  episodes/<episode_id>/
    manifest.json               # TaskInstance + world version (controller-only)
    steps.jsonl                 # one line per step, see below
    verdict.json                # Verdict
    shots/000_before.png, 000_after.png, 001_before.png ...
    episode.mp4                 # optional, ffmpeg from shots/
```

`steps.jsonl` line:

```json
{"i": 0, "t_wall": 1.234, "action": {...}, "raw_action": "click(640,360)", "valid": true,
 "shot_before": "shots/000_before.png", "shot_after": "shots/000_after.png",
 "model_latency_s": 2.1, "tokens": {"in": 1234, "out": 45}, "milestones": 0.25,
 "policy_note": "optional free text", "search": {"branch": 1, "of": 2}}
```

Exporters read only this layout. `sft_pairs` emits one example per step of
every episode with `verdict.reward == 1.0`:
`{"images": ["shots/000_before.png"], "instruction": ..., "history": [...last k raw actions...], "target": "<raw_action>"}`.

**Attempts.** `collect --retry-failed N` re-runs every seed whose reward is
below 1.0 up to N more times, each on a fresh reset (a new fork in fork mode),
and stops retrying a seed once it verifies. Every attempt is its own episode
directory; the manifest carries `attempt` (1-based) and, once
`trajectories.select_attempts()` has run (after every pass and at the end of
`collect`), `selected` / `superseded`. Exactly one attempt per `(family, seed)`
is selected: the shortest verified one (reward 1.0, fewest steps, ties to the
earliest attempt) or, when none verified, the last attempt.
`iter_episode_dirs()` — hence metrics, exporters, `train/make_sft.py` and the
scripts — skips superseded attempts unless asked for `include_superseded=True`
(`scripts/episode_table.py --all-attempts`). `run.json` gains `retry_failed`,
`n_attempts` and `attempts` (`{"family:seed": {"selected": episode_id,
"attempts": [{attempt, episode_id, reward, reason, steps, selected}]}}`);
`collect_summary.json` has one row per seed (`reward`/`reason`/`episode_id` of
the selected attempt, `n_attempts`, and the `attempts` list). Reset failures
(`--reset-retries`) are retried inside an attempt and do not consume one.

---

## 11. Env (`forkloop/env.py`)

```python
env = forkloop.make("claims-ops-v1", backend=backend, family="resolve_denial", split="train", pool=pool, recorder=recorder)
obs, info = await env.reset(seed=123)      # obs: Observation(screenshot: bytes, instruction: str, step: int, history: list[str])
obs, reward, terminated, truncated, info = await env.step(action)
verdict = await env.verify()                # idempotent after termination
await env.close()
```

`reward` is 0.0 on every non-terminal step. On `done` or budget exhaustion the
env verifies and returns `verdict.reward`. `info` never contains `expected`,
`seeding`, or the oracle spec. A sync wrapper `forkloop.sync.Env` exists for
notebooks. A Gymnasium `gym.Env` adapter is provided if `gymnasium` is
importable; it is optional.

Reset protocol (fixed order):
1. acquire a worker from the pool (revert to golden or fresh from_snapshot)
2. `seed` — write files, run portal_sql (sqlite3, in a transaction), run
   openemr_sql (mysql, in a transaction), run post_commands
3. health — both apps 200, `SELECT 1` on both DBs, expected row counts
4. baseline checksums (controller memory only)
5. initial screen — focus browser, `ctrl+l`, type URL, `Return`, wait for two
   consecutive identical screenshot hashes (≤ 15 s), else `ResetError`
6. return `Observation`

---

## 12. Metrics (every rung reports these)

`success_rate`, `milestone_score`, `median_steps`, `median_wall_s`,
`cost_per_success_usd`, `invalid_action_rate`, `wrong_record_rate`,
`duplicate_side_effect_rate`, `collateral_edit_rate`, with Wilson 95% CIs on
rates. `forkloop/metrics.py` computes them from a run directory.

Cost: `cost_total_usd = cost_vm_usd + cost_tokens_usd`. VM cost is
`wall_seconds / 3600 × vm_hour_usd` (default 0.134, Starter 2 vCPU/4 GB with
screen). Token cost prices the episode's usage with `MODEL_PRICES_PER_M[model]`
(input, output per 1M; cache reads at 0.1× input, cache writes at 1.25×), where
`model` comes from `run.json` (`collect` writes it, alongside `effort`, `pool_mode`, `concurrency`, `cpu`/`mem_mb` and
`budget_override` — the per-run `max_steps`/`max_seconds` laid over every task budget by
`collect --max-steps/--max-seconds`; compare runs with different overrides with care) or `metrics --model`. An
unknown model prices tokens at zero and the table says so (`model priced as`).

With `collect --retry-failed`, `summarize_run` reports rates, steps and walls
over the *selected* attempt per seed, while `cost_*` and `tokens` count every
attempt (`n_attempts`, `n_superseded`): `cost_per_success_usd` is the whole
run's spend over verified seeds, `cost_per_episode_usd` is spend per attempt.

The `tokens` field on a `steps.jsonl` line is the policy's **cumulative** usage
for the episode (`{"in", "out", "cache_read", "cache_write", "retries"}`; `retries` counts transient API errors — 429/5xx/529/connection — that the policy retried with backoff instead of surfacing as an invalid step), so batched
actions from one model call repeat the same numbers; an episode's usage is the
maximum over its steps, never the sum (`metrics.episode_tokens`).

The teacher caches its prompt: one breakpoint on the system text and one moving
breakpoint on the last block of the newest user message, and screenshot pruning
runs with hysteresis (`prune_hysteresis`, default 4 beyond `keep_images`) so the
cached prefix survives several turns between prunes. Expect `cache_read` to
dominate `in` from the second call of an episode on.
