# Historical Lambda v3 experiment: inconclusive because Solari desktop health RPC timeouts prevented matched live evaluation.

Lambda v3 student-learning experiment — September 6, 2026.

**Historical record, not current operating instructions.** The [current product handoff](../product-handoff.md) is authoritative. The Solari spending hold remains unresolved; this document authorizes no new GPU, Solari, or other paid work. Any future execution requires fresh explicit authorization and resolution of the applicable hold.

**Public-source scope:** all run evidence, datasets, images, checkpoints, archives, ledgers, and archived helper scripts referenced here are retained locally and not distributed in the public source release. Artifact identifiers beginning `projects/forkloop/` are relative to the repository root, not download links. Historical command paths are relative to their stated working directories; the required local artifacts and environments are absent from public clones.

The repaired inputs supported the completed training run and showed a narrow gain in immediate authorization typing on fixed development observations. The main adapter typed all three fitting targets and one of two development targets; the matched base typed none. However, the base seed-140 raw `pause_and_memorize_fact.fact` already contained the correct authorization (`AUTH-61H18482`), which the runtime mapped to a short wait. The 1/2 versus 0/2 development result measures immediate-entry behavior, not newly acquired reading ability. However, the first live setup failed before any model action, and one bounded readiness diagnostic reproduced the health-RPC failure after reconnecting. There are **zero valid matched live episodes**, so complete workflow success, invalid-action rate, collateral changes, and task action/time comparisons remain unmeasured. These are unavailable results, not 0/3 model failures.

The work is preserved and billable experiment resources are cleaned up. Lambda confirmed termination at **18:24:33 UTC / 08:24:33 HST**. The empty dedicated filesystem was deleted at **18:25:40 UTC / 08:25:40 HST**. Both Solari VMs were killed; the original golden snapshot and the two unrelated Lambda filesystems remain.

## Results and interpretation

| Measure | Matched base | Separate eight-example diagnostic adapter | Main 25-episode adapter |
|---|---:|---:|---:|
| Recorded-action agreement on the fitting subset | 1/8 | 8/8 | 6/8 |
| Exact authorization action on fitting examples | 0/3 | 3/3 | 3/3 |
| Recorded-action agreement on separate paired development observations | 0/4 | Not tested | 3/4 |
| Exact authorization action on paired development observations | 0/2 | Not tested | 1/2 |
| Exact authorization action on current-only development observations | 0/2 | Not tested | 0/2 |
| Completed valid live development episodes | 0 | Not tested | 0 |
| Complete task success, verified submitted authorization, invalid actions, collateral changes, episode action count/time | Unmeasured | Not tested | Unmeasured |

The fitting subset was declared before training: original dataset indices **0, 1, 10, 44, 60, 100, 139, 218**, with five navigation examples and three authorization examples. Its success threshold was at least 6/8 actions and all 3/3 authorization targets, within 120 optimizer steps. The diagnostic adapter passed 8/8 and was never used to initialize the main run.

The fixed development cases came from existing successful teacher trajectories for **seeds 140 and 141**: the first observation and first authorization-type observation from each. They were selected before the new model probes. Their seeds, exact screenshot hashes, and authorization targets do not overlap the 25-episode training set. None of the five authorization probes contains its exact target value in model-visible text.

Direct visual inspection confirmed that the previous screenshot shows the authorization document and the current screenshot shows the matching appeal form with a blank, focused authorization field. With paired input, the main adapter correctly emitted `AUTH-61H18482` for seed 140. For seed 141 it emitted **`AUTH-57A40946` instead of `AUTH-57A40046`**, a one-digit error. With current-only input, it clicked back to the OpenEMR tab in both cases. That condition removes the visible value, so returning to the document can be reasonable; its 0/2 immediate-entry score is not itself a complete-task failure.

Recorded-action agreement is narrower than task correctness: a different valid navigation route can score as a miss. The main adapter's other fitting misses were a different navigation click at index 60 and a wait at index 100. The fixed-observation gain concerns immediate authorization entry. Base seed 140 already emitted the exact number in a structured memory action; neither newly acquired reading ability nor reliable workflow competence follows from these two cases. The runtime memory-action mapping remains unchanged for both models.

