# Recorded live failure: submitted, but the authorization was wrong

**[Open the hosted evidence report](https://rynitepsd-tech.github.io/forkloop/report.html)**.
No Python, installation, account, model endpoint or Solari session is needed.
For offline inspection, [download the self-contained HTML](report.html) using
GitHub's **Download raw file** action and open it locally. The HTML embeds its
images and was generated from retained artifacts, not manually entered result data.

[33-second evidence explainer (MP4; download and play, not a replay)](demo.mp4)

The silent video uses large on-screen explanation, 1080×1350 portrait framing
(4:5), and approximately 1.0 MB. It is an editorial explainer built from actual
retained image details and annotated recorded values, not a fresh screen recording.
[Poster](demo-poster.png) · [Descriptive WebVTT captions](demo-captions.vtt).
Both the video and report use sharing copies with the top 114 screenshot pixels
removed to omit browser chrome containing a session token. The crop is disclosed;
historical source screenshots and verdicts are unchanged.
The video additionally enlarges detail crops for phone readability. Its comparison
and check summaries are explanatory graphics, not recreated application screens.

The synthetic payer portal showed **“Appeal submitted.”** The recorded database
query found `AUTH-3614538`, where the task required `AUTH-36G14538`. Forkloop rejected
the episode with `WRONG_VALUE`, reward **0**. Submission and the policy's success
message were not sufficient.

This was a **live policy episode on a Solari desktop**, with real OpenEMR 8.3,
a synthetic payer portal and synthetic patient/claims data. Solari snapshots,
revert and fork make the application/workflow state reusable and resettable;
they are not needed to open this static report. The separate offline constructed
controls test verifier behavior, not policy performance.

Start with the report's expected/actual check, then inspect the document, typed
value and submitted form. **Six of 148 referenced frames are retained across
74 recorded steps**; this is an inspectable failure, not a complete visual replay.

## Optional: inspect or regenerate with Python

**Local retained-source checkout only.** The existing HTML needs no regeneration.
The commands below require the original episode metadata and screenshots, which
are excluded from the public sharing package. A fresh public clone or asset download
does not include those sources and cannot regenerate this historical report.
The artifact inventory below describes retained local source files, not promised
public bundle contents.

If you have that retained-source checkout, start from `projects/forkloop` after the
[README's optional Python installation](../../README.md#try-it-without-an-account):

```bash
forkloop report docs/worked-example/episodes/resolve_denial-train-000200-018067
forkloop report docs/worked-example/episodes/resolve_denial-train-000200-018067 \
  --format html --crop-top 114 --out docs/worked-example/report.html
forkloop report docs/worked-example --all
```

The first command preserves the text report. The second writes one shareable HTML
file containing checks, provenance, scoped interpretation and labeled screenshot
derivatives with the top 114 pixels cropped to remove browser chrome containing an
OpenEMR session token. Original screenshots and verdict bytes remain unchanged.
The third inspects the **one episode present here**, not the original two-episode
adapter cohort. None reconnects to Solari or reruns a database query.

## What was retained

| Artifact | Source and limits |
| --- | --- |
| Episode | `runs/live-paired-v3-20260906-trained/episodes/resolve_denial-train-000200-018067`; recorded finish `2026-09-06T22:21:47+00:00`. |
| World | Real OpenEMR 8.3 and a synthetic payer portal on a Solari desktop; synthetic patient/claims data. |
| Model | `microsoft/Fara1.5-4B` plus frozen v3 LoRA adapter. `run.json` records model/adapter hashes; the separate policy-name field is absent, not guessed. |
| Source | Recorded HEAD `973a90d`; the live session also had uncommitted changes. That short SHA alone does not identify the whole execution source. See the [paired results](../live-paired-v3-results.md#harness-and-backend-changes). |
| Original files | `run.json`, episode `manifest.json`, `verdict.json`, `steps.jsonl`, `reset.json`, `accounting.json`, `baseline-digest.json`. Original artifacts were not rewritten for the new renderer. |
| Verdict fingerprint | SHA-256 `7d405b8fde73d2ea26b89ba9b1b768bf7bcaae9a91d44ae579a4d3367b1a3c08`, checked again during offline integration. A fingerprint identifies bytes, not authenticity. |
| Screenshots | **6 of 148 referenced frames**, across **74 recorded steps**. The other 142 are unavailable in this portable copy. This is not a complete replay. |

## Follow the value

Expand these frames in the HTML report. Their labels describe actual recorded
actions; the interpretation below is specific to this retained episode.

- `046_before.png`: the authorization letter in OpenEMR, at 75% viewer zoom.
- `066_before.png` and `066_after.png`: the appeal form and the typed wrong value.
- `072_after.png` and `073_after.png`: submission and the terminal portal screen.
- `000_before.png`: the initial preserved screen. Step 73's **before** frame was
  not preserved; only its after frame is included.

The model alternated between `AUTH-36I4538` and `AUTH-3614538` in its output and typed
the latter at step 66. Five returns to the document did not correct it. This supports
investigating reading/verification at that state, not claiming magnification fixes it.
The later [magnification diagnostic](../document-magnification-results.md) stopped
before model scoring.

## What the verifier established

**Effects: 2/3 passed.** Claim status was `APPEAL_SUBMITTED`; appeal reason was
`PRECERT_OBTAINED`; the persisted authorization was wrong.

**Invariants: 5/5 passed in this recording.** Exactly one appeal was present. The
specified distractor remained denied. The checksum check detected no unexpected
changes outside its allow-list in the 14 recorded tables; the target claim row was
allowed and the appeals table exempt. The audit tripwire found a matching audit row
for the changed claim, and the portal check found no forbidden page among seven
recorded page views. This does **not** establish that nothing else changed, that every
write was necessarily UI-originated, or that every invariant detects every violation.
The report exposes the original query/allow-list/exemption definitions; the digest
does not independently enumerate all historical ignored columns.

**Milestones: 6/6 reached, still a failed task.** Login, chart, document, claim, appeal
form and submission are diagnostic progress signals, not reward or exact-entry proof.
Separate [offline controls](../../README.md#try-it-without-an-account) now show
representative verifier acceptance and rejection, but are deliberately constructed
SQLite/HTTP scenarios, not additional live agent episodes.

## Evidence boundaries

This is one selected episode from a completed comparison of two development seeds:
**base 0/2, frozen adapter 0/2**. It does not establish a success rate or a positive
small-model learning curve. Historical base/SFT development runs were **0/30** each;
saved-observation improvements are a different diagnostic measurement.

The recorded reset took 31.0 seconds; that favorable individual reset is not typical
platform latency. Token usage was 305,680 input / 4,562 output in `accounting.json`,
not a dollar invoice. No new live execution, adoption or production-readiness claim
is made. See [limitations](../limitations.md) and the [current system state](../../system.md).

The export is static, script-free and self-contained, with text escaping, bounded
PNG decoding and traversal/symlink rejection. It omits infrastructure references from
text and strips PNG metadata. This report also crops browser chrome as described
above; metadata stripping alone does not remove secrets visible in pixels.
Review screenshots/free text before sharing other recordings or original source
images; this is not a general secret scanner.
