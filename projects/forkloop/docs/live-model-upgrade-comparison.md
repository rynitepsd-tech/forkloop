# Live comparison: gpt-5.6-luna vs gpt-6-luna, same agent (2026-09-24)

**Result: upgrading the model from `gpt-5.6-luna` to `gpt-6-luna`, with nothing else changed,
cut full-task success from 23/24 to 4/24 on held-out seeds. All 19 pairs where the arms differed
favoured the older model (19–0), exact two-sided McNemar p = 3.8 × 10⁻⁶, significant at the
pre-registered α = 0.05.**

The newer model did not fail by looking lost. It filed an appeal on 17 of 24 seeds, and the
payer portal confirmed every one of them. The database rejected 13 of those 17 because the
authorization number was wrong, and 12 of the 13 were missing exactly one digit
(`AUTH-45W3268` for `AUTH-45W53268`). `gpt-5.6-luna`'s 23 appeals were all correct. A demo or
the agent's own "done" would have scored the upgrade 17/24; the database scores it 4/24.

The [protocol](protocol-model-upgrade-live.md) was committed (`12cc278`) before any allocation
or model request. It followed a paired offline study on frozen screens that found the same
direction ([reading study](reading-model-upgrade-results.md): 20/20 vs 10/20, p = 0.002).

## Setup

| | |
| --- | --- |
| Config | [`configs/luna-model-upgrade.yaml`](../configs/luna-model-upgrade.yaml), `heldout_seeds` 100328–100351 (never used before), two parallel halves |
| Only difference | `model: gpt-5.6-luna` vs `gpt-6-luna` (checked programmatically; per-arm `configuration_sha256` in the protocol). The `gpt-6-luna` arm is identical to the high-detail arm of the [image-detail comparison](live-image-detail-comparison.md) |
| Agent | Workflow prompt v5, `image_detail: high`, previous screenshot, `reasoning_effort: high`, 4,096-token cap |
| Budget | 120 charged actions and 1,200 s per episode |
| Reset | `fork` from the Sept 15 golden, with the new feasibility gate and the portal login check. Median 50.0 s (46.8–72.4 s) |
| Safety | 35-minute lifetime bound per machine, a separate reaper loop, `caffeinate` on AC power; afterwards the account listed 0 machines |

## Outcomes

| Seed | Expected | `gpt-5.6-luna` | `gpt-6-luna` | Pair |
| --- | --- | --- | --- | --- |
| 100328 | `AUTH-86L69093` | **OK** (42 steps) | NOT_DONE, reached the portal claim | 5.6 only |
| 100329 | `AUTH-46H78500` | **OK** (60) | NOT_DONE, stuck in Patient Finder | 5.6 only |
| 100330† | `AUTH-45W53268` | **OK** (47) | WRONG_VALUE `AUTH-45W3268` | 5.6 only |
| 100331 | `AUTH-40A18403` | **OK** (53) | WRONG_VALUE `AUTH-40A1803` | 5.6 only |
| 100332 | `AUTH-52D79995` | **OK** (63) | WRONG_VALUE `AUTH-52D7995` | 5.6 only |
| 100333 | `AUTH-71T98024` | **OK** (49) | WRONG_VALUE `AUTH-7T98024` | 5.6 only |
| 100334 | `AUTH-98E43006` | **OK** (65) | NOT_DONE, reached the portal claim | 5.6 only |
| 100335 | `AUTH-53T80271` | **OK** (50) | **OK** (44) | both |
| 100336 | `AUTH-76U92506` | **OK** (53) | **OK** (60) | both |
| 100337 | `AUTH-78T90177` | **OK** (44) | **OK** (49) | both |
| 100338 | `AUTH-55C52107` | **OK** (48) | WRONG_VALUE `AUTH-5C52107` | 5.6 only |
| 100339 | `AUTH-84G28026` | **OK** (60) | **OK** (57) | both |
| 100340 | `AUTH-35C96620` | **OK** (38) | WRONG_VALUE `AUTH-35C9620` | 5.6 only |
| 100341 | `AUTH-83Z80771` | **OK** (58) | WRONG_VALUE `AUTH-83Z0771` | 5.6 only |
| 100342 | `AUTH-19N75166` | **OK** (50) | NOT_DONE, stuck in Patient Finder | 5.6 only |
| 100343 | `AUTH-41S34084` | **OK** (41) | WRONG_VALUE `AUTH-41S3Q84` | 5.6 only |
| 100344 | `AUTH-43Y94809` | **OK** (43) | WRONG_VALUE `AUTH-43Y9409` | 5.6 only |
| 100345 | `AUTH-47E78752` | **OK** (59) | WRONG_VALUE `AUTH-47E8752` | 5.6 only |
| 100346 | `AUTH-48N19078` | NOT_DONE, reached the portal claim | NOT_DONE, opened the letter | neither |
| 100347 | `AUTH-62Y62526` | **OK** (46) | WRONG_VALUE `AUTH-62Y2526` | 5.6 only |
| 100348 | `AUTH-62M85488` | **OK** (51) | WRONG_VALUE `AUTH-62M5488` | 5.6 only |
| 100349 | `AUTH-67M93891` | **OK** (43) | WRONG_VALUE `AUTH-67M3891` | 5.6 only |
| 100350 | `AUTH-48K92731` | **OK** (45) | NOT_DONE, stuck in Patient Finder | 5.6 only |
| 100351 | `AUTH-63F94183` | **OK** (68) | NOT_DONE, stuck in Patient Finder | 5.6 only |