Every base/main fixed pair has identical request, image, expected-action, and decoding checks. Results and raw outputs are in `projects/forkloop/runs/lambda-v3-20260906/matched-comparison.json` (retained locally; not distributed), with input separation and inspection evidence in `projects/forkloop/runs/lambda-v3-20260906/development-separation.json` (retained locally; not distributed), `projects/forkloop/runs/lambda-v3-20260906/authorization-text-leakage-check.json` (retained locally; not distributed), and `projects/forkloop/runs/lambda-v3-20260906/development-visual-inspection.json` (retained locally; not distributed).

**Seeds 100500–100529 remain sealed.** Historical current-only 0/30 runs were not used as a matched baseline. No new OpenAI teacher or judge calls were made.

## Why live evaluation stopped

The declared comparison was base versus main on development seeds **200–202**, best-of-one, one VM at a time, 120 actions and 900 seconds per episode, with the same observation and decoding contract. Neither model received expected answers.

The base seed-200 setup took **240.39 seconds** and ended with `ResetError: restore: TimeoutError`. Solari reported the VM running, but setup never produced an episode manifest and the policy made **zero requests**. The session-scoped cleanup killed that VM and confirmed no remaining owned resources.

A single additional infrastructure diagnostic used the same golden snapshot and reservation ledger. The create request returned after **77.70 seconds**; WebSocket connection succeeded in about **0.31 seconds**. A health RPC then timed out at 10 seconds. The diagnostic forcibly closed and reconnected the channel once, and the next health RPC also timed out at 10 seconds. That VM was killed too. The diagnostic ran no model and generated no task seed.

The existing readiness loop checks its elapsed-time limit between RPCs, while its configured RPC timeout can exceed that limit. Shortening the diagnostic calls and forcing one reconnect did not restore readiness. This identifies a reproducible **desktop health/readiness RPC blocker**, not a demonstrated global Solari outage or a student-policy failure. No unverified readiness patch was applied to the shared backend.

No model episode was retried or selected from multiple attempts. The additional VM was explicitly an infrastructure diagnostic after a zero-action setup failure. Further live attempts and the optional development expansion were stopped after this bounded diagnosis. Complete model trajectories do not exist because model execution never began; all setup records, errors, resource lifetimes, and cleanup confirmations are retained.

Evidence: `projects/forkloop/runs/lambda-v3-20260906/base-live-results.json` (retained locally; not distributed), `projects/forkloop/runs/lambda-v3-20260906/base-live-cleanup.json` (retained locally; not distributed), `projects/forkloop/runs/lambda-v3-20260906/readiness-diagnostic-events.json` (retained locally; not distributed), `projects/forkloop/runs/lambda-v3-20260906/readiness-diagnostic-result.json` (retained locally; not distributed), and `projects/forkloop/runs/lambda-v3-20260906/live-evaluation-blocker.json` (retained locally; not distributed).

## Training and verification

The main run started from the original pinned **`microsoft/Fara1.5-4B` revision `776a33ae5b2ad503796a97ae20fdc66f61d2feea`**, with a fresh adapter. The original dataset remains unchanged: **25 episodes, 1,758 examples, 3,491 image references, 1,758 referenced image paths**. Only remote image paths were rewritten; input/target semantics and image bytes were verified.

| Setting or observation | Actual value |
|---|---|
| Main optimizer steps | **411 / 440 planned** |
| Epochs | **1 complete + 0.869169 of the second**, 1.869169 total |
| Example presentations | **3,286** |
| Stop reason | Original 380-minute wall cap, checked at optimizer boundaries |
| Training elapsed time including final save | **22,852.34 seconds**, 380.87 minutes |
| Mean throughput | **55.60 seconds / optimizer step** |
| First / final step loss | **1.627073 / 0.139364** |
| Peak PyTorch CUDA allocation | **25.303 GB** |
| Gradients | All finite; minimum recorded norm **0.573197** |
| LoRA | Rank 16, alpha 32, dropout 0.05 |
| Target modules | `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj` |
| Optimization | bf16, batch 1, accumulation 8, learning rate 1e-4, gradient checkpointing, SDPA |
| Training seed | **0** |
| Tokenized sequence length | 3,203–4,314 tokens |

