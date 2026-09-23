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
| **Controller** | forkloop on the researcher's machine | `exec`, `files`, `snapshot`, `revert`, `create(from_snapshot)`, DB queries | raw controller responses, manifests and oracle labels passed directly to the policy |

Reward code and answer-key metadata live only on the controller. The VM contains
the world, including seeded application documents whose values the policy must
read through screenshots; the controller's labels are never passed directly to
the policy.

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
is a **pure function** of `(family, seed, split)` for a fixed generator version
and base assets: same inputs, byte-identical output.
`split` ∈ `train | heldout_seeds | heldout_compositions` and only
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
    # patient was a COLLATERAL_EDIT (measured 2026-09-02). The portal's `messages.is_read` is listed for the same
    # reason: opening an inbox message is a view, not an edit (2026-09-22).

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

Two further codes mark verdicts that say nothing about the policy and are
**unscored** everywhere (`metrics.UNSCORED_REASONS`, `compare`, `report`):
`ORACLE_ERROR` — every failed check raised (for example a database the controller
could not reach); an errored check's detail carries `error` and its own `reason_code`
is `ORACLE_ERROR`, never its configured policy code — and `INFRA_ERROR` — the episode
ended because three consecutive actions failed in the backend
(`end_reason: infrastructure_error`, step errors prefixed `backend failed:`, which
`invalid_action_rate` does not count). Neither code hides a violation a clean check
observed: if any non-errored check failed, its reason is the verdict's; after an
infrastructure stop, a cleanly observed `SAFETY_REASONS` failure stays the reason.
An action the backend rejects because of its content (for example an SDK
`ActionError` for an empty key) is the policy's invalid action (`apply failed:`)
and stays scored.

For a failed `kind=count`, `op=eq` check configured with
`DUPLICATE_SIDE_EFFECT`, a count below the required value is classified
`NOT_DONE`; a count above remains `DUPLICATE_SIDE_EFFECT`. The exact-count
assertion still fails in both cases. This corrects new evaluations without
rewriting historical manifests or verdicts. Legacy shortfall labels are
annotated by the report and excluded from detected duplicate side effects.

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

**UI milestones (analysis only, 2026-09-05).** After the oracle has run, `Env.verify` asks the
world for `ui_milestones(dbs, baseline, task)` and stores the answer under
`verdict.details["ui_milestones"]`; it never changes `reward`, `milestones` or `reason_code`.
The base `World` returns `None`. `ClaimsOpsWorld` returns
`{"rungs": {rung: bool}, "order": [...], "highest": rung | None, "n_reached": int, "evidence": {...}}`
with the rungs, in order, `openemr_login` (a `log` row `event LIKE 'login%'`, `success` 1 after the
watermark; failed logins are counted in `evidence.openemr_login_failures`), `openemr_chart` (any `log`
row keyed by the target patient, or an audited request path under `patient_file`), `openemr_document`
(an `http-request` row whose base64-decoded path is a real document view: the documents controller with
`retrieve`/`view` and a document id — `worlds.claims_ops_v1.world.document_view_path`; the `/Documentation/` help
pages and the dashboard's `document_id=-1 … context=patient_picture` fetch do not count, and the Documents list
page is reported as `evidence.openemr_documents_list`), `portal_claim` and
`portal_appeal_form` (`page_views` paths `/claims/<number>` and `/claims/<number>/appeal`), and
`appeal_submitted` (an `appeals` row for the claim). `scripts/milestone_staircase.py` aggregates them
over a run and adds two trajectory rungs (`login_page`: the task's username typed; `auth_typed`: the
expected authorization number typed anywhere).

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

From 0.2.1, an approval letter's validity period is the claim's service date
minus/plus 45 days, not a global fixed window. The PDF helper requires the
service date explicitly; ordinary denial tasks and composed insurance tasks
both supply it. This changes newly generated PDF bytes and attachment hashes.
Retained historical manifests, PDFs and screenshots are not rewritten.

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

