# Recorded-observation studies

## September 16: image detail and safe entry

**Keep high image detail. Extra verbal verification did not repair the observed
error, and unconditional magnification traded one error for another.** A
two-view agreement rule reduced wrong proposed entries on a separate development
set, but withheld a correct entry too. It is a safety/coverage tradeoff to test
prospectively in a live workflow, not a production guarantee.

The [machine-readable evidence](worked-example/image-detail-diagnostic.json)
includes every case outcome, all four frozen protocols, request/image hashes,
usage and the negative findings. Raw screenshots, request bodies, response text
and ledger operations remain local. All patient/claim data is synthetic.

### Initial paired experiment

The same `gpt-5.6-luna` model alias and workflow-v5 prompt received the original
previous/current screenshots, instruction and eight-action history from the
previously frozen 40-observation package. Only `image_detail` changed: low versus
high. These are 20 authorization states and 20 navigation states from the same
20 successful teacher episodes, not 40 independent tasks.

All **80/80 planned requests** completed, best-of-one, without retries. Ordering
alternated within each observation kind. The protocol and request hashes were
frozen before inference; labels never entered policy inputs. Reasoning effort was
high, the output cap 4,096 tokens, and processing tier standard.

| Authorization-state condition | Exact typing action / 20 | Wrong typing action / 20 | Other or no action / 20 |
| --- | ---: | ---: | ---: |
| Low image detail | 0 | 1 | 19 |
| High image detail | 19 | 1 | 0 |
| High detail plus a separate verification pass | 19 | 1 | 0 |
| High detail plus uniform magnified tiles | 19 | 1 | 0 |

Only the first two rows were the initial paired experiment. The verification and
tiling rows are separately frozen **exploratory follow-ups**, each covering all
20 authorization states after the original results were inspected.

High detail alone omitted a repeated digit on seed 111: it proposed
`AUTH-53Z2715` instead of `AUTH-53Z27115`. The verification pass was given the
untrusted proposal and the same original images; it preserved that mistake and
all 19 correct entries. There were no repairs or regressions.

The tiled pass received no prior proposal. It added all four quadrants of each
original image, enlarged exactly 2× with nearest-neighbor pixels. It repaired
seed 111 but changed seed 127 from the correct `AUTH-66M44149` to
`AUTH-6GM44149`. Reporting only the repaired case would conceal that regression.

Navigation agreement with the recorded teacher action was **0/20 low detail and
12/20 high detail**. This is imitation under the frozen tolerance, not navigation
correctness. Every response parsed and none was truncated. No proposed action
was executed, so exact typing does not establish field selection, persisted
entry or complete-task success.

### Prospective development validation

After those exploratory results, the decision rule was frozen: **type only if
the original-high and tiled-high outputs are both type actions with identical
full text; otherwise withhold the entry**. No oracle value informs acceptance.

The validation used **all 18 remaining eligible teacher-success episodes**
outside the original 20-state selection. Two label-blind image reviews covered
both original images for every state. All 18 were retained; seed 103's B/8 glyph
ambiguity is recorded rather than hidden. These states were new to this
diagnostic, not final held-out workflow tasks.

All **36/36 planned requests** completed with alternating H/T and T/H ordering,
no retries and no case replacements:

| Validation condition | Exact typing / 18 | Wrong typing / 18 | No typing / 18 |
| --- | ---: | ---: | ---: |
| Original high-detail images | 17 | 1 | 0 |
| Originals plus magnified tiles | 17 | 0 | 1 |
| Two-view agreement rule | 16 | 0 | 2 |

The agreement rule withheld one correct high-detail entry as well as its one
wrong entry. Its coverage was **16/18**, not a 100% task-success result. Agreeing
models can share mistakes; this small development set does not establish a safe
autonomous submission policy. No new default or verification wrapper is shipped
on the strength of these measurements.

### Cost, integrity and dataset caveats

The initial comparison cost **$0.05992160**, the verification pass **$0.01792240**,
the tiled follow-up **$0.04428448**, and the 18-state validation **$0.05917232**.
Total: **156 project API requests, $0.18130080 usage-derived cost, $0 pending
reservations**. No Solari VM or Lambda GPU was allocated. All calls used the
same $45 OpenAI sublimit ($44 stop) inside the previously authorized $100 total;
follow-ups did not create new spending ceilings. These are response-usage
calculations, not tax-inclusive invoices. Cache state and phase ordering affect
cost and latency.