Two epochs remained the planned recipe. Full-data throughput showed that completing all 440 steps would consume evaluation/retrieval time, so the existing cap was retained. **Do not describe this run as two completed epochs.** Recovery adapters at steps 100, 200, 300, and 400 were copied back during training; the final adapter and all seven associated files were also hash-verified. These are adapter/processor checkpoints, not full optimizer-state resume snapshots.

The real eight-example GPU smoke completed eight finite forward/backward updates in **55.08 seconds**, with nonzero gradients, all **256 adapter tensors changed**, maximum absolute change **0.0004515752**, and peak allocation **25.036 GB**. Its saved adapter reloaded and generated through the intended HTTP serving path. Actual serving and training prompt-prefix hashes matched on the checked observations. The single smoke reload authorization output had one wrong digit; that check established serving operation, not correctness.

The separate fitting adapter completed **120 steps / 15 epochs** in **753.64 seconds**, with final loss **0.000661394** and peak allocation **24.953 GB**. Its 8/8 action result established trainability only.

Verification completed: **39 focused offline tests**, **3 tests in the GPU environment** covering the real processor contract and accumulation/progress behavior, **4 evaluation-harness tests**, and **1 serving concurrency integration test**. The last test used a CPU-only fake generator through the real FastAPI request path, so it did not contend with GPU training. These checks are software evidence, not workflow-success evidence.

Full configurations, token statistics, losses, and gradient norms: `projects/forkloop/runs/lambda-v3-20260906/final-artifacts/project/checkpoints/v3-main25/train_summary.json` (retained locally; not distributed); recovery verification: `projects/forkloop/runs/lambda-v3-20260906/checkpoint-verifications.jsonl` (retained locally; not distributed).

## Environment and source changes

Verified instance configuration: Utah `us-west-3`, `gpu_1x_h100_pcie`, provider rate **$3.29/hour**. Hardware inspection found one H100 PCIe reporting **81,559 MiB**, **221 GiB system RAM**, 26 provider vCPUs, a roughly **993 GiB ext4 root volume**, NVIDIA driver **580.105.08**, CUDA toolkit **12.8**, and driver CUDA compatibility **13.0**.

Work ran in a fresh remote experiment directory on the root volume. The attached dedicated filesystem remained empty. A dedicated Python **3.11.16** environment used torch **2.7.1+cu128**, torchvision **0.22.1+cu128**, transformers **5.16.1**, peft **0.20.0**, accelerate **1.14.0**, Pillow **12.3.0**, huggingface-hub **1.30.0**, FastAPI **0.141.1**, and uvicorn **0.52.4**. The same compatible Transformers environment served all variants; vLLM and a merged full-model export were unnecessary. The original system environment was preserved until the authorized instance termination.

The Mac evaluation environment used Python **3.11.14**, `solari-core`, `solari-sandbox`, and `solari-desktop` **0.2.0**, httpx **0.28.1**, and Pillow **12.3.0**. Full version records are `projects/forkloop/runs/lambda-v3-20260906/package-freeze.txt` (retained locally; not distributed) and `projects/forkloop/runs/lambda-v3-20260906/local-evaluation-environment.json` (retained locally; not distributed).

The working tree was already dirty at Git HEAD `973a90de10f3b220a8f09cd2af5a97c13385ce43`. Current source, including required untracked repairs, was transferred; a clean clone of HEAD would not reproduce this experiment. No commits or pushes were made. Existing local datasets, runs, checkpoints, environments, and modifications were preserved.

Focused changes:

- Corrected epoch-local gradient accumulation and six-example tail scaling; recorded actual example/epoch progress; added finite-gradient/loss checks and smoke weight-change evidence. The first full main epoch ended at exactly **1,758 examples / 220 optimizer steps**.
- Added a loopback-only Transformers serving adapter and isolated fixed probes. An early probe helper incorrectly shared a navigation-macro queue across independent rows. Those two initial control files were explicitly discarded; all reported controls use a fresh policy per observation. The regression test verifies separate model requests.
- Added the bounded development harness and external metrics. Exact text typing is distinguished from a deterministically verified submitted authorization field; zero appeals are not mislabeled as duplicate side effects. No live scores were fabricated when setup failed.
- Serialized serving requests so a canceled client request cannot overlap the next GPU generation. The input/generation function body is AST-identical to the earlier sequential base control; both live variants were prepared to use the same updated wrapper. Real threaded HTTP testing verified serialization and lock release after an exception.
- Added a standalone, reserved readiness diagnostic. Its reconnect experiment is retained as evidence, not promoted as a successful shared-backend fix.

