# Protocol: offline reading study, gpt-5.6-luna vs gpt-6-luna (pre-registered 2026-09-24)

Committed before any inference request for this study. Nothing below changes after the first
request. Any departure will be reported as a deviation in
[`reading-model-upgrade-results.md`](reading-model-upgrade-results.md).

## Hypothesis

On the same frozen screens, `gpt-6-luna` types the exact authorization number less often than
`gpt-5.6-luna` at high image detail.

Why this is worth testing: in the live image-detail comparison
([results](live-image-detail-comparison.md)), `gpt-6-luna`'s first written authorization was
exact in 3 of 14 run-1 episodes, and 6 of its 10 run-2 appeals stored a misread number.
`gpt-5.6-luna` with the same prompt got its first read right in 89 of 109 recorded episodes
(Sept 15). Those numbers come from different seeds, dates and letter renders, so they are
observational. This study pairs the two models on identical inputs.

**Prior context only, not an arm:** on Sept 16 `gpt-5.6-luna` typed the exact authorization on
19/20 of these states at high detail ([study](frozen-v3-evaluation-results.md#september-16-image-detail-and-safe-entry)).
That result is not reused: both arms are run now, on the same date, code and tier.

## Population

The frozen Sept 16 package `runs/evaluation-readiness-20260906/saved-dev-v3` (local):
40 observations from 20 successful `gpt-5.6-luna` workflow-v5 episodes on `train` seeds
101–139 (`runs/luna-v5-f3-s100-139`). Each episode contributes one **authorization state**
(the letter is on screen and the recorded next action typed the authorization) and one
**navigation state**. Authorization seeds: 101, 102, 104, 105, 106, 109, 111, 114, 115, 116,
121, 122, 123, 127, 131, 133, 134, 136, 138, 139.

| Package file | SHA-256 |
| --- | --- |
| `manifest.json` | `708511a23f24d0b8c5387cc070a190f9360569bb54fe0d3b343da55d0b0e307e` |
| `cases.jsonl` | `7b881466820d672bb6d1dd3d51a73c6af2693ff488c5bb79584fde43050ed1b9` |
| `labels.jsonl` | `5af543b02bb28083fc0d6fa875c930e1a7d6033ff53179be42fe1fa23f0f9745` |

Known bias, stated in advance: these states were reached and selected from `gpt-5.6-luna`'s
own successful episodes, and each carries that model's own eight-action history. They are
on-distribution for arm A. A gap in A's favour is therefore partly expected even without a
reading difference, and the navigation metric (imitation of A's recorded action) is biased
towards A by construction.

## Arms

Everything identical except the model string.

| Arm | Model |
| --- | --- |
| A | `gpt-5.6-luna` |
| B | `gpt-6-luna` |

Shared options (the Sept 16 high-detail arm): workflow prompt v5
(`hosted_gui_agent_v5.md`, SHA-256 `ae8f9cce…a8983`), `prompt_style: compact`,
`image_max_side: 1280`, `image_detail: high`, previous + current screenshot, `history_k: 8`,
`history_notes: false`, hosted reasoning, `reasoning_effort: high`,
`max_completion_tokens: 4096`, `service_tier: default` (standard), 120 s per request.

Both model aliases were confirmed served before registration with one text-only request each
(no image, no task data): the API returned `model: "gpt-5.6-luna"` and `model: "gpt-6-luna"`
(`runs/session-20260924/model-probe.txt`, cost $0.0000107). Two earlier probe requests were
rejected with HTTP 400 because the probe script sent `max_tokens` instead of
`max_completion_tokens`; the ledger keeps their reservations as uncertain.

**Request identity, checked programmatically at freeze time** (`preflight.json`):

- For every one of the 40 states, the flattened request bodies of the two arms differ in exactly
  one field: `["model"]`.
- Arm A's 40 request bodies are byte-identical (same SHA-256) to the Sept 16 high-detail arm's
  bodies, so today's code reproduces the Sept 16 request shape exactly.
- Each cell's `request_sha256` and `without_model_sha256` are in `protocol.json`; the run aborts
  on any request drift.

## Metrics and analysis

- **Primary:** exact parsed runtime `type` action on the 20 authorization states (the typed text
  equals the expected authorization). Compared by **exact two-sided McNemar** on the discordant
  pairs, α = 0.05. `gpt-6-luna` is called worse only if p < 0.05 and A-only pairs outnumber
  B-only pairs; otherwise "no significant difference" with the observed counts. For reference at
  20 pairs: 6-0 discordant gives p = 0.031, 8-1 gives p = 0.039, 5-0 gives p = 0.063.
- **Secondary, descriptive, no test claimed:** navigation agreement with the recorded action on
  the 20 navigation states (imitation of arm A's own past action, not correctness); every wrong
  typed value with its edit distance and a mechanical pattern class (dropped repeated character,
  dropped character, letter/digit confusion, other substitution, other); non-typing actions;
  parse failures and truncations; tokens, reasoning tokens, latency and usage-derived cost.
- Unscored cells (a request that fails after its retry) are excluded from both arms' pair.

## Execution, stopping and retries

- One attempt per cell. Arm order alternates by state within each observation kind (A/B, then
  B/A), in the original package order: 80 requests in total.
- **Retry rule:** a cell whose request fails with a transport error (an `httpx` transport error,
  a timeout, an `OSError`, or HTTP 429/5xx) is retried exactly once on a fresh policy instance,
  and the retry is logged in the cell. Any other provider, identity or accounting failure, or a
  transport error that fails its retry, stops the run; the remaining cells stay missing.
- Invalid, unparsable or truncated model outputs are scored failures, never retried.
- `results.json` is written once; the runner refuses to start if it exists, so no scored cell is
  rerun.

## Phase 3 decision rule (fixed now)

The live model-upgrade A/B runs only if **`gpt-6-luna` ≤ 14/20 exact typing and
`gpt-5.6-luna` ≥ 16/20** (a gap of at least 5 states). Otherwise it is skipped and the null is
reported. The decision is written into the results document before any Phase 3 work.

## Spend

Session ledger `runs/session-20260924/session-ledger.sqlite`: OpenAI ceiling $20 (stop $18),
Solari $8, GPU $0. The Sept 16 high-detail arm cost $0.035 for these 40 requests on
`gpt-5.6-luna`; `gpt-6-luna` lists at about half the token price. Projected: **about $0.06–0.15**.
The guard reserves a full 1.05M-token context per request before sending it ($0.53 for
`gpt-5.6-luna`, $0.27 for `gpt-6-luna`), sequentially, then reconciles to provider usage.

## Frozen artifacts

| Artifact | SHA-256 |
| --- | --- |
| Runner `runs/reading-model-upgrade-20260924.py` | `6017d3892090c74b01ccbe007c2a02e8e61e0d49188e83757f27750a719507f0` |
| Analyzer `runs/analyze-reading-model-upgrade-20260924.py` | `30c226595fae148e68993f5786d7c8e37bc1be775ee21f1e9d01680dd8f95bd0` |
| `runs/reading-model-upgrade-20260924/protocol.json` | `1cca846598b8e09d03f01f41e821c9af05c429d3559a4a5f2d65f9cc861ff608` |
| Policy `forkloop/policies/student.py` | `997f88161359da33869cdc8163b8aeae749e455b0dfe98cb442fb2d7217238d5` |

The runner and analyzer are committed with this protocol. The package, request bodies and
responses stay local (`runs/` is not distributed).