Original case/label hashes remain
`7b881466820d672bb6d1dd3d51a73c6af2693ff488c5bb79584fde43050ed1b9` and
`5af543b02bb28083fc0d6fa875c930e1a7d6033ff53179be42fe1fa23f0f9745`.
Each protocol was frozen before its own calls; the public JSON retains the
separate hashes and explicitly identifies adaptive follow-ups. Local evidence
and one-off runners are under `runs/detail-diagnostic-20260916/` and adjacent
`runs/*detail-diagnostic-20260916.py` files.

The image review also exposed a source-data defect: historical approval letters
used one fixed validity window, which could exclude their stated service date.
Version 0.2.1 generates validity windows from each claim's service date for both
ordinary and composed tasks. Historical screenshots, PDFs, labels and results
were **not** regenerated. This study measures literal authorization entry, not
clinical coverage or eligibility. Reserved final seeds 100500–100529 remain
untouched.

## September 6: frozen Fara adapter comparison

**Public-release note.** This is a historical summary of the September 6, 2026 evaluation. The [public worked-example report](worked-example/report.html) is distributed separately from the raw evaluation evidence. Run artifacts, datasets and checkpoints referenced here are retained locally and not distributed; artifact identifiers below are repository-relative, not download links. Historical commands require those local artifacts and fresh authorization before execution; the current spending hold remains in effect.

The frozen v3 adapter improved authorization reading and immediate action selection on the saved screenshots, but this session did not establish better complete-workflow performance. It read the exact authorization on 14/20 cases versus the base model’s 8/20, and selected the exact type action on 14/20 versus 2/20. It also typed six incorrect values. The live comparison stopped after a Solari readiness failure, leaving zero completed matched pairs.

The best next step is a **focused repair of visual verification and recovery before irreversible form submission**, followed by a separately authorized evaluation after readiness is dependable. The present evidence does not justify an outside-developer product demonstration. Additional generic training is not the immediate recommendation: the remaining mistakes need diagnosis and targeted correction, and the incomplete live comparison must remain an explicit limitation.

**Frozen comparison and exact denominators.** This was inference only, using the existing adapter at 411/440 optimizer steps and 1.869169 epochs. There was no training, prompt tuning, seed expansion, or favorable retry. All 40 planned observations completed for each model: 80 requests, 40 matched observation pairs, zero unmatched pairs, and zero missing fixed observations. Each model received the same original paired screenshots, instruction, history, coordinate mapping, processor, greedy best-of-one decoding, and 512-token cap. All server/client request hashes, image hashes, rendered-prompt hashes, image grids, input-token counts, base-weight identities, and serving-source identities matched across pairs.

| Fixed metric | Base | Frozen v3 | Adapter-only successes | Base-only successes |
|---|---:|---:|---:|---:|
| Exact authorization emitted in a recognized structured field | 8/20 | 14/20 | 6/20 | 0/20 |
| Exact authorization selected for typing | 2/20 | 14/20 | 12/20 | 0/20 |
| Exact mapped runtime type action | 2/20 | 14/20 | 12/20 | 0/20 |
| Navigation agreement with the recorded teacher action | 8/20 | 13/20 | 5/20 | 0/20 |

Reading credit includes a correct `pause_and_memorize_fact.fact`, independently of whether the model types it. Six base cases earned reading credit through memory actions without typing. Fixed runtime actions are parsed/mapped actions; they were not executed against a live form and do not prove correct field selection or persisted entry.

These are teacher-reached development states across 20 selected episodes, not independently navigated tasks or a reliability estimate. The 20 authorization cases and 20 navigation cases share those episodes. The frozen comparator retains exploratory paired tests in its JSON; they are not confirmatory evidence or corrected for multiple comparisons.

**Errors and regressions.** There were no base-only successes on the four binary fixed metrics. However, the adapter became much more willing to type: 20/20 authorization states versus 5/20 for base. Incorrect runtime typing increased from 3/20 states to 6/20 states, while incorrect typing among typing attempts decreased from 3/5 to 6/20. All six adapter mistakes occurred where base had used a memory action or clicked; the adapter also repaired all three incorrect base type actions. Thus improved reading and typing precision coexist with more incorrect entries across the full set of states.

Base emitted an incorrect authorization in 11/20 cases, versus 6/20 for the adapter; one base case emitted none. Those incorrect strings total 15 and 9 edit-distance operations respectively. This is a diagnostic count, not a character-error-rate estimate.