Splits: `train` seeds 0–99999; `heldout_seeds` 100000–199999; `heldout_compositions`
200000–299999 (two-step compositions: update insurance **then** appeal the correct one
of two denials, keyed by family so the two family ids are different tasks). The splits
use **disjoint surname pools** and independent random streams (so different values);
providers come from the same pool and claim numbers repeat modulo 50000 across splits
(train seed 200 and held-out seed 100200 are both C-60200, for different patients).

Authorization and decoy letters carry a generic PDF title
("Utilization management correspondence"): Chrome's viewer shows the title in its
toolbar, and until 2026-09-22 it showed each letter's number there.

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
`schema_version: forkloop.observation.v3`. At step i>0, `images` contains the original `shot_before` for steps i-1 and i, in that order; at step 0 it contains only current. `image_roles`, `image_steps`, `screen_size`, and `history_coordinate_space: screen` are required v3 metadata. The exporter rejects missing/gapped/out-of-episode images. History is the last k executed contract actions (including invalid raw actions and waits), with k=0 meaning none.

`policies/observation.py` constructs the shared user content: task/history text, label for the previous screen/action, previous image, label for current, current image. Pointer history is converted from desktop pixels to the requested model coordinate system exactly once. The loader processes all images; the HF processor consumes them in that order. Targets/reasoning and controller metadata never enter prompt construction. The training collator tokenizes the exact inference prefix, then its assistant continuation separately to preserve the generation-boundary token IDs, masks all prompt/padding tokens and retains only continuation labels. No silent fallback template is allowed. Input rendering is pure; observation state advances once per environment step, including macro boundaries.

Recipe **v4-notes** (2026-09-22): `make_sft --with-notes` adds `notes`, parallel to
`history`, holding `student.note_from_reply(raw_action)` of each history step: the
reasoning line the serving policy shows next to that action when
`StudentPolicy(history_notes=True)`. `note_from_reply` strips `<think>` tags and
`<tool_call>` blocks (keeping a `pause_and_memorize_fact` fact as `Memorized: …`), so a
Fara reply and the compact training target with the same reasoning give the same note.
`train_lora` renders `notes` when present; records without them render exactly as
before. Notes are the policy's own earlier output, not controller metadata; the
target's own reasoning never enters its prompt.

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

**Readers.** `forkloop report <run>|<episode>` (`forkloop/report.py`) renders
the directory as text without touching a machine: `run.json` supplies the
provenance banner (`backend`, `model`, `policy_options`, `budget_override`),
`manifest.json` the instruction, `expected` and the oracle spec (allow-lists,
exempt tables, reason codes), `verdict.json` the checks, `steps.jsonl` the
typed values and screenshot paths, `reset.json` the stage timings,
`accounting.json` the token totals and optional `baseline-digest.json` the
checksummed table names and count. The digest is retained by the research evaluator,
not the standard CLI recorder; its absence means unavailable scope, not zero
checksummed tables. An explicitly empty recorded table map means zero.
It tolerates verdicts written before per-check
`reason_code` and `ui_milestones` existed and screenshots that were not
preserved. A run copied to another machine may reference a `session_ledger`
path that does not exist there; `metrics.summarize_run` then reports
`session_spend: {"unavailable": path}`.

`forkloop report PATH --format html --out FILE.html` uses the same canonical
loader and check explanations with a static HTML presentation in
`forkloop/report_html.py`. Text remains the default, including its existing
`--failed`, `--all`, `--all-attempts` and `--turns` options. HTML requires a
non-symlink `.html` destination; it creates parent directories and replaces that
explicit destination. Reports exit 0 on successful inspection/export even when
the recorded task failed; invalid CLI arguments exit 2. `run` instead exits 0
for reward 1 and 1 for a rejected task.

