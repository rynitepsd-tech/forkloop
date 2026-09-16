# Limitations and verification boundaries — 2026-09-10

The supported user path is **evaluate a policy on `resolve_denial`, inspect
`forkloop report`** ([README](../README.md), [contracts](contracts.md)). Other task
families, search and training remain research paths. Offline inspection and
constructed controls do not establish live setup reliability, model performance
or adoption. The [dated verification record](product-handoff.md) identifies the
installation, commands, tests and browser behavior actually exercised, including
optional dependencies and live paths that were not checked.

## Evidence hierarchy and present result

- **Retained live failure:** the [worked example](worked-example/) is the adapter's
  seed-200 appeal, `WRONG_VALUE`: `AUTH-3614538` instead of `AUTH-36G14538`.
  It retains all 74 recorded steps but only **6/148 referenced screenshots**.
  Missing visual evidence is not reconstructed or assumed clean.
- **Completed paired live comparison:** base **0/2**, frozen v3 adapter **0/2** on
  development seeds 200/201; four completed cells, two matched pairs. Adapter 200
  submitted the wrong value; adapter 201 hit a Chrome renderer crash. This is not
  a demonstrated positive workflow learning curve. [Result of record](live-paired-v3-results.md).
- **Saved observations:** 40 cases per model from teacher-reached development
  states. Exact authorization selected for typing improved 2/20 → 14/20 and
  navigation agreement 8/20 → 13/20. These are diagnostic outputs, not independent
  navigation or proof of persisted entry. [Frozen results](frozen-v3-evaluation-results.md).
- **Magnification:** preparation stopped before model scoring because of renderer
  crashes. **0/0 matched pairs** is missing evidence, not a zero-success estimate;
  the proposed intervention is untested. [Stopped diagnostic](document-magnification-results.md).
- **Offline controls:** `forkloop demo --out runs/offline-controls`
  emits separate Recorder runs labeled `backend=fake`,
  `evidence_kind=constructed_control` and an explanatory `evidence_note`. The
  legitimate-success reference and negative scenarios use known controller state
  and portal HTTP facilities with fake OpenEMR/SQLite, not policy navigation.
  An interrupted case has no verdict; absent evidence is not success or a scored
  policy failure. Controls show verifier behavior, not application reliability.

Older `spikes.md`, student ledgers and handoffs are historical records. In
particular the locally retained `overnight-handoff.md` predates v3 GPU training;
its proposed smoke is not the current next step. Old Free-plan failures are not
statements about later Starter capabilities.

## Training happened; workflow improvement remains unproven

The [v3 training handoff](lambda-v3-handoff.md) records an H100 run completing
**411/440 planned optimizer steps, 1.869169 epochs**, on 25 demonstrations / 1,758
examples, in 380.87 minutes including final save. The remaining 29 steps were not
run. Paired-image GPU forward/backward, checkpoint saving and subsequent serving
therefore are no longer merely proposed or processor-only claims. This does not
make the unfinished two-epoch run complete or establish student workflow success.

Historical base vLLM, SFT-v1 and SFT-v2 runs each scored **0/30** on repeatedly used
seeds 200–229; local MLX fair 4B and 9B runs also scored 0/30. They are not a matched
control for later paired-image v3 evaluation. Teacher success and navigation
milestones are not student learning gains. Final seeds 100500–100529 remain sealed.
Any further paid stability/magnification experiment needs fresh authorization;
see the stopped diagnostic rather than restarting the superseded training proposal.

## Oracle and isolation scope

- **Reward is backend-specific.** Reward 1 means all recorded configured effects
  and invariants passed. On fake it means simulated state passed, not a live GUI
  task. On Solari it is scoped application database verification, not global safety.
- **Checksums are scoped.** The world lists 14 checksummed tables across the two
  applications; roughly 300 other OpenEMR tables are not diffed. Unlisted changes
  can be invisible. Reports expose the allow-list and exemptions; they do not
  expand this scope by rendering a green check.
- **Audit is a tripwire.** Portal audit rows identify changed rows. OpenEMR evidence
  is coarser and patient-keyed, with decoded SQL evidence for some bookkeeping.
  Direct writes plus plausible forged audit rows can evade this check. The policy
  interface has no shell or SQL channel, but this is not a hostile-code sandbox or
  cryptographic proof of UI-only writes.
- **Secondary failures matter.** An incorrect value can coexist with collateral
  edits, wrong-record writes or duplicate appeals. Read all checks, not only the
  primary reason. A count below the required one is incompletion, not duplication.
- **Other families are not revalidated live after repair.** Insurance-plan,
  visit-category and field-preservation controls use real portal HTTP facilities
  with fake OpenEMR state. Real family-1/2 edit-form bookkeeping may need narrowly
  evidenced allow-list changes; historical success is not verification of the repair.
