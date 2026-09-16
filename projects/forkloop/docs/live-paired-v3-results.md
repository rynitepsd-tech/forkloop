# Frozen v3 adapter vs. base: live paired workflow comparison — 2026-09-06

**Public-release note.** The [public worked-example report](worked-example/report.html) is included; raw screenshots, logs, receipts, datasets and checkpoints are retained locally and not distributed. Artifact identifiers below are repository-relative, not download links. This document records the September 6, 2026 session, not current execution permission. Historical commands and further evaluation require the locally retained artifacts and fresh authorization; the current spending hold remains in effect.

**Both planned pairs completed on independent verified resets; neither model succeeded on either seed (0/2 base, 0/2 frozen v3).** The frozen adapter completed the entire development workflow on seed 200 — correct patient, correct document, the appeal form, one submitted appeal, correct reason — and failed only because it transcribed the authorization number as `AUTH-3614538` instead of `AUTH-36G14538` (one dropped character), which persisted into the claim. Base never got past the OpenEMR login/menu bar on either seed. On seed 201 the adapter found the correct patient and opened both documents, correctly rejected the decoy letter, then an in-VM Chrome renderer crash ("Aw, Snap!", error code 5) logged it out; after re-login it issued 67 consecutive `wait` actions against a stalled OpenEMR calendar frame until the 120-call cap.

Denominators: **4 planned cells, 4 completed, 2/2 matched pairs, 0 missing, 0 setup failures, 0 recovery attempts used.** No cell was rerun, discarded, or selected. The historical base seed-200 failure from the earlier session (`projects/forkloop/runs/frozen-v3-eval-20260906-base`, retained locally and not distributed) is preserved separately and is not part of this pairing; this session's base seed 200 is the primary paired observation.

The adapter's first consequential failure is **visual reading of a small-rendered document string** (PDF at 75% zoom, ~10–13 px glyphs), not action selection, field entry, or submission: it reached and executed every later stage with the wrong value. Its five tab switches back to OpenEMR "to verify" never changed the value it carried. Recommended next intervention, supported directly by the trace: magnify the document region before transcription (use the viewer's zoom or a high-resolution crop of the document body at the document-view state). This is a diagnosis from one adapter success-path episode and one crash-interrupted episode; it is not a reliability estimate.

## Paired outcomes

| Seed | Model | Finished? | Correct patient and document | Read/typed exact authorization | Submitted and persisted | Collateral / forbidden | Where progress stopped | Model calls / charged actions / seconds |
|---|---|---|---|---|---|---|---|---:|
| 200 | Frozen v3 | Yes (`done`, 74 calls) | Yes / yes (`authorization_letter_1.pdf`, doc 700000) | No: typed `AUTH-3614538` for `AUTH-36G14538` | Appeal submitted; persisted value wrong (`WRONG_VALUE`) | None observed (all safety checks passed, 1 appeal, distractor untouched) | Reading the number in the PDF viewer (steps 45–46) | 74 / 74 / 350.0 |
| 200 | Base | No (120-charged-action cap) | No / no (4 logins, 0 document views) | No entry | No appeal (0) | None observed | OpenEMR login → calendar; omnibox/login loops | 105 / 120 (123 recorded incl. macros) / 498.5 |
| 201 | Frozen v3 | No (120-call cap) | Yes / yes (viewed docs 701001 and 701000; identified 701001 as decoy) | No entry | No appeal (0) | None observed | Chrome renderer crash at step 42 → F5 → logout → stalled "Loading Calendar" → 67 waits | 120 / 120 (121 recorded) / 636.7 |
| 201 | Base | No (120-charged-action cap) | No / no (5 logins, 0 document views) | No entry | No appeal (0) | None observed | Admin → Documents menu item clicked 29 times (steps 84–118) | 106 / 120 (121 recorded) / 462.7 |