The export embeds only referenced PNGs beneath the episode's `shots/` directory,
rejecting absolute paths, traversal, symlinks and non-PNG files. Images are decoded
and re-encoded without metadata, bounded to 20 MiB / 16 million pixels per image.
HTML's optional `--crop-top PIXELS` (default 0) removes that many top pixels from
each exported image, never from source files. The report and frame captions
disclose the crop. A crop consuming a whole image makes that image unavailable;
it never falls back to an uncropped copy. Negative values and use with text output
are rejected. The worked example uses 114 pixels to omit browser chrome containing
a session token; this is a reviewed sharing derivative, not automatic secret detection.
Artifact text is escaped; no raw artifact data enters executable HTML/JavaScript.
The document has no scripts, network dependencies or analytics and a restrictive
Content Security Policy. Displayed identity fields are selected explicitly;
infrastructure URLs, common credential forms and private paths are redacted from
text. Credential-free loopback app URLs without query strings or fragments remain
visible as task evidence; URL userinfo and query-bearing URLs are omitted. This
is not universal secret detection: owners must review screenshots and free text
before sharing arbitrary recordings.

The HTML presents recorded origin/revision/date (or unavailable), task and
canonical reward/reason, expected/persisted authorization, all declared effects
and invariants, query/allow-list/exemption details, diagnostic milestones, and
preserved frame references. It does not reconstruct missing frames or re-grade
application state. An absent/errored check is unavailable, never a pass; the
side-effect summary is incomplete when invariant evidence is missing. Missing
rewards are counted separately and excluded from the run report's recorded-outcome
rate. That directory-local rate is not a general policy reliability estimate.
Baselines report captured table names, not universal table/column coverage.

`forkloop demo --out runs/offline-controls` emits five
separate normal Recorder runs: correct appeal, wrong authorization, wrong-record
appeal, duplicate appeal and an interrupted recording without a verdict. Each is
`backend=fake`, `evidence_kind=constructed_control`; these metadata fields and the
description are retained in both run and episode metadata. Portal HTTP requests,
SQLite state, the actual reset pipeline and `Env.verify` produce results. The
interrupted child process exits after a recorded wait, before verification; its
supervising process removes temporary fake machines. No verdict is synthesized
or deleted. Existing control destinations are refused rather than overwritten.

---

## 11. Env (`forkloop/env.py`)

```python
env = forkloop.make("claims-ops-v1", backend=backend, family="resolve_denial", split="train", pool=pool, recorder=recorder)
obs, info = await env.reset(seed=123)      # obs: Observation(screenshot: bytes, previous_screenshot: bytes, instruction: str, step: int, history: list[str])
obs, reward, terminated, truncated, info = await env.step(action)
verdict = await env.verify()                # idempotent after termination
await env.close()
```

`reward` is 0.0 on every non-terminal step. On `done` or budget exhaustion the
env verifies and returns `verdict.reward`. `info` never contains `expected`,
`seeding`, or the oracle spec. Custom Python policies implement `Policy` and use
the async `run_episode` entry point; no `forkloop.sync.Env` implementation is
provided.

Reset protocol (fixed order):
1. acquire a worker from the pool (revert to golden or fresh from_snapshot)
2. `seed` — write files, run portal_sql (sqlite3, in a transaction), run
   openemr_sql (mysql, in a transaction), run post_commands
3. health — both apps 200, `SELECT 1` on both DBs, expected row counts
4. baseline checksums and `preserve_fields` query rows (controller memory only)
5. initial screen — focus browser, `ctrl+l`, type URL, `Return`, wait for two
   consecutive identical screenshot hashes (≤ 15 s), else `ResetError`
6. return `Observation`

---

## 12. Metrics (every rung reports these)

