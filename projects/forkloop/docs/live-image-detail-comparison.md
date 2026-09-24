# Live comparison: screenshot detail high vs low, gpt-6-luna (2026-09-23)

**Result: high detail completed the task on 4 of 14 held-out seeds and low detail on 0 of 14.
All four pairs where the arms differed favoured high detail, but at exact McNemar p = 0.125 that
is not significant at the pre-registered α = 0.05.** Two failures sit behind that number, and
Forkloop's evidence shows both:

- **Low detail never got past the first screen that needed a click.** None of its 14 episodes
  logged into OpenEMR. With `detail: low` the model sees a 512 × 288 copy of the 1280 × 720
  screen, and it returned coordinates in that small image's pixels: 1,164 of its 1,170 clicks
  (99%) landed in the screen's top-left 512 × 288 corner.
- **High detail filed an appeal on 10 of 14 seeds, and 6 of those 10 carried a wrong
  authorization number.** The portal confirmed every one of them. The database did not. The
  misreads all drop a repeated digit or confuse two characters (`AUTH-9E44275` for
  `AUTH-95E44275`, `AUTH-B1X60784` for `AUTH-81X60784`).

So the cost-cutting setting broke the agent completely, but the pre-registered metric (full-task
success) could only register that on the seeds where the high-detail arm also read correctly.
The [protocol](protocol-image-detail-live.md) was committed before any allocation. This is
**run 2**. Run 1, below, completed but was invalid for the primary metric because of a reset
defect found afterwards.

## Setup

| | |
| --- | --- |
| Config | [`configs/luna-image-detail.yaml`](../configs/luna-image-detail.yaml), held-out seeds 100314–100327, run as two parallel halves |
| Only difference | `image_detail: high` vs `low` (checked programmatically; per-arm `configuration_sha256` in the protocol) |
| Model | `gpt-6-luna`, workflow prompt v5, `reasoning_effort: high`, 4,096-token cap, 16-action history, previous screenshot |
| Budget | 120 charged actions and 1,200 s per episode |
| Reset | `fork` from the Sept 15 golden. The reset now confirms a logged-in portal screen (`f3e1b1e`). Median 57 s (46–87 s) |
| Lifetime bound | 45 minutes, then 35 minutes after the resume; a separate `reap --older-than-min` loop; prepaid balance as the last-resort cap |

## Outcomes

| Seed | Expected | High detail | Low detail | Pair |
| --- | --- | --- | --- | --- |
| 100314 | `AUTH-81Q23673` | NOT_DONE, stuck in Patient Finder | NOT_DONE, never logged in | neither |
| 100315† | `AUTH-81X60784` | WRONG_VALUE: `AUTH-B1X60784` | NOT_DONE, never logged in | neither |
| 100316 | `AUTH-64F32946` | NOT_DONE, stuck in Patient Finder | NOT_DONE, never logged in | neither |
| 100317 | `AUTH-98T95012` | WRONG_VALUE: `AUTH-9T85012` | NOT_DONE, never logged in | neither |
| 100318 | `AUTH-13N50523` | **OK** in 65 steps | NOT_DONE, never logged in | **high only** |
| 100319 | `AUTH-77L96364` | NOT_DONE, stuck in Patient Finder | NOT_DONE, never logged in | neither |
| 100320 | `AUTH-95L17123` | WRONG_VALUE: `AUTH-95L1723` | NOT_DONE, never logged in | neither |
| 100321 | `AUTH-32A55810` | **OK** in 53 steps | NOT_DONE, never logged in | **high only** |
| 100322† | `AUTH-63X34711` | NOT_DONE, reached the letter | NOT_DONE, never logged in | neither |
| 100323† | `AUTH-32Q78622` | WRONG_VALUE: `AUTH-32Q7822` | NOT_DONE, never logged in | neither |
| 100324 | `AUTH-37R82456` | **OK** in 44 steps | NOT_DONE, never logged in | **high only** |
| 100325 | `AUTH-36V65898` | **OK** in 65 steps | NOT_DONE, never logged in | **high only** |
| 100326 | `AUTH-95E44275` | WRONG_VALUE: `AUTH-9E44275` | NOT_DONE, never logged in | neither |
| 100327 | `AUTH-42X22956` | WRONG_VALUE: `AUTH-4X22956` | NOT_DONE, never logged in | neither |

† Infrastructure-affected seeds, handled by the pre-registered retry rule (see *Deviations*).
Seed 100322 pairs its original scored low-detail cell with the retried high-detail cell.

Every low-detail cell ended `NOT_DONE` after 120 actions without logging into OpenEMR.

## Test

| | High detail | Low detail |
| --- | ---: | ---: |
| Full-task success | 4/14 | 0/14 |
| Discordant pairs | 4 | 0 |

