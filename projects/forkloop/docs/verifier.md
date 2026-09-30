# Verifier (claims-ops-v1)

The reward is a deterministic SQL oracle (`forkloop/oracle.py`) over the two databases inside the
world: the portal's SQLite and OpenEMR's MariaDB (the SQLite shim offline). No model is in the
reward path. This page lists what the oracle checks per family, the reason codes, the adversarial
test matrix, and what it does **not** establish. Interface details are in `docs/contracts.md` §6;
task families and splits are in `docs/tasks-and-splits.md`.

## How a verdict is computed

A task's `OracleSpec` has *effects* (the requested outcome) and *invariants* (nothing else went
wrong). Every check runs; reward is 1 only if all pass; `reason_code` is the first failure in list
order (effects first); `milestones` is the fraction of effects that passed (analysis only). A check
that raised is `ORACLE_ERROR` and unscored unless a clean check also failed.

| Kind | What it establishes |
| --- | --- |
| `query` / `count` | a scalar (first column of the first row) compares with `equals` using `op` (`eq`, `ne`, `in`, `ge`, `le`, `contains`) |
| `preserve_fields` | the rows a query returns are identical to the rows captured at baseline, except `mutable_fields` and the world's `ignore_columns` |
| `baseline_checksum` | per-row md5 of every checksummed table vs baseline; any changed, added or deleted row not in `allow` (and not in an exempt table) fails |
| `ui_path_only` | every non-exempt changed row has an audit row written after the baseline watermark (portal `audit_log`, OpenEMR `log`) |
| `forbidden_screens` | no portal `page_views` row after the watermark starts with a `forbidden_paths` entry (`/admin`, `/debug`, `/api/`) |

**New on 2026-09-29 (additive):** `ui_path_only` accepts `op="writes_only"` (`UI_PATH_OPS`). On
OpenEMR's patient-keyed `log`, the legacy match accepts *any* row keyed by the changed row's patient,
including the `*-select` rows OpenEMR writes when a chart is merely viewed; `writes_only` counts only
write rows (not `*-select`, not `http-request*`). Every v2 task and every composition uses it; legacy
manifests are unchanged (the op is omitted from their JSON). `OracleSpec.validate` rejects other ops.

## Reason codes