Repeated non-progress actions: base 201 clicked the same Admin → Documents entry 29 times; base 200 re-logged in four times and toggled `ctrl+l`/Tab on the omnibox; trained 201 issued 67 consecutive `wait(2.0)` after focusing the omnibox with `ctrl+l` and never typing; trained 200 made five OpenEMR round-trips that re-asserted the same misread value. Milestone rungs reached: adapter 6/6 (seed 200) and 3/6 (seed 201, highest `openemr_document`); base 1/6 on both seeds (`openemr_login`). Adapter mean model latency 3.57 s and 3.15 s; base 3.59 s and 3.21 s.

The `failure_codes` field in the comparison JSON still carries the raw oracle's `DUPLICATE_SIDE_EFFECT` label for zero appeals; the observed-safety scorer reports no safety failures for any of the four episodes.

## Adapter trajectory interpretation

Seed 200 (`resolve_denial-train-000200-018067`): the adapter logged in, searched "Castellano", opened the chart, dismissed the clinical-reminders alert, expanded Medical Record, opened the letter, said it would "zoom in" but clicked inside the viewer and then scrolled, and at step 46 stated the number as `AUTH-36I4538`; from step 47 on its text alternated between `AUTH-36I4538` and `AUTH-3614538`. It then found claim C-60200 in the portal, opened the appeal form, selected the correct reason, switched to OpenEMR and back five times (steps 50/51, 54/55, 57/58, 61/62, 64/65), typed `AUTH-3614538`, supplied a narrative after the form rejected an empty one, submitted, and declared success. The letter body reads `Authorization number: AUTH-36G14538` in a monospaced face at 75% zoom; the viewer title bar truncates it to `AUTH-36…`. The `G`→dropped/`I`/`1` confusion is the same one-character deletion pattern as fixed-evaluation seeds 116, 121 and 138. Because the adapter reached typing and submission, later transcription or submission safeguards would have been exercised on this episode; a pre-submission re-read at higher magnification is the point where this failure could have been caught.

Seed 201 (`resolve_denial-train-000201-bc751f`): the adapter reached the Documents tree at step 34, opened the letter, recognised at step 41 that it was "explicitly for a different service, so it is a decoy" (the expected document is `um_determination_1.pdf`, doc 701000), clicked to open the determination document, and at step 42 the OpenEMR tab rendered Chrome's "Aw, Snap! Error code: 5" page (episode `diagnostics/chrome_ps.txt` shows a fresh renderer, client id 14). The adapter pressed F5 "as instructed", which returned it to the login page; after re-login OpenEMR's tabs page stayed at "Loading Calendar / Loading…". It pressed `ctrl+l` intending to navigate directly, never typed a URL, and waited 67 times. The earliest consequential event is the environment crash; the model-side failure is the absence of any recovery action after the stall. No authorization was read or typed.

## Harness and backend changes

All changes are controller-side on the Mac; the GPU served from the unchanged frozen payload.