- **Revised search is not live-validated.** Branch state, budgets, waits and cleanup
  have historical fake regressions; the current paid paired evaluation used
  best-of-one. Earlier successful live search predates those repairs.
- **Reset is a pipeline.** Restore/fork is followed by seeding, world preparation,
  health, baseline capture and initial/stable screen. The paired live run checked
  task fingerprints, checksummed tables and watermarks; one pair's screenshots
  differed in clock pixels. Equal seeds are not a byte-identical-VM guarantee.
- **Cleanup scopes differ.** Automatic pool orphan reaping checks its own `run_id`;
  `forkloop reap` defaults to the selected session ledger. Account-wide cleanup
  requires `--all-sessions`. Never reap a still-running session. Failed kills and
  pending charges must not be discarded merely to report cleanup.


The HTML export is a reader of artifacts, not a new oracle. Missing check evidence
is visibly incomplete; missing rewards are excluded from the run report's
recorded-outcome rate and counted separately. Six retained frames do not make a
74-step replay. Text escaping, a script-free document and screenshot path/PNG
validation address malicious artifact content; URL/path/credential redaction
is not universal secret detection. Review screenshots and free text before sharing
other recordings. Historical world-column exclusions are not independently
enumerated by a baseline digest's table names.

## Operational limits

New Forkloop Solari allocations are disabled by a release-wide capability hold,
including the historical spike allocators. A fresh ledger or pricing review
cannot clear it. The supported offline workflow and existing-resource cleanup
remain available. No verified hard lifetime bound was found in the provider API;
idle timers and local watchdogs do not provide one across every failure.

- **No fresh-account claim.** The prior September 15 session completed a new
  golden build on the existing account; recovery confirmed that snapshot is
  present. This was not repeated and does not prove installation on another account.
- **Guard is narrow.** Solari creates admit Starter and the September 2026 price
  bounds, failing closed on/after 2026-10-01. The student endpoint guard covers exact
  host `api.openai.com` and `gpt-5.6-luna`; arbitrary compatible endpoints, Anthropic
  and GPU rental are not guarded by that path. A ledger is not universal spending
  protection. See [cost](cost.md).
- **Observed lifetime-bound failure.** Recovery found two session desktops still
  reported running around ten hours after creation. Both were killed; no selected
  machines remained active. The requested timeout is idle-based, not a hard
  deadline. Accounting retains larger observed exposure and blocks new Solari
  reservations in that ledger. Neither explicit cleanup nor a pricing review
  establishes a reliable provider lifetime cap. See the current cost record.
- **Costs are not invoices.** Response token counts are authoritative usage; priced
  dollars are a calculation. Legacy runs can omit failed calls, idle and storage.
  Solari/Lambda pending reservations remain until billing reconciliation even after
  provider-confirmed termination. Do not interpret `actual_usd: 0` as free compute.
- **Desktop instability affects outcomes.** Historical restores were bimodal
  (~22 s or 70–160 s). Chrome renderer crashes interrupted the adapter and stopped
  magnification preparation; in-episode crash symptoms can end as `NOT_DONE` or
  `BUDGET_EXCEEDED`, not a distinct infrastructure exception. Inspect the trace.
- **Fake is not a browser.** Directory snapshots and SQLite stand-ins exercise
  controller semantics. Claims-ops screenshots are blank; only toy-counter has a
  rendered simulator. Never quote fake timings as Solari performance.
- **Bounded observation memory.** Previous/current screenshots preserve evidence
  for the audited first authorization-entry steps, not arbitrary longer gaps.
  Decoy selection, exact reading and retention still require policy competence.
- **Actions have limits.** Scroll uses Page Up/Down or arrow keys, not real wheel
  events. Triple-click maps to double-click in the teacher; raw mouse-down/up are
  unsupported (drag is supported). Attachment tasks add download/file-chooser work.
- **Starter concurrency is two machines.** Fork search and parallel collection
  compete for that cap; a local endpoint does not remove the VM constraint.
- **Synthetic data only.** Nothing here is a PHI deployment or HIPAA assurance.
  No GRPO implementation or demonstrated positive held-out learning curve is supplied.

## Product evidence is separate

The README's recurring job is a matched policy comparison yielding an inspectable
failure and a next experiment, or a concrete reason not to use this environment.
The audience is a hypothesis. Interest, an assisted first session, independent use,
repeat use and a changed research decision are different evidence levels. None is
claimed here; a runnable control, teacher success or model benchmark is not adoption.

The September 15 comparison is incomplete: navigation has two comparable pairs
out of four planned (A 0/2, B 2/2); memory has one comparable pair out of four
planned (A and B each 0/2 scored, with another A attempt excluded for a backend
action error). Completed, excluded, unfinished and missing cells remain visible.
The positive navigation observations are development evidence, not a winner,
held-out result, learning curve or adoption claim. The public comparison edition
contains check summaries; full cropped episode HTML is retained locally.