Exact two-sided McNemar on the 4 discordant pairs: **p = 0.125**. By the pre-registered rule,
no leader is named. Reaching p < 0.05 needed 6 or more pairs going one way with none the other.
The sensitivity analysis, which takes every resumed seed's own pair, gives the same 4 vs 0.

## Milestone staircase (secondary, descriptive)

| Milestone | High detail | Low detail |
| --- | ---: | ---: |
| OpenEMR login | 14/14 | 0/14 |
| Patient chart | 11/14 | 0/14 |
| Authorization letter open | 11/14 | 0/14 |
| Portal claim page | 10/14 | 0/14 |
| Appeal form | 10/14 | 0/14 |
| Appeal submitted | 10/14 | 0/14 |
| …with the correct authorization | 4/14 | 0/14 |

Not pre-registered: high detail submitted an appeal on 10 pairs where low detail did not, and
never the other way round (exact sign test p = 2 × 0.5¹⁰ ≈ 0.002). This measures workflow
completion, not correct completion.

## What the evidence says

- **Low detail fails on coordinates, not only on legibility.** A 512 × 288 image still shows the
  OpenEMR login form clearly, but the model clicked where the username field sits in that small
  image, about (278, 172), which on the real screen is empty page. The authorization digits are
  also unreadable at that size, but no low-detail episode got far enough for that to matter.
- **High detail's ceiling is reading.** When it finished, it did so quickly: 43–67 steps and
  148–290 s for the 10 submitted appeals, so the budget never decided a completed episode. It
  misread the authorization on 6 of the 10 appeals. In run 1 the first authorization it wrote
  down was exact in only 3 of 14 episodes. `gpt-5.6-luna` with the same prompt got the first
  read right in 89 of 109 recorded episodes (Sept 15, different seeds and letter render), so
  this looks like a regression in the newer model, but no paired test has measured it.
- **Three high-detail episodes never found the patient.** Each typed the full name into
  OpenEMR's top search, landed in the Patient Finder, and kept getting "No matching records
  found" from its name filter. All 11 that found the patient searched the surname alone. The
  same seeding path worked on the other 11 seeds; these three patients were not queried in the
  database after the run.
- **A portal confirmation is not evidence.** Six "Appeal submitted" banners at high detail
  stored the wrong number. A demo recording would have shown ten successes.

**Addendum, 2026-09-24: the three "patient not found" seeds were agent behaviour, not a world
defect.** Checked after the run; run 2 is not re-scored.

- *The patients existed.* The Patient Finder's own footer counts every row of `patient_data`:
  it read "filtered from 43 total entries" on seeds 100314 and 100319 and "42" on 100316, which
  is exactly the 40 base patients plus the 3, 3 and 2 patients each seed inserts. The names are
  ordinary (Benjamin Fontaine, Charlotte Ashworth, Noah Joubert), with no hyphen, apostrophe,
  suffix or accent.
- *What the agent did.* In all three episodes the first search was the full name in OpenEMR's
  top search box. That opens the Patient Finder with `search_any=<full name>`. The agent then
  typed the **surname alone** into the finder's name column 21–22 times per episode (plus the
  first name, the DOB and prefixes such as `Ash`, `Jou`), and every search returned "No matching
  records found".
- *Why, from OpenEMR 8.3's source* (`interface/main/finder/dynamic_finder.php` and
  `dynamic_finder_ajax.php`, tag `v8_3_0`): the finder bakes `search_any` into its DataTables
  server URL, so every later request carries it; the source's own comment says the URL "persists
  not allowing easy way to unset any for normal search". The any-search matches the whole string
  against each demographics field separately (`field LIKE '%Benjamin Fontaine%'`), so a full name
  matches nothing, and each column filter is ANDed onto that empty result. A fresh finder (the
  Finder menu), a new top search, or text in the finder's own "Search:" box (which takes
  precedence over `search_any`) gets out of it. All 11 episodes that found the patient typed
  the surname into the top search first.
- *What changed.* Nothing in the world: this is how the real application behaves, and a GUI agent
  has to recover from it. Separately, every reset now runs a controller-side **feasibility gate**
  after seeding (`136e5c5`): it checks via SQL that the target patient (with the instruction's
  name and DOB), the denied claim and the authorization document (row and file bytes) exist, and
  fails the reset, leaving the cell unscored, otherwise. The portal-login check from `f3e1b1e`
  stays.

## Deviations

- **Controller host slept (22:09–22:38 UTC).** Two model requests failed with `ReadError`
  (seed 100315 low, seed 100322 high), and two desktop creates timed out after the wake (seeds
  100315 and 100323, high). Both halves stopped with `setup_error`. No episode was running
  during the sleep. The resumption rule was committed before any resumed cell started
  (`1a48390`): the planned seeds without a scored pair ran as planned, each failed cell got its
  one retry, arm order restarted within the resume, and the lifetime bound dropped to 35 minutes
  to fit the Solari stop. No scored cell was rerun. One extra low-detail cell (seed 100322,
  `NOT_DONE`) is reported but not counted.