- `forkloop/backends/solari.py::SolariMachine._ready`: the readiness loop previously redialed the control channel at most once, then polled `health()` against a socket the guest had already closed (`_on_close` sets `_ws=None`, every call raises `Not connected — call connect() first`) until the deadline. That is exactly the seed-201 failure of the earlier session: the second fork was created 0.02 s after killing the first (`started_at` 1788726887.6 vs. kill at 1788726322.3+565.3 s), during which the SDK's own comment says the host may answer control upgrades with `guest_unreachable` while tearing down a sibling. The loop now redials after every transport error (and once after a non-transport health error) inside the same monotonic deadline, with no timeout increase and the health gate unchanged. `readiness_redials` is recorded.
- `SolariMachine.refresh_lifetime()`: re-arms the ≤30-minute kill-on-timeout window (`POST /sandboxes/:id/timeout`) so a machine reused across episodes keeps its guard; the harness re-arms it before every cell (responses recorded in `live-results.json`).
- `scripts/lambda_development_eval.py`: rewritten as a paired sequencer. `--plan trained:200,base:200,base:201,trained:201` is validated to pair both models on a seed before the next seed; one `WorkerPool(mode='revert', fallback_to_fork=False)` machine is reverted to the golden snapshot before every episode; each cell gets a fresh `Env`, fresh `ProbePolicy` (policy state cleared), the full reset pipeline (restore, seeding, `before_episode`, health, baseline, initial screen, stable screen), and its own recorder per label. Cells are classified `completed` / `setup_failed_before_model` (0 model calls) / `incomplete_model_episode`; at most one reserved replacement allocation is allowed per session; a model episode is never rerun; the secondary seed starts only when both primary cells completed and 2×1260 s + 600 s remain. Baseline digests and initial-observation hashes are written per episode.
- `scripts/compare_live_evaluation.py`: reads the paired results, reports planned/completed/matched/missing cells, checks task fingerprints and reset equivalence (14 checksummed tables, watermarks, preserved rows, initial observation, reset stages), and lists every infrastructure attempt.
- New: `scripts/live_paired_readiness.py` (one-allocation revert-cycle diagnostic), `scripts/lambda_provision.py` (single-instance launch with persisted request, inventory reconciliation by unique name, bounded capacity wait, no filesystem).
- Tests added: `tests/test_live_paired_sequence.py` (order, single recovery, no model rerun, partner/secondary gating, time reserve, plan validation) and four cases in `tests/test_solari_readiness_deadline.py` (redial after repeated drops, dial-failure retry, one redial for non-transport errors, bounded lifetime refresh). 51 focused tests passed, including the existing readiness-deadline, live-guard, watchdog, development-metric and core tests.

Execution source: Mac controller at git HEAD `973a90de` with uncommitted changes, manifest SHA-256 `767ed238cad27d5f0563319d1f3137eea558a01b55ea9bd5fe67a6aa0f2f3839` (`projects/forkloop/runs/live-paired-v3-20260906/execution-source-manifest.json`, retained locally and not distributed); the serving files in the frozen payload are byte-identical to the current source, so the GPU's `serving_source_sha256` equals the fixed evaluation's.

## Reset and task-state equivalence

Readiness diagnostic (one allocation, 234 s lifetime, 21:58–22:02 UTC): a golden fork, then three golden reverts on the same machine with the real reset pipeline for seeds 140, 141, 142 and a repeat of 140. All four resets passed; restore stages 0.0 s (fresh fork), 24.5 s, 123.9 s and 23.1 s. The 123.9 s revert dropped its control channel twice and the new loop recovered (`readiness_redials: 2`); under the previous code this would have run into the 240 s revert deadline. The repeated seed 140 reset produced zero differing baseline tables, identical watermarks and preserved rows, and a byte-identical initial screenshot. `set_timeout` returned `expiresAt` 30 minutes ahead. Cleanup left no owned machine; the snapshot inventory was unchanged and the golden snapshot present. Evidence retained locally and not distributed: `projects/forkloop/runs/live-paired-v3-20260906/readiness/diagnostic-results.json`, `projects/forkloop/runs/live-paired-v3-20260906/readiness/events.jsonl`.

Evaluation: one machine created 22:15:26 UTC (fresh golden fork, 21.0 s), then reverted before base 200 (87.8 s), base 201 (21.6 s) and trained 201 (21.3 s). For both matched seeds: task fingerprints identical (`cd2f3c2a…` for 200, `0f53cb92…` for 201), all 14 checksummed tables identical, watermarks and preserved rows identical, reset stages identical and all `ok`. Seed 201 initial screenshots are byte-identical; seed 200's differ in 1,730 of 921,600 pixels, all within the top bar's clock region (bbox x 868–1212, y 0–26), which is not task state.

## Model, adapter, environment and provider identities