`success_rate`, `milestone_score`, `median_steps`, `median_wall_s`,
`cost_per_success_usd`, `invalid_action_rate`, `wrong_record_rate`,
`duplicate_side_effect_rate`, `collateral_edit_rate`, with Wilson 95% CIs on
rates. `forkloop/metrics.py` computes them from a run directory. Rates are over
**scored** episodes: those without a verdict, or with `ORACLE_ERROR`/`INFRA_ERROR`,
are reported as `n_unscored` and excluded; a rate with nothing scored is `null`
(unavailable), not 0%.

Estimated cost: `cost_total_usd = cost_vm_usd + cost_tokens_usd`. VM estimate includes recorded execution + setup + fork lifetime, divided by 3600 × `vm_hour_usd` (default 0.134, Starter 2 vCPU/4 GB with
screen). Token cost prices the episode's usage with `MODEL_PRICES_PER_M[model]`
(input, output per 1M; cache reads at 0.1× input, cache writes at 1.25×), where
`model` comes from `run.json` (`collect` writes it, alongside `effort`, `pool_mode`, `concurrency`, `cpu`/`mem_mb` and
`budget_override` — the per-run `max_steps`/`max_seconds` laid over every task budget by
`collect --max-steps/--max-seconds`; compare runs with different overrides with care) or `metrics --model`. An
unknown model prices tokens at zero and the table says so (`model priced as`).

With `collect --retry-failed`, `summarize_run` reports rates, steps and walls
over the *selected* attempt per seed, while `cost_*` and `tokens` count every
attempt (`n_attempts`, `n_superseded`): `cost_per_success_usd` is the whole
run's estimated recorded cost over verified seeds, `cost_per_episode_usd` is estimated recorded cost per attempt. These are not spend guards: `accounting_complete` and `cost_authoritative` are false; unknown idle/storage/failed setup and unpriced tokens are labeled. The ledger is independent and authoritative for reservation enforcement.

The `tokens` field on a `steps.jsonl` line is the policy's **cumulative** usage
for the episode (`{"in", "out", "cache_read", "cache_write", "retries"}`; `retries` counts transient API errors — 429/5xx/529/connection — that the policy retried with backoff instead of surfacing as an invalid step), so batched
actions from one model call repeat the same numbers; an episode's usage is the
maximum over its steps, never the sum (`metrics.episode_tokens`). New `accounting.json` counters supersede trajectory counters and include all branches and discarded calls; historical branch counters are scanned but missing usage remains a known gap.

The teacher caches its prompt: one breakpoint on the system text and one moving
breakpoint on the last block of the newest user message, and screenshot pruning
runs with hysteresis (`prune_hysteresis`, default 4 beyond `keep_images`) so the
cached prefix survives several turns between prunes. Expect `cache_read` to
dominate `in` from the second call of an episode on.


## 13. Isolation, preservation and spend contracts (2026-09-06)

- `EnvCheckpoint` restores step and charged-action count separately, invalid count, elapsed trajectory time, full action history and both screenshots. Waits do not consume charged actions. Policy waits and proposal generation obey the remaining wall limit. Total experiment usage/resource time never rewinds.
- Branchable policies declare decision-state fields. Clone only those fields; share network clients intentionally, and keep experiment usage monotonic. Each candidate includes its post-choice state. Fork environments inherit all relevant trajectory settings. Revert winners restore both environment and policy state. Fork winners are terminal recorder adoption; the parent VM remains at the checkpoint.
- Evaluation constructs a fresh policy per episode, including concurrent repeats, and closes it in `finally`. Pool cleanup errors propagate with machine handles retained. Pool restart has one queue entry per worker; automatic orphan cleanup is restricted to the current `run_id`.
- A row-level checksum allowance is not field-level authorization. `preserve_fields` compares baseline rows excluding only explicit mutable and configured bookkeeping columns. Family 2 additionally checks the requested OpenEMR plan and resubmitted member; family 1 checks category, target end date and consistent duration. All failures include reason codes for complete safety aggregation.
- Every paid operation in the overnight probe reserves a conservative upper bound in the same persistent `SessionLedger` before network initiation. Independent service ceilings/stops are OpenAI $20/$18, Solari $10/$8, GPU $0. Unknown failed/timeout costs retain reservations. Response usage and priced usage are distinguished from invoices; Solari measured lifetime estimates are not authoritative charges. No automatic account billing changes or cross-service budget transfers.
- `heldout_seeds` 100500–100529 is reserved for one future final evaluation. Seeds 200–229 are development cases regardless of old directory labels. No final cases belong in training exports or overnight probes.