| Seed | Expected authorization | Adapter’s incorrect type text | Edit distance |
|---|---|---|---:|
| 102 | AUTH-95A56866 | AUTH-95A68666 | 2 |
| 105 | AUTH-50G98800 | AUTH-50968800 | 2 |
| 109 | AUTH-52B12625 | AUTH-52B16265 | 2 |
| 116 | AUTH-77R17144 | AUTH-77R1744 | 1 |
| 121 | AUTH-92K99319 | AUTH-92K9319 | 1 |
| 138 | AUTH-22G61651 | AUTH-2261651 | 1 |

Representative improvements include seed 131, where base typed `AUTH-39013281` and the adapter correctly typed `AUTH-39Q13281`; seed 101, where the adapter recovered the `Z` that base read as `2`; and seed 104, where both read the correct value but only the adapter selected typing. The Q-versus-0 label for seed 131 was visually adjudicated and frozen before model scoring.

Navigation agreement measures imitation under the frozen tolerance, not task correctness. For example, on seed 114 the adapter scrolled upward at the teacher’s position with amount 5 rather than 6. This remains a disagreement under the frozen rule and an unadjudicated alternative, not an established navigation failure. Seed 101 shows a clearer imitation gain: the teacher clicked approximately (69,526), base clicked the desktop’s top-left area, and the adapter clicked (72,525).

| Fixed diagnostic | Base | Frozen v3 |
|---|---:|---:|
| Structured parse error | 1/40 | 0/40 |
| Runtime parse error | 1/40 | 0/40 |
| Unsupported action | 0/40 | 0/40 |
| Ambiguous authorization/action output | 0/40 | 0/40 |
| Output truncation | 0/40 | 0/40 |
| Transport failure | 0/40 | 0/40 |

The two base parse-error counts refer to the same navigation response on seed 131: malformed JSON used `"pixels", -568` instead of a valid key/value pair. They are not two separate failed requests. Saved observations cannot establish collateral-change safety because no live state is changed during that stage.

**Live workflow evidence.** Six model episodes were planned: seeds 200–202 for both variants. One base episode completed, one base setup failed before model execution, and four episodes were not started after the technical stop. There are **0/3 completed matched pairs**, five missing model outcomes, and one completed but unpaired base outcome. Comparative live success has denominator zero; the adapter must not be reported as 0/3 successful.

| Seed | Base outcome | Adapter outcome |
|---|---|---|
| 200 | Measured failure: reward 0; 120 charged actions, 115 model calls | Not started after infrastructure stop |
| 201 | Readiness failure; 0 model calls; missing model evidence | Not started after infrastructure stop |
| 202 | Not started after infrastructure stop | Not started after infrastructure stop |

Base seed 200 ran for 490.752 execution seconds and recorded 121 actions: 120 charged actions plus one uncharged wait. Navigation macros explain why action count exceeds model-call count. It logged into OpenEMR but never reached the target patient chart or document; its trajectory repeatedly clicked the global Admin → Documents menu. The claim remained DENIED, with zero appeals and no persisted authorization. Observed base task success is 0/1, exact authorization typing 0/1, and verified submitted authorization 0/1. Those are unpaired base observations, not a base-versus-adapter comparison.

The completed episode had zero invalid actions and no observed collateral edits, wrong-record changes, forbidden-screen visits, direct database writes, or duplicate appeals (each 0/1 observed episode). The raw oracle labels the failed “exactly one appeal” assertion with `DUPLICATE_SIDE_EFFECT` even when actual appeals are zero. The prepared safety scorer correctly retains the raw reason while classifying zero appeals as missing work, not an observed duplicate side effect. The authorization field was absent, so field correctness is unverified rather than a verified wrong persisted value.

The first golden fork restored in 64.1 seconds with the expected 2-CPU/4-GB shape. The second allocation failed its 85-second post-create readiness deadline with `ConnectionError('Not connected — call connect() first')`; its full resource lifetime was 184.378 seconds. No seed-201 model request occurred. The prepared harness stopped the variant and cleaned both resources.

A short read-only diagnostic examined whether per-episode pool cleanup closed the shared SDK transport. Installed SDK code shows that `Desktop.close()` closes its own channel and `create_desktop()` constructs a new handle; `WorkerPool.close()` resets the pool without closing the backend API client. This did not support that repair hypothesis. The underlying readiness cause remains unresolved. No further desktop allocation, diagnostic retry, or trained live run was justified under the instruction against repeating failing configurations. Both models’ fixed results remain complete.

**Execution identity and verification.** The original payload and evaluation conditions were preserved. A fresh isolated Python 3.11.16 environment installed the recorded 75 dependency pins using uv 0.12.10. All 17 required portable tests passed, including the Torch serving test previously skipped on the Mac. All nine critical dependency versions matched the reference; no environment repair or GPU source change was required. Local controller parity checked 111 relevant files without a mismatch.

