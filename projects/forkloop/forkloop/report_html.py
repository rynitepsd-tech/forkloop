"""Offline HTML presentation of the canonical recorded report. No network or script runtime."""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from .report import (
    BACKEND_NOTES, _action_text, _check_evidence, _check_line, _missing_checks,
    _read_json, _side_effect_summary, episode_report, report, run_meta_for,
)
from .trajectories import iter_episode_dirs, load_episode

# Read mode, extending the portal's system type and slate palette. The verdict and
# compared values lead; document-sized frames follow. Native details controls keep
# a shared file keyboard-accessible with no JavaScript, fonts, or hosted services.
_STYLE = """
:root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #172333; background: #f3f4f6; }
* { box-sizing: border-box; }
html { scroll-padding-top: 20px; }
body { margin: 0; font-size: 16px; line-height: 1.6; }
main { max-width: 1120px; padding: 32px 36px 80px; margin: auto; background: #fff; }
header { border-bottom: 1px solid #cbd2da; padding-bottom: 28px; }
.brand { font-weight: 700; font-size: 18px; margin: 0; }
h1 { font-size: clamp(2rem, 5vw, 3.5rem); line-height: 1.12; letter-spacing: -.025em; margin: 24px 0 16px; overflow-wrap: anywhere; }
h2 { font-size: 1.5rem; line-height: 1.3; margin: 40px 0 16px; }
h3 { font-size: 1.08rem; margin: 24px 0 8px; }
p { max-width: 74ch; margin: 12px 0; overflow-wrap: anywhere; }
a { color: #1d4ed8; text-underline-offset: 3px; }
a:hover { color: #12338e; }
a, summary { touch-action: manipulation; }
:focus-visible { outline: 3px solid #1d4ed8; outline-offset: 4px; }
.skip-link { position: absolute; top: 8px; left: 8px; transform: translateY(-200%); padding: 8px 16px; background: #fff; z-index: 1; }
.skip-link:focus { transform: none; }
nav { display: flex; flex-wrap: wrap; gap: 8px 24px; margin-top: 24px; }
nav a { padding: 8px 0; }
.bad { color: #9b2525; } .good { color: #176447; } .unknown { color: #795211; }
.label { display: inline-block; font-weight: 650; border: 1px solid #9aa8b8; padding: 3px 9px; margin: 12px 8px 0 0; }
.muted, figcaption { color: #4b5563; }
.comparison { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); border-block: 1px solid #cbd2da; margin: 20px 0; gap: 24px; padding: 16px 0; }
.comparison dt { margin-bottom: 8px; }
.comparison dd { font-size: clamp(1.15rem, 2.5vw, 1.6rem); margin: 0; }
code, pre { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; }
pre { white-space: pre-wrap; font-size: .85rem; background: #f3f4f6; padding: 16px; margin: 12px 0; }
dl.meta { display: grid; grid-template-columns: minmax(110px, 180px) minmax(0, 1fr); gap: 8px 20px; }
dl.meta dt { color: #4b5563; } dl.meta dd { margin: 0; overflow-wrap: anywhere; }
.check { border-top: 1px solid #d8dee6; padding: 12px 0; }
.check p { margin: 6px 0; }
.check summary { font-weight: 600; }
details { margin: 12px 0; }
summary { cursor: pointer; padding: 10px 0; min-height: 44px; overflow-wrap: anywhere; }
summary:hover { color: #1d4ed8; }
figure { margin: 16px 0 28px; }
figure img { display: block; width: 100%; height: auto; border: 1px solid #cbd2da; }
.image-view { max-width: 100%; overflow: auto; }
.image-view img { max-width: none; }
.pixel-toggle { width: 18px; height: 18px; accent-color: #1d4ed8; vertical-align: middle; }
.pixel-label { display: inline-block; padding: 10px 4px; cursor: pointer; }
.pixel-toggle:checked ~ .image-view img { width: auto; }
figcaption { font-size: .9rem; padding-top: 8px; overflow-wrap: anywhere; }
.frame-index { display: flex; flex-wrap: wrap; gap: 8px 20px; }
.frame-index a { padding: 8px 0; }
.trace { list-style: none; padding: 0; }
.trace-row { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 8px 16px; border-top: 1px solid #d8dee6; padding: 16px 0; }
.trace-row p, .trace-row details { margin: 0; }
.trace-row .step-evidence { margin-top: 8px; }
.step-evidence { display: flex; flex-wrap: wrap; gap: 4px 20px; }
.step-evidence a { display: inline-block; padding: 8px 0; }
.frame { border-top: 1px solid #d8dee6; padding: 4px 0; }
.frame summary { font-weight: 600; }
.check-links { display: flex; flex-wrap: wrap; gap: 4px 20px; }
.check-links a { padding: 8px 0; overflow-wrap: anywhere; }
.missing { color: #795211; }
.summary-line { font-size: .95rem; color: #4b5563; }
:target { outline: 2px solid #1d4ed8; outline-offset: 5px; }
ul { padding-left: 24px; }
section, article, details, .check, .trace-row { scroll-margin-top: 20px; }
.episode + .episode { border-top: 2px solid #cbd2da; margin-top: 56px; padding-top: 24px; }
footer { border-top: 1px solid #cbd2da; margin-top: 48px; padding-top: 20px; font-size: .9rem; }
@media (max-width: 600px) { main { padding: 24px 18px 48px; } .comparison { grid-template-columns: minmax(0, 1fr); gap: 16px; } dl.meta { grid-template-columns: minmax(0, 1fr); gap: 2px; } dl.meta dd { margin-bottom: 12px; } .trace-row { grid-template-columns: minmax(0, 1fr); gap: 4px; } nav { gap: 4px 20px; } }
@media print { main { max-width: none; padding: 0; } nav { display: none; } details { break-inside: avoid; } }
"""


