# Protocol: live model-upgrade comparison, gpt-5.6-luna vs gpt-6-luna (pre-registered 2026-09-24)

Committed before any Solari allocation or model request for this experiment. Nothing below
changes after the first cell starts. Any departure is reported as a deviation in
`docs/live-model-upgrade-comparison.md`.

## Hypothesis

On the full `resolve_denial` workflow, the same GUI agent completes the task less often when its
model is upgraded from `gpt-5.6-luna` to `gpt-6-luna`, with everything else unchanged.

This is the regression Forkloop exists to catch: the upgraded model looks just as capable in a
demo, but the database shows whether the appeal it filed carries the right number.

**Evidence that motivated it (not reused as data):**

- Offline, paired, same frozen screens ([results](reading-model-upgrade-results.md), registered
  in advance): exact authorization typed 20/20 by `gpt-5.6-luna` and 10/20 by `gpt-6-luna`,
  10–0 discordant, exact McNemar p = 0.002. Nine of the ten wrong values were one or two
  characters short.
- Live, unpaired: `gpt-6-luna` completed 4/14 held-out seeds at high detail in run 2 of the
  [image-detail comparison](live-image-detail-comparison.md); 6 of its 10 submitted appeals
  stored a wrong number. `gpt-5.6-luna` with the v5 prompt completed 132 of 155 recorded
  episodes on Sept 15 (`runs/luna-high-v5-s0-19`, `runs/luna-v5-f3-s20-99`,
  `runs/luna-v5-f3-s100-139`: 17/20, 77/90, 38/45), but on `train` seeds, an older golden and a
  60-step, 600 s budget, so that rate is not comparable.

## Arms

`configs/luna-model-upgrade.yaml`. Both arms copy the "Luna high detail" arm of
`configs/luna-image-detail.yaml` exactly: `system_prompt_file: ../forkloop/policies/prompts/hosted_gui_agent_v5.md`,
`prompt_style: compact`, `image_max_side: 1280`, `image_detail: high`, `history_k: 16`,
`prev_screenshot: true`, `history_notes: false`, `hosted_reasoning: true`, `max_tokens: 4096`,
`timeout_s: 120`, `reasoning_effort: high`.

| Arm | Name | `model` | `configuration_sha256` |
| --- | --- | --- | --- |
| A | Luna 5.6 | `gpt-5.6-luna` | `9925f3cc078b1646dc1cbd19080f7c108eb98b1dfc132ede6d5803a4419a7d95` |
| B | Luna 6 | `gpt-6-luna` | `300b2e2681e26a1fd7ea307c30f15c0797d42f786e4e8d41b34a7f7bc918c5ee` |

Programmatic diff of the two arms as `forkloop compare --check` resolves them (every key,
flattened; `runs/session-20260924/model-upgrade-check.json`):

```
A vs B: {'name': ('Luna 5.6', 'Luna 6'),
         'identity.options.model': ('gpt-5.6-luna', 'gpt-6-luna'),
         'identity.configuration_sha256': ('9925f3cc…', '300b2e26…')}
B vs the image-detail "Luna high detail" arm: {'name': ('Luna 6', 'Luna high detail')}
```

Policy source SHA-256 (both arms): `997f88161359da33869cdc8163b8aeae749e455b0dfe98cb442fb2d7217238d5`.

**No parameter confound.** Both models accepted the same request fields in the offline study:
80/80 requests whose bodies differed only in `model` (`image_detail: high`,
`reasoning_effort: high`, `max_completion_tokens: 4096`, `service_tier: default`). The live arms
send the same fields. `forkloop compare --check` is clean for the main config and both halves.

## Seeds and split

`heldout_seeds` **100328–100351** (24 seeds), family `resolve_denial`, world `claims-ops-v1`.
Never used anywhere before this experiment. Structured scan (`runs/session-20260924/seed_evidence.py`,
output `seed-evidence.json`):

- 854 episode manifests under `runs/` and `docs/`: the only `heldout_seeds` ever run are
  100300–100327 (image-detail runs 1 and 2) and 100400 (the reset smoke check for `f3e1b1e`).
  None of 100328–100351.
- `heldout_seeds-NNNNNN` task IDs in any file name or text file in the project: only
  100300–100327 and 100400.
- `seeds:` lists in all 16 YAML configs under `configs/` and `runs/`: the same set.
- 13 SFT/collection datasets under `data/`: only `sft_f3_25_v3.provenance.json` mentions
  `heldout_seeds`, and it records the reserved block as unused.
- Claim numbers repeat modulo 50000, so `train` seeds 328–351 share these claim numbers; no
  `train` episode on 328–351 exists.
- 100314 is used today by a live smoke check of the reset (below); it is not an experiment seed.
  The reserved final block 100500–100529 is untouched.

## Power (stated before any outcome)

Minimum one-way discordant count for exact two-sided p < 0.05: 6–0, 8–1, 10–2, 12–3, 13–4, 15–5.

Simulated power at 24 pairs (200,000 runs each; the model is stated, the numbers are not data):