| Identity | Value |
|---|---|
| Base | `microsoft/Fara1.5-4B` @ `776a33ae5b2ad503796a97ae20fdc66f61d2feea` |
| Base file-set SHA-256 | `dd3f40c9914b08ec68e2b8466b3eb85dfa6b44e69bb071322064591724dff848` |
| Adapter SHA-256 (411/440 steps, 1.869169 epochs) | `97b1f2d4581332ce679f555834c7ac842c54d585fe22799c702bcb64b3a5018d` (verified on the GPU before and after the run) |
| Serving source SHA-256 (both variants) | `0e90de60f48bf7d215fdd61e396e0da779ee9b2c4417759bfaf2a9442e8e6b51` |
| Transfer archive SHA-256 | `c21f763aeef6c0b5357475fc2641d0ba588b8259ad9405b3179b4039593ac8c3` (verified remotely) |
| Environment | Python 3.11.16 via uv 0.12.10; torch 2.7.1+cu128, torchvision 0.22.1+cu128, transformers 5.16.1, peft 0.20.0, accelerate 1.14.0, Pillow 12.3.0, huggingface-hub 1.30.0, FastAPI 0.141.1, uvicorn 0.52.4 |
| Serving | Two `scripts.lambda_serve` processes on loopback 8011 (base) and 8012 (base+adapter), bf16, sdpa, greedy, 512-token cap, reached through one SSH tunnel; both models resident simultaneously (24,199 MiB used) |
| Policy contract | previous+current screenshots, history 8, norm1000, image max side 1280, navigation macros, `fara_no_user_v1.md`, credential note, best-of-one, 120 calls / 120 charged actions / 900 s |
| GPU | Lambda `gpu_1x_h100_pcie`, us-west-3, endpoint discovered via the API, NVIDIA H100 PCIe 81,559 MiB, driver 570.148.08, SSH key authentication, no filesystem attached |
| Solari | SDK 0.2.0 (core/sandbox/desktop), websockets 17.1; golden snapshot |

Every one of the 405 model responses carried the matching identity (validated per response by `AuditedPolicy`); request hashes, prompt hashes, image grids, token counts and peak allocation are in the mirrored server logs.

## Infrastructure attempts and classification

| Attempt | Classification |
|---|---|
| Solari diagnostic fork, 21:58:36–22:02:30 UTC | Passed; disposable; killed; no model calls |
| Lambda launch attempt 1, 22:03:34 UTC | HTTP 200, one instance, capacity present, no ambiguity, no reconciliation needed |
| Solari evaluation machine, 22:15:26–22:51:08 UTC | One create; four resets (1 fork + 3 reverts) all passed; zero replacements; killed |
| Cells trained:200, base:200, base:201, trained:201 | All `completed` at attempt 1; no setup failure, no transport interruption, no recovery used |
| GPU termination | Requested 22:52:41 UTC, provider status `terminated` confirmed 22:57:15 UTC |

No infrastructure interruption, missing model evidence, or fabricated pair. The earlier session's readiness failure at seed 201 is now explained by the single-redial loop above and reproduced in `tests/test_solari_readiness_deadline.py::test_channel_dropped_after_redial_is_redialed_again`.

## Spending, reservations and cleanup receipts

| Item | Amount | Basis |
|---|---:|---|
| Lambda compute, conservative estimate | $2.943227 | 22:03:34 launch request through 22:57:15 termination confirmation at $3.29/h; the API exposes no usage endpoint (404), final invoice and tax unavailable |
| Lambda reservation (pending) | $11.844 | 170-min lifetime + 10 min cleanup + 20% tax allowance; retained until an invoice; $15 ceiling |
| Persistent filesystem | none created | Two pre-existing filesystems untouched |
| Solari compute estimate | $0.088467 | 234.3 s + 2,142.4 s at $0.134/h |
| Solari new reservation (pending) | $1.384667 | Two full Starter 5h10m reservations; $3 new ceiling |
| Solari cumulative accounted upper | $6.923333 | $5.538667 prior (four ledgers verified, each $1.384667) + $1.384667; below the $10 ceiling |
| OpenAI / other paid services | $0 | No calls |

