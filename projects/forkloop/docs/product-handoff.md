# Forkloop publication handoff

## Current handoff — curated public release

The reviewed evidence is now hosted at
[rynitepsd-tech.github.io/forkloop](https://rynitepsd-tech.github.io/forkloop/).
The [recovered comparison](https://rynitepsd-tech.github.io/forkloop/navigation-comparison.html)
and [33-second explainer](https://rynitepsd-tech.github.io/forkloop/demo.mp4)
are also public. GitHub Pages serves exactly seven allow-listed files:
the report as `index.html` and `report.html`, the comparison, video, poster,
captions and `.nojekyll`. All six HTTP asset checks returned 200 and matched
the reviewed local SHA-256 hashes.

The [0.2.0 prerelease](https://github.com/rynitepsd-tech/forkloop/releases/tag/v0.2.0)
publishes source commit `327812c` and an installable wheel for the offline
evaluation and evidence-inspection workflow, not production healthcare or a live
reliability claim. Raw worked-example episodes, credentials, runs, datasets and checkpoints
are excluded. Their original local files remain untouched. Ignore rules now
protect those untracked artifacts from accidental staging. Linked research
documents use public summaries and explicitly local-only raw-evidence references.

### Safety decision and spending

The continuation authorized up to **$100 new total** across the three providers.
**New paid spend: $0.** No model request, Solari allocation or Lambda GPU was made.
The prior ledger was preserved byte-for-byte; its uncertain charges are unchanged.
The final read-only account inventory returned no visible Solari desktop or
sandbox VMs and no Lambda instances. This is not a statement about retained
snapshot storage or unresolved historical charges. The existing ledger's
`reap --dry-run` selected zero machines and changed nothing.

The [provider API](https://docs.getsolari.com/api-reference/sandboxes) describes
rolling idle timeouts; activity and open connections extend them. No verified
hard lifetime or session spending bound was found. A local watchdog cannot
guarantee termination after host failure. The five-hour reservation assumption
is therefore not a defensible bound.

New Forkloop Solari allocations now fail closed across **all** ledgers, including
historical spike allocators. Pricing acknowledgments cannot bypass the hold.
Doctor reports `solari.lifetime` as failed without advertising a fictitious total.
Cleanup, read-only inspection and all offline workflows remain available.
The interrupted live experiments were not resumed or rewritten; final held-out
seeds remain sealed. The next live comparison is blocked on enforceable provider
lifetime/spending controls, not on budget authorization.

### Verification for this release

The isolated, curated source tree passed **404 tests, 3 skipped, 3 dependency
deprecation warnings** in **114.76 seconds**. This tree excludes the private
worked-example fixture. Its report tests now construct offline evidence instead;
the original missing-fixture failure was reproduced before the repair.
Fresh-ledger desktop/sandbox bypass regressions also failed before the safety
repair and passed afterward.

A wheel installed outside the checkout passed fake doctor, all five demo controls,
the complete two-pair offline comparison and cropped HTML bundle export.
The installed wheel reports the allocation hold; the standalone sandbox probe
also refuses before contacting a provider. Cleanup regression tests pass.
The downloaded public wheel's SHA-256 matches the tested local wheel:
`2c5de45f539a85a4005f214a0f81b6733ee714b23a237a3f195cdcb5450d47f0`.
A selected-file scan against six configured credential values found no matches.
This does not certify arbitrary screenshots or free text as secret-free.

Chromium exercised the public report at desktop and mobile sizes, decoded all
six 1280×606 sharing images and followed its screenshot navigation. The public
comparison displayed the incomplete-run warning and planned denominators.
The five video scenes were inspected at 2, 8, 15, 22 and 30 seconds.
Only the reviewed sharing derivatives were inspected/published, never raw images.

Local verification and accounting receipts are under `runs/release-20260915/`.
The [independent-evaluation invitation](https://github.com/rynitepsd-tech/forkloop/issues/1)
and [live-execution spending blocker](https://github.com/rynitepsd-tech/forkloop/issues/2)
are public GitHub issues. Social posts and researcher messages remain unsent.
Independent adoption, repeat use and a changed research decision still require
an actual external user.

## Historical handoff — September 15 session recovery

The interrupted work is recovered locally. Supported commands now include
`demo`, `doctor`, configurable `compare`, evidence `compare-report`, HTML-only
sharing bundles and session-scoped orphan cleanup. The root report link is fixed.
Nothing was committed, pushed, deployed, posted, submitted or sent externally in
this recovery. External adoption and an interview are not established.

### Start with these artifacts

- [Public recovered comparison](worked-example/navigation-comparison.html):
  all eight planned cells, comparison settings, reset checks and verifier evidence.
  This is a no-account check-summary edition with no screenshots or outbound links.
- [Existing wrong-authorization report](worked-example/report.html): the selected
  historical adapter failure with six retained screenshots; it is unchanged.
- Local full cropped comparison bundle:
  `runs/product-20260915/recovery/navigation-share/comparison.html`. It includes
  linked episode HTML with embedded images, not raw controller files. The seed-200
  success retains all 54 steps and 108 screenshot references. The bundle requires
  pixel/free-text review before public sharing; 114-pixel browser-chrome cropping
  is not an automatic secret scanner.
- Canonical source evidence and regenerated summary views remain in
  `runs/product-20260915/live-memory/` and `live-navigation/`. Original protocols,
  cell records and episodes were not rewritten to simulate completion.

### What the interrupted live runs actually establish

Both original experiments planned four development seed pairs (200–203), with
one attempt per arm, alternating order, 120 charged actions and 900 seconds.
These are not held-out seeds. Recovery did not rerun, append, select retries or
reduce either plan to the completed subset.

| Experiment | Comparable pairs / planned | A pass / scored | B pass / scored | Unscored cells |
| --- | ---: | ---: | ---: | ---: |
| Reasoning-history memory | 1/4 | 0/2 | 0/2 | 4 |
| Compact vs workflow prompt v5 | 2/4 | 0/2 | 2/2 | 4 |

Memory A seed 200 has a recorded verifier failure but is excluded from comparison
scoring because a backend action failed (`type("")` was rejected). A later B cell
was interrupted and two seed-203 cells were never recorded. Navigation retained
both completed pairs; A seed 202 was interrupted and three cells were never
recorded. Offline recovery distinguishes recorded `running` from observed
incompleteness and does not claim to determine process liveness.

Both completed navigation pairs have matching task fingerprints, effective
budgets, 14 baseline table hashes, watermarks, preserved-row digests, golden and
successful revert semantics. The workflow prompt persisted the expected
authorizations `AUTH-36G14538` and `AUTH-71N39501`; all configured checks passed.
It used 54/58 recorded steps and 152.695/176.846 episode wall seconds. This is
promising development evidence, not a winning-policy decision, a reliability
estimate, a training gain or a medical outcome.

### Reliability changes completed during recovery

Missing, malformed, truncated, interrupted and error-bearing evidence cannot be
quietly counted as model failures or a winner. Provider timeouts are distinct
from an actually expired trajectory deadline. Cleanup errors are unscored and
stop later cells. Missing screenshots and incomplete step/check records prevent
comparison scoring while retaining available evidence for inspection.

Custom async factories now resolve correctly and receive independent nested
options. Public configuration rejects nested credentials and authenticated URLs;
guarded OpenAI handling recognizes the absolute DNS hostname spelling, validates
usage, and refuses nonstandard service tiers before a paid request.

`compare-report --format html --bundle NEW_DIRECTORY --crop-top PIXELS` exports
only regenerated HTML with working relative episode links and disclosed image
cropping. It never copies controller JSON, logs or original screenshot files,
and refuses existing destinations.

`reap --ledger PATH --dry-run` scopes recovery to exact ledger operations or
saved machine IDs, including uncertain creates. Omit `--dry-run` for cleanup;
account-wide selection requires `--all-sessions`. The command checks survivors
after killing and retains unknown charges. Do not use it on an active session.

### Provider cleanup, budget hold and spending

No local evaluation process survived. Provider metadata nevertheless listed two
session-owned desktops as running, approximately 10.249 and 10.005 hours after
creation. Recovery explicitly killed both; subsequent inventory and the
session-scoped CLI dry-run found no selected active machines. The new golden
snapshot was present and preserved; unrelated snapshots were not deleted.

The five-hour Starter reservation assumption was invalidated by those observed
lifetimes. The SDK timeout is idle-based, not a hard deadline. Closing a resource
now retains the larger observed exposure and persistently blocks further
reservations for that service in the existing ledger. `doctor` reports the hold.
**Do not recreate the ledger, raise a nominal lifetime or spend again until the
provider lifetime discrepancy and safe execution bound are resolved.**

| Service | Current recorded accounting | Meaning |
| --- | ---: | --- |
| OpenAI | $0.82271164 usage-priced + $0.53237280 uncertain = $1.35508444 | 1,172 responses and one unresolved request; not an invoice. |
| Solari | $3.40627836 retained pending exposure | Three original allocations; observed overrun retained; service blocked. Not a guaranteed new upper bound or invoice. |
| GPU | $0 | No allocation in this product session or recovery. |

Original authorized ceilings remain OpenAI $50 and Solari $20. No budget was
transferred. Recovery made no model requests, GPU allocations or new Solari
allocations. Existing desktops could accrue usage until the confirmed kills.
The ledger is `runs/product-20260915/session-ledger.sqlite`; provider recovery
receipt is `runs/product-20260915/recovery-resources.json`.

### Verification performed during recovery

An independently installed wheel in a clean virtual environment, outside the
checkout and without service credentials, successfully ran fake `doctor`, all
five `demo` controls, the supplied two-pair `compare`, and a cropped HTML-only
bundle. A separate external async factory completed four fresh policy instances
across two matched toy-world pairs, exercising independent nested options.
These are consumer-path smoke checks, not real browser-policy performance.

The source checkout's demo also met all five expected verifier outcomes.
Its offline comparison completed all four cells, two equivalent reset pairs,
and correctly labeled both intentionally incomplete policies as failures.
Recovered reports preserve all planned cells and withhold a winner.

Chromium opened the actual comparison and linked successful episode at
1280×800 and 390×844. The retained authorization, pass/scored/planned counts,
incomplete-run warning and crop disclosure were visible; no page-level horizontal
overflow was observed. Comparison tables scroll within their region on mobile.
The public summary contains eight cells, zero scripts, zero images and zero
outbound links. The report implementation detector returned no findings.

The fresh dependency resolution also emitted a Starlette/httpx deprecation warning;
the exercised workflows still completed. No warning was suppressed.

Final offline suite: **400 passed, 3 skipped, 3 dependency deprecation warnings**
in **117.92 seconds**, using `venv/bin/python -m pytest`. The recovery tests cover
provider errors versus real deadlines, truncated/missing evidence, crash-left
records, cleanup scoping, larger retained exposure, async factories and cropped
sharing without source-image mutation. No live or paid experiments ran in pytest.

All **72 relative links** in the updated entry-point and reference documents
resolved locally. The final wheel is
`runs/product-20260915/recovery/final-wheel/forkloop-0.2.0-py3-none-any.whl`;
it was installed and its doctor/report commands exercised outside the checkout.
Machine-readable verification is retained at
`runs/product-20260915/recovery/verification.json`. The disposable consumer
environment was removed after preserving those receipts.

Earlier dated sections below are historical. In particular, their `$0` execution
authorization, publication prerequisites and claims that comparison is still only
a research path do not describe this recovery's authorized implementation work.

## Current social video and launch copy

The current [demo.mp4](worked-example/demo.mp4) replaces the original 65-second
report recording with a **33-second, 1080×1350 (4:5), 30 fps** evidence explainer.
It is H.264/yuv420p with fast-start MP4 layout, **1,000,039 bytes**, and no audio.
The matching [poster](worked-example/demo-poster.png) is extracted at 14 seconds;
the [WebVTT](worked-example/demo-captions.vtt) has five descriptive scene cues.
Large on-screen copy carries the story without sound. The preferred X draft now
explains what Forkloop is before the example: **268 weighted characters** with
tags and link. Its limitation reply is 244 characters. Both remain unpublished.

This is an editorial composition of actual retained evidence, not a new browser
session recording or live agent replay. It shows the actual submission banner,
source-letter detail and entered-value field, alongside clearly explanatory
expected/actual values and check summaries. All image details come from the
already-reviewed, browser-chrome-cropped report. No raw screenshots were used.
The portable report and original verdict retain their pre-session SHA-256 values.

**Verification performed for this refinement:** all five compositions inspected
at export and 390-pixel phone scale, with no content overflow or footer overlap;
the design detector returned no findings. The complete 990-frame MP4 decoded
without errors. Chromium reported 33 seconds and 1080×1350, played the file without
an error, and sought to 2, 8, 15, 22 and 30 seconds for frame review. The mismatch
remains readable at phone scale. No application behavior changed and no application
test suite or live service was run.

The previous media files are preserved as `previous-demo.mp4`,
`previous-demo-poster.png` and `previous-demo-captions.vtt` under
`runs/social-video-refinement/`. That directory also retains the composition,
safe image derivatives, frame captures, encoding command and verification receipts.
It is local production material, not an expanded publication allowlist.
The historical release-preparation measurements below describe the previous
65-second edit, not the current files. Use the current media and social copy.

The source-publication boundary remains unchanged. Nothing was staged, committed,
pushed, posted or sent. No paid compute was used.

## Historical release preparation — 2026-09-11

**Ready locally: a no-install evidence report, a finished 65-second demonstration,
and unpublished launch/outreach copy. Nothing was staged, committed, pushed,
deployed, posted, submitted, or sent in this preparation session. Spending: $0.**
External feedback and a positive learning curve are not gates for submitting the build.

### What a reviewer should do

The root README now explains the build, Solari's resettable application state,
the concrete wrong-value failure, synthetic-data boundaries, and the next click.
Choose the report file, use GitHub's **Download raw file**, save `report.html`,
and open it locally. No Python, account, model endpoint, server or VM is needed.
GitHub's HTML source view is not a rendered report. No hosting was introduced.

The [public root README](https://raw.githubusercontent.com/rynitepsd-tech/forkloop/main/README.md)
was checked again on September 11 and still identifies the repository as Solari
Cookbook. The [intended report URL](https://raw.githubusercontent.com/rynitepsd-tech/forkloop/main/projects/forkloop/docs/worked-example/report.html)
returned **404**. The repository exists; the reviewed report/media and updated
entry path are not publicly available there yet. Do not send the launch post first.

### Exact assets and files to review

Paths below are relative to `projects/forkloop`:

| Asset | Ready local file | Details |
| --- | --- | --- |
| Portable report | [docs/worked-example/report.html](worked-example/report.html) | Self-contained; six retained images, each 1280×606 after the disclosed sharing crop. |
| Preferred attachment | [docs/worked-example/demo.mp4](worked-example/demo.mp4) | **65.00 seconds**, 1280×900, H.264/yuv420p, 15 fps, **1,063,254 bytes**; silent with burned-in captions. |
| Poster | [docs/worked-example/demo-poster.png](worked-example/demo-poster.png) | 1280×900; extracted from the actual video at 12 seconds. |
| Caption file | [docs/worked-example/demo-captions.vtt](worked-example/demo-captions.vtt) | Eight synchronized cues; the same words/timing are burned into the video. |

Review the root README, project README, worked-example README, and
[solari-social.md](solari-social.md) for the first-click path, preferred X post,
limitation reply, 199-word LinkedIn post, final captions, alt text and unsent outreach.
The narrow sharing fix changed `forkloop/cli.py`, `forkloop/report_html.py`,
`tests/test_report_html.py`, `docs/contracts.md` and `system.md`. No training,
infrastructure, benchmark coverage or live-execution path was added.

### Specific sharing issue found and fixed

The retained OpenEMR screenshot includes a session token in its browser address
bar. The export now supports explicit `--crop-top PIXELS`; the reviewed report
uses **114**. Cropping affects embedded sharing copies only and is disclosed in
the report and frame captions. A crop consuming a whole frame omits it rather
than falling back to an unredacted image. Historical PNGs and the verdict are
unchanged. Do not attach the original source PNGs or regenerate the sharing report
without the crop. This is not automatic secret detection for arbitrary recordings.

The chosen report's text and all six chosen screenshots were reviewed. No service
credentials or local-machine paths were found in the sharing copy. The task still
names its synthetic local demonstration login (`admin / pass`); it is not a real
service credential. Actual patient/claims names and values shown here are synthetic.

### Verification performed in this preparation session
When repeating the commands, first create a disposable output directory with
`REVIEW_TMP="$(mktemp -d)"`; the exact session paths are in the local receipt.


```bash
forkloop report docs/worked-example/episodes/resolve_denial-train-000200-018067 \
  --format html --crop-top 114 --out docs/worked-example/report.html
python -m pytest tests/test_report_html.py \
  -k 'sharing_crop or retained_live or png_metadata or screenshot_references or html_destination' \
  --basetemp "$REVIEW_TMP/pytest"
```

The export exited 0. **8 targeted checks passed, 5 deselected, in 0.31 seconds.**
Negative crop values and crop use with text output were rejected with exit 2.
The historical **330 passed / 3 skipped** result below was **not rerun** or
relabelled as current verification.

Chromium opened the actual report using `file://`, at 1280×720 and 390×844:
the failure and values were readable, source/portal frames were reachable, and
retention, crop, origin and scope labels were visible. No horizontal overflow,
scripts or external requests were observed. All six embedded images were checked
pixel-for-pixel against the corresponding original with only its top 114 rows
removed; metadata was stripped.

The existing successful and wrong-authorization control HTML files were also
opened locally for the session outline. They visibly identify constructed fake
controls and contrasting verdicts; no control run was repeated.

The MP4 records actual report interaction and image inspection, not an agent
reenactment. No offline controls or invented intermediate frames appear in it.
The complete MP4 decoded successfully (975 frames). It played in Chromium with
65-second duration, 1280×900 dimensions and no playback error. Frames at 2, 12,
20, 30, 38, 46, 54 and 61 seconds were inspected for readability, timing, crop
boundaries and provenance; the poster is an extracted video frame.

Receipts are local under `runs/release-prep-20260911T092222Z/`: commands,
targeted checks, source integrity, pixel review, desktop/mobile captures,
actual recording events, MP4 decode/playback, caption consistency, copy lengths
and **72 checked local documentation links**. Temporary recording/encoding tools
were installed only in a disposable environment; that environment and raw recordings
were removed after verification. All owned review/research tabs were released.
No permanent media dependency or application server was added.

### Small human publication checklist

- [ ] Review the HTML, MP4 and poster above, plus the exact launch copy.
- [ ] Authorize a curated repository publication so the report/media links work.
      Do not stage the whole working directory: unrelated code, datasets,
      checkpoints, raw artifacts and scratch outputs are outside this asset review.
      Publish only the named sharing assets from `docs/worked-example/`:
      `report.html`, `demo.mp4`, `demo-poster.png` and `demo-captions.vtt`, alongside
      the reviewed documentation. **Exclude `docs/worked-example/episodes/**/shots/**`
      and raw run metadata from that publication.** Do not stage the entire
      worked-example directory. Historical screenshots remain untouched; neither
      their deletion nor a `.gitignore` change is part of this preparation.
      The MP4 and its extracted poster use the cropped report render, not raw PNGs.
      README regeneration commands are explicitly local-retained-source-only;
      the public sharing assets can be inspected but do not reconstruct the
      historical episode's raw source files.
      A future source publication must verify its exact selected tree; the historical
      dirty-checkout test tally is not proof of an arbitrary curated commit.
- [ ] After confirming public download/open and video access, optionally authorize
      the **current preferred X post with the MP4**. Its limitation reply
      carries the essential evidence limits. LinkedIn is a separate prepared option.
- [ ] Use verified X tags **@harrychow_** and **@getsolari**; primary-source evidence
      is in the social draft. Your own X history remains unresolved and is not a gate.
- [ ] If desired, separately authorize the first DM to **Kevin Qinghong Lin / ShowUI**.
      Ask what model/prompt/observation/memory change he is deciding whether to keep,
      and whether this workflow exposes a failure his current evaluation misses.
      The no-install 15-minute outline and blank notes are ready; nothing was sent.

| State | What belongs here |
| --- | --- |
| **Ready locally** | Reviewed report/media, first-click docs, launch copy, sourced targets, unsent DM/email, 15-minute outline and blank evaluation note. |
| **Requires publication authorization** | Curated GitHub publication and verification of its public links; posting, submission and outreach each remain unauthorized. |
| **Requires an external response** | A real pending comparison, usefulness/friction feedback, interest or later use. None is established or needed to submit the internship build. |
| **Requires separate future spending authorization** | Any live Solari/model/GPU execution or training. Current financial authorization remains $0 for every service. |

**Smallest next decision:** authorize publication of the reviewed GitHub package.
There is no remaining blocker in the chosen local assets. Public access is blocked
by their unpublished state, not by missing model improvement, feedback, or a live run.

## Historical implementation record — 2026-09-10

The record below describes the earlier implementation session. Its commands,
test counts and measurements are historical, not newly exercised above. Its old
uncropped export commands are not the sharing recipe for the September 11 package.

<details>
<summary>Earlier implementation, 330-test result and evidence boundaries</summary>

# Forkloop product handoff — September 10 implementation session

**The retained live failure is now a self-contained HTML evidence file, generated
through `forkloop report`.** A reviewer can inspect the wrong persisted authorization
without accounts, a model endpoint, a server or a VM. Nothing was pushed, posted,
submitted, contacted or paid for in this session. The public `main` README inspected
read-only is older than this checkout; the no-account improvements are local, not
already delivered by a fresh public clone.

## Deliverables

- [Portable recorded-live report](worked-example/report.html) and
  [worked-example interpretation](worked-example/README.md). Download/open the HTML
  as a file rather than expecting GitHub to render it.
- `forkloop/report_html.py`, `forkloop/report.py`, `forkloop/cli.py`: HTML option,
  canonical check explanations, selected provenance, embedded PNGs, explicit
  missing evidence, and unchanged default text-report invocation.
- `scripts/verification_controls.py`: five reproducible controller-constructed
  offline scenarios using actual portal routes, reset, recorder and oracle.
- `forkloop/oracle.py` and behavioral regressions: exact-count shortfalls are
  `NOT_DONE`, not duplicate side effects; the exact-one invariant is unchanged.
- Root/project READMEs, `system.md`, contracts, limitations, cost and worked-example
  docs now distinguish current product behavior from historical measurements.
- [Unpublished X/LinkedIn drafts and demonstration outline](solari-social.md).
  The X draft is 240 weighted characters before verified tags. Actual social
  history and handles remain unresolved; drafts do not imply no prior posts exist.
  No additional upstream contribution is proposed because the existing snapshot/
  revert/fork example already covers those SDK operations.

## Commands exercised and outcomes

A **new disposable Python 3.11.14 venv**, not the existing development environment,
installed `.[dev]` successfully. The commands below ran from `projects/forkloop`
with that venv's executables. The runtime smoke removed credential/provider
variables from its subprocess environment. Installation downloaded packages;
subsequent project commands used no cloud or model service.

```bash
python3.11 -m venv "$VERIFY_TMP/venv"
"$VERIFY_TMP/venv/bin/pip" install -e '.[dev]'
python -m pip check
forkloop worlds
forkloop task --family resolve_denial --seed 200 --full
forkloop report docs/worked-example/episodes/resolve_denial-train-000200-018067
forkloop report docs/worked-example/episodes/resolve_denial-train-000200-018067 --format html --out docs/worked-example/report.html
forkloop run --backend fake --world claims-ops-v1 --family resolve_denial --policy scripted --seed 4 --runs runs --run-id offline-empty-policy
forkloop report runs/offline-empty-policy --failed
python -m scripts.verification_controls --out runs/offline-controls
forkloop report docs/worked-example --all --format html --out runs/product-verification-20260910/live-run.html
python -m pytest tests/test_report.py tests/test_report_html.py tests/test_claims_ops_world.py
python -m pytest tests/test_report_html.py tests/test_report.py --basetemp "$VERIFY_TMP/report-pytest"
python -m pytest --basetemp "$VERIFY_TMP/pytest" -o faulthandler_timeout=60
```

`VERIFY_TMP` represents the disposable directory used for each verification pass.
Both environments were removed; executable prefixes/temp paths are normalized here,
not a reusable preconfigured environment. Activate a newly installed venv for
another run. Choose fresh run IDs and a fresh controls output directory rather than
overwriting retained evidence.

Observed outcomes:

| Path | Result |
| --- | --- |
| Install / `pip check` | Success; no broken requirements. Solari SDK core/desktop/sandbox resolved to 0.2.0. |
| Worlds / task / retained text report | Exit 0; expected worlds and generated manifest; recorded `WRONG_VALUE`, reward 0. |
| HTML export / retained run report | Exit 0; generated file, one retained episode counted. A report command's success does not mean task success. |
| Empty scripted fake policy | Exit **1**, reward 0, `NOT_DONE`; zero appeals is still a failed exact-one assertion, not duplication. |
| Report of empty-policy run | Exit 0; simulation label, failed effects, no detected scoped side effects. |
| Constructed controls | Exit 0: all five intended control expectations met, development seed 20. |
| Initial targeted report/oracle tests | **33 passed**, two dependency warnings. |
| Initial post-redaction report regression check | **17 passed** in 3.18 seconds. |
| Final boundary report regression check | **19 passed** in 3.50 seconds. |
| Final full offline suite | **330 passed, 3 skipped**, four dependency warnings, 90.05 seconds. Torch-dependent modules were not exercised by `.[dev]`. |

The five control outcomes were correct appeal → `OK`/reward 1; wrong authorization
→ `WRONG_VALUE`; wrong-record appeal → `COLLATERAL_EDIT` plus `WRONG_RECORD`;
duplicate → `DUPLICATE_SIDE_EFFECT` with actual count 2; interruption → no verdict,
unscored. Each has its own normal Recorder directory under `runs/offline-controls/`.
Each run was also exported using `forkloop report runs/offline-controls/NAME --all
--format html --out runs/offline-controls/NAME.html`, with exit 0. These are not
policy success-rate measurements. The interruption uses a real abruptly exited
child recorder, not a deleted or hand-authored verdict.

The final boundary recheck used a second disposable Python 3.11 environment.
It reran `python -m scripts.verification_controls --out
runs/product-verification-20260910/controls-recheck`, with exit 0 and the same five
outcomes. The wrong-record acceptance check now requires both `no_collateral` and
`distractor_0_untouched`, including the latter's recorded `WRONG_RECORD` reason.
The targeted report tests passed **19 tests in 3.50 seconds** after these corrections.
The exact commands are in `scope-commands.json`; the control output is in
`controls-recheck.txt`, under the verification directory.

Local command receipts and browser captures: `runs/product-verification-20260910/`
(`commands.json`, command output files, `pytest-final.txt`, `browser-checks.json`,
`redaction-recheck.json`, desktop/narrow and document/form/final-screen captures).
These generated `runs/` artifacts are
local/gitignored; the portable worked example/report is under `docs/`.

## Browser observations

Opened the exported HTML through a `file://` URL in Chromium, without a server.
At **1440×1000** and **390×844**, the headline and authorization comparison were
readable and there was no horizontal page overflow. Long model/revision/adapter
hashes wrapped on the narrow viewport. Native disclosures opened/closed with Enter;
keyboard navigation reached the source section. The document, form-entry and final
portal frames loaded from embedded PNGs. The original six-frame limit and 142
unavailable references stayed visible; no replay was manufactured.
The final browser check also confirmed that the retained instruction appears
verbatim, including its credential-free loopback application URLs and punctuation.
Credential-bearing loopback URLs in the hostile fixture were omitted.

A separately labeled, deliberately altered rendering fixture exercised missing
metadata, one missing preserved screenshot, a missing invariant result, a long
model name and hostile-looking raw model output. Chromium displayed the script/img
payload literally: **zero script elements, no execution marker, zero external
requests**, explicit unknown origin/incomplete check evidence and 5/148 frames.
The actual interrupted-control report showed `NO_VERDICT`, fake/constructed labels,
and an unavailable rate instead of a scored model failure. Rendering fixtures are
not new live policy results.

A boundary fixture also confirmed that an errored check renders as `UNAVAILABLE`,
not a detected side effect, and that a missing checksum digest renders as unavailable
scope, not zero tables. Domain text such as `authorization: AUTH-36G14538` remains
visible while Bearer/Basic credential values are omitted. These checks used Chromium
at 390×844 with no overflow, script execution or external requests; receipts and
captures are `scope-browser.json`, `boundary-browser.json`,
`checksum-scope-unavailable.png` and `raised-check-unavailable.png`.

## Actual bugs corrected

The report previously said “none detected” when invariant results were absent.
It now marks incomplete coverage, while retaining any known failed checks. Missing
rewards are separately counted and excluded from the run report's recorded-outcome
denominator rather than silently counted as failed policy episodes. A missing
recording timestamp is no longer labeled “no verdict” when a verdict exists.

The exact-one appeal check previously labeled a zero-count shortfall as
`DUPLICATE_SIDE_EFFECT`. New oracle evaluations classify that failed equality as
`NOT_DONE`; excess still means duplicate. Neither the invariant nor the retained
historical verdict was changed. The worked-example verdict SHA-256 still equals
`7d405b8fde73d2ea26b89ba9b1b768bf7bcaae9a91d44ae579a4d3367b1a3c08`.

The export sanitizer initially over-redacted local application URLs, including
matching part of `http://` as a Windows path. URL-aware redaction and a drive-letter
boundary now preserve the retained task while removing infrastructure and
credential-bearing URLs. Both the regression test and Chromium check cover this.

The standard CLI recorder does not retain `baseline-digest.json`; that file is
optional research-evaluator evidence. Text and HTML now distinguish an absent
digest from an explicitly empty table map, and the public artifact contract says
so. Raised checks no longer inherit a policy-violation accusation from their
configured failure reason. Domain authorization numbers are no longer treated
as HTTP authorization credentials. Publication status and session test tallies
are kept in this dated handoff rather than the public product introductions.

## What remains unverified

No Solari allocation/build/reset, fresh-account setup, model endpoint, GPU/training
or live evaluation was attempted. Current reporter/control/oracle changes are
verified offline, not by historical live measurements. The revised live search
path and previously unstable Chrome renderer remain separately unverified.
The spend guard is limited and dated; arbitrary model endpoints are not universally
capped. No prospective user participated, so no interest, adoption, independent or
repeat usage, or decision impact is established.

The model evidence remains **0/2 base and 0/2 frozen adapter** in the paired live
comparison, historical base/SFT **0/30**, saved-observation diagnostic gains only,
and magnification stopped before scoring. Scope-limited checks are not production
certification or proof against forged audit logs. HTML redaction is not a universal
secret scanner; owners must review screenshot pixels and free text before sharing
other recordings.

## Cleanup and the next human action

Session-created disposable environments, fake machines and altered source copies
are removed; managed browser tabs are released. No local server was needed. Original
local work, datasets, checkpoints and historical artifacts are preserved.

**Give one GUI-agent researcher the portable HTML and ask which concrete policy
comparison would justify running Forkloop.** Use the README's decision-focused
feedback request, not a generic request to like the idea. This is a proposed human
action, not outreach performed by the assistant.

</details>
