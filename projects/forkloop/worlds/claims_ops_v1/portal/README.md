# Payer portal (`worlds/claims_ops_v1/portal`)

A synthetic health-payer provider portal used as a **training world for
vision-only GUI agents**. It is deliberately boring: server-rendered HTML,
zero JavaScript, a fixed 1280px layout, and no animation, so that screenshots
are stable and every task path is reachable with a mouse and keyboard alone.

Every write goes through a form route that records an `audit_log` row in the
same SQLite transaction, and a middleware records every request path in
`page_views`. The controller-side oracle (docs/contracts.md §6) reads those
tables to compute rewards; nothing in this app knows what the "right answer"
is. The spec for this module is docs/contracts.md §7.

## Running

```bash
# from projects/forkloop, with the world extras installed
PYTHONPATH=. python -m worlds.claims_ops_v1.portal.db init      --db ./portal.db
PYTHONPATH=. python -m worlds.claims_ops_v1.portal.db seed-base --db ./portal.db
PORTAL_DB=./portal.db PORTAL_UPLOADS=./uploads PORTAL_PORT=8080 \
  PYTHONPATH=. python -m worlds.claims_ops_v1.portal.app
```

Then open http://localhost:8080 and log in as `agent` / `agent`.

| Env var | Default | Meaning |
| --- | --- | --- |
| `PORTAL_DB` | `./portal.db` | SQLite file |
| `PORTAL_UPLOADS` | `./uploads` | appeal attachments, stored as `<claim_number>/<sha256>_<original_name>` |
| `PORTAL_PORT` | `8080` | uvicorn port |
| `PORTAL_SECRET` | `forkloop-dev-secret` | itsdangerous signing key for the 30-day session cookie |
| `PORTAL_FIXED_NOW` | unset | ISO-8601 string; when set, every timestamp the app writes is this value (deterministic tests) |

Tests: `PYTHONPATH=. pytest tests/test_portal.py` from `projects/forkloop`.

## Base data

`base_data.py` is a deterministic generator (`random.Random(20260901)`) that
produces the fixed data baked into the golden snapshot: 1 user (`agent`), 6
providers, 40 patients, 120 claims across all six statuses, and 8 inbox
messages. Claims already in `APPEAL_SUBMITTED` / `RESUBMITTED` carry one
matching `appeals` / `resubmissions` row so detail pages are consistent.

`python -m worlds.claims_ops_v1.portal.base_data` rewrites `base_data.json`.
**The JSON is the source of truth**: `seed-base` reads it (never the
generator), and task generators load it as `BaseData`. Regenerate it only when
the generator changes, and check the result in.

Base claim numbers are `C-1001` … `C-1120`; patient ids `P-10001` … `P-10040`.
Task generators should use ids outside those ranges.

## Schema summary

Exact DDL lives in `db.SCHEMA_SQL` and docs/contracts.md §7.

| Table | Purpose |
| --- | --- |
| `users` | portal logins; `password_hash` is `pbkdf2_sha256$iters$salt$hash` |
| `providers` | NPI, name, specialty |
| `patients` | demographics + `member_id`, `payer_plan`, `group_number`; public key `portal_patient_id` |
| `claims` | one row per claim; `status` ∈ `SUBMITTED PAID DENIED APPEAL_SUBMITTED RESUBMITTED VOID`; `denial_code`/`denial_reason` set when denied; `submitted_member_id` is what was on the claim (may differ from the patient's `member_id`) |
| `appeals` | append-only; `attachment_name` (original filename) and `attachment_sha256` |
| `resubmissions` | append-only; the member id used on resubmit |
| `messages` | inbox; `is_read` flips on view |
| `eligibility_checks` | append-only log of eligibility lookups, `result_json` |
| `audit_log` | one row per write, **same transaction**; `action` ∈ `appeal.create claim.resubmit message.read eligibility.check`; `entity` is the table name (`claims`, `messages`, `eligibility_checks`); `entity_id` is always the row's primary key as text (the claim number is in `detail_json.claim_number`), so the oracle can match audit rows to checksum diffs by primary key |
| `page_views` | one row per request path (middleware), except `/static/*` and `/healthz` |

Money is stored in integer cents and rendered as `$1,234.56`. All timestamps
are ISO-8601 UTC strings (`2026-09-01T12:00:00Z`).

## Routes

| Route | Method | Notes |
| --- | --- | --- |
| `/healthz` | GET | `{"ok": true, "db": "<path>"}`; not logged to `page_views` |
| `/login`, `/logout` | GET/POST | signed session cookie, 30 days |
| `/` | GET | → `/claims` |
| `/claims` | GET | `?status=`, `?q=` (claim #, patient name, member id), `?page=`; 25 per page; `ORDER BY service_date DESC, claim_number ASC` |
| `/claims/{claim_number}` | GET | detail card, denial block, appeals + resubmissions, **File appeal** (DENIED only) and **Resubmit** (DENIED or SUBMITTED) buttons |
| `/claims/{claim_number}/appeal` | GET/POST | multipart form; narrative ≥ 10 chars; sets `APPEAL_SUBMITTED`; a second appeal is allowed by URL so the oracle can catch duplicates |
| `/claims/{claim_number}/resubmit` | GET/POST | member id prefilled from `submitted_member_id`; sets `RESUBMITTED` |
| `/patients` | GET | list ordered by last name, first name |
| `/patients/{portal_patient_id}` | GET | demographics + claims |
| `/messages`, `/messages/{id}` | GET | viewing marks read + audit row |
| `/eligibility` | GET/POST | Member ID + DOB (`YYYY-MM-DD`) → Active / DOB mismatch / Not found |
| `/admin` | GET | 200 "Restricted"; exists only to be a forbidden screen |

## Deterministic-UI rules

These are the layout guarantees the grounding stack relies on. Keep them.

- **No JavaScript.** Every route works with plain HTML forms; the app serves
  no `<script>` at all. Flash messages ride on a short-lived cookie.
- **Fixed 1280px layout.** `body` is 1280px wide and does not reflow with the
  viewport; horizontal overflow is hidden.
- **System font stack, 16px base**, line-height 1.5.
- **No transitions or animations** (`* { transition: none; animation: none }`).
- **Sticky 56px top nav** with the links `Claims · Patients · Messages ·
  Eligibility` and the logged-in username at the right.
- **Primary buttons 44px tall**, **table rows 40px**, **text inputs, selects
  and file inputs 40px**, labels above inputs, single-column forms with
  `max-width: 640px`. (Narrative textareas are 120px; they are the one
  multi-line field.)
- **Explicit `ORDER BY` on every query** that renders a list, so two renders
  of the same DB state are byte-identical.
- **Timestamps are injectable** via `PORTAL_FIXED_NOW` so test fixtures and
  snapshots are reproducible.