† Retried under the pre-registered rule: the original `gpt-6-luna` cell ended unscored after a
provider HTTP 400 (see *Deviations*). The pair uses the original scored `gpt-5.6-luna` cell and
the retried `gpt-6-luna` cell.

## Test

| | `gpt-5.6-luna` | `gpt-6-luna` |
| --- | ---: | ---: |
| Full-task success | **23/24** | **4/24** |
| Discordant pairs | 19 | 0 |

Both 4, 5.6 only 19, 6 only 0, neither 1. **Exact two-sided McNemar p = 3.8 × 10⁻⁶**
(2 × 0.5¹⁹). By the pre-registered rule `gpt-5.6-luna` is named better.

Sensitivity, not pre-registered:

- `compare`'s own reset-equivalence check is stricter than the registered rule and flags seed
  100329, where one portal page-view watermark differs (all 14 table hashes match). Without it:
  18–0, p = 7.6 × 10⁻⁶.
- Using the retry's own pair for seed 100330 instead (its extra `gpt-5.6-luna` cell ended
  NOT_DONE): 18–0, p = 7.6 × 10⁻⁶. With both changes: 17–0, p = 1.5 × 10⁻⁵.

## Milestone staircase (secondary, descriptive)

| Milestone | `gpt-5.6-luna` | `gpt-6-luna` |
| --- | ---: | ---: |
| OpenEMR login | 24/24 | 24/24 |
| Patient chart | 24/24 | 20/24 |
| Authorization letter open | 24/24 | 20/24 |
| Portal claim page | 24/24 | 19/24 |
| Appeal form | 23/24 | 17/24 |
| Appeal submitted | 23/24 | 17/24 |
| …with the correct authorization | 23/24 | 4/24 |

## Portal said yes, database said no

| | `gpt-5.6-luna` | `gpt-6-luna` |
| --- | ---: | ---: |
| Appeals the portal confirmed | 23 | 17 |
| …rejected by the database | 0 | **13** |
| …rejected for a wrong authorization number | 0 | 13 |

`gpt-6-luna`'s 13 wrong values: 9 drop one character, 3 drop one of a repeated pair
(`AUTH-52D7995` for `AUTH-52D79995`), and 1 drops a character and reads `0` as `Q`
(`AUTH-41S3Q84`). None is a decoy number from the letter. This is the error the offline study
found on frozen screens (9 of 10 wrong values short by one or two characters).

**First authorization in the agent's own text** (pre-registered, descriptive): exact in 19 of 24
`gpt-5.6-luna` cells and 9 of 24 `gpt-6-luna` cells. `gpt-5.6-luna`'s 5 "wrong" first mentions
were 3 decoy case numbers it quoted from the letter and 2 misreads it corrected before typing;
`gpt-6-luna` wrote no authorization at all in 4 cells.

