# Document magnification diagnostic — stopped before model scoring (2026-09-07)

**Public-release note.** This is a historical summary of the September 7, 2026 diagnostic. The [public worked-example report](worked-example/report.html) is included, but is not raw evidence for this diagnostic. All referenced run artifacts, datasets and checkpoints are retained locally and not distributed; artifact identifiers below are repository-relative, not download links. Historical commands and the recommended follow-up require locally retained artifacts and fresh authorization; the current spending hold remains in effect.

**Whether PDF-viewer magnification improves the frozen v3 adapter's authorization transcription was not tested: no model request was made and no GPU was rented.** The session stopped at the observation-preparation stage because Chrome's renderer in the Solari desktop crashed ("Aw, Snap!", error code 5) on OpenEMR page loads 13 times across the preparation attempts, above the 12-crash session limit written into the frozen protocol, and only 1 of the 20 scored seeds (plus the neutral setup example) had been prepared after about an hour. This is an infrastructure failure, not a model result: magnified-versus-standard success counts are 0/0 matched pairs, not zero successes.

What the session did establish, on the neutral example (seed 107) and on seed 101:

- The manipulation itself works through the viewer's visible controls. Clicking the zoom readout, typing `150`, Enter renders the same PDF at 150% inside the unchanged OpenEMR document panel; the page field re-selects the page; Left x10, Right x2, Down x9 puts the 7th template line (the authorization line) fully inside the pane. Measured label-free text row height rose from 7 px (default 70% fit) to 16 px on both prepared seeds, with the outer page scroll, viewport, tabs and tree identical between conditions.
- Downstream preservation is an expectation from code reading, not a measurement from this session (no inference ran): screenshots stay 1280x720, the client resizes only above 1280 px on the long side, and the server processor caps at 1280x1280 pixels, so the 150% render should reach the model as larger glyphs; the comparator would confirm it from the logged `image_grid_thw`.
- The reconstructed standard condition matches the historical geometry (70% fit zoom, toolbar at y=396 after the teacher's outer Page_Down x2), and the appeal-form screen reproduces the original form state (reason selected, authorization field focused).

## Frozen protocol and deviations

The protocol was frozen before any scoring in `projects/forkloop/runs/magnification-20260906/protocol.md` (retained locally and not distributed): all 20 authorization cases of `saved-dev-v3`, both conditions from one reconstructed application state per seed, original instruction/history/step and the frozen adapter contract unchanged, controller navigation from recorded provenance only (pid, claim number, document name/page/hash; expected values stripped before the harness runs), one neutral example (seed 107) for geometry, fixed-metrics scoring, alternating condition order, symmetric exclusion, and the live-follow-up gate.

Deviations, all before scoring:
- Geometry was chosen on seed 107 over four attempts (Down x0 → Down x4 → Down x9 with Right x2, and the switch from an outer scroll-up/zoom/scroll-down sequence to zooming in place at the detected toolbar position, after the first sequence hit the Documents uploader and opened a file dialog). No scored-seed output informed any of this.
- Controller-only crash handling was added during preparation: a renderer crash abandons the controller's own OpenEMR navigation, reloads, and repeats it (at most 4 per seed), then retries the seed from a fresh golden revert (at most 3). Every crash and attempt is recorded. No agent, policy, reset-pipeline or product code was changed; no crash recovery exists on any agent path. The counter that enforced the 12-crash session cap reset with each worker restart, so the cap was recognised as exceeded from the preserved event files rather than by the harness; the session was then stopped.
- After `FORKLOOP_CHROME_FLAGS='--v=0'` was set for the last worker (forcing the existing reset hook to relaunch Chrome after each revert) a fresh Chrome process crashed 4 times in 3 minutes on seed 102, which excludes stale-browser-state explanations. A probe that set the guest clock back to 2026-09-06 saw no crash in three logins, but neither did the same probe on 2026-09-07, so the crashes are irregular rather than date-determined.

## Preparation denominators

| Item | Count |
|---|---:|
| Planned scored seeds | 20 |
| Prepared and eligible | 1 (seed 101, third attempt) |
| Attempted, not prepared | 1 (seed 102: four consecutive renderer crashes in one attempt) |
| Not attempted | 18 |
| Neutral example prepared | 1 (seed 107) |
| Renderer crashes observed during preparation (all controller navigation) | 13 |
| Model requests, matched pairs, GPU instances | 0, 0, 0 |

Per-attempt evidence (actions, timestamps, screenshot hashes, Chrome diagnostics on every crash) is retained locally and not distributed under `projects/forkloop/runs/magnification-20260906/observations-work/` and the aborted worker directories. Historical rate for comparison: 2 of 45 teacher episodes on the same seeds mentioned a crash on 2026-09-04, and the adapter's seed-200 live episode earlier on 2026-09-06 navigated login → chart → documents → viewer without one; on 2026-09-07 the crash occurred at login, finder, chart and Documents loads alike, typically 17–30 s after a login.

## Representative screenshots

These raw screenshots and adjacent diagnostics are retained locally and not distributed.

- Seed 107 standard (page 2/2 at 70%): `projects/forkloop/runs/magnification-20260906/observations-work/calibration/attempt-1/standard.png`
- Seed 107 magnified (150%, authorization line centred): `projects/forkloop/runs/magnification-20260906/observations-work/calibration/attempt-1/magnified.png`
- Seed 107 appeal form (shared current image): `projects/forkloop/runs/magnification-20260906/observations-work/calibration/attempt-1/form.png`
- Seed 101 standard / magnified: `projects/forkloop/runs/magnification-20260906/observations-work/seed-101/attempt-3/{standard,magnified}.png`
- Renderer crash after login: `projects/forkloop/runs/magnification-20260906/observations-work-aborted-2/calibration/attempt-2/post_login_crash_1.png`, diagnostics `chrome-crash-diagnostics.json` beside each crash

## Identities and hashes

| Identity | Value |
|---|---|
| Base / adapter (not loaded this session) | `microsoft/Fara1.5-4B` @ `776a33ae5b2ad503796a97ae20fdc66f61d2feea`; adapter `97b1f2d4581332ce679f555834c7ac842c54d585fe22799c702bcb64b3a5018d` |
| Original package | `saved-dev-v3`, cases `7b881466…d9ed9b`, labels `5af543b0…7445` (unchanged) |
| New scripts | `scripts/magnification_common.py` `948aa906…5cb71`, `scripts/magnification_observations.py` `19ff84b3…3cd9c`, `scripts/build_magnification_package.py` `71d9f9e0…20709`, `scripts/compare_magnification.py` `dda12ce2…ca0fb` (git HEAD `973a90d`, uncommitted) |
| Seed 101 captures | standard `4f423b6e…d7666`, magnified `e74ff7b5…41a93` |
| Solari | SDK 0.2.0; golden snapshot preserved; two VMs (157 s and 3,393 s) |

## Resources, spending, cleanup

| Item | Amount | Basis |
|---|---:|---|
| Lambda | $0; no launch attempt, no instance, no filesystem change | `projects/forkloop/runs/magnification-20260906/provider-final-inventory.json` (retained locally; not distributed): no instances; the two pre-existing filesystems untouched |
| Solari new reservation (pending) | $1.384667 | two `machine_create` reservations of $0.692333; $2.00 new ceiling |
| Solari compute estimate | $0.132140 | 157.3 s + 3,392.7 s at $0.134/h; invoice unavailable, reservations retained |
| Solari cumulative accounted upper | $8.308000 | $6.923333 prior (five ledgers verified) + $1.384667; below the $10 ceiling, above the former $8 threshold as authorized |
| OpenAI / other paid services | $0 | no calls |

Cleanup receipts (retained locally and not distributed): holder process killed the preparation VM and reported no remaining owned machine, golden present, snapshot inventory unchanged (`projects/forkloop/runs/magnification-20260906/observations/summary.json`); the independent watchdog reported `remaining: []` (`projects/forkloop/runs/magnification-20260906/live-guard/watchdog-cleanup.json`); no forkloop worker, watchdog or session `caffeinate` process remains. Costs: `projects/forkloop/runs/magnification-20260906/final-cost-accounting.json` (retained locally and not distributed).

## Interpretation and recommendation

The magnification hypothesis stays untested; the session neither supports nor refutes it. The only new facts are that the intervention is mechanically feasible through the viewer's own controls and that the intended geometry is a fixed, label-free recipe; that it reaches the model without downscaling is expected from the code paths, not measured. Zero successes, missing evidence and infrastructure failure are distinct here: everything is missing evidence.

The historical recommendation, requiring fresh authorization and locally retained artifacts before execution: before repeating this diagnostic, run a short disposable Solari check of OpenEMR page-load stability on a fresh fork (login, finder, chart, Documents, viewer; three cycles). If the renderer crash rate is back at the 2026-09-04 level, rerun `scripts.magnification_observations` (exercised end to end on seeds 107 and 101), then `build_magnification_package` and `compare_magnification`. Those two are scaffolding validated only offline: the builder produced a `verify_dataset`-clean package from the one prepared seed, and the comparator was checked on synthetic rows (it now rejects pairs whose server telemetry is absent); neither has processed real model output. Expect one allocation of roughly 50 minutes for 20 seeds if loads do not crash, then the GPU step. If the crash rate is still at the 2026-09-07 level, the environment problem, already observed on seed 201, must be characterised separately before any further Solari-based evaluation is worth its cost.