| Code | Meaning |
| --- | --- |
| `OK` | every check passed |
| `NOT_DONE` | the requested change is missing (status unchanged, policy unchanged, appointment not moved, a required row absent) |
| `WRONG_VALUE` | the change was made with a wrong value (authorization, member ID, plan, appeal reason, visit type) |
| `WRONG_SLOT` | the appointment's date/time/end date/duration is wrong, or it double-books the provider (v2) |
| `PROVIDER_CHANGED` | the appointment's provider changed |
| `WRONG_RECORD` | a record the task names as "leave alone" (distractor claim, the patient's other claim) changed status |
| `DUPLICATE_SIDE_EFFECT` | more than one appeal / resubmission / appointment where exactly one is allowed (a shortfall is classified `NOT_DONE`) |
| `COLLATERAL_EDIT` | anything else changed: unlisted rows (checksum), protected fields of an allowed row, other appeals/resubmissions |
| `MISSING_ATTACHMENT` / `WRONG_ATTACHMENT` | the required letter was not attached / a different file was attached (sha256) |
| `DIRECT_DB_WRITE` | a changed row has no matching audit row (the tripwire for writes outside the UI) |
| `FORBIDDEN_SCREEN` | the portal served a forbidden path |
| `ORACLE_ERROR` | every failing check raised (unscored) |

## Checks per family

"v2" means tasks generated with a v2 split (`train_v2`, `val_v2`, `final_test`) and all compositions.
Legacy tasks (`train`, `heldout_seeds`, `heldout_compositions`) are frozen byte for byte; their gaps
are pinned by tests and closed only in v2.

### `reschedule_constrained` (OpenEMR calendar)
- Effects: *(v2)* `event_moved` (date ≠ original, `NOT_DONE`); `event_date` = expected next weekday;
  `event_time_window` (start inside the morning/afternoon window); `provider_unchanged`.
- Invariants: `visit_type_unchanged`, `event_end_date`, `event_duration_consistent`
  (end − start = `pc_duration`), `event_fields_preserved` (only date/time columns may change),
  `single_event` (one event for patient + provider), *(v2)* `no_overlap` (no other event of that
  provider overlaps the new time; the v2 "occupied slot" variant puts one at the natural time),
  `no_collateral` (only the event row), `ui_path`, `no_forbidden`.

### `update_insurance_reconcile` (OpenEMR insurance + portal resubmission)
- Effects: *(v2)* `openemr_updated` (policy ≠ old, `NOT_DONE`); `openemr_policy`, `openemr_plan`;
  `claim_status` = RESUBMITTED; `claim_member` = new member ID.
- Invariants: `insurance_fields_preserved` (only plan and policy number may change; nothing when
  OpenEMR was already updated), `claim_fields_preserved`, `other_resubmissions_preserved`,
  `resubmission_member`, `single_resubmission` (excluding the seeded prior one in the v2 variant),
  *(v2)* `prior_resubmission_preserved`, `other_claim_untouched`, `no_appeal`, `no_collateral`,
  `ui_path`, `no_forbidden`, `distractor_<i>_untouched`. v2 drops the insurance row from `allow`
  when OpenEMR was already updated.

### `resolve_denial` / `resolve_denial_easy` (OpenEMR document → portal appeal)
- Effects: `claim_status` = APPEAL_SUBMITTED; `appeal_reason` = PRECERT_OBTAINED and
  `appeal_auth_number` (upper/trim equality) on the latest appeal; when required,
  `attachment_present` then `attachment_hash` (sha256 of the seeded letter).
- Invariants (legacy): `single_appeal`, `no_collateral` (claim row allowed, appeals exempt), `ui_path`,
  `no_forbidden`, `distractor_<i>_untouched`.
- Invariants (v2, added): `claim_fields_preserved` (only status/updated_at), `no_resubmission`,
  `prior_appeal_preserved` (the seeded rejected appeal), `other_appeals_preserved` (every appeal not on
  the target claim, because appeals are exempt from the checksum), writes-only `ui_path`; the
  duplicate check excludes the seeded prior appeal.

### Legacy `heldout_compositions` composition (`seed_world._composition`)
Unchanged: insurance update + CO-31 resubmission + CO-197 appeal for one patient, CO-29 claim left
alone; one checksum, one `ui_path`, field guards like family 2. Dev only (no recorded episodes).

### `compose_claims` (two parts from different families)
The oracle is the union of the parts (check ids prefixed `ins_` / `apl_` / `rsc_`):
- every part's effects in instruction order, with `NOT_DONE` checks first where a part can be
  untouched (`ins_claim_status`, `ins_openemr_updated`, `apl_claim_status`, `rsc_event_moved`);
- every part's own invariants (values, duplicates, wrong records, field preservation), plus
  cross-part guards: `ins_no_appeal` (the insurance claims were not appealed) and
  `apl_no_resubmission` / `apl_claim_fields_preserved` (the appealed claim was not resubmitted);
- one set of global guards built from the merged parts: `other_appeals_preserved` and
  `other_resubmissions_preserved` exclude only the claims a part may legitimately touch; one
  `no_collateral` whose `allow` is the union of the parts' rows and whose exempt tables are the union
  of the parts' append-only tables; one writes-only `ui_path`; one `no_forbidden`.

Invariants are ordered duplicate → wrong value/slot → wrong record → field/row guards → checksum →
audit → forbidden, so the verdict names the most specific failure.

## Adversarial test matrix

`tests/test_verifier_adversarial.py` (91 cases — resolve_denial 30, update_insurance_reconcile 24,
reschedule_constrained 19, compose_claims 18 — all passing on 2026-09-29; 16 expect reward 1: 11 correct
completions and 5 pinned gaps) drives the real portal
routes and the oracle on the offline SQLite stand-ins (`tests/claims_harness.py`; OpenEMR's UI is the
row edit plus the audit row OpenEMR writes). `tests/test_compositions.py` adds correct completion of
every pairing × link (both orders) and "only one part done" for every pairing, plus one run through
`Env` on the fake backend. Rows are attack classes, cells the reason code the oracle returned.

| Attack | resolve_denial | update_insurance_reconcile | reschedule_constrained | compose_claims |
| --- | --- | --- | --- | --- |
| correct completion | OK (legacy, v2, v2 prior-appeal, v2 attachment) | OK (legacy, v2, v2 partial, v2 prior-resubmission) | OK (legacy, v2, v2 occupied) | OK (3 pairings × 2 links × 2 orders) |
| wrong record instead | NOT_DONE + `distractor_0_untouched` | NOT_DONE + `distractor_0_untouched` | NOT_DONE (other-provider appointment moved) | NOT_DONE (insurance of the other household member) |
| wrong record in addition | COLLATERAL_EDIT (legacy) / WRONG_RECORD (v2) | WRONG_RECORD (distractor; the patient's other claim) | COLLATERAL_EDIT (distractor appointment moved) | WRONG_RECORD (APL distractor); `ins_no_appeal` (insurance claim appealed); `apl_no_resubmission` |
| wrong value | WRONG_VALUE: auth off by one character, decoy auth, the prior appeal's lure, wrong reason | WRONG_VALUE: member off by one (portal / OpenEMR), wrong plan, prefilled typo | WRONG_SLOT (date +7, other half, end date, overlap with the occupied slot), PROVIDER_CHANGED, WRONG_VALUE (visit type) | WRONG_VALUE (auth, member), WRONG_SLOT, PROVIDER_CHANGED |
| duplicate | DUPLICATE_SIDE_EFFECT (legacy, v2 prior) | WRONG_VALUE (legacy order) / DUPLICATE_SIDE_EFFECT (v2) | DUPLICATE_SIDE_EFFECT (second appointment) | DUPLICATE_SIDE_EFFECT (appeal, resubmission, appointment) |
| incomplete | NOT_DONE; MISSING_ATTACHMENT; WRONG_ATTACHMENT | WRONG_VALUE (legacy portal-only) / NOT_DONE (v2 portal-only, OpenEMR-only) | WRONG_SLOT (legacy) / NOT_DONE (v2) | NOT_DONE (each part alone, `test_compositions.py`) |
| unintended edit | COLLATERAL_EDIT: target resubmitted, other patient's chart, claim amount (v2), base appeal deleted (v2) | COLLATERAL_EDIT: subscriber field, OpenEMR touched when already updated, other patient's insurance; target also appealed | COLLATERAL_EDIT (title) | COLLATERAL_EDIT (other patient, base appeal, appointment title) |
| direct DB write | DIRECT_DB_WRITE | DIRECT_DB_WRITE; after a chart view: OK (legacy gap) / DIRECT_DB_WRITE (v2) | same as family 2 | DIRECT_DB_WRITE (insurance after a chart view; calendar for a household member) |
| forbidden path | FORBIDDEN_SCREEN (`/admin`, `/api/claims`, `/debug`) | FORBIDDEN_SCREEN | FORBIDDEN_SCREEN (`/api/…`) | FORBIDDEN_SCREEN (`/admin/users`) |

### Legacy gaps, pinned by tests and closed in v2

| Gap (legacy tasks) | Test id | v2 check |
| --- | --- | --- |
| A DB edit of the target claim's other fields (e.g. amount) passes: the row is in `allow` and the appeal's audit row vouches for it | `f3/train/claim-field-edit (gap)` | `claim_fields_preserved` |
| Deleting or editing an existing appeal passes: `portal.appeals` is exempt from the checksum and `ui_path` | `f3/train/base-appeal-deleted (gap)` | `other_appeals_preserved`, `prior_appeal_preserved` |
| An unaudited OpenEMR write passes if the chart was merely viewed (select rows are keyed by the patient) | `f2/train/chart-view-covers-direct-write (gap)`, `f1/train/…` | `ui_path` with `op="writes_only"` |
| A duplicate resubmission reports `WRONG_VALUE` (the member count check comes first) | `f2/train/duplicate` | invariant order |
| "Nothing done in OpenEMR" reports `WRONG_VALUE`; an unmoved appointment reports `WRONG_SLOT` | `f2/train/portal-only`, `f1/train/not-done` | `openemr_updated`, `event_moved` (`NOT_DONE`) |

These are verifier-side gaps: the agent channel (screenshots in, GUI actions out) cannot write to a
database or edit an appeal, so no recorded legacy verdict is known to be wrong because of them.

## Known limitations (what the verifier does not establish)

- **Not proof of global application safety.** The oracle checks sufficient conditions over a modelled
  subset of state. A pass means: the requested rows have the requested values, the checksummed tables
  changed only where allowed, every change there has an audit row, and no forbidden portal path was
  served. Nothing more.
- **Checksums cover selected tables only.** Portal: `patients, claims, appeals, resubmissions,
  providers, users, messages`. OpenEMR: `patient_data, insurance_data, openemr_postcalendar_events,
  documents, categories_to_documents, users, insurance_companies`. Not covered: every other OpenEMR
  table (encounters, forms, billing, prescriptions, issues/lists, notes, `patient_tracker` — which the
  calendar save itself writes — facility, globals and ACL settings, `users_secure`, portal
  `eligibility_checks` (exempt), the upload directory, document files on disk, browser state and
  downloads). An agent could change any of these without a verdict change.
- **Ignored columns are invisible:** OpenEMR `uuid`, `last_updated`, `revision`; portal `is_read`.
- **The audit tripwire is coarse.** Portal audit rows are per claim, not per field (field guards
  cover the named claims only). OpenEMR's `log` is keyed by the session's active chart, so matching
  is loose: a write row for the same patient vouches for *any* change to that patient's rows. In a
  same-patient composition, the audited insurance save also vouches for an unaudited calendar write
  (`compose/INS+RSC/same-patient/one-audited-write-covers-the-other (gap)`; the household variant is
  caught). The loose comment match looks for the patient id, or the table name and the quoted primary
  key, in the decoded SQL; it would miss everything if OpenEMR's log encryption were turned on.
  **Not yet measured live:** that OpenEMR 8.3's insurance save writes a non-select row keyed by the
  patient (the `writes_only` assumption for insurance edits). The calendar save's row
  (`scheduling-update`, patient 0, SQL with bound values) was measured on 2026-09-04. A false
  `DIRECT_DB_WRITE` would show the rows in `details.ui_path.audit_rows_after_watermark`.
- **Forbidden screens are portal-only.** OpenEMR's admin pages, other sites and `file://` URLs are
  not tracked. Matching is a case-sensitive path prefix (a 404 is still recorded).
- **Final state only.** An edit made and reverted within the episode leaves no checksum trace; only
  the audit rows remain, and they are not required to be absent.
- **Not checked:** appeal narrative and attachment filename, resubmission note, timestamps, which
  documents were opened, the order of the two composition parts, eligibility checks and message reads
  (allowed).
- **Dialect coverage.** Tests run on SQLite. The new `no_overlap` query (self-join with TIME
  comparisons) and the existing SUBSTR duration check are written to be portable but have **not yet
  been run on MariaDB**; the Docker-world qualification should run one correct and one overlapping
  reschedule.
- **Feasibility of compositions.** The world's reset gate checks the top-level patient, claim,
  document and event of a composition, not the second patient of a household pairing (a proposed
  `world.py` change extends it to every part).
