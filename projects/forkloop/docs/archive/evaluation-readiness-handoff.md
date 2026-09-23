# Forkloop frozen v3 evaluation readiness — 2026-09-06

**Historical status on September 6, 2026: Solari was ready at the time tested, and the fixed-observation package was ready.** The recommendation then was a short GPU inference session: 40 saved observations first, followed by three matched live development seeds if time, infrastructure, and a new session's authorization permitted. No training, model inference, hardware rental, OpenAI request, push, deployment or publication occurred in this preparation session.

**Current authority:** the [current product handoff](../product-handoff.md) supersedes this historical readiness recommendation. The Solari spending hold remains unresolved. No command, readiness result, or proposed budget here authorizes new GPU, Solari, or other paid work; future execution requires fresh explicit authorization and resolution of the applicable hold.

**Public-source scope:** the run evidence, saved-state dataset and images, adapter, transfer payload, ledgers, archived source, and environment receipts referenced here are retained locally and not distributed. Artifact identifiers beginning `projects/forkloop/` are repository-root-relative, unlinked references. Command paths refer to the stated historical working directories. Public clones do not contain the required retained artifacts or local environments.

The frozen adapter remains at **411/440 optimizer steps, 1.869169 epochs**. Do not resume training or change its weights. The next comparison can establish differences in decisions at teacher-reached states; complete-workflow improvement still requires live episodes.

## Infrastructure result and its limits

Disposable diagnostics ran **19:07:57–19:10:03 UTC (09:07:57–09:10:03 HST)**. The installed Python SDK remained `solari-core/solari-sandbox/solari-desktop 0.2.0`, with websockets 17.1 and httpx 0.28.1. No alternate SDK/environment was needed.

| Measured operation | Clean desktop | Golden-snapshot fork |
|---|---:|---:|
| Provider create | 0.185 s | 77.878 s |
| Direct SDK WebSocket connect | 0.254 s | 0.315 s |
| Direct SDK responsive health | 0.079 s | 2.313 s |
| Normal Forkloop connect/health | 0.079 s | 0.119 s |
| Screenshot and controller `true` | Passed | Passed |
| First application reset/seeding/observation, seed 140 | Not applicable | 10.018 s, passed |
| Second reset after golden revert, seed 141 | Not applicable | 31.973 s, passed |
| Normal GUI Escape and subsequent screenshot | Not applicable | Passed after both resets |
| Confirmed kill | Passed | Passed |

The real reset pipeline performed seeding, application health checks, baseline capture, initial-screen setup and stable observation generation. The original golden snapshot was never modified. Before/after snapshot inventories are identical. Only disposable resources were inspected. No sealed seed was generated or opened.

Evidence: `projects/forkloop/runs/evaluation-readiness-20260906/readiness-summary.json` (retained locally; not distributed), `projects/forkloop/runs/evaluation-readiness-20260906/events.jsonl` (retained locally; not distributed), `projects/forkloop/runs/evaluation-readiness-20260906/diagnostic-results.json` (retained locally; not distributed), `projects/forkloop/runs/evaluation-readiness-20260906/cleanup.json` (retained locally; not distributed), and `projects/forkloop/runs/evaluation-readiness-20260906/watchdog-cleanup.json` (retained locally; not distributed). All screenshots are in that evidence directory.

**The earlier outage's root cause remains unknown.** The clean and golden instances both succeeded with the same SDK during this September 6 session. This rules out a persistent failure of that installed SDK/configuration at that point in time; it does not prove an earlier global outage, a permanently healthy golden snapshot, or that the timeout patch cured the outage. The successful initial health calls did not require reconnection.

The confirmed local defects were narrower:

