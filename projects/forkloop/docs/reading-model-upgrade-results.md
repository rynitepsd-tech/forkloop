# Offline reading study: gpt-5.6-luna vs gpt-6-luna (2026-09-24)

**Result: on the same 20 frozen screens, `gpt-5.6-luna` typed the exact authorization number
20/20 times and `gpt-6-luna` 10/20. All 10 discordant pairs favour the older model (10–0), exact
two-sided McNemar p = 0.002, significant at the pre-registered α = 0.05.** The newer model typed
a number every time; half of them were wrong, mostly by dropping one digit.

Pre-registered in [`protocol-reading-model-upgrade.md`](protocol-reading-model-upgrade.md)
(commit `7560386`, before any request). No deviations: 80/80 planned requests completed, no
retries, no parse failures, no truncations.

## Setup

| | |
| --- | --- |
| States | The Sept 16 frozen package: 20 authorization states and 20 navigation states from 20 successful `gpt-5.6-luna` workflow-v5 episodes (`train` seeds 101–139) |
| Arms | A `gpt-5.6-luna`, B `gpt-6-luna` (model IDs as returned by the API) |
| Only difference | The `model` field. Checked on every state: the flattened request bodies differ in `["model"]` only. Arm A's 40 bodies are byte-identical to the Sept 16 high-detail arm |
| Request | Workflow prompt v5, `image_detail: high`, previous + current screenshot, 8-action history, `reasoning_effort: high`, 4,096-token cap, standard tier, one attempt |
| Order | A/B then B/A, alternating by state within each kind |

In every authorization state the **previous** screenshot shows the authorization letter in
OpenEMR, and the **current** screenshot shows the portal's appeal form with the authorization
field focused. The number appears in no text input, so the model must read it from the earlier
image and carry it into the typing action.

## Primary: exact typing on the 20 authorization states

| | `gpt-5.6-luna` | `gpt-6-luna` |
| --- | ---: | ---: |
| Exact authorization typed | **20/20** | **10/20** |
| Wrong authorization typed | 0/20 | 10/20 |
| Other or no action | 0/20 | 0/20 |

Pairs: both 10, A only 10, B only 0, neither 0. **Exact two-sided McNemar p = 0.00195.**

| Seed | Expected | `gpt-5.6-luna` | `gpt-6-luna` |
| --- | --- | --- | --- |
| 101 | `AUTH-44Z32354` | exact | `AUTH-4AZ3254` |
| 102 | `AUTH-95A56866` | exact | `AUTH-95A586` |
| 104 | `AUTH-70N25229` | exact | `AUTH-70N2529` |
| 105 | `AUTH-50G98800` | exact | `AUTH-50G8800` |
| 106 | `AUTH-65E97242` | exact | exact |
| 109 | `AUTH-52B12625` | exact | exact |
| 111 | `AUTH-53Z27115` | exact | exact |
| 114 | `AUTH-21Y59013` | exact | `AUTH-2IY59013` |
| 115 | `AUTH-19Y77467` | exact | exact |
| 116 | `AUTH-77R17144` | exact | exact |
| 121 | `AUTH-92K99319` | exact | exact |
| 122 | `AUTH-12B36231` | exact | exact |
| 123 | `AUTH-59C16352` | exact | exact |
| 127 | `AUTH-66M44149` | exact | `AUTH-66M4149` |
| 131 | `AUTH-39Q13281` | exact | `AUTH-39Q1321` |
| 133 | `AUTH-53D25126` | exact | exact |
| 134 | `AUTH-25L23873` | exact | exact |
| 136 | `AUTH-18N46287` | exact | `AUTH-18A6Z7` |
| 138 | `AUTH-22G61651` | exact | `AUTH-22G1651` |
| 139 | `AUTH-32N99724` | exact | `AUTH-32N9724` |

## Wrong values (secondary, descriptive)

All 10 wrong values are `gpt-6-luna`'s. Classes are the pre-registered mechanical ones:

| Pattern | Count | Seeds |
| --- | ---: | --- |
| Dropped one character that repeats beside it | 3 | 104, 127, 139 |
| Dropped one other character | 3 | 105, 131, 138 |
| Letter/digit confusion (`1` → `I`) | 1 | 114 |
| Other (two or more edits) | 3 | 101, 102, 136 |