Cleanup: GPU terminated (`projects/forkloop/runs/live-paired-v3-20260906/gpu-guard/termination-confirmed.json`); provider inventory afterwards shows no instances and only the two pre-existing filesystems (`projects/forkloop/runs/live-paired-v3-20260906/provider-final-inventory.json`). Solari: harness cleanup and the independent session watchdog both report an empty owned-resource list (`projects/forkloop/runs/live-paired-v3-20260906/eval/live-cleanup.json`, `projects/forkloop/runs/live-paired-v3-20260906/live-guard/watchdog-cleanup.json`); no forkloop machine of any session remains and the snapshot inventory matches the pre-diagnostic inventory with the golden snapshot present (`projects/forkloop/runs/live-paired-v3-20260906/solari-final-inventory.json`). Owned servers were stopped (GPU memory 0 MiB before termination), the SSH tunnel, both watchdogs, the harness and the sleep inhibitor have exited (`projects/forkloop/runs/live-paired-v3-20260906/controller-process-cleanup.json`). All receipts are retained locally and not distributed. Nothing was trained, committed, pushed, published, deployed, or purchased; no one was contacted.

## Evidence

| Evidence | Local-only artifact identifier (retained locally; not distributed) |
|---|---|
| Paired comparison with reset equivalence and attempts | `projects/forkloop/runs/live-paired-v3-20260906/comparison.json` |
| Harness results, plan, cleanup, ledger snapshot | `projects/forkloop/runs/live-paired-v3-20260906/eval/` |
| Adapter seed 200: document view as read | `projects/forkloop/runs/live-paired-v3-20260906-trained/episodes/resolve_denial-train-000200-018067/shots/046_before.png` |
| Adapter seed 200: typing the wrong value / submitted claim | `projects/forkloop/runs/live-paired-v3-20260906-trained/episodes/resolve_denial-train-000200-018067/shots/066_before.png`, `projects/forkloop/runs/live-paired-v3-20260906-trained/episodes/resolve_denial-train-000200-018067/shots/073_before.png` |
| Adapter seed 201: renderer crash / stalled calendar | `projects/forkloop/runs/live-paired-v3-20260906-trained/episodes/resolve_denial-train-000201-bc751f/shots/042_after.png`, `projects/forkloop/runs/live-paired-v3-20260906-trained/episodes/resolve_denial-train-000201-bc751f/shots/090_before.png` |
| Base seed 200 / 201 final screens | `projects/forkloop/runs/live-paired-v3-20260906-base/episodes/resolve_denial-train-000200-df6141/shots/122_after.png`, `projects/forkloop/runs/live-paired-v3-20260906-base/episodes/resolve_denial-train-000201-0df3f7/shots/120_after.png` |
| Trajectories (steps, raw responses, verdicts, resets, baseline digests, diagnostics) | `projects/forkloop/runs/live-paired-v3-20260906-trained/`, `projects/forkloop/runs/live-paired-v3-20260906-base/` |
| Raw server request logs, server logs, telemetry, setup log, remote hashes | `projects/forkloop/runs/live-paired-v3-20260906/final-download/` |
| Provisioning request/response, inventories, termination probe | `projects/forkloop/runs/live-paired-v3-20260906/gpu-provision/` |
| GPU watchdog ledger, events, termination receipt | `projects/forkloop/runs/live-paired-v3-20260906/gpu-guard/` |
| Solari guard ledger and watchdog receipts | `projects/forkloop/runs/live-paired-v3-20260906/live-guard/` |
| Cost accounting | `projects/forkloop/runs/live-paired-v3-20260906/final-cost-accounting.json` |
| Artifact manifest (984 files) | `projects/forkloop/runs/live-paired-v3-20260906/artifact-manifest.json` |

Seeds 100500–100529 remain sealed; seeds 200 and 201 are development seeds. The fixed 40-observation comparison was not repeated.