- **Run 2 exists because run 1 was infeasible** (below). Both were pre-registered, and run 2's
  only changes were the environment fix and new seeds.

## Reset equivalence

Every scored pair had identical baseline hashes on all 14 tables and fork resets. Step-0
screenshots were byte-identical on 10 of 14 pairs. On the other four, the difference is a
2 × 1-pixel region at (10, 67) on the window border in three pairs, and cross-run capture timing
for seed 100322. `compare` counts all 14 as equivalent, and every one opened on the logged-in
denied-claims list.

## Cost

| | Run 1 | Run 2 |
| --- | --- | --- |
| OpenAI (`gpt-6-luna`, from provider usage) | $1.20 | $1.00, plus up to $0.53 kept reserved for the two failed requests |
| Solari compute (recorded machine time × $0.134/h) | $0.56 (28 cells) | $0.54 (33 cells), plus about $0.15 for two desktops orphaned by the sleep |
| Solari ledger upper bound (worst-case reservations, never reconciled) | $3.69 | $3.52 |

Per cell, high detail cost $0.028 at the median and low detail $0.032: low detail's cheaper
images were outweighed by its longer, failing episodes.

## Run 1 (seeds 100300–100313): complete, but the task was infeasible

**0/14 vs 0/14, exact McNemar p = 1, and not a test of the hypothesis.** Every cell in both arms
opened on the payer portal's **login page** rather than the logged-in claims list the task
assumes. No task gives the portal credentials, so no agent could have filed the appeal. The cause
was a Forkloop reset defect, found only after all 28 cells had been scored: on the Sept 15 golden,
the stale-policy Chrome relaunch added that morning (`a3d255c`) dropped the portal session. Reset
equivalence still held on 14/14 pairs, because both arms started from the same broken screen.
Equivalence checks that the two arms match, not that the world is usable, so the reset now also
confirms the portal screen and fails the cell otherwise (`f3e1b1e`). No cell of run 1 was rerun or
replaced.

The OpenEMR half of the workflow did not depend on the portal session, and there the arms separate
completely:

| Milestone reached | High detail | Low detail |
| --- | ---: | ---: |
| OpenEMR login | 14/14 | 1/14 |
| Patient chart | 14/14 | 0/14 |
| Authorization letter open | 14/14 | 0/14 |

This was a secondary, descriptive metric. High detail reached further on all 14 pairs. An exact
sign test on that, which was **not pre-registered**, gives p = 2 × 0.5¹⁴ ≈ 0.0001.

What each arm did:

- **Low detail clicked in the wrong coordinate system.** 1,206 of its 1,214 clicks (99%) landed
  inside the top-left 512 × 288 region of the 1280 × 720 screen, against 42% for high detail.
  `detail: low` fits the image inside 512 × 512, and the model returned coordinates in that
  smaller image's pixels even though the prompt states the screen size. Its clicks on the
  OpenEMR username field landed at about (278, 172), which is exactly where the field sits in the
  512 × 288 image. It then typed `admin` and `pass` into nothing until its 120 actions ran out.
  The authorization digits also blur together at that size (see the letter crop), but the arm
  never got far enough for that to matter.
- **High detail read the letter, then hit the portal login wall.** An authorization number
  appeared in its reasoning at step 21 (median). The exact value appeared in 8 of 14 cells, and
  the rest dropped or confused a character (`AUTH-10042234` for `AUTH-10Q42234`). It then spent
  most of its remaining steps failing to log into the portal: 12 of 14 cells mention failed
  portal logins, 18 times at the median. It never typed an authorization into a form.
- **One false success.** On seed 100308 the high-detail agent called `done()` after 45 steps,
  believing it had finished, holding a misread authorization and with no appeal filed. The
  database verdict was `NOT_DONE`.

Budget: the 120-action cap ended 27 of 28 cells, with a median of 504 s (high) and 471 s (low),
well inside the 1200 s limit.

## Caveats

- 14 held-out seeds on one task family, one model and one prompt. A development experiment,
  not a benchmark.
- Run 2's result is not significant. The observed direction is consistent with the offline
  study, but this experiment does not establish its size.
- The 5.6-versus-6 reading comparison is observational: different seeds, dates and letter
  renders.
- Evidence (local): `runs/image-detail-live-20260923/` and `runs/image-detail-live2-20260923/`,
  with `pool.py`, `pooled.json`, the cropped HTML bundles `share-*` and both session ledgers.