The ten focused project source files match the retrieved bundle: `projects/forkloop/runs/lambda-v3-20260906/focused-changes-manifest-final.json` (retained locally; not distributed). The tracked working-tree patch is `projects/forkloop/runs/lambda-v3-20260906/source-diff-final-working.patch` (retained locally; not distributed); untracked source is included as actual files in the bundle, not merely assumed to exist in that patch. The discarded-control notice and serving compatibility proof are `projects/forkloop/runs/lambda-v3-20260906/discarded-probe-notice.json` (retained locally; not distributed) and `projects/forkloop/runs/lambda-v3-20260906/serving-serialization-change.json` (retained locally; not distributed).

## Artifacts and hashes

A compact companion bundle of local probes, setup failures, ledgers, receipts, source changes, development images, and this handoff is `projects/forkloop/runs/lambda-v3-20260906/local-evidence.tar.gz` (retained locally; not distributed). Its file hashes are in `projects/forkloop/runs/lambda-v3-20260906/local-evidence-manifest.json` (retained locally; not distributed).

Primary archive: `projects/forkloop/runs/lambda-v3-20260906/artifacts-final.tar.gz` (retained locally; not distributed) — **818,706,469 bytes**, **2,014 verified files**. Its full file manifest is `projects/forkloop/runs/lambda-v3-20260906/artifact-manifest.json` (retained locally; not distributed), and its verified extraction is `projects/forkloop/runs/lambda-v3-20260906/final-artifacts`. The archive includes every saved adapter, processor/tokenizer assets, remote logs, staged training data and necessary screenshots, environment details, source, and tests. The pinned base weights can be downloaded separately; no merged model is required.

The final adapter is `projects/forkloop/runs/lambda-v3-20260906/final-artifacts/project/checkpoints/v3-main25/final`. Probe results, budgets, setup failures, and provider receipts are in `projects/forkloop/runs/lambda-v3-20260906` (retained locally; not distributed). Six development screenshot assets were copied into `projects/forkloop/runs/lambda-v3-20260906/development-probe-images` (retained locally; not distributed); `projects/forkloop/runs/lambda-v3-20260906/development-fixed.portable.jsonl` (retained locally; not distributed) preserves row semantics and image bytes with local artifact paths. Original files were not changed.

| Artifact | SHA-256 |
|---|---|
| Main final adapter | `97b1f2d4581332ce679f555834c7ac842c54d585fe22799c702bcb64b3a5018d` |
| Diagnostic final adapter | `2396b7c648d70d7156813ffa4b3bfa3fd8cc3a701d49f0e2011ca2b0ea8c25ca` |
| Original v3 dataset | `194022b808f8e393d784da24fdfcb6fbf634d4b7d6deafb25faa19781863947b` |
| GPU dataset with rewritten image paths | `3dfa881edaaeb423adf6f9896b9d9a420abd91c6044e59ac55e707a8dfb12679` |
| Corrected launch source manifest | `2b22c4b9d332e66a24232c8b0bff8e416200e83aeca516bd91702044272b8fe1` |
| Remote artifact archive | `774112cf2ceb26754f88c879a7f07a5ef135d9e7749b38d45ab76151160ae1cc` |
| Remote artifact manifest | `7c341143742dbe82de9c62c9e4f6dc822a59355cf91adde50bafea8545c587fe` |

The full archive and every member hash passed. The remote archive and the local companion evidence bundle passed scans for configured secret values. The supplied Lambda key stayed in the ignored local `.env` with mode 0600; it was not transferred or committed. See `projects/forkloop/runs/lambda-v3-20260906/final-artifact-verification.json` (retained locally; not distributed) and `projects/forkloop/runs/lambda-v3-20260906/local-evidence-secret-scan.json` (retained locally; not distributed).

## Historical reproduction record