Seven of the ten are one edit away from the truth, and nine of ten are shorter than it. In each
case the model's own text stated the wrong value with confidence (for example "The authorization
number is **AUTH-32N9724**; the reason is already set …" on seed 139). This is the same error
shape as the live run: 6 of `gpt-6-luna`'s 10 confirmed appeals in
[run 2](live-image-detail-comparison.md) stored a number with a dropped or confused character.

## Navigation agreement (secondary, descriptive)

Agreement with the action `gpt-5.6-luna` recorded in the original episode: 13/20 for
`gpt-5.6-luna`, 11/20 for `gpt-6-luna` (both 11, A only 2, B only 0, neither 7; p = 0.5). This is
imitation of arm A's own past behaviour, biased towards A by construction, and not a measure of
correct navigation.

## Cost and latency

| | `gpt-5.6-luna` | `gpt-6-luna` |
| --- | ---: | ---: |
| Input tokens (40 requests) | 139,924 | 139,924 |
| Output tokens (of which reasoning) | 5,516 (3,668) | 7,053 (5,142) |
| Usage-derived cost | $0.0346 | $0.0175 |
| Median latency | 3.07 s | 2.72 s |

Total **$0.0521** for the 80 requests, plus $0.00001 for the reachability probe. The session
ledger also keeps $0.79 reserved as "uncertain" for two probe requests the API rejected with
HTTP 400 before inference (a field-name bug in the probe script, not in the study).

## Phase 3 decision (written before any Phase 3 work)

Pre-registered rule: GO if `gpt-6-luna` ≤ 14/20 and `gpt-5.6-luna` ≥ 16/20 (gap ≥ 5).
Observed: 10/20 and 20/20, gap 10. **GO.** The live model-upgrade A/B is registered next, in
[`protocol-model-upgrade-live.md`](protocol-model-upgrade-live.md).

## Caveats

- **Reading, not workflow success.** A parsed `type` action is not a field selection, a stored
  value or a completed task. The live A/B measures those.
- **The states favour `gpt-5.6-luna`.** They come from its own successful episodes and carry its
  own action history. That selection cannot explain a newer model misreading a clearly legible
  number, but it can inflate A's score. Sept 16's run of the same arm-A requests gave 19/20; today
  gave 20/20 (seed 111 changed), so single-sample noise is about one state.
- **One sample per state** and one letter design (the pre-0.2.1 generator's letters; held-out live
  letters differ in layout details).
- **Model aliases, not pinned weights.** Both were served under the aliases the API reported on
  2026-09-24.
- Evidence (local): `runs/reading-model-upgrade-20260924/` (`protocol.json`, `results.json`,
  `analysis.json`, `ledger.json`), runner and analyzer beside it.

## Follow-up (exploratory): is it the prompt?

Frozen before any request in [`protocol-gpt6-reading-probe.md`](protocol-gpt6-reading-probe.md), after
the results above were known, so it suggests a cause rather than establishing one. `gpt-6-luna` only,
the same 20 authorization states, one request per arm and state:

| Arm | Exact / 20 |
| --- | ---: |
| `base`: the identical request again (noise check) | 11 |
| `careful`: v5 prompt plus a paragraph asking for character-by-character transcription and a check of repeated characters | 9 |
| `tiles`: the base request plus 2 × 2 tiles of both screenshots, enlarged 2× | 9 |

- **Noise is about one state.** The rerun agreed with the reading study's `gpt-6-luna` result on
  17 of 20 states (11/20 now vs 10/20 then).
- **Neither change helped.** `careful` vs `base`: 0 repaired, 2 broken (p = 0.5). `tiles` vs `base`:
  1 repaired, 3 broken (p = 0.63).
- **The misreads are close to deterministic.** 8 states were wrong in all three arms, and on 5 of
  them all three arms typed the *identical* wrong string (for example `AUTH-50G9800` for
  `AUTH-50G98800`, `AUTH-66M4149` for `AUTH-66M44149`), including the arm told to check repeated
  characters. 29 of the 31 wrong values in this follow-up are shorter than the truth.

So, on this evidence, the reading regression is not prompt fit and not image resolution: the same
pixels at 2× give the same dropped digits. It looks like a property of how `gpt-6-luna` transcribes
digit strings, most often collapsing a repeated character. A prompt could still fix the separate
Patient Finder behaviour (4 of `gpt-6-luna`'s 20 live failures). 60 requests, $0.043.