| Assumed per-seed behaviour | E[A only] | E[B only] | Power |
| --- | ---: | ---: | ---: |
| Shared navigation 71% (run 2's appeal rate), reading A 95% / B 50% (Phase 1) | 8.1 | 0.4 | 0.74 |
| Shared navigation 71%, reading A 95% / B 40% (run 2's live read rate) | 9.7 | 0.3 | 0.90 |
| Shared navigation 50%, reading A 95% / B 50% | 5.7 | 0.3 | 0.44 |
| Independent arms, success A 80% / B 29% | 13.6 | 1.4 | 0.92 |
| Independent arms, success A 60% / B 29% | 10.2 | 2.8 | 0.45 |

So a significant result is likely if `gpt-5.6-luna` navigates this task about as often as
`gpt-6-luna` did in run 2, and roughly a coin flip if it navigates much less often. `gpt-5.6-luna`'s
navigation rate on held-out seeds at this budget has not been measured. The seed count is
fixed at 24 and will not be raised after any outcome.

## Budget, reset and execution

- Budget per episode: 120 charged actions and **1,200 s**, as in the image-detail runs.
- `reset_mode: fork` from the Sept 15 golden `snap_dlft9omnpkyw`: a fresh desktop per cell.
  Every reset now runs the **feasibility gate** (`136e5c5`): patient, denied claim and
  authorization document (row and file bytes) checked by SQL after seeding, and the portal
  re-login check (`f3e1b1e`). Either failing fails the reset, and the cell is unscored.
- One attempt per cell and a fresh policy instance per cell. Arm order alternates by seed index
  within each half (A first on even indices).
- Two halves run in parallel on separate desktops (the Starter cap is 2 VMs):
  `runs/model-upgrade-live-20260924/luna-model-upgrade-a.yaml` (100328–100339) and `…-b.yaml`
  (100340–100351). They differ from the main config only in `seeds` and the prompt path's depth;
  their per-arm `configuration_sha256` values equal the ones above.
- Lifetime safety: `FORKLOOP_SOLARI_MAX_LIFETIME_MIN=35` (stricter than the session's 45-minute
  rule, so that 48 worst-case ledger reservations of $0.1005 fit under the ledger's $6.40 Solari
  stop; the longest possible cell is about 27 minutes), `FORKLOOP_SOLARI_ACCEPT_BALANCE_BOUND=1`,
  one `forkloop reap --older-than-min 50` loop for the whole live window, `caffeinate -dimsu`
  around both halves, host on AC power. A ledger-wide `reap` runs only after both halves finish.
  Afterwards an account listing must show 0 machines.
- **Start condition.** When this protocol was written (06:25 UTC), every Solari create returned
  HTTP 500 (`runs/session-20260924/solari-outage.log`). The run starts only after (1) creates
  succeed again and (2) one live smoke check of the new reset passes on seed 100314
  (`runs/session-20260924/finder-check.yaml`, not an experiment cell). If the gate fails
  spuriously there, the run does not start; the fix and a re-registration come first.

## Metrics and analysis

- **Primary:** full-task success (a verdict with reward 1.0). Unscored cells (`INFRA_ERROR`,
  `ORACLE_ERROR`, setup errors, missing verdicts) are excluded, never counted as failures.
- **Test:** exact two-sided McNemar on the discordant pairs, pooled over both halves, α = 0.05.
  Only pairs with both cells scored and identical baseline table hashes enter the test. Arm A
  is named better only if p < 0.05 and A-only pairs outnumber B-only pairs (and B likewise);
  otherwise "no significant difference" with the observed counts.
- **Secondary (descriptive, no test):** the `ui_milestones` staircase per arm; "portal said yes,
  database said no" (appeal submitted, reward not 1.0) per arm, and how many of those stored a
  wrong authorization; the first `AUTH-…` value in the agent's own text per cell, exact or not;
  reason codes; steps, wall seconds, reset seconds and model cost per cell; pairs with equal
  step-0 screenshots.
- **Validity checks before scoring, reported with counts:** the feasibility gate passed and the
  initial-screen stage (portal logged in) passed in every counted cell's `reset.json`, and the
  step-0 screenshot of **every** counted cell is inspected by eye for the logged-in denied-claims
  list (the count goes in the report).
- Analysis script: `runs/model-upgrade-live-20260924/pool.py`, SHA-256
  `b6d95483e8438ca5ebf8fbce611e2f9dc584db3ebc39528ab6962939b98fbdcd`, written and dry-run on run 2's
  data before this commit.

## Stopping and retry rules

- All 48 planned cells, one attempt each. No scored cell is rerun, and no seed is added or
  replaced after any outcome is seen.
- A cell that ends unscored because of an infrastructure failure (setup error, reset failure
  including the feasibility gate, transport error) gets **exactly one retry**, on a fresh fork,
  as a single-seed run of the same half config in a `retry-*` directory. It is marked "retried".
  If the retry also fails, the pair stays unscored and is excluded.
- **Early world check.** The step-0 screenshots of the first cell of each half are inspected as
  soon as they exist. If the world is broken (as in image-detail run 1), both halves are
  stopped, the run is voided and reported, the defect is fixed, and a new registration with new
  seeds comes before any further cell.
- If a spending stop, a Solari outage or an operator abort interrupts the run, the report states
  exactly which cells completed. A resumption rule, if any, is written down and committed before
  any resumed cell starts. Missing cells are not replaced.

## Projected spend

Session ledger `runs/session-20260924/session-ledger.sqlite`: Solari ceiling $8 (stop $6.40),
OpenAI ceiling $20 (stop $18), GPU $0. At registration: OpenAI $0.84 accounted (of which $0.05
actual and $0.79 kept for two probe requests rejected with HTTP 400), Solari $0.25 accounted
(two creates that returned HTTP 500; no machine existed afterwards).

- **OpenAI.** Run 2's `gpt-6-luna` high-detail cells cost $0.028 at the median and $0.064 at most.
  `gpt-5.6-luna` lists at about 2–2.4× the token price. Planning figures: at most $0.30 per A cell
  and $0.15 per B cell, so **≤ $10.80 for 48 cells**; expected about $2–3. The guard reserves a full
  context per request before sending it and reconciles to provider usage.
- **Solari.** 48 reservations × $0.1005 = **$4.82**, plus $0.25 already booked and about $0.20 for
  the smoke check: $5.27, under the $6.40 stop, leaving room for about 11 retries. Expected compute:
  about 12 machine-minutes per cell, **about $1.3**.