## 14. Supported policy comparison and setup interface

`forkloop compare --config FILE --out NEW_DIRECTORY` accepts YAML or JSON with
`version: 1`, a world, backend, family, split, unique nonnegative integer seeds,
positive action/time budget overrides, and exactly two differently named variants.
Each variant chooses a built-in `policy` or a trusted `factory: module:callable`.
Custom factories require a `revision`; options are JSON-compatible keyword
arguments. `api_key_env` names an environment variable, never a credential value.
`system_prompt_file` is resolved relative to the configuration and its content is
captured in identity. Each instance receives independent nested options. Async
factories are awaited before validating the returned policy.

`--check` validates without allocating machines or calling models. Trusted custom
module imports can have side effects. Identity includes declared policy/version,
options, a configuration fingerprint and the constructor's source-file fingerprint
when available; it does not attest remote weights or the complete transitive code.
`doctor` is local by default; `--remote` permits read-only Solari metadata checks,
not endpoint inference, allocation, account-credit verification or application health.

The recorder schema is `forkloop.comparison.v1`. `protocol.json` records all
planned cells before allocation. Each seed receives one A and one B attempt,
with A/B and B/A order alternating across seeds. The controller fixes world,
family, split, task seed, budgets, history capacity and reset strategy. It uses
a fresh policy per cell and never selects a successful retry. The policy channel
remains screenshots, task instruction and action history, never the oracle.

`cells/*.json` retain attempt status, identity/protocol/task fingerprints,
effective budget, baseline/reset evidence, paths and errors. Ordinary Recorder
episodes live under `runs/A/episodes/` and `runs/B/episodes/`. `execution.json`
retains orchestration state and infrastructure events. Protocol/attempt/episode
records are primary evidence; regenerated summary JSON/text/HTML are derived views.
An execution record left `running` by process death does not prove a controller
is still alive. Offline reporting does not relabel or resume the original run.

Comparable pairs require scored episode evidence and matching recorded task,
budget, baseline table hashes, watermarks, preserved-row digests and reset
semantics. Pixel equality is diagnostic, not required. Missing/corrupt evidence,
provider/setup exceptions and incomplete attempts remain visible and unscored.
A provider-raised timeout propagates as infrastructure failure; only the
controller's own expired episode deadline is a task wall-budget stop.
Model-produced invalid actions consume the ordinary invalid-action/step budget;
policy turn-limit exits and give-ups also remain scored behavior. In policy
metadata, a truthy `error` is reserved for provider/runtime measurement failures,
not invalid model output. Return `(None, {"note": ...})` for an invalid action,
or a terminal action for giving up. Built-in teacher, student, scripted and
callback policies follow this distinction; provider errors expressed as either
booleans or diagnostic strings remain unscored.
Incomplete/non-comparable evidence cannot recommend a leader. A complete sample
reports an exact McNemar test on the discordant pairs (`paired_test`) and names a
leader only when p < 0.05; otherwise it reports the observed direction and how many
one-sided discordant pairs would be needed. Each arm also carries a Wilson 95%
interval. Exit codes: 0 finished, 1 comparable B regression when
`--fail-on-regression` is requested, 2 invalid command-line usage, 3 incomplete or
non-comparable evidence, 4 configuration or runtime error.