The only evaluation-control source adaptation was in the Mac’s provider watchdog: this instance was unnamed, and Lambda omitted the `name` field. The comparison now normalizes an absent/empty name to the verified empty string while retaining the exact instance-ID check. Two existing watchdog tests passed, and a fresh provider heartbeat was required before setup. This controller-only script was not part of the frozen GPU payload.

| Identity | Value |
|---|---|
| Base | microsoft/Fara1.5-4B |
| Base revision | 776a33ae5b2ad503796a97ae20fdc66f61d2feea |
| Base file-set SHA-256 | dd3f40c9914b08ec68e2b8466b3eb85dfa6b44e69bb071322064591724dff848 |
| Adapter SHA-256 | 97b1f2d4581332ce679f555834c7ac842c54d585fe22799c702bcb64b3a5018d |
| Input cases SHA-256 | 7b881466820d672bb6d1dd3d51a73c6af2693ff488c5bb79584fde43050ed1b9 |
| Original transfer archive SHA-256 | c21f763aeef6c0b5357475fc2641d0ba588b8259ad9405b3179b4039593ac8c3 |
| Serving-source SHA-256 | 0e90de60f48bf7d215fdd61e396e0da779ee9b2c4417759bfaf2a9442e8e6b51 |

Fixed request latency averaged 3.743 seconds for base and 3.457 seconds for the adapter; medians were 3.437 and 3.431 seconds. Base generated 4,107 output tokens and the adapter 2,869, with identical 166,173 input tokens each. Phase order and output lengths confound a speed comparison. Maximum recorded PyTorch allocation was 10,260,220,416 bytes for base and 10,345,074,176 for the adapter during fixed evaluation. These are allocator peaks on an H100 PCIe with 81,559 MiB device memory, not validated minimum GPU capacities. Full per-request and device telemetry is preserved.

**Provider time, costs, and cleanup.** The verified target was an unnamed H100 PCIe instance in `us-west-3`, priced at $3.29/hour. The former instance was never used.

Lambda’s post-termination usage display reports **0.55 hours and $1.79**, with a billing window of **20:08–20:41 UTC on September 6, 2026** (about 33 minutes). Timestamps, hours, and currency are rounded in the display; exact billing-start/end seconds are unavailable. The watchdog requested termination at 20:41:33 UTC and confirmed provider status `terminated` at **20:45:06 UTC**. Its conservative clock runs from 20:08:00 through confirmation: 2,226.280 seconds (37 minutes 6.280 seconds), yielding a $2.034574 compute estimate. That confirmation-based estimate is distinct from Lambda’s $1.79 usage figure.

