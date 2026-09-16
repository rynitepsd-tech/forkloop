# Forkloop launch and first evaluation — UNPUBLISHED / UNSENT

**Prepared copy, not publication or contact authorization.** Nothing here is a
posting receipt, an outreach result, an endorsement, or an upstream submission.
Historical technical reports remain in [solari-message.md](solari-message.md)
and [spikes.md](spikes.md).

**Social history is unresolved.** Earlier drafts and the
[planned-post table](buildlog.md#planned-posts-from-the-plan-11) are not posting
receipts. No actual X/LinkedIn post URL or account-history evidence was found in
the repository review; that does not establish that no posts exist. Preserve
that uncertainty without blocking preparation of these new, unsent drafts.

## Sharing gate — curated evidence is public

The reviewed [evidence report](https://rynitepsd-tech.github.io/forkloop/),
[recovered comparison](https://rynitepsd-tech.github.io/forkloop/navigation-comparison.html)
and [33-second video](https://rynitepsd-tech.github.io/forkloop/demo.mp4) are hosted
on GitHub Pages. The report can also be downloaded and opened offline.
This publication does not establish external feedback or adoption.

Social posts and direct outreach below remain **unpublished and unsent**.
Do not send original source PNGs: a browser address bar contains a session token.
Share only the explicitly reviewed HTML and media. The Pages branch contains
only those selected assets, not source-run metadata or raw screenshots.
The larger local screenshot-bearing comparison bundle still requires pixel and
free-text review before sharing. New Solari allocations remain paused.

## Verified launch tags — public primary sources

Checked 2026-09-11, using public pages only; no login, follow, message, or contact:

| Intended name | Verified exact X handle | Accessible evidence |
| --- | --- | --- |
| Harry Chow | **@harrychow_** | [Public X profile](https://x.com/harrychow_) displays “Harry Chow”, `@harrychow_`, and “Building … @getsolari”. His [public LinkedIn post](https://www.linkedin.com/posts/harry-chow1_im-hiring-a-growth-engineer-for-solari-activity-7496999620317749248-I-WZ) independently identifies his Solari work. |
| Solari | **@getsolari** | The [official Solari homepage](https://www.getsolari.com/) links directly to `https://x.com/getsolari`; that [public profile](https://x.com/getsolari) displays `@getsolari`, “The Fastest Agent Infra”, and `getsolari.com`. |

The text reader could not fetch either X profile; a separate, unauthenticated
browser could display both. Verification is of the identity/handle relationship,
not an endorsement. A same-name GitHub profile was not used to establish Harry's
identity. Do not invent alternate handles if access changes.

## X — preferred launch, UNPUBLISHED

> I built Forkloop to test AI agents that use computers.
>
> In this run, an agent filed an insurance appeal with the wrong code. The website accepted it. Forkloop caught the mistake in the database.
>
> Synthetic data. Built on @getsolari.
> @harrychow_ https://github.com/rynitepsd-tech/forkloop

**268 X-weighted characters**, including paragraph breaks, both tags and the
intended GitHub URL (URL counted as 23; blockquote markers excluded). Attach the
33-second portrait edit below. The opening explains the product before the
example; “database” replaces “persisted state.” The sharing gate above applies.

### Short limitation reply — UNPUBLISHED

> This video explains one recorded run, not a live replay. Real OpenEMR; synthetic payer portal and data. The report retains 6 of 148 screenshots. Offline controls test the verifier, not the agent. No end-to-end model improvement is demonstrated.

**244 characters**; no URL or tags. Do not turn a request for feedback
into a claim that a researcher has evaluated or adopted Forkloop.

## LinkedIn — UNPUBLISHED

> The portal said "Appeal submitted." The saved authorization was wrong.
>
> The agent's task was to find an authorization in OpenEMR documents and file exactly one appeal in a payer portal. In one retained Forkloop episode, a small GUI model entered AUTH-3614538 instead of AUTH-36G14538. The persisted-state verifier returned WRONG_VALUE, reward 0, despite the model declaring success.
>
> The workflow uses real OpenEMR 8.3 with synthetic records and a synthetic payer portal, not a healthcare deployment. The local report connects the expected value, recorded verdict, typed action, and retained screenshots. Six of 148 frames are retained across 74 steps; it is inspectable evidence, not a complete replay.
>
> Solari's snapshot, revert, and fork capabilities make application state reusable and resettable for controlled comparisons. They do not make this episode a success. Opening the self-contained report requires no Solari account, installation, or model calls: download report.html and open it locally.
>
> I am building Forkloop with AI assistance and looking for researcher feedback, not endorsements. Would this evidence change your next model, prompt, observation, or memory experiment? What comparison or missing evidence would you need before deciding?
>
> No small-model end-to-end improvement is demonstrated. Constructed offline controls exercise the verifier, not policy performance.
>
> Repository: https://github.com/rynitepsd-tech/forkloop

**199 words**, whitespace-delimited, including “Repository:” and its URL;
blockquote markers excluded. The sharing gate applies. Names can be plain text
on LinkedIn; verified X handles do not establish LinkedIn mention entities.

## Media — retained-evidence explainer, UNPUBLISHED

- Preferred attachment: [33-second MP4](worked-example/demo.mp4), **1080×1350
  (4:5)**, H.264/yuv420p, 30 fps, **1,000,039 bytes**. Silent, with large on-screen
  explanation, restrained crossfades and no generated voice or music.
- [Poster](worked-example/demo-poster.png), 1080×1350, extracted at 14 seconds.
- [WebVTT captions](worked-example/demo-captions.vtt), five synchronized descriptive
  cues. These summarize each scene; they are not a verbatim transcription of every
  label visible on screen.

This edit replaces the dense 65-second report recording. It is an **editorial
explainer assembled from actual retained evidence**, not a fresh screen recording,
live agent run or complete replay. Enlarged expected/actual values and check rows
are explanatory graphics derived from the recorded verdict, not recreated
application UI. The banner, source letter and input field are detail crops of the
already-reviewed report's embedded images. No missing agent frames were invented.
No offline controls were spliced into the live trajectory.

The report's sharing copies already omit the top 114 browser-chrome pixels.
The video makes additional disclosed detail crops for phone legibility. It does
not alter the historical screenshots, verdict or portable report. The report
still contains all six preserved sharing images.

### Final scene timing and descriptive captions

Every scene identifies synthetic data and recorded evidence. The final scene
states that six of 148 screenshot references are retained.

| Time | Descriptive caption |
| --- | --- |
| 00:00–00:05 | An AI agent filed an insurance appeal. The website said success. The agent was wrong. |
| 00:05–00:11 | Its job: read the authorization code in OpenEMR, then submit one appeal in the payer portal. |
| 00:11–00:19 | Expected: AUTH-36G14538. Saved: AUTH-3614538. One missing G changed the value. |
| 00:19–00:25 | Forkloop checked the database. The appeal was submitted, but the authorization was wrong. WRONG_VALUE: task failed. |
| 00:25–00:33 | Forkloop tests what computer-use agents actually change. Reset with Solari, run the agent, check the database. Inspect the evidence on GitHub. |

The five scenes crossfade over 0.25 seconds at each boundary; the video is designed
to be understood with sound off. Small source-image text is backed by large,
explicit explanatory type rather than requiring a viewer to read an entire UI.

### Final accessibility descriptions

**Video description:** “A 33-second portrait explainer of Forkloop, an evaluation
environment for AI agents that use computers. An actual recorded portal banner
reads ‘Appeal submitted.’ A source-letter crop shows the agent's task. Large text
compares expected AUTH-36G14538 with saved AUTH-3614538, highlighting the missing G.
An actual input-field crop corroborates the entered value. A summary of the
recorded database checks shows that submission passed but the authorization
failed: WRONG_VALUE, reward 0. The ending explains resetting with Solari, running
the agent and checking the database. Synthetic data, selected recorded evidence,
not a live replay; six of 148 frames retained.”

**Poster alt text:** “Forkloop: One missing letter. A different code. Expected
authorization AUTH-36G14538 has its G highlighted; the saved value is AUTH-3614538.
A detail crop of the actual agent input contains the same incorrect value.
Labels identify synthetic data and values from a recorded verdict.”

## First external evaluation — prepared, UNSENT

### Two concrete candidates, prioritized by fit

These are research candidates, not contacted users. Public identity links are
provided instead of a contact list; no private addresses or inferred handles
are included. Neither candidate's availability or interest is known.

| Priority | Person / project | One-sentence fit and primary source |
| --- | --- | --- |
| **1 — preferred** | **Kevin Qinghong Lin / ShowUI**, **@KevinQHLin** | ShowUI's lightweight GUI model and grounding/navigation evaluation make him a concrete reviewer of whether a wrong-value trace supports changing observations versus model behavior: the [project README](https://github.com/showlab/ShowUI) names him as first author, and his [own homepage](https://qinghonglin.github.io/) links ShowUI and the exact [Twitter handle](https://twitter.com/KevinQHLin). |
| 2 | **Thibault Le Sellier De Chezelles / BrowserGym ecosystem**; no X handle asserted | As first author of the [BrowserGym ecosystem paper](https://arxiv.org/abs/2412.05467), which introduces AgentLab's testing/analysis role, he is a fit for judging whether Forkloop adds useful persisted-state evidence beyond existing [AgentLab experiment and trace tooling](https://github.com/ServiceNow/AgentLab). |

Prefer a narrow request to Kevin about an observation-versus-policy decision,
not a general “please promote this” request. Thibault is a research-author
candidate, not a claim about current maintainership or a small-company lead.
The two candidates are sufficient; no third target is added merely to fill a
quota. For any later authorized email, use only a channel the person publicly
offers; do not infer an address. Do not file a repository issue as unsolicited
promotion.

### Concise DM — to Kevin, UNSENT

> Hi Kevin — your ShowUI work is why I’m asking: what model, prompt, observation, or memory change are you deciding whether to keep? Forkloop caught an agent submitting an appeal with the wrong saved authorization. Would this expose a failure your current evaluation misses? I can send the no-install report for a 15-minute inspection of synthetic-data evidence and separate verifier controls. No live run, paid compute, or endorsement needed.

### Slightly longer email — to Kevin, UNSENT

**Subject:** Would this wrong-value GUI trace change your next experiment?

> Hi Kevin,
>
> Your ShowUI work on lightweight GUI models and grounding/navigation evaluation seems closely related to a question I'm testing with Forkloop.
>
> In one retained Solari episode, the portal said “Appeal submitted” but the agent saved AUTH-3614538 instead of AUTH-36G14538. The persisted-state verdict was WRONG_VALUE, reward 0. The workflow uses real OpenEMR 8.3, synthetic records, and a synthetic payer portal.
>
> Would you be willing to inspect the reviewed local HTML report for 15 minutes? It needs no installation, account, live environment, model calls, or paid compute. We can compare your current debugging method with the report's action/verdict evidence, then inspect constructed offline controls separately. Those controls test verifier behavior, not agent performance.
>
> What model, prompt, observation, or memory change are you deciding whether to keep, and would this workflow expose a failure your current evaluation misses? What comparison or missing evidence prevents that decision? No endorsement requested; declining with a reason is useful.
>
> Only six of 148 frames are retained, so this is not a complete replay or an improvement claim. I can send the reviewed report directly; the local artifact updates are not yet on the public repository.
>
> Thanks for considering it.

These drafts deliberately offer direct file sharing rather than promise a
working public report URL. No DM or email has been sent.

### Fifteen minutes, existing evidence only

No Solari setup, VM creation, cloud calls, model endpoints, training, or paid
compute. Use the existing report and already generated offline-control
artifacts. The report itself does not require Solari.

Facilitator files, relative to `projects/forkloop`: open
`runs/offline-controls/legitimate_correct_appeal.html` for the existing `OK`/reward-1
control, and `runs/offline-controls/wrong_authorization.html` for the existing
`WRONG_VALUE`/reward-0 contrast. Both were opened and inspected locally; neither
was rerun for this preparation. These gitignored local session props are not
public URLs or part of the approved launch attachments.

| Time | Activity and observable question |
| --- | --- |
| 0–3 min | Ask for their current GUI debugging/evaluation method and a real pending model, prompt, observation, or memory decision; do not assume they have one. |
| 3–7 min | Let them inspect the reviewed HTML report without hints first: locate expected/actual authorization, verdict, typed action, and evidence limits. Record any assistance separately. |
| 7–10 min | Compare with their current method: what can they decide here that they could not decide there, and what information is still absent? No fabricated baseline run. |
| 10–12 min | Inspect the existing offline controls, visibly labeled **constructed control, fake backend**. Ask whether their differing verdicts clarify verifier boundaries; a success control is not policy success. |
| 12–15 min | Ask them to choose the next experiment or explicitly defer it; record the reason, missing comparison, and whether they would use this on their own evidence or decline. Do not solicit a testimonial. |

This session can measure comprehension and decision usefulness, not improvement
in agent success rates. An assisted walkthrough is not independent use. Any
later repeat evaluation requires its own observation and authorization, not a
prescheduled compute run.

### Compact blank evaluation note

Leave unobserved fields blank; do not prefill candidate responses.

```text
Date / evaluator-approved identifier:
Current method and actual pending decision:
Comparison inspected (or unavailable):
Missing evidence / unresolved question:
Friction / assistance given:
Use or decline, with reason:

Interest expressed (exact evidence/date):
Assisted inspection completed (what required help):
Independent use observed (what they did unaided):
Repeat use observed (separate occasion/evidence):
Decision impact (model/prompt/observation/memory; chosen/deferred; why):
Permission for any quote or attribution:
```

Keep **interest, assisted inspection, independent use, repeat use, and decision
impact separate**. A reply, meeting, download, or compliment alone is not
adoption, repeat use, or decision impact. At preparation time none of those
outcomes has been established for these candidates.

## Upstream decision — no new submission

**No upstream submission is part of this package.** The existing root cookbook example,
[`examples/desktop-snapshot-revert-py`](../../../examples/desktop-snapshot-revert-py/README.md),
already creates a desktop, snapshots mousepad state, reverts and reconnects,
captures the restored screen, forks independently, checks file isolation, and
cleans up the VMs and snapshot. A new snapshot/reset/fork example would duplicate
that work.

The inspectable persisted-value verdict is a different capability, but it is
currently a Forkloop-specific report over its recorder/oracle artifacts, not an
independently justified missing Solari SDK example. Keep it in this project.
There is no demonstrated upstream gap or external request here that warrants
another contribution, and no acceptance should be implied.

## Evidence behind these drafts

- [Retained worked example](worked-example/README.md), including the episode's
  `manifest.json`, `verdict.json`, `steps.jsonl`, and six screenshots.
- [Paired live results](live-paired-v3-results.md): two development seeds,
  four completed cells, no end-to-end successes.
- [Historical evaluation ladder](spikes.md): base/SFT full-workflow results;
  distinct experiments, not pooled with the two-seed pairing.
- [Saved-observation comparison](frozen-v3-evaluation-results.md): diagnostic
  gains at teacher-reached states, not independent navigation.
- [Magnification preparation stop](document-magnification-results.md): no model
  scoring; missing outcomes must not be reported as failed model attempts.
