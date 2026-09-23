# Protocol: live image-detail comparison (pre-registered 2026-09-23)

Committed before any Solari allocation for this experiment. Nothing below changes after the
first cell starts. Any departure will be reported as a deviation in the results document.

## Hypothesis

On the full `resolve_denial` workflow, a GUI agent that receives screenshots at
`image_detail: high` succeeds more often than the same agent at `image_detail: low`.
Low detail fits each image inside 512 × 512 before the model sees it. Teams switch to it to
save tokens, so a regression test should catch it if it breaks the agent.

Prior evidence is offline and comes from a different model. On 20 recorded authorization
screens, `gpt-5.6-luna` typed the exact authorization 19/20 times at high detail and 0/20 at
low detail ([study](frozen-v3-evaluation-results.md)). This experiment uses **`gpt-6-luna`**,
per the owner's instruction on 2026-09-23 (given before this protocol was written). No
`gpt-6-luna` episode on this task has been run, so the prior evidence predicts the direction,
not the size of the effect.

## Arms

`configs/luna-image-detail.yaml`. Both arms use the options of the "Luna workflow prompt v5"
arm of `configs/denial-navigation.json`, with `model: gpt-6-luna` substituted:
`system_prompt_file: ../forkloop/policies/prompts/hosted_gui_agent_v5.md`,
`prompt_style: compact`, `image_max_side: 1280`, `history_k: 16`, `prev_screenshot: true`,
`history_notes: false`, `hosted_reasoning: true`, `max_tokens: 4096`, `timeout_s: 120`,
`reasoning_effort: high`.

| Arm | Name | `image_detail` | `configuration_sha256` |
| --- | --- | --- | --- |
| A | Luna high detail | `high` | `45eabeae8ee60c9777348648de5cf0190cacf3cb46c83306aad582209a7fdf72` |
| B | Luna low detail | `low` | `7ade20a70d7a60022478cd06a69d0826f568d00d3df058cd158ef606295e4b95` |

Programmatic diff of the two arms (every top-level and `options` key, flattened):

```
differing keys: {'options.image_detail': ('"high"', '"low"')}
```

Diff of arm A against the reference v5 arm in `configs/denial-navigation.json`:

```
{'options.model': ('"gpt-5.6-luna"', '"gpt-6-luna"')}
```

Policy source SHA-256 (both arms): `997f88161359da33869cdc8163b8aeae749e455b0dfe98cb442fb2d7217238d5`.

**Compatibility check before registration.** Two guarded requests went to `gpt-6-luna` with a
synthetic 1280 × 720 image of made-up text, one at each detail level. No task data or seed was
involved. Both levels were accepted: low detail used 196 prompt tokens and high detail 1,128.
The cost was $0.0007, recorded in the session ledger. No other model call or allocation
preceded this commit.

## Seeds and split

`heldout_seeds` 100300–100313 (14 seeds), family `resolve_denial`, world `claims-ops-v1`.

Evidence that these seeds were never used:

- Every recorded episode directory under `runs/` is `train` 0–141 or `train` 200–229. No
  episode on any `heldout_seeds` or `heldout_compositions` seed exists.
- No SFT dataset in `data/*.jsonl` contains a `heldout_seeds` task. The provenance file
  `data/sft_f3_25_v3.provenance.json` records the reserved block as unused.
- The only other `heldout_seeds` references are the offline test fixture `100000` and the
  block `100500–100529`, which is reserved for a final evaluation (contracts §13). Both are
  excluded here.
- Claim numbers repeat modulo 50000 across splits. The mirrored `train` seeds 300–313 were
  never used either.

## Budget, reset and execution

- Budget per episode: 120 charged actions, **1200 s**. The 110 `gpt-5.6-luna` v5 episodes on
  record (`runs/luna-high-v5-s0-19`, `runs/luna-v5-f3-s20-99`) ran 226–242 s at the median
  and 807 s at most. `gpt-6-luna`'s speed on this task is unmeasured, and low detail may need
  more steps. A tie decided by the time budget would be uninformative, so the budget is
  1200 s rather than 900 s.
- `reset_mode: fork` from the Sept 15 golden `snap_dlft9omnpkyw`: a fresh desktop per cell.
- One attempt per cell and a fresh policy instance per cell. Arm order alternates by seed
  index within each half: A first on even indices.
- Execution: two halves run in parallel on separate desktops (the Starter cap is 2 concurrent
  VMs). `runs/image-detail-live-20260923/luna-image-detail-a.yaml` has seeds 100300–100306
  and `…-b.yaml` has 100307–100313. The half configs differ from the main config only in
  `seeds` and the prompt path's relative depth. Their per-arm `configuration_sha256` values
  equal the ones above.
- Lifetime safety: `FORKLOOP_SOLARI_MAX_LIFETIME_MIN=45`, `FORKLOOP_SOLARI_ACCEPT_BALANCE_BOUND=1`,
  and one shared `forkloop reap --older-than-min 50` loop for the whole session. A
  ledger-wide `reap` runs only after both halves finish. Afterwards an account listing must
  show 0 forkloop machines.

## Metrics and analysis

- **Primary:** full-task success, meaning a verdict with reward 1.0. Unscored cells
  (`INFRA_ERROR`, `ORACLE_ERROR`, missing verdict) are excluded, never counted as failures.
- **Test:** exact two-sided McNemar on the discordant pairs, pooled over both halves, with
  α = 0.05. Only pairs with both cells scored and reset equivalence holding enter the test.
  Arm A is named better only if p < 0.05 and A-only pairs outnumber B-only pairs. Otherwise
  the result is reported as no significant difference, with the observed counts. For
  reference: 6 of 6 discordant pairs one way gives p = 0.031, and 8 vs 1 gives p = 0.039.
- **Secondary (descriptive, no test):** the `ui_milestones` staircase per arm (highest
  milestone reached), reason codes, steps, wall seconds, reset seconds, and model tokens and
  cost per cell. The number of pairs where reset equivalence held is also reported.

## Stopping rule

All 28 planned cells, one attempt each. No scored cell is rerun, and no seeds are added
after any outcome is seen. A cell that ends unscored because of an infrastructure failure
may be retried **once**, on a fresh fork, as a single-seed run of the same half config. It is
marked "retried" in the report. If the retry also fails, the pair stays unscored and is
excluded. If a spending stop or an operator abort interrupts the run, the report states
exactly which cells completed. Missing cells are not replaced.

## Projected spend

Session ledger `runs/image-detail-live-20260923/session-ledger.sqlite`: Solari ceiling $6
(stop $4.80), OpenAI ceiling $25 (stop $22.50), GPU $0.

- **OpenAI.** Recorded v5 episodes repriced at `gpt-6-luna` rates ($0.10 input / $0.50
  output per 1M tokens at ≤ 272K prompt tokens): median $0.027 and at most $0.055 per
  episode. Planning figure: at most $0.15 per cell (about 3× the recorded maximum), so
  **≤ $4.20 for 28 cells**, with an expected value near $1. The guard reserves the full
  1.05M-token context per request before sending it ($0.265 at most), then reconciles to the
  usage the provider reports.
- **Solari.** The ledger reserves $0.134/h × (45 + 10) min = $0.123 per desktop, so 28 cells
  reserve **$3.44** at most, below the $4.80 stop. At about 1.5 min reset, up to 20 min per
  episode and about 3 min of setup, the expected compute cost is **$0.8–1.6**.

Both projections fit inside the caps, so the seed count stays at 14.