def _public_text(value: Any) -> str:
    """Redact infrastructure references before escaping; never publish raw run metadata.

    This is defense in depth, not a secret scanner. Screenshots and free text still
    require the owner's review before sharing arbitrary recordings.
    """
    text = str(value)
    def redact_url(match: re.Match[str]) -> str:
        raw = match.group()
        url = raw.rstrip(".,;!)")
        punctuation = raw[len(url):]
        try:
            parsed = urlsplit(url)
            local_app = (parsed.scheme in ("http", "https") and parsed.hostname in ("localhost", "127.0.0.1", "::1")
                         and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment)
        except ValueError:
            local_app = False
        return (url if local_app else "[URL omitted]") + punctuation

    text = re.sub(r"(?:https?|wss?|file)://[^\s<>\"']+", redact_url, text, flags=re.I)
    text = re.sub(r"(?:\b[A-Za-z]:[\\/]|/(?:Users|home|private|var|tmp|etc|opt|root)/|~/)[^\s<>\"']+", "[private path omitted]", text)
    text = re.sub(r"\b(?:sk-|slr_live_|slr_test_)[A-Za-z0-9_-]+", "[credential omitted]", text)
    text = re.sub(r"\b[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]+)?", "[capability omitted]", text)
    text = re.sub(r"(?i)\b(api[_ -]?key|access[_ -]?token|secret|password)[\"']?\s*[:=]\s*[\"']?(?:Bearer\s+)?[^\s,;\"']+", r"\1=[credential omitted]", text)
    text = re.sub(r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?(?:Bearer|Basic)\s+[^\s,;\"']+", "authorization=[credential omitted]", text)
    return text


def _text(value: Any) -> str:
    return html.escape(_public_text(value), quote=True)


def _json(value: Any) -> str:
    return _text(json.dumps(value, ensure_ascii=False, indent=2))


def _image(ep: Path, rel: Any, crop_top: int = 0) -> tuple[str | None, str]:
    """Only local PNGs under this episode's real shots directory; re-encode pixels.

    Reject traversal and symlinks, including a symlinked shots directory. Re-encoding
    strips metadata and prevents a mislabeled SVG/HTML file from becoming content.
    """
    if not isinstance(rel, str):
        return None, "Screenshot reference unavailable"
    part = Path(rel)
    if part.is_absolute() or not part.parts or part.parts[0] != "shots" or ".." in part.parts or part.suffix.lower() != ".png":
        return None, "Screenshot reference rejected (outside evidence location or not PNG)"
    target = ep / part
    if any((ep / Path(*part.parts[:i])).is_symlink() for i in range(1, len(part.parts) + 1)):
        return None, "Screenshot reference rejected (symlink)"
    try:
        target.resolve().relative_to((ep / "shots").resolve())
        if not target.is_file():
            return None, "Screenshot not preserved"
        if target.stat().st_size > 20 * 1024 * 1024:
            return None, "Screenshot unavailable (file exceeds 20 MiB limit)"
        with Image.open(target) as image:
            if image.format != "PNG" or image.width * image.height > 16_000_000:
                return None, "Screenshot unavailable (invalid format or dimensions)"
            if crop_top >= image.height:
                return None, "Screenshot unavailable (crop removes the entire frame)"
            pixels = image.crop((0, crop_top, image.width, image.height)) if crop_top else image.copy()
            if pixels.mode != "RGB":
                pixels = pixels.convert("RGB")
            pixels.info.clear()
            output = io.BytesIO()
            pixels.save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii"), "Preserved screenshot"
    except (OSError, ValueError, Image.DecompressionBombError):
        return None, "Screenshot unavailable (unreadable PNG)"


def _metadata(run: dict[str, Any], verdict: dict[str, Any], manifest: dict[str, Any]) -> str:
    identity = run.get("model_identity") or {}
    rows = [("Recorded finish", verdict.get("finished_at")), ("Run started", run.get("started_at")),
            ("Policy", run.get("policy")), ("Model label", run.get("model")),
            ("World", manifest.get("world")), ("Source revision", run.get("git_sha"))]
    for key in ("base_model", "base_revision", "adapter_sha256", "serving_source_sha256"):
        rows.append((key.replace("_", " ").capitalize(), identity.get(key)))
    return '<dl class="meta">' + "".join(f"<dt>{_text(k)}</dt><dd>{_text(v) if v is not None else 'Not recorded'}</dd>" for k, v in rows) + "</dl>"


def _typed_matches(steps: list[dict[str, Any]], detail: dict[str, Any]) -> list[tuple[int, str]]:
    """Navigation hints only: literal typed-value matches are not proof of a write."""
    matches = []
    for position, step in enumerate(steps):
        action = step.get("action") or {}
        if action.get("type") != "type":
            continue
        typed = str(action.get("text", "")).strip().upper()
        labels = []
        for key in ("expected", "actual"):
            value = detail.get(key)
            if isinstance(value, str) and len(value) >= 4 and not value.isdigit() and value.upper() in typed:
                labels.append(key)
        if labels:
            matches.append((position, " / ".join(labels)))
    return matches


def _checks(manifest: dict[str, Any], verdict: dict[str, Any], group: str,
            prefix: str, check_ids: dict[str, str], steps: list[dict[str, Any]]) -> str:
    specs = (manifest.get("oracle") or {}).get(group, [])
    details = verdict.get("details") or {}
    if not specs:
        return "<p>Specification unavailable. No passing checks inferred.</p>"
    passed = sum(isinstance(details.get(c["id"]), dict) and details[c["id"]].get("passed") is True
                 and "error" not in details[c["id"]] for c in specs)
    missing = _missing_checks(details, {c["id"]: c for c in specs})
    blocks = [f"<p>{passed}/{len(specs)} passed; {len(missing)} unavailable or errored.</p>"]
    for spec in specs:
        cid = spec["id"]
        d = details.get(cid) if isinstance(details.get(cid), dict) else {}
        status = "UNAVAILABLE"
        if "error" not in d and isinstance(d.get("passed"), bool):
            status = "PASS" if d["passed"] else "FAIL"
        tone = "good" if status == "PASS" else "bad" if status == "FAIL" else "unknown"
        evidence = _check_line(cid, d, spec).strip() if d else "No recorded check result."
        evidence += "\n" + "\n".join(_check_evidence(d, spec))
        blocks.append(f'<div class="check" id="{check_ids[cid]}" tabindex="-1"><p><strong class="{tone}">{status}</strong> · <code>{_text(cid)}</code></p><pre>{_text(evidence.strip())}</pre>')
        matches = _typed_matches(steps, d)
        if matches:
            blocks.append('<p>Typed text matches compared values; this does not establish which field received the text or caused the database result.</p><div class="check-links">')
            blocks.extend(f'<a href="#{prefix}-step-{position}">Step {_text(steps[position].get("i", "?"))}: {_text(label)} text</a>' for position, label in matches)
            blocks.append('</div>')
        blocks.append(f'<details><summary>Inspect recorded query and scope</summary><pre>{_json(spec)}</pre></details>'
                      f'<div class="check-links"><a href="#{prefix}-trace">Inspect action trace</a><a href="#{prefix}-source">Verification limits</a><a href="#{prefix}">Episode summary</a></div></div>')
    return "".join(blocks)


def _episode(ep: Path, index: int, turns: int, crop_top: int) -> str:
    loaded = load_episode(ep)
    m, v, steps = loaded["manifest"], loaded["verdict"] or {}, loaded["steps"]
    run = run_meta_for(ep)
    details = v.get("details") or {}
    prefix = f"episode-{index}"
    reward = v.get("reward")
    outcome = "Outcome unavailable" if reward is None else "Task accepted" if reward == 1 else "Task rejected"
    tone = "unknown" if reward is None else "good" if reward == 1 else "bad"
    specs = {c["id"]: c for group in ("effects", "invariants") for c in (m.get("oracle") or {}).get(group, [])}
    incomplete = not specs or bool(_missing_checks(details, specs))
    if reward == 1 and (incomplete or v.get("failed") or any(d.get("passed") is False for d in details.values() if isinstance(d, dict))):
        outcome, tone = "Recorded acceptance · evidence incomplete or inconsistent", "unknown"
    check_ids = {cid: f"{prefix}-check-{n}" for n, cid in enumerate(specs)}
    failed_ids = [cid for cid in specs if isinstance(details.get(cid), dict)
                  and details[cid].get("passed") is False and "error" not in details[cid]]
    missing_ids = _missing_checks(details, specs)

    # Keep every recorded action, including actions whose images were not retained.
    # Resolve each unique image once; a shared frame can still serve multiple steps.
    references: dict[str, tuple[int, str]] = {}
    absent = []
    frames = []
    frame_ids = {}
    frame_status = {}
    for position, step in enumerate(steps):
        for key in ("shot_before", "shot_after"):
            rel = step.get(key)
            if isinstance(rel, str):
                references.setdefault(rel, (position, key))
            else:
                absent.append((position, f'Step {step.get("i", "?")} {key}: reference unavailable'))
    for rel, (position, key) in references.items():
        image, status = _image(ep, rel, crop_top)
        frame_status[rel] = status
        if image:
            frame_ids[rel] = f"{prefix}-frame-{len(frames)}"
            frames.append((rel, position, key, image))
        else:
            absent.append((position, f'Step {steps[position].get("i", "?")} {key}: {status}'))

    backend = run.get("backend")
    constructed = run.get("evidence_kind") == "constructed_control"
    label = ("CONSTRUCTED CONTROL · NOT POLICY PERFORMANCE" if constructed else
             "RECORDED LIVE · SOLARI" if backend == "solari" else
             "OFFLINE SIMULATION · FAKE" if backend == "fake" else "ORIGIN UNAVAILABLE")
    parts = [f'<article class="episode" id="{prefix}" tabindex="-1"><header><h1 class="{tone}">{outcome}</h1>',
             f'<p><strong>{_text(v.get("reason_code") or "NO_VERDICT")}</strong> · Recorded reward: {_text(reward) if reward is not None else "unavailable"}</p>',
             f'<p class="summary-line">{_text(m.get("family") or "Task family unavailable")} · seed {_text(m.get("seed"))} · split {_text(m.get("split"))}</p>',
             f'<span class="label">{label}</span>']
    if constructed:
        parts.append('<p>Known state constructed to exercise verification; not a policy completing the workflow.</p>')
    else:
        parts.append(f'<p>{_text(BACKEND_NOTES.get(backend, "Backend not recorded. Do not infer live execution."))}</p>')
    comparison_id = "appeal_auth_number" if "appeal_auth_number" in specs or isinstance(details.get("appeal_auth_number"), dict) else next(
        (cid for cid in failed_ids if "expected" in details[cid] or "actual" in details[cid]), None)
    if comparison_id is not None:
        compared = details.get(comparison_id) if isinstance(details.get(comparison_id), dict) else {}
        expected = compared.get("expected", specs.get(comparison_id, {}).get("equals"))
        expected_label = "Expected authorization · controller ground truth" if comparison_id == "appeal_auth_number" else f"Expected · {comparison_id}"
        actual_label = "Persisted authorization · recorded database query" if comparison_id == "appeal_auth_number" else f"Actual · {comparison_id}"
        actual = (_text(compared["actual"]) if compared.get("actual") is not None else
                  "No persisted value recorded" if "actual" in compared else "Unavailable · query result not recorded")
        parts.append(f'<dl class="comparison"><div><dt>{_text(expected_label)}</dt>'
                     f'<dd><code>{_text(expected) if expected is not None else "Unavailable"}</code></dd></div>'
                     f'<div><dt>{_text(actual_label)}</dt><dd><code>{actual}</code></dd></div></dl>')
    if incomplete:
        parts.append('<p class="unknown"><strong>Check evidence is incomplete.</strong> Missing results are unavailable, not passing checks. The recorded reward above is not recomputed.</p>')
    if reward is None:
        parts.append('<p>No recorded verification result. An interruption or missing artifact is not a measured task rejection.</p>')
    attention = failed_ids + missing_ids
    if attention:
        parts.append('<p><strong>Inspect next:</strong> failed or unavailable checks.</p><div class="check-links">')
        parts.extend(f'<a href="#{check_ids[cid]}">{_text(cid)} · {"failed" if cid in failed_ids else "unavailable"}</a>' for cid in attention)
        if comparison_id is not None:
            first_actual = next((position for position, match_label in _typed_matches(steps, compared)
                                 if "actual" in match_label), None)
            if first_actual is not None:
                parts.append(f'<a href="#{prefix}-step-{first_actual}">Typed actual-value text · step {_text(steps[first_actual].get("i", "?"))}</a>')
        parts.append('</div>')
    else:
        parts.append(f'<p><strong>Inspect next:</strong> <a href="#{prefix}-checks">review check scope</a>, then <a href="#{prefix}-trace">the recorded actions</a>.</p>')
    parts.append(f'<p class="summary-line">{len(steps)} recorded steps · {len(frames)} of {len(references)} referenced frames available · ended by {_text(v.get("end_reason") or "not recorded")}.</p>'
                 f'<nav aria-label="Episode sections"><a href="#{prefix}-task">Task</a><a href="#{prefix}-checks">Check verdicts</a><a href="#{prefix}-trace">Action trace</a><a href="#{prefix}-frames">Inspect screenshots</a><a href="#{prefix}-source">Source and limits</a></nav></header>')
    parts.append(f'<section id="{prefix}-task" tabindex="-1"><h2>Task</h2><p>{_text(m.get("instruction") or "Instruction unavailable")}</p>'
                 '<p>An agent’s “done” message and a submitted form are not the task verdict.</p></section>')
    parts.append(f'<section id="{prefix}-checks" tabindex="-1"><h2>Effects</h2>{_checks(m, v, "effects", prefix, check_ids, steps)}<h2>Invariants</h2>{_checks(m, v, "invariants", prefix, check_ids, steps)}')
    inv = {c["id"]: c for c in (m.get("oracle") or {}).get("invariants", [])}
    parts.append(f'<p><strong>Selected side-effect checks:</strong> {_text(_side_effect_summary(details, inv))}.</p></section>')

    parts.append(f'<section id="{prefix}-trace" tabindex="-1"><h2>Recorded action trace</h2><p>All {len(steps)} retained step records, in recorded order. Missing screenshots do not remove actions. '
                 'Text matches are navigation hints, not evidence that an action wrote a particular field.</p>')
    if steps:
        positions = sorted(set(range(0, len(steps), 20)) | {len(steps) - 1})
        parts.append('<nav aria-label="Trace positions">' + ''.join(
            f'<a href="#{prefix}-step-{position}">Step {_text(steps[position].get("i", "?"))}</a>' for position in positions) + '</nav>')
    else:
        parts.append('<p>No action records retained. The sequence of policy actions cannot be inspected.</p>')
    parts.append('<ol class="trace">')
    for position, step in enumerate(steps):
        parts.append(f'<li class="trace-row" id="{prefix}-step-{position}" tabindex="-1"><strong>Step {_text(step.get("i", "?"))}</strong><div><p><code>{_text(_action_text(step))}</code></p>')
        if step.get("valid") is False:
            parts.append('<p class="bad">Recorded action invalid</p>')
        parts.append('<div class="step-evidence">')
        for key, label_text in (("shot_before", "Before"), ("shot_after", "After")):
            rel = step.get(key)
            if isinstance(rel, str) and rel in frame_ids:
                parts.append(f'<a href="#{frame_ids[rel]}">{label_text}: inspect screenshot</a>')
            else:
                status = frame_status.get(rel, "Screenshot reference unavailable") if isinstance(rel, str) else "Screenshot reference unavailable"
                parts.append(f'<span class="missing">{label_text}: {_text(status)}</span>')
        parts.append('</div>')
        raw, note = step.get("raw_action"), step.get("policy_note")
        if raw or note:
            parts.append('<details><summary>Recorded model output · not a verifier conclusion</summary>')
            if raw:
                parts.append(f'<pre>{_text(raw)}</pre>')
            if note and note != raw:
                parts.append(f'<p>Policy note</p><pre>{_text(note)}</pre>')
            parts.append('</details>')
        parts.append(f'<div class="check-links"><a href="#{prefix}-trace">Trace index</a><a href="#{prefix}-checks">Check verdicts</a></div></div></li>')
    parts.append('</ol></section>')

    parts.append(f'<section id="{prefix}-frames" tabindex="-1"><h2>Preserved screenshots</h2><p>{len(frames)} of {len(references)} referenced frames available; {len(steps)} recorded steps. '
                 'These are individual retained frames, not a complete replay. Frame labels name recorded actions, not inferred screen contents. Expand a frame to inspect it at page width. '
                 'For small text, enable original pixel size, then scroll the image with touch or arrow keys while it has focus.</p>')
    if crop_top:
        parts.append(f'<p>Sharing crop: the top {crop_top} pixels are omitted from each image. '
                     'Only the exported copies are cropped; source screenshots and recorded verdicts are unchanged. '
                     'This is a selected crop, not automatic secret detection.</p>')
    if frames:
        if len(frames) > 12:
            parts.append(f'<details><summary>Browse all {len(frames)} preserved frames</summary>')
        parts.append('<nav class="frame-index" aria-label="Preserved frames">' + ''.join(
            f'<a href="#{frame_ids[rel]}">{_text(rel.removeprefix("shots/"))}</a>' for rel, _, _, _ in frames) + '</nav>')
        if len(frames) > 12:
            parts.append('</details>')
    else:
        parts.append('<p>No preserved screenshots available. Inspect the recorded actions and database checks; visual state cannot be recovered from those records.</p>')
    for n, (rel, position, key, image) in enumerate(frames):
        step = steps[position]
        label_text = f'Step {step.get("i", "?")} · {"before" if key == "shot_before" else "after"} · {_action_text(step)}'
        crop_note = f' · top {crop_top} pixels omitted for sharing' if crop_top else ''
        frame_id = frame_ids[rel]
        parts.append(f'<details class="frame"{" open" if n == 0 else ""}><summary id="{frame_id}">{_text(label_text)}</summary>'
                     f'<figure><input class="pixel-toggle" type="checkbox" id="{frame_id}-pixels">'
                     f'<label class="pixel-label" for="{frame_id}-pixels">Original pixel size · scroll to inspect</label>'
                     f'<div class="image-view" role="region" tabindex="0" aria-label="{_text(label_text)} screenshot">'
                     f'<img src="{image}" alt="{_text(label_text + crop_note)}" loading="lazy"></div>'
                     f'<figcaption>{_text(rel + crop_note)}</figcaption></figure>'
                     f'<div class="check-links"><a href="#{prefix}-step-{position}">Recorded action and model output</a><a href="#{prefix}-frames">Screenshot index</a><a href="#{prefix}-checks">Check verdicts</a></div></details>')
    if absent:
        parts.append(f'<details><summary>Unavailable frame references ({len(absent)})</summary><ul>' + ''.join(
            f'<li><a href="#{prefix}-step-{position}">{_text(row)}</a></li>' for position, row in absent) + '</ul></details>')
    parts.append('</section>')
    um = details.get("ui_milestones") or {}
    parts.append('<section><h2>Diagnostic milestones are not task success</h2><p>These rungs describe recorded progress; they do not contribute to reward or establish correct field values.</p>')
    if isinstance(um.get("rungs"), dict):
        parts.append('<ul>' + ''.join(f'<li>{_text(k)}: {"reached" if val is True else "not reached" if val is False else "unavailable"}</li>' for k, val in um["rungs"].items()) + '</ul>')
    else:
        parts.append('<p>Milestone evidence unavailable.</p>')
    parts.append('</section>')
    digest = _read_json(ep / 'baseline-digest.json') or {}
    tables = digest.get('tables')
    scope = f'{len(tables)} recorded tables' if isinstance(tables, dict) else 'unavailable — baseline digest not recorded'
    parts.append(f'<section id="{prefix}-source" tabindex="-1"><h2>Source and verification limits</h2>{_metadata(run, v, m)}'
                 '<p>This file inspects retained artifacts. Nothing was re-executed. One episode does not establish a success rate.</p>'
                 '<p>A source revision identifies the recorded HEAD, not necessarily the full executed working tree. Uncommitted changes and execution-source receipts must be checked separately.</p>')
    if run.get("evidence_kind"):
        parts.append(f'<p><strong>{_text(run["evidence_kind"])}</strong>: {_text(run.get("evidence_note") or "Description not recorded")}</p>')
    if constructed:
        parts.append(f'<p>{_text(BACKEND_NOTES.get(backend, "Backend not recorded. Do not infer live execution."))}</p>')
    parts.append('<p>Checks cover only their declared tables, allowed rows, fields and exemptions. The audit check is a scoped provenance tripwire, not a cryptographic guarantee of UI-only writes. Passing checks do not demonstrate detection of every possible violation or production readiness.</p>')
    parts.append(f'<details><summary>Checksum scope: {scope}</summary><pre>{_json(list(tables)) if isinstance(tables, dict) else "Baseline table evidence unavailable"}</pre>'
                 '<p>Per-check row allowances and exemptions appear under “Inspect recorded query and scope.” Columns excluded by the historical world configuration are not independently enumerated in this digest; a table count does not mean every column or table was protected.</p></details>')
    parts.append('<details><summary>Artifact fingerprints (SHA-256)</summary><pre>' + '\n'.join(f'{name}  {hashlib.sha256((ep / name).read_bytes()).hexdigest()}' for name in ('manifest.json', 'verdict.json', 'steps.jsonl', 'reset.json', 'baseline-digest.json') if (ep / name).is_file()) + '</pre><p>Fingerprints identify these input files, not authenticity or a live re-verification.</p></details>')
    parts.append('<details><summary>Canonical text report (sharing redactions applied where needed)</summary><pre>' + _text(episode_report(ep, turns=turns).replace(str(ep), '[episode]')) + '</pre></details></section></article>')
    return ''.join(parts)


def html_report(path: str | Path, *, turns: int = 6, failed: bool = False,
                all_episodes: bool = False, all_attempts: bool = False, crop_top: int = 0) -> str:
    """Same directory/filter semantics as report(); escaped static HTML with inline PNGs."""
    if crop_top < 0:
        raise ValueError("crop_top must be nonnegative")
    path = Path(path)
    canonical = report(path, turns=turns, all_attempts=all_attempts)
    episode = (path / 'manifest.json').is_file()
    parts = []
    if episode:
        dirs = [path]
    else:
        parts.append('<header><h1>Recorded run evidence</h1><p>This is an inspection, not a fresh execution. Only episodes present in this directory are counted.</p></header><pre>' + _text(canonical.replace(str(path), '[run]')) + '</pre>')
        dirs = iter_episode_dirs(path, include_superseded=all_attempts) if failed or all_episodes else []
        if failed and not all_episodes:
            dirs = [d for d in dirs if ((_read_json(d / 'verdict.json') or {}).get('reward') or 0) < 1]
        if not dirs:
            parts.append('<p>No expanded episodes selected. Use --all or --failed to include check evidence and preserved screenshots.</p>')
    if len(dirs) > 1:
        parts.append('<nav aria-label="Expanded episodes">' + ''.join(
            f'<a href="#episode-{i}">Episode {i + 1} · {_text(ep.name)}</a>' for i, ep in enumerate(dirs)) + '</nav>')
    parts.extend(_episode(ep, i, turns, crop_top) for i, ep in enumerate(dirs))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<meta name="referrer" content="no-referrer"><title>Forkloop · Recorded evidence</title><style>' + _STYLE + '</style></head>'
            '<body><a class="skip-link" href="#report-content">Skip to report</a><main id="report-content" tabindex="-1"><p class="brand">Forkloop / Evidence report</p>' + ''.join(parts) +
            '<footer><p>Self-contained export. No scripts, CDN dependencies, analytics or live connection. '
            'Infrastructure URLs and private paths are omitted from text. Model output remains untrusted; '
            'review screenshots and free text before sharing any other recording. Synthetic data only.</p></footer></main></body></html>')
