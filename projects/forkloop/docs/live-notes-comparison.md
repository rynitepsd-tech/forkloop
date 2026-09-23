# Live matched comparison: Fara-4B v3 with and without history notes (2026-09-23)

**Result: no measurable difference. Each arm solved 1 of 10 matched development seeds. The two
discordant seeds went one each way (exact McNemar p = 1.0). Both arms also submitted the
same wrong authorization on seed 209.** The offline study showed that notes let the model
carry a number it has read (37/38 with notes vs 0/38 without). Live, the bottleneck was
elsewhere: 16 of 20 episodes ran out of the 900-second budget before finishing, and the one
wrong entry was a misreading that both arms made identically.

This is the first complete, fully comparable live comparison Forkloop has run:

- 20 of 20 planned cells completed, 0 unscored;
- reset equivalence held on 10 of 10 pairs (14 baseline tables each, fork reset, identical initial screens);
- one attempt per cell, with the order alternated by seed.

It is development evidence on seeds 200–209, not a held-out benchmark.

## Setup

| | |
| --- | --- |
| Config | [`configs/fara-notes.yaml`](../configs/fara-notes.yaml), run as two halves in parallel (seeds 200–204 and 205–209), one Solari desktop each |
| Only difference | `history_notes: false` vs `true` |
| Model | Frozen v3 adapter on `microsoft/Fara1.5-4B` @776a33a, served by `scripts/lambda_serve.py` on a rented A100, greedy decoding, 512-token cap |
| Budget | 120 charged actions, 900 s per episode |
| Reset | `fork` from the Sept 15 golden; median reset 43 s (30–64 s) |
| Lifetime bound | Each desktop killed at 45 minutes, a separate `reap --older-than-min 50` loop, prepaid balance as the last-resort cap |

## Outcomes

| Seed | Without notes | With notes | Pair |
| --- | --- | --- | --- |
| 200 | **OK** in 44 steps | NOT_DONE (time), reached the portal claim | A only |
| 201 | NOT_DONE (time) | NOT_DONE (time) | neither |
| 202 | NOT_DONE (time), reached the appeal form | **OK** in 37 steps | B only |
| 203–208 | NOT_DONE (time) | NOT_DONE (time) | neither |
| 209 | **WRONG_VALUE**: `AUTH-66H40175` | **WRONG_VALUE**: `AUTH-66H40175` | neither |

The expected value on seed 209 was `AUTH-66H44175`. Both arms read a 0 where the letter has
a 4. The notes arm wrote the misread number into its notes at step 23 and carried it
unchanged to the form. It got there in 37 steps against 83 without notes: it no longer had
to go back to re-read the letter. Carrying speeds the workflow up without making the
reading any more accurate.

## What the evidence says

- **Carrying works; reading and speed do not.** Notes carried the number faithfully, including a wrong one. The failures came earlier in the workflow: navigation, document reading and time.
- **The time budget decided most cells.** Steps took about 10.5 s each: about 5 s of generation, doubled because two episodes shared one serialized server, plus the screenshot round trip. That left room for about 85 actions in 900 s. The two successes took 37 and 44 steps, while the timeouts were still paging through documents or filling the form. A faster server (vLLM, one client per server) or a longer budget is the obvious next comparison, and the next run should test that before any new model change.
- **Memory can make a loop sticky.** In one notes episode the model clicked Chrome's taskbar entry, which minimizes the window in XFCE. It then wrote "the portal window is closed" into its notes and re-read that belief for 90 steps. It never restored the window.
- **Environment defect found and fixed.** Every episode opened with Chrome's "password found in a data breach" dialog over the claims list, which cost the agent its first action. The world now sets `PasswordLeakDetectionEnabled: false` and installs the policy on older goldens.

## Cost

- **Solari:** 20 desktops; the ledger upper bound is $2.46, and the compute estimate is about $0.70.
- **Serving GPU:** Lambda A100 for about 2.7 h, about $5.40.
- **Model calls:** none paid.

Evidence (local): `runs/solari-live-20260923/compare-{a,b}/`, the cropped HTML bundles
`share-{a,b}/`, `pooled-cells.json`, and the session ledger.