## What the evidence says

- **The regression is reading, and it is silent.** On the 17 seeds where `gpt-6-luna` reached and
  submitted the appeal, it typed the right number 4 times. Every wrong submission got the portal's
  "Appeal submitted" banner.
- **The Patient Finder trap is also model-specific.** `gpt-5.6-luna` searched OpenEMR by surname
  first in 24 of 24 cells. `gpt-6-luna` searched the full name first in 4 of 24, and all 4 then
  stayed stuck in the Patient Finder's persisted full-name filter until the budget ran out
  (the behaviour documented in the [image-detail addendum](live-image-detail-comparison.md)).
  That accounts for 4 of its 7 NOT_DONE cells.
- **Speed and cost did not explain it.** Successful episodes took 38–68 steps (`gpt-5.6-luna`) and
  44–60 steps (`gpt-6-luna`), 121–220 s and 142–204 s, far inside the budget. `gpt-6-luna` was
  cheaper per cell ($0.024 vs $0.041 at the median).
- **Consistent with run 2.** `gpt-6-luna` at high detail completed 4/14 there and 4/24 here.

## Deviations

- **Start delayed.** Every Solari create returned HTTP 500 from 06:12 to about 09:45 UTC
  (`runs/session-20260924/solari-outage.log`). The run started at 09:52 UTC after the start
  condition in the protocol was met. Nothing in the protocol changed.
- **One provider 400.** Seed 100330's `gpt-6-luna` cell got HTTP 400 from OpenAI at step 23 and
  ended unscored. Rebuilt offline, the same request succeeded. The protocol's execution note
  (`a84c1a2`) recorded it as an infrastructure failure before the retry. The one retry ran after
  both halves (`retry-100330`) and ended WRONG_VALUE. The retry run also produced an extra
  `gpt-5.6-luna` cell (NOT_DONE), reported here but not counted.
- No other cell failed, and no scored cell was rerun.

## Validity checks and reset equivalence

- **Feasibility gate and portal login:** passed in 48 of 48 counted cells (`reset.json`).
- **Step-0 screenshots:** all 48 inspected on a contact sheet; every one shows the logged-in
  denied-claims list, and the portal header and filter region is pixel-identical to the first
  cell's in 48 of 48.
- **Pairs:** all 24 pairs have identical hashes on all 14 baseline tables. Step-0 screenshots are
  byte-identical in 14 pairs; in the other 10 the only difference is the same 2 × 1-pixel region
  on the window border at (10, 67) seen in run 2. `compare` counts 23 of 24 pairs as equivalent
  (the exception is 100329's page-view watermark, above).

## Cost

| | This run (48 counted cells + retry) |
| --- | ---: |
| OpenAI, `gpt-5.6-luna` (counted cells, recorded usage) | $1.09 |
| OpenAI, `gpt-6-luna` (counted cells, recorded usage) | $0.81 |
| OpenAI session total (ledger, all phases, usage-derived) | $2.08 actual, plus $1.05 kept reserved for three rejected requests |
| Solari compute (4.07 recorded machine-hours × $0.134/h, including the smoke check and retry) | ≈ $0.54 |
| Solari ledger upper bound (worst-case reservations, never reconciled) | $5.47 |

Per cell, `gpt-5.6-luna` cost $0.041 of model tokens at the median ($0.108 at most) and
`gpt-6-luna` $0.024 ($0.069 at most).

## Caveats

- One task family, one prompt, 24 held-out seeds, one attempt per cell. This establishes a
  regression on this workflow, not a general ranking of the two models.
- The prompt (workflow v5) was developed against `gpt-5.6-luna` in September. A prompt tuned for
  `gpt-6-luna` might close part of the gap; that is a separate experiment.
- Model aliases as served on 2026-09-24, not pinned weights.
- Evidence (local): `runs/model-upgrade-live-20260924/` (`pooled.json`, `pool.py`, the cell files,
  episode directories and the step-0 contact sheet); HTML bundles in `runs/share-model-upgrade/`
  (cropped with `--crop-top 114`).