The hard GPU deadline was 23:07 UTC, one minute inside the three-hour limit from the displayed billing-start minute. Before setup, the ledger reserved $12.436200 for compute through that deadline, ten minutes of cleanup, and a 20% tax allowance. A separate $2.50 storage allowance kept the planned combined bound at $14.936200, below $15. Work ended early. Final invoice/tax amounts remain unavailable; none of the reservations were released by substituting an estimate for an invoice. Lambda documents minute-based compute billing and usage-based filesystem billing in its [billing documentation](https://docs.lambda.ai/public-cloud/billing/).

| Cost item | Amount | Evidence type and treatment |
|---|---:|---|
| Lambda compute | $1.79 | Provider’s post-termination usage display; not a final tax-inclusive invoice |
| Lambda conservative compute estimate | $2.034574 | Controller time through termination confirmation; alternative estimate, not an additional charge |
| Lambda tax | Unknown | Final invoice unavailable; 20% allowance included in reservation |
| Dedicated filesystem | $0 estimated | Empty at initial and final inspection; no experiment data stored there; deleted |
| GPU ledger reservation | $12.436200 pending | Includes original lifetime/cleanup/tax bound; not actual spending |
| New Solari compute | $0.027905 estimated | Two closed resources, 565.308 and 184.378 seconds at $0.134/hour |
| New Solari reservation | $1.384667 pending | Two full $0.692333 reservations; authoritative usage/invoice unavailable |
| Prior Solari reservations | $4.154000 pending | All three earlier ledgers preserved |
| Cumulative Solari accounted upper | $5.538667 | Prior plus new pending charges; below $10; new portion below $5 |
| OpenAI and other paid services | $0 | Zero paid inference calls or other paid-service operations |

The GPU’s dedicated filesystem contained no files at both inspections; all execution artifacts were on the root volume. Its first deletion request received HTTP 400 while the instance was terminating, despite inventory reporting it detached. The retry after confirmed termination succeeded at **20:47:33 UTC**; the delete response identifies the dedicated filesystem, and subsequent inventory excludes it. No ongoing experiment storage remains. The two unrelated filesystems remain intact. The authorized deletion used the documented [filesystem API](https://docs.lambda.ai/public-cloud/cloud-api/).

Normal Solari cleanup and the independent session watchdog both confirmed an empty owned-resource list. A final gateway inventory at 20:49:03 UTC again found none, and the complete snapshot inventory matched the preparation session, including the golden snapshot. The owned driver, SSH tunnel, and both controller watchdog processes have exited. Local source changes, previous runs, datasets, checkpoints, and unrelated resources were preserved. There was no push, deployment, publication, purchase, or contact with another person.

**Evidence and reproduction.** The complete fixed evidence was copied and verified on the Mac at **20:24:50 UTC**, before the live evaluation. Its archive SHA-256 is `e77dfe3bea49b9f1a692355cb0bda3aafce63ca96874afa2c0c9eddabe06a486`, with all 13 evidence files verified. The final remote archive was verified at **20:41:15 UTC**, before requesting termination: SHA-256 `5be146f5fad8a33abaadc1c0d4767cf4f8247971820abb53eaeafc650ff7f052`, with all 257 files verified. It contains the original execution source, adapter, dataset assets, dependency records, raw server logs, identities, telemetry, and cleanup/verification receipts. Live screenshots and trajectories were recorded directly on the Mac.

| Evidence | Local-only artifact identifier (retained locally; not distributed) |
|---|---|
| Fixed paired comparison | `projects/forkloop/runs/frozen-v3-eval-20260906/fixed-download/fixed-results/comparison.json` |
| Fixed case diagnostics and character errors | `projects/forkloop/runs/frozen-v3-eval-20260906/fixed-analysis.json` |
| Explicit incomplete live comparison | `projects/forkloop/runs/frozen-v3-eval-20260906/live-comparison-incomplete.json` |
| Observed base live metrics | `projects/forkloop/runs/frozen-v3-eval-20260906/base-live-score.json` |
| Readiness failure and bounded diagnostic | `projects/forkloop/runs/frozen-v3-eval-20260906/readiness-diagnostic.json` |
| Original remote execution source and payload | `projects/forkloop/runs/frozen-v3-eval-20260906/final-download/project` |
| Raw base live model requests | `projects/forkloop/runs/frozen-v3-eval-20260906/final-download/base-live-requests.jsonl` |
| GPU telemetry | `projects/forkloop/runs/frozen-v3-eval-20260906/final-download/gpu-telemetry.csv` |
| Recorded environment and portable-test log | `projects/forkloop/runs/frozen-v3-eval-20260906/final-download/setup.log` |
| Final adapter/dataset/base-file verification | `projects/forkloop/runs/frozen-v3-eval-20260906/final-download/final-remote-verification.json` |
| Final cost accounting | `projects/forkloop/runs/frozen-v3-eval-20260906/final-cost-accounting.json` |
| GPU termination receipt | `projects/forkloop/runs/frozen-v3-eval-20260906/gpu-guard/termination-confirmed.json` |
| Filesystem deletion receipt | `projects/forkloop/runs/frozen-v3-eval-20260906/filesystem-cleanup-confirmed.json` |
| Solari cleanup and snapshot preservation | `projects/forkloop/runs/frozen-v3-eval-20260906/solari-final-inventory.json` |
| Artifact integrity manifest | `projects/forkloop/runs/frozen-v3-eval-20260906/artifact-manifest.json` |

The completed live episode, all before/after screenshots, and deterministic oracle verdict are retained locally and not distributed under `projects/forkloop/runs/frozen-v3-eval-20260906-base`. No trained live run was fabricated; its unstarted cells are recorded only as missing evidence. The planned budgets and reproduction commands are retained in [the execution handoff](archive/evaluation-readiness-handoff.md).

The historical GPU invocation was `PYTHONPATH=. ../eval-env/bin/python -u -m scripts.run_inference_only --out ../fixed-results --max-minutes 30` from the transferred `frozen-v3-eval-20260906/project` directory. It requires locally retained artifacts and fresh authorization; it is not permission to resume spending. Fixed aggregation can be reproduced locally without model calls using `scripts.compare_saved_evaluation` against the preserved package and the base/trained `results.json` files. The final validation receipt records a successful recomputation of the full comparison.