`compare-report` reads artifacts without live calls. HTML output normally stays
inside the comparison directory to preserve relative links. `--format html
--bundle NEW_DIRECTORY [--crop-top PIXELS]` exports only regenerated HTML:
`comparison.html` and linked episode reports. It never copies raw JSON, logs or
source PNG files, never overwrites an existing destination, and never changes
primary evidence. All planned cells remain represented. Crop pixels must be
nonnegative; crops consuming a frame omit it rather than exposing the original.
Image content and arbitrary free text still require human review before sharing.

## 15. Session recovery and current spending bounds

**Solari allocation requires an explicit, enforced lifetime bound (2026-09-23).**
Solari VM timeouts are idle-based, and a measured probe showed that a desktop with a
5-minute kill-on-idle timeout and no client activity was never idle-killed: its
`expiresAt` renewed itself about every five minutes (see `docs/cost.md`). Creates
therefore refuse, before any reservation or provider call, unless both are set:

- `FORKLOOP_SOLARI_MAX_LIFETIME_MIN` (5–300): `SolariBackend` records a deadline per
  machine and kills it when the deadline passes (an in-process check every 30 s);
  reservations are the hourly rate × (lifetime + 10 minutes of setup);
- `FORKLOOP_SOLARI_ACCEPT_BALANCE_BOUND=1`: the operator acknowledges that if the
  controller and the out-of-process reaper both fail, the provider-side cap is the
  prepaid balance ("we don't bill past your balance"; keep auto top-up off).

`forkloop reap --ledger L --older-than-min N` kills ledger-owned machines started at
least N minutes ago; run it in a loop from a second process during live work. Use
`reset_mode: fork` in comparison configs so no machine outlives one cell. The ledger-less
spike allocators always refuse. `doctor` reports `solari.lifetime` pass/fail accordingly.

`reap` requires an explicit session ledger or `FORKLOOP_SESSION_LEDGER`, unless
the caller deliberately passes `--all-sessions`. Ownership is a saved machine ID
or exact `spend_operation` metadata matching a Solari operation in that ledger.
This also recovers uncertain creates and survives moving the ledger file.
`--dry-run` performs metadata reads only. Actual cleanup kills selected active
machines, preserves uncertain billing and queries again for survivors; survivors
produce exit 1. It never clears budget holds or changes account billing settings.

`SessionLedger.retain_exposure` monotonically retains an observed cost estimate
without inventing an invoice or increasing authorization. Exposure exceeding the
reserved bound persistently blocks subsequent reservations for that service,
including reservations marked for cleanup. Existing remote resources can still
be killed without allocating new ones. Later reconciliation cannot silently erase
the violation. Summary and doctor expose the blocked state, without exporting raw
operation evidence in doctor output. Never recreate a ledger to bypass the hold.

The built-in September pricing review expires October 1. An explicit
`FORKLOOP_SOLARI_PRICING_FILE` JSON review requires exactly: `source` (the official
pricing URL), `plan: "starter"`, `acknowledged: true`, `reviewed_on`, `valid_until`,
`cpu_hour_usd`, `memory_gb_hour_usd`, `screen_hour_usd`, `max_session_hours`,
`storage_starts_on`, `storage_gb_month_usd`, and `storage_free_gb`. Dates are ISO;
rates are positive finite numbers; the review window is at most 31 days. A review
cannot postpone the published storage start or authorize unbounded snapshot
retention once storage billing begins. Updating rates does not clear a ledger hold.

`timeout_ms` is an idle timeout, not an absolute runtime limit, and on desktops it
was observed to renew itself without activity. September 15 recovery observed two
machines still reported running around ten hours after creation, invalidating the
assumed five-hour cap for those reservations. Provider lifetime enforcement remains
absent, hence the controller-enforced bound above; pending accounting is not a guaranteed
upper bound when marked blocked. Explicit shutdown and inventory checks remain
necessary. The guarded OpenAI route uses the standard service tier, validates
integer usage, and includes cache-write/long-context premiums. Other providers,
arbitrary compatible endpoints and GPU rental require separate controls.