The commands below record the executed configuration using retained local source and artifacts; they are not runnable instructions for a public clone or authorization to restart spending. The [current product handoff](../product-handoff.md) governs any future work, and the Solari hold remains unresolved. Only after fresh explicit authorization and resolution of the applicable hold could a new environment and output directory be prepared, without overwriting the completed artifacts. The former instance is terminated. Training/serving helpers assume the archived remote directory layout; adapt paths with the retained path/provenance checks. The adapter configuration contains the original local cache path, so the pinned base revision must be loaded explicitly before applying the adapter.

The exact main command is preserved in `projects/forkloop/runs/lambda-v3-20260906/run_main.sh` (retained locally; not distributed):

```bash
#!/usr/bin/env bash
set -euo pipefail
cd project  # Relative to the reconstructed remote experiment directory.
export OMP_NUM_THREADS=8 TOKENIZERS_PARALLELISM=false
../train-env/bin/python - <<'PY'
import json,time,datetime
from pathlib import Path
p=Path('runs/lambda-v3/main-authorization-gate.json')
g=json.loads(p.read_text());assert g['smoke_passed'] and g['fit_passed'] and g['budget_fits']
assert datetime.datetime.now(datetime.timezone.utc).isoformat()<g['latest_start_utc']
assert not Path('checkpoints/v3-main25').exists()
PY
MODEL=$(cat runs/lambda-v3/base-model-path.txt)
NOTE='Credentials for OpenEMR: username admin, password pass. Click the Username field, type admin, click the Password field, type pass, click Login.'
exec ../train-env/bin/python -u -m train.train_lora --model "$MODEL" --data data/sft_f3_25_v3.gpu.jsonl --output-dir checkpoints/v3-main25 --prompt-style fara --coord-space norm1000 --history-k 8 --max-image-side 1280 --system-prompt-file forkloop/policies/prompts/fara_no_user_v1.md --instruction-note "$NOTE" --nav-macro --epochs 2 --max-steps 440 --batch-size 1 --grad-accum 8 --lr 1e-4 --lora-r 16 --lora-alpha 32 --lora-dropout 0.05 --dtype bf16 --attn sdpa --max-minutes 380 --log-steps 10 --save-steps 100
```

The smoke and fitting commands are preserved in `projects/forkloop/runs/lambda-v3-20260906/run_smoke.sh` (retained locally; not distributed) and `projects/forkloop/runs/lambda-v3-20260906/run_fit.sh` (retained locally; not distributed). In the historical remote directory layout, `train-env/bin/python remote_prepare.py` verified the source manifest and dataset, rewrote image paths, and downloaded the pinned base. The diagnostic gate preceded the main script. A wall-time cap can produce a different final step count on different hardware; the locally retained adapter preserves the exact evaluated weights.

The original serving commands, from the reconstructed remote root, are:

```bash
bash start_server.sh base-reproduction
# Stop that server before loading the final adapter:
bash start_server.sh trained-reproduction --adapter checkpoints/v3-main25/final
```

Both bind remote loopback port 8011; use an authorized SSH tunnel to local port 8011. Greedy decoding is temperature 0, best-of-one, maximum 512 output tokens. Both policies use previous/current images in temporal order, first step current-only, history 8, norm1000 coordinates, image max side 1280, navigation macros on, `fara_no_user_v1.md`, and the same synthetic OpenEMR login note.

Historical fixed-probe example on the Mac, starting at the repository root after the appropriate server was ready (requires retained local artifacts and environment):

```bash
cd projects/forkloop
PYTHONPATH=. ./venv/bin/python scripts/lambda_fixed_probe.py --label trained-reproduction --out /private/tmp/forkloop-v3-reproduction-fitting.json
PYTHONPATH=. ./venv/bin/python scripts/lambda_fixed_probe.py --label trained-reproduction --data runs/lambda-v3-20260906/development-fixed.portable.jsonl --indices 0,1,2,3 --out /private/tmp/forkloop-v3-reproduction-development.json
PYTHONPATH=. ./venv/bin/python scripts/lambda_fixed_probe.py --label trained-current-only-reproduction --data runs/lambda-v3-20260906/development-fixed.portable.jsonl --indices 1,3 --current-only --out /private/tmp/forkloop-v3-reproduction-current-only.json
python3 runs/lambda-v3-20260906/compare_results.py
```