- Forkloop previously checked elapsed time only between operations; individual connection/RPC timeouts and cleanup could overrun readiness limits. `SolariMachine` now encloses dial, health, reconnect, close and backoff in one monotonic timeout. Revert and reconnect have corresponding enclosing deadlines. A failed/cancelled create reserves part of its post-create readiness budget for bounded cleanup; pending resources remain retryable through the gateway watchdog.
- SDK `reconnect()` returns immediately on an open socket. Forkloop now explicitly closes an unresponsive channel before reconnecting. It does not weaken the health gate.
- External cancellation is propagated, including cancellation during create/readiness; failure cleanup does not silently release reservations.

The diagnostic requested a **10-minute provider kill-on-timeout lifetime per VM**, with a detached session-only gateway watchdog established before creates and a **20-minute session deadline**. The two actual controller lifetimes were 1.217 s and 123.898 s. Watchdog startup initially failed for missing credentials before any create; that empty ledger/log was preserved. The successful launch used the existing `~/.config/forkloop/env`. No more paid diagnostics were attempted after acceptance passed. These watchdogs still depend on controller power/network and provider API responsiveness.

Official references checked during this session: [Solari SDKs](https://docs.getsolari.com/languages), [desktop lifecycle](https://docs.getsolari.com/desktops), and [pricing](https://docs.getsolari.com/pricing).

## Frozen saved-state evaluation

The frozen package was **`projects/forkloop/runs/evaluation-readiness-20260906/saved-dev-v3`** (retained locally; not distributed), or the identical `evaluation/` directory inside the retained final transfer payload. Earlier `saved-dev-v1`, `saved-dev-v1-reviewed`, `saved-dev-v2` and `inference-payload` directories are retained local preparation artifacts, not distributed, and were superseded. No model outputs informed any selection revision.

- **45 source episode records; 38 successful, unsuperseded and eligible.** There are 7 unsuccessful records, including 5 superseded attempts. Seeds 119 and 130 have no successful current episode.
- **20 selected episodes, 40 cases: 20 authorization-entry + 20 navigation observations; 80 image references, 78 unique original PNG assets.** Seeds: `101,102,104,105,106,109,111,114,115,116,121,122,123,127,131,133,134,136,138,139`.
- The 18 eligible but unselected seeds are `100,103,107,108,110,112,113,117,118,120,124,125,126,128,129,132,135,137`. Every source record and exclusion reason is in `exclusions.json`.
- Eligibility checks cover seed, episode, expected authorization value and exact input-image separation from the frozen 25-episode training dataset; original instruction and last-eight-compact-action history are retained. No expected authorization appears in input text. No reasoning/teacher explanation, oracle, label or OCR metadata enters model inputs.
- Authorization selection uses the first valid type of the exact expected value, with the **actual adjacent previous/current screenshots**. All selected previous images show the supporting patient approval document; current images show the matching claim's blank, focused authorization field. OCR inspected original bytes, and visual review checked the context. For seed 131, direct original-image review resolved OCR's `Q`→`0` error; both readings are retained in `manual-ocr-review.json`. All 38 successful episodes therefore qualify.
- Navigation chooses a valid content-area click/scroll near the middle of the pre-authorization trajectory, at step ≥10 and at least three steps before authorization, with a changed recorded after-image. Clicks within two steps of `admin`/`pass` typing are excluded, including re-login. Screens cover claim selection, patient search/results, document tree/content, dialogs and scrolling.
- Rank eligible episodes by SHA-256 of `forkloop-saved-dev-v1:<decimal seed>`, take the first 20, then output in seed order. The exact selection rule, the pre-scoring login-filter refinement, and OCR resolution are recorded. Source manifest/steps/verdict hashes and project-relative provenance are retained in controller labels; image paths used at execution are package-relative.

**Seeds 140/141 remain separately labeled historical diagnostics. Seeds 100500–100529 remain sealed.** Saved teacher states test what a model does when already at those states. They do not test whether it independently reaches them. The deterministic development sample is not a sealed final test set.

| Frozen artifact | SHA-256 |
|---|---|
| Dataset manifest | `708511a23f24d0b8c5387cc070a190f9360569bb54fe0d3b343da55d0b0e307e` |
| Model-input cases.jsonl | `7b881466820d672bb6d1dd3d51a73c6af2693ff488c5bb79584fde43050ed1b9` |
| Controller labels.jsonl | `5af543b02bb28083fc0d6fa875c930e1a7d6033ff53179be42fe1fa23f0f9745` |
| Final adapter_model.safetensors | `97b1f2d4581332ce679f555834c7ac842c54d585fe22799c702bcb64b3a5018d` |
| Original training data, unchanged | `194022b808f8e393d784da24fdfcb6fbf634d4b7d6deafb25faa19781863947b` |
| Transfer payload manifest | `bf2e28fe50157870037de524aac54b8982218c55d9aa759a455397eacc0f9034` |
| Transfer archive | `c21f763aeef6c0b5357475fc2641d0ba588b8259ad9405b3179b4039593ac8c3` |

The `projects/forkloop/runs/evaluation-readiness-20260906/inference-only-final.tar.gz` (retained locally; not distributed) is **90,953,725 bytes**, containing 228 manifested files plus the payload manifest. Its `projects/forkloop/runs/evaluation-readiness-20260906/archive-verification.json` (retained locally; not distributed) records integrity and zero matches against four configured secret values. It contains current required source, the final adapter, synthetic evaluation assets, portable regression fixtures and environment receipts. It contains no training dataset/screenshots, other checkpoints, credentials, old ledgers or base weights. The original experiment archive is preserved.

## Metrics and interpretation fixed before execution

`forkloop/fixed_metrics.py` defines the controller-only extraction rules. Reading credit requires a **single strict JSON `computer_use` action**, in a completed `<tool_call>` block or a whole structured response. Only `type.text` (or the runtime's `value` alias) and `pause_and_memorize_fact.fact` are recognized authorization fields. A bounded `AUTH-...` token is extracted only there. Prose elsewhere never earns credit. Multiple calls, conflicting type fields or multiple distinct authorization tokens are ambiguous and do not earn exact credit. Raw response JSON/text is preserved.

Report separately:

1. Exact authorization emitted in a recognized structured field, including a memory fact.
2. Exact authorization selected as the entire type value, and exact runtime type action.
3. Incorrect values with edit distance and character edit locations.
4. Navigation agreement with the recorded teacher action: click within 20 screen pixels with the same button; other actions require their recorded fields to agree. A disagreement remains an unadjudicated alternative, not a complete-task failure.
5. Structured/runtime parse errors, unsupported actions, ambiguity, truncation and transport failures.

The runtime still maps a memory action to a 0.1-second wait for **both** models. It has not been given a new persistent memory interface. Greedy decoding, cap 512, best-of-one, history 8, paired images, image max side 1280, norm1000 coordinates, navigation macros, login note and `fara_no_user_v1.md` are unchanged.

The corrected historical interpretation is explicit in [lambda-v3-handoff.md](lambda-v3-handoff.md) and `projects/forkloop/runs/evaluation-readiness-20260906/historical-rescored.json` (retained locally; not distributed): **base and adapter each emitted 1/2 exact authorization values; only the adapter immediately typed 1/2, versus base 0/2.** Base seed 140 already put `AUTH-61H18482` in its memory fact. Adapter seed 141 typed `AUTH-57A40946` instead of `AUTH-57A40046`, one character wrong. No historical raw output or original metric was rewritten.

The new fixed runner starts a fresh policy for each case, validates identities before requests and in responses, and refuses existing output directories. Aggregation checks manifest, request/image hashes, server prompt hash, processor image grid/token count, decoding, base-file identity and serving-source identity. Only matched cases enter paired metrics; missing/error records remain in the report with planned and matched denominators. Exact paired p-values are exploratory development statistics, not a promotion gate. A partial or heavily failed comparison is not decisive evidence.

## Checks actually executed

- **144 focused tests passed; 1 skipped** across readiness/cancellation/cleanup, reservations, raw metrics, new runner/aggregation, policy state, observation contracts and existing development metrics. The skip is the Torch-dependent HTTP serving concurrency test: this Mac environment does not contain Torch. See `projects/forkloop/runs/evaluation-readiness-20260906/final-focused-tests.log` (retained locally; not distributed).
- **16 portable package tests passed** when run from the final payload directory, including actual historical-response fixtures, independent mocked requests, matched aggregation, integrity and live guards.
- **2 GPU-watchdog arithmetic/refusal tests passed**; that future provider watchdog was not connected to Lambda or executed against hardware.
- Final archive was extracted into a fresh `/private/tmp` directory and passed relocated imports and full payload verification; all selected source image and source manifest/steps/verdict hashes were rechecked. Final payload file/image/adapter hashes verified; critical environment pins match the archived version report. All modified/new Python scripts compile.
- Two real Solari disposable diagnostics and both real reset cycles passed, as above. No real model was loaded or scored locally or remotely.

## Historical fresh-machine inference-only commands

These are **historical retained-source instructions, not current restart commands**. They require the locally retained transfer archive, source, adapter, data, and environment receipts, none of which is supplied by a public clone. Any future GPU or Solari work requires fresh explicit authorization under the [current product handoff](../product-handoff.md) and resolution of the applicable hold; the Solari hold is unresolved. The previous instance is terminated. Provisioning itself was deliberately outside these scripts. The historical recommendation was one H100 PCIe 80 GB, the previously validated architecture, with all provider credentials kept on the controller Mac.

The historical procedure first required starting the provider watchdog in the next subsection and confirming its fresh heartbeat. The Mac transfer example below starts at the repository root, with `GPU_HOST` set to a newly authorized host and a newly created remote directory:

```bash
cd 'projects/forkloop'
: "${GPU_HOST:?Set GPU_HOST to the newly authorized user@host}"
REMOTE_DIR=$(ssh "$GPU_HOST" 'mktemp -d "$HOME/forkloop-evaluation.XXXXXXXX"')
scp runs/evaluation-readiness-20260906/inference-only-final.tar.gz "$GPU_HOST:$REMOTE_DIR/payload.tar.gz"
printf '%s\n' "$REMOTE_DIR"
```

On that fresh GPU host, `cd` to the printed directory and run:

```bash
printf '%s  %s\n' c21f763aeef6c0b5357475fc2641d0ba588b8259ad9405b3179b4039593ac8c3 payload.tar.gz | sha256sum -c -
mkdir project
tar -xzf payload.tar.gz -C project --strip-components=1
cd project
uv venv --python 3.11.16 ../eval-env
uv pip install --python ../eval-env/bin/python \
  --extra-index-url https://download.pytorch.org/whl/cu128 \
  --index-strategy unsafe-best-match -r requirements-recorded.txt
uv pip install --python ../eval-env/bin/python --no-deps -e .
PYTHONPATH=. ../eval-env/bin/python - <<'PY'
from scripts.run_inference_only import verify_payload
verify_payload('.')
print('Payload verified; no inference performed by this check.')
PY
PYTHONPATH=. ../eval-env/bin/python -m pytest -ra \
  tests/test_fixed_metrics.py tests/test_saved_evaluation.py \
  tests/test_solari_readiness_deadline.py tests/test_evaluation_live_guards.py \
  tests/test_lambda_serving_serialization.py
PYTHONPATH=. ../eval-env/bin/python -m scripts.run_inference_only \
  --out ../fixed-results --max-minutes 30
```

Use an installed `uv`, or install it from its official distribution before these steps. The recorded environment is Python 3.11.16, torch 2.7.1+cu128, torchvision 0.22.1+cu128, transformers 5.16.1, peft 0.20.0, accelerate 1.14.0, Pillow 12.3.0, huggingface-hub 1.30.0, FastAPI 0.141.1 and uvicorn 0.52.4. The **75 pins** in `requirements-recorded.txt` come from preserved dependency-install receipts. `package-freeze.txt` itself contains “No module named pip”; it is preserved as failed evidence, not used as a lockfile. A fresh install of these receipts has not been executed in this session. The runner refuses a mismatch in the nine critical package versions.

The driver explicitly downloads/loads `microsoft/Fara1.5-4B` revision **`776a33ae5b2ad503796a97ae20fdc66f61d2feea`**, ignoring the old local base path inside adapter config. It serves base, evaluates all cases, stops its owned process, serves the same base plus the frozen adapter, evaluates again and writes `../fixed-results/comparison.json`. It binds loopback only, rejects an occupied port, records identity and GPU allocation telemetry, and terminates its own server on failure/deadline. It does **not** terminate the rented provider instance; establish the provider watchdog below first. Retrieve the entire fresh results directory, including failed/missing rows and server request logs, before terminating early.

### Provider lifetime and ledger on the Mac

Before GPU work, obtain the new exact `GPU_INSTANCE_ID`, `GPU_INSTANCE_NAME`, and timezone-aware `GPU_BILLING_START` (include time already billed since creation). Set `GPU_DEADLINE` to no more than 90 minutes after that billing start for fixed-only, or 180 minutes for an authorized combined session. Source the ignored local Lambda key; never transfer it. Run this in a persistent controller terminal:

```bash
cd 'projects/forkloop'
set -a
source .env
set +a
: "${GPU_INSTANCE_ID:?New authorized instance ID required}"
: "${GPU_INSTANCE_NAME:?Exact new provider name required}"
: "${GPU_BILLING_START:?Timezone-aware billing start required}"
: "${GPU_DEADLINE:?Timezone-aware provider termination deadline required}"
GPU_GUARD="runs/gpu-inference-guard-$(date -u +%Y%m%dT%H%M%SZ)"
printf 'GPU guard directory: %s/%s\n' "$PWD" "$GPU_GUARD"
caffeinate -i ./venv/bin/python -u -m scripts.gpu_inference_watchdog \
  --instance-id "$GPU_INSTANCE_ID" --instance-name "$GPU_INSTANCE_NAME" \
  --billing-start "$GPU_BILLING_START" --deadline "$GPU_DEADLINE" \
  --hourly-usd 3.29 --ceiling 8 --out "$GPU_GUARD"
```

For an explicitly authorized 180-minute combined session, use `--ceiling 15`. This watchdog reserves accrued compute through the deadline plus ten minutes cleanup and a 20% tax allowance, terminates only the exact ID/name, and retains its reservation until an authoritative invoice. Its fresh `heartbeat.json` must exist before inference starts. In another terminal, set `GPU_GUARD` to the printed guard directory; `touch "${GPU_GUARD:?Set the exact guard directory}/terminate-now"` requests early termination after retrieval; then require `termination-confirmed.json`. Do not close the watchdog terminal before confirmation. It refuses the former instance ID. It never creates/deletes filesystems or touches unrelated resources.

## Historical live comparison after fixed results

**Superseded on 2026-09-06 (later session):** `scripts/lambda_development_eval.py` is now the paired sequencer (`--plan trained:200,base:200,base:201,trained:201 --base-url … --trained-url …`, one reverted golden machine, at most one replacement allocation) and `scripts/compare_live_evaluation.py` takes `--out-dir`. The per-label commands below are retained as history; see [live-paired-v3-results.md](../live-paired-v3-results.md).

At this preparation stage, `scripts/lambda_development_eval.py` took a fresh output/run ID, an explicit timezone-aware experiment deadline and a fresh ledger-bound watchdog heartbeat. The obsolete absolute September 6 deadline had been removed. It validated the loopback server's model identity before any VM creation. It used **seeds 200–202, best-of-one, one disposable golden fork per model/seed, 120 actions, 900 seconds**, the same policy contract and bounded setup/cleanup. The 120-call cap was retained too. Each model required its own clean reset; setup failures produced missing evidence, not model failures. A failure stopped further cases for that variant without retries.

The following historical Mac guard procedure required future Solari budget authorization and included every known prior pending ledger at that time under the earlier $10 authorization. Those ledgers are retained locally, not distributed. This list is not a current accounting or permission to restart; the current product handoff is authoritative and the Solari hold remains unresolved.

```bash
cd 'projects/forkloop'
set -a
source ~/.config/forkloop/env
set +a
LIVE_GUARD="runs/live-evaluation-guard-$(date -u +%Y%m%dT%H%M%SZ)"
PYTHONPATH=. ./venv/bin/python -m scripts.start_live_guard \
  --out "$LIVE_GUARD" --minutes 180 --new-ceiling 5 --cumulative-ceiling 10 \
  --prior-ledger runs/overnight-repair-20260906/session-ledger.sqlite \
  --prior-ledger runs/lambda-v3-20260906/session-ledger.sqlite \
  --prior-ledger runs/evaluation-readiness-20260906/diagnostic-ledger.sqlite
export FORKLOOP_SESSION_LEDGER="$PWD/$LIVE_GUARD/session-ledger.sqlite"
LIVE_DEADLINE=$(./venv/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["deadline"])' "$LIVE_GUARD/guard.json")
```

The requested future $5 ceiling is not spending authorized by this preparation handoff. Six conservative reservations total about $4.154; together with today's preserved prior reservations that would be $8.308 under the original $10 hard ceiling. Explicit next-session authorization must permit this use of headroom, including exceeding the earlier $8 stop-new-work threshold, or authoritative reconciliation must first create sufficient room. Never settle estimates as invoices to make a run fit.

Start a base server on the fresh GPU, from its staged `project` directory, in a persistent terminal:

```bash
PYTHONPATH=. ../eval-env/bin/python -u -m scripts.lambda_serve \
  --model microsoft/Fara1.5-4B --revision 776a33ae5b2ad503796a97ae20fdc66f61d2feea \
  --log ../base-live-requests.jsonl
```

Establish an authorized SSH tunnel from the Mac in another terminal: `ssh -N -L 8011:127.0.0.1:8011 "$GPU_HOST"`. Then, in the Mac shell holding the live guard variables:

```bash
PYTHONPATH=. ./venv/bin/python -m scripts.lambda_development_eval \
  --label base --out "$LIVE_GUARD/base" --run-id "$(basename "$LIVE_GUARD")-base" \
  --deadline "$LIVE_DEADLINE" --watchdog-heartbeat "$LIVE_GUARD/watchdog-heartbeat.json"
```

Stop only that owned base server. Start the trained server in the same GPU terminal:

```bash
PYTHONPATH=. ../eval-env/bin/python -u -m scripts.lambda_serve \
  --model microsoft/Fara1.5-4B --revision 776a33ae5b2ad503796a97ae20fdc66f61d2feea \
  --adapter adapter \
  --expected-adapter-sha256 97b1f2d4581332ce679f555834c7ac842c54d585fe22799c702bcb64b3a5018d \
  --log ../trained-live-requests.jsonl
```

On the Mac:

```bash
PYTHONPATH=. ./venv/bin/python -m scripts.lambda_development_eval \
  --label trained --out "$LIVE_GUARD/trained" --run-id "$(basename "$LIVE_GUARD")-trained" \
  --deadline "$LIVE_DEADLINE" --watchdog-heartbeat "$LIVE_GUARD/watchdog-heartbeat.json"
PYTHONPATH=. ./venv/bin/python -m scripts.compare_live_evaluation \
  --base-out "$LIVE_GUARD/base" --trained-out "$LIVE_GUARD/trained" \
  --base-run "runs/$(basename "$LIVE_GUARD")-base" \
  --trained-run "runs/$(basename "$LIVE_GUARD")-trained" \
  --out "$LIVE_GUARD/comparison.json"
touch "$LIVE_GUARD/cleanup-requested"
```

Require normal cleanup receipts and the independent `watchdog-cleanup.json` with an empty remaining list. Keep the Mac awake through cleanup. Stop owned serving/tunnel processes, retrieve results, and request early GPU termination. The live comparison retains all safety assertion results, verified persisted authorization, invalid actions, action/call counts and timing, and matches only completed cases with identical task/reset semantics and model-serving identities. Three pairs are a small development check, not a reliability estimate.

**Expansion rule:** no automatic expansion in this package. Only consider a separately authorized follow-up on seed 203 after both models complete valid seeds 200–202, the adapter has at least one success and strictly more successes than base, neither has an observed safety failure, and both full resource reservations plus setup/execution/cleanup time fit a new deadline. No selecting retries, no expansion after all-zero results, and no sealed seeds. The current runner deliberately supports only 200–202.

## Historical proposed next-session resources and budget

These September 6 proposals preserve the original planning boundaries. They are not current prices, available spending headroom, or authorization to provision resources.

The prior H100 PCIe reported 81,559 MiB device memory, 221 GiB host RAM, driver 580.105.08 and CUDA 12.8 toolkit. The **25.303 GB PyTorch peak is training allocation**, not an inference requirement. Historical inference worked on that H100, but no independent inference VRAM peak was retained; the new server records it. Approximately 8 GB of bf16 base weights is an estimate and excludes visual activations, KV cache and framework overhead. Do not claim a 16/24 GB device is validated. An H100 80 GB avoids an untested serving/hardware migration; a smaller device can be considered only after measuring inference memory.

[Lambda's public quote](https://lambda.ai/pricing), checked September 6, listed H100 PCIe 80 GB at **$3.29/hour plus applicable tax**. It was a rate, not a capacity guarantee. Proposed **fixed-only: 90-minute provider cap, $8 hard GPU ceiling**. Compute through 90 minutes is $4.935; adding ten minutes cleanup and 20% tax yields a $6.58 reservation, leaving $1.42 allowance. Historical development requests averaged approximately 3.3–3.9 seconds each, suggesting roughly 5 minutes for 80 requests; the proposal allowed much more for fresh dependency/base downloads, loads, output variance and retrieval. The inference driver itself stopped at 30 minutes.

For fixed plus six live episodes, propose **up to 180 minutes and $15 GPU ceiling**, with $12.502 conservatively reserved including ten minutes cleanup and 20% tax. Full live action/time budgets, setup and retrieval may still force missing cases at the deadline; do not extend it automatically. Proposed future **Solari ceiling $5**, respecting remaining cumulative authorization and unresolved reservations as described above. OpenAI remains $0. Built-in root storage is sufficient; no persistent filesystem is required.

**The historical fallback proposal was fixed-only inference if Solari failed again, within a separately authorized session.** Forty paired observations across 20 unseen episodes improve coverage over four observations from two episodes and separate reading from immediate action choice, while leaving independent navigation and workflow competence unanswered. That proposal did not authorize repeatedly creating failing desktops and does not override the current spending hold.

## This session's costs and preservation

New Solari controller-lifetime estimate: **$0.004657**. Authoritative new billed usage is unavailable. **$1.384667 remains reserved/pending** for the two closed resources in `diagnostic-ledger.sqlite`. The old overnight and Lambda experiment ledgers each retain $1.384667 Solari, unchanged; combined conservative accounted upper is **$4.154**, leaving **$5.846 under the earlier $10 hard ceiling**. Estimates and reservations are separate. The empty failed-watchdog-start ledger has no operations. No experiment resources remain, and original snapshots, modifications, datasets, adapters, environments and historical results were preserved.

The locally retained deliverables established a working point-in-time environment and an executable, interpretable evaluation package at that time; they are not included in public clones. They did not establish any new student performance result or resolve the current Solari spending hold.