The live attempt used `scripts/lambda_development_eval.py --label base` after sourcing the existing local credential file and setting `FORKLOOP_SESSION_LEDGER` to this session's ledger. The historical harness required a fresh ledger/run ID/output and expired at that session's absolute deadline. A later revision accepted an explicit new deadline and verified the session watchdog and model identity; [evaluation-readiness-handoff.md](evaluation-readiness-handoff.md) records that historical revision, matched budgets, and deterministic scorer, not current restart instructions. The [current product handoff](../product-handoff.md) remains authoritative. More reproduction context is in `projects/forkloop/runs/lambda-v3-20260906/reproduction-notes.md` (retained locally; not distributed).

## Spending and confirmed cleanup

| Service | Evidence / estimate | Independent ceiling |
|---|---|---:|
| Lambda compute | Authenticated usage dashboard reports **$24.16**, **7.35 hours**, 11:00–18:20 UTC; final tax/invoice not available | $50 including attributable storage/tax |
| Lambda conservative allowance | Earlier 10:54 clock through provider termination confirmation, rounded up: **$24.73 compute**, **$29.68 with 20% tax allowance**; adding the untouched $10 uncertainty reserve gives **$39.68**, below the ceiling | Included above |
| Dedicated filesystem | **$0 estimated**: inspected empty before termination, then deletion confirmed; no ongoing experiment filesystem charge | Included above |
| Solari | Two create attempts; **339.05 seconds** conservative controller lifetime, **$0.01262 estimated compute**. Both closed; **$1.38467** reservation upper bound retained pending actual usage | $10; stop-new-work threshold $8 |
| OpenAI API | **0 new calls, $0** | $30; stop threshold $27 |
| Other paid services | **$0** | $0 |

Stage questions and stopping decisions are recorded in `projects/forkloop/runs/lambda-v3-20260906/stage-decisions.jsonl` (retained locally; not distributed).

Reservations are not charges. The repaired SQLite ledger retains **$45.9268** for the closed Lambda operation and **$1.38467** for the closed Solari operations as pending invoice upper bounds; estimated costs were not falsely settled as authoritative actual charges. Previous-session ledgers were preserved. See `projects/forkloop/runs/lambda-v3-20260906/final-cost-summary.json` (retained locally; not distributed) and `projects/forkloop/runs/lambda-v3-20260906/session-spend.json` (retained locally; not distributed).

The original provider deadline was **20:00 UTC / 10:00 HST**, with a locally detached, caffeinated watchdog using the provider termination API. It survived disconnected SSH sessions; it still depended on Mac power and provider connectivity. Manual termination completed well before that deadline. Official references: [Lambda billing](https://docs.lambda.ai/public-cloud/billing/), [Lambda filesystem lifecycle/API](https://docs.lambda.ai/public-cloud/cloud-api/), and [Solari pricing](https://docs.getsolari.com/pricing). Provider usage observations and actual cleanup receipts take precedence over estimates.

Cleanup proof:

- Experiment instance: provider status **terminated**, `projects/forkloop/runs/lambda-v3-20260906/termination-confirmed.json` (retained locally; not distributed) and `projects/forkloop/runs/lambda-v3-20260906/termination.log` (retained locally; not distributed).
- Dedicated filesystem: verified empty, detached, deleted, and absent from the subsequent provider inventory; `projects/forkloop/runs/lambda-v3-20260906/filesystem-deletion-confirmed.json` (retained locally; not distributed).
- Both unrelated filesystems were preserved.
- Solari: no session-owned VMs remain; the original golden snapshot is preserved; `projects/forkloop/runs/lambda-v3-20260906/solari-final-cleanup-confirmed.json` (retained locally; not distributed).
- The experiment watchdog, artifact mirror, and SSH tunnel processes have stopped; `projects/forkloop/runs/lambda-v3-20260906/local-process-cleanup.json` (retained locally; not distributed). No unrelated processes or resources were killed.

## Historical next recommendation

**The recommendation at the time was to run the larger saved-state comparison, then a matched live development comparison under a new authorized inference budget.** [evaluation-readiness-handoff.md](evaluation-readiness-handoff.md) records the later point-in-time readiness check and frozen development package. This recommendation is historical, not permission to execute: the [current product handoff](../product-handoff.md) is authoritative and the Solari hold remains unresolved. The expensive training work is retained locally, not distributed. These fixed probes did not establish full workflow improvement or readiness for the sealed final evaluation.
