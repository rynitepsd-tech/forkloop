"""Human-readable reports over recorded run directories (``forkloop report``).

Everything printed here is read from the artifacts a run leaves on disk (contracts §10):
``run.json``, ``manifest.json``, ``verdict.json``, ``steps.jsonl``, ``reset.json``,
``accounting.json`` and ``shots/``. Nothing is recomputed against a machine, so a report is
always an inspection of a *recorded* run; the provenance banner says which backend recorded
it (a Solari desktop with the real applications, or the offline fake backend) and when.

The report is for the researcher, so it prints the controller-only ``expected`` block from
the manifest next to the oracle's verdict. That block was never sent to the policy
(``TaskInstance.public_info`` is the agent-visible subset) and the report never touches a
policy or an environment.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from .metrics import episode_tokens, wilson
from .trajectories import iter_episode_dirs, load_episode

BACKEND_NOTES = {
    "solari": "Recorded live Solari desktop (real OpenEMR 8.3 + synthetic payer portal, synthetic data); "
              "verdict recorded from application databases, not re-executed",
    "fake": "OFFLINE SIMULATION on the in-process fake backend: SQLite state, synthetic screenshots, "
            "no browser. This is not live application or vision-policy evidence",
}
MILESTONE_ORDER = ["openemr_login", "openemr_chart", "openemr_document", "portal_claim",
                   "portal_appeal_form", "appeal_submitted"]


def _read_json(p: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(p.read_text()) if p.is_file() else None
    except (OSError, ValueError):
        return None


def run_meta_for(ep_dir: Path) -> dict[str, Any]:
    """``run.json`` of the run an episode directory belongs to (``<run>/episodes/<ep>``)."""
    return _read_json(ep_dir.parent.parent / "run.json") or {}


def _fmt_value(v: Any) -> str:
    if v is None:
        return "(no row)"
    return json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v


def _model_text(step: dict[str, Any]) -> str:
    note = (step.get("policy_note") or "").strip()
    raw = (step.get("raw_action") or "").strip()
    return " ".join((note or raw).split())


def _action_text(step: dict[str, Any]) -> str:
    a = step.get("action") or {}
    t = a.get("type")
    if not t:
        return "INVALID"
    if t == "type":
        return f'type {json.dumps(a.get("text", ""), ensure_ascii=False)}'
    if t in ("click", "double_click", "right_click", "move"):
        return f'{t} ({a.get("x")}, {a.get("y")})'
    if t == "key":
        return f'key {a.get("keys") or a.get("key") or a.get("text")}'
    if t == "scroll":
        return f'scroll {a.get("direction")} x{a.get("amount")} at ({a.get("x")}, {a.get("y")})'
    if t == "done":
        return "done" + (" (success)" if a.get("success") else "")
    return t


def _shots(ep_dir: Path, step: dict[str, Any]) -> str:
    """Screenshot paths of a step, or a note when the PNGs were not preserved."""
    out = []
    for key in ("shot_before", "shot_after"):
        rel = step.get(key)
        if not rel:
            continue
        p = ep_dir / rel
        out.append(rel if p.is_file() else f"{rel} (not preserved)")
    return ", ".join(out)


def _provenance_lines(ep_dir: Path, run: dict[str, Any], verdict: dict[str, Any]) -> list[str]:
    backend = run.get("backend") or "unknown"
    note = BACKEND_NOTES.get(backend, "backend not recorded in run.json")
    finished = verdict.get("finished_at") or "(timestamp not recorded)"
    lines = [f"episode   {ep_dir}",
             f"recorded  {finished}  (inspection of a recorded run; nothing was re-executed)",
             f"backend   {backend}: {note}"]
    if run.get("evidence_kind"):
        lines.append(f"evidence  {run['evidence_kind']}: {run.get('evidence_note') or '(no description recorded)'}")
    model = run.get("model") or run.get("policy") or "(not recorded)"
    extra = []
    if run.get("policy") and run.get("model"):
        extra.append(f"policy {run['policy']}")
    if run.get("policy_options"):
        extra.append("options " + json.dumps(run["policy_options"], sort_keys=True))
    if run.get("budget_override"):
        extra.append("budget override " + json.dumps(run["budget_override"], sort_keys=True))
    lines.append(f"model     {model}" + (("  [" + "; ".join(extra) + "]") if extra else ""))
    if run.get("git_sha"):
        lines.append(f"git       {run['git_sha']}")
    return lines


def _check_line(cid: str, d: dict[str, Any], spec: dict[str, Any]) -> str:
    """One line per check: status, id, evidence, reason code on failure."""
    status = "????"
    if "error" not in d and isinstance(d.get("passed"), bool):
        status = "ok  " if d["passed"] else "FAIL"
    kind = spec.get("kind", "query")
    if not isinstance(d.get("passed"), bool):
        body = "evidence unavailable (no pass/fail result)"
    elif "error" in d:
        body = f"check raised {d['error']}"
    elif kind in ("query", "count") or "expected" in d:
        body = f"expected {_fmt_value(d.get('expected'))}  actual {_fmt_value(d.get('actual'))}"
        if d.get("op") and d.get("op") != "eq":
            body += f"  (op {d['op']})"
    elif kind == "baseline_checksum":
        allow = spec.get("allow") or {}
        exempt = spec.get("exempt_tables") or []
        scope = "; ".join(f"{t} rows {', '.join(map(str, pks))} may change" for t, pks in sorted(allow.items()))
        if exempt:
            scope += (("; " if scope else "") + f"{', '.join(exempt)} exempt")
        body = f"{d.get('n_unexpected', 0)} row(s) changed outside the allow-list" + (f" [{scope}]" if scope else "")
    elif kind == "ui_path_only":
        body = (f"{d.get('checked', 0)} changed row(s) checked for an audit-log row written after the reset "
                f"watermark, {len(d.get('unaudited_changes') or [])} without one")
    elif kind == "forbidden_screens":
        body = f"{len(d.get('visited') or [])} forbidden page(s) among {d.get('n_pages', 0)} portal page views"
    elif kind == "preserve_fields":
        body = "protected fields unchanged" if d.get("passed") else "protected fields changed"
        if spec.get("mutable_fields"):
            body += f" (mutable: {', '.join(spec['mutable_fields'])})"
    else:
        body = json.dumps({k: v for k, v in d.items() if k != "reason_code"}, ensure_ascii=False)
    code = d.get("reason_code") or spec.get("reason_code")
    if status == "FAIL" and code:
        body += f"  -> {code}"
        if kind == "count" and code in ("DUPLICATE_SIDE_EFFECT", "NOT_DONE") and _fewer_than_required(d):
            body += " (not a duplicate: fewer rows than required)"
    return f"  {status}  {cid:<26} {body}"


SIDE_EFFECT_CODES = {"COLLATERAL_EDIT", "DIRECT_DB_WRITE", "FORBIDDEN_SCREEN", "WRONG_RECORD", "DUPLICATE_SIDE_EFFECT"}


def _fewer_than_required(d: dict[str, Any]) -> bool:
    """A ``count == N`` invariant that failed because the count is *below* N: the task was not
    done, not a duplicate side effect."""
    try:
        return float(d.get("actual") or 0) < float(d.get("expected"))
    except (TypeError, ValueError):
        return False


def side_effect_failures(details: dict[str, Any], spec: dict[str, dict[str, Any]]) -> list[str]:
    """Failed invariants that mean the policy touched something it should not have."""
    out = []
    for cid, c in spec.items():
        d = details.get(cid)
        if not isinstance(d, dict) or d.get("passed") is not False or "error" in d:
            continue
        code = d.get("reason_code") or c.get("reason_code")
        if code not in SIDE_EFFECT_CODES or (code == "DUPLICATE_SIDE_EFFECT" and _fewer_than_required(d)):
            continue
        out.append(cid)
    return out


def _missing_checks(details: dict[str, Any], spec: dict[str, dict[str, Any]]) -> list[str]:
    return [cid for cid in spec if not isinstance(details.get(cid), dict)
            or not isinstance(details[cid].get("passed"), bool) or "error" in details[cid]]


def _side_effect_summary(details: dict[str, Any], spec: dict[str, dict[str, Any]]) -> str:
    detected = side_effect_failures(details, spec)
    missing = _missing_checks(details, spec)
    if not spec:
        return "UNAVAILABLE (no invariant specification)"
    result = "DETECTED: " + ", ".join(detected) if detected else "none detected within evaluated checks"
    if missing:
        result = (result + "; " if detected else "") + "INCOMPLETE: " + ", ".join(missing) + " unavailable"
    return result


def _rows(items: list[dict[str, Any]]) -> list[str]:
    return [f"        {r.get('kind', '?'):<8} {r.get('table')} pk={r.get('pk')}" for r in items]


def _check_evidence(d: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    """Full row lists behind a failed structural check (the verdict caps them at 50/20)."""
    out: list[str] = []
    if d.get("passed"):
        return out
    kind = spec.get("kind")
    if kind == "baseline_checksum" and d.get("unexpected_changes"):
        n = d.get("n_unexpected", len(d["unexpected_changes"]))
        out.append(f"      changed rows outside the allow-list ({len(d['unexpected_changes'])} of {n} listed):")
        out += _rows(d["unexpected_changes"])
    if kind == "ui_path_only" and d.get("unaudited_changes"):
        out.append("      changed rows with no audit-log row after the watermark:")
        out += _rows(d["unaudited_changes"])
        for db, rows in (d.get("audit_rows_after_watermark") or {}).items():
            out.append(f"      newest audit rows in {db} after the watermark ({len(rows)} shown):")
            for r in rows:
                out.append("        " + json.dumps(r, ensure_ascii=False)[:300])
    if kind == "forbidden_screens" and d.get("visited"):
        out.append("      forbidden pages visited: " + ", ".join(d["visited"]))
    if kind == "preserve_fields" and d.get("before") is not None:
        out.append("      before: " + json.dumps(d.get("before"), ensure_ascii=False)[:600])
        out.append("      after:  " + json.dumps(d.get("after"), ensure_ascii=False)[:600])
    return out


def _value_steps(ep_dir: Path, steps: list[dict[str, Any]], verdict: dict[str, Any],
                 spec: dict[str, dict[str, Any]]) -> list[str]:
    """Typed values that match what the oracle compared: which step typed the value it found,
    and whether the expected value was ever typed."""
    want: dict[str, list[str]] = {}
    for cid, d in (verdict.get("details") or {}).items():
        if not isinstance(d, dict) or spec.get(cid, {}).get("kind") not in (None, "query", "count"):
            continue
        for label, key in (("expected", "expected"), ("value the oracle found", "actual")):
            v = d.get(key)
            if isinstance(v, str) and len(v) >= 4 and not v.isdigit():
                want.setdefault(v.upper(), []).append(f"{label} for {cid}")
    if not want:
        return []
    out: list[str] = []
    for s in steps:
        a = s.get("action") or {}
        if a.get("type") != "type":
            continue
        text = str(a.get("text", "")).strip().upper()
        hits = [tag for v, tags in want.items() if v in text for tag in tags]
        if hits:
            out.append(f"  step {s['i']:>3}  {_action_text(s)}  <- {'; '.join(sorted(set(hits)))}  [{_shots(ep_dir, s)}]")
    return out


def _milestone_lines(details: dict[str, Any]) -> list[str]:
    um = details.get("ui_milestones")
    if not isinstance(um, dict) or "rungs" not in um:
        return ["ui milestones  (not recorded for this episode)"]
    rungs = um["rungs"]
    order = um.get("order") or [r for r in MILESTONE_ORDER if r in rungs] or list(rungs)
    marks = "  ".join(f"{r}={'yes' if rungs.get(r) else 'no'}" for r in order)
    highest = um.get("highest") or "none"
    lines = [f"ui milestones (from the audit trails; analysis only, never in the reward): {um.get('n_reached', 0)}/{len(order)} "
             f"reached, highest {highest}", f"  {marks}"]
    ev = um.get("evidence") or {}
    if ev:
        keys = ("openemr_logins", "openemr_login_failures", "openemr_document_paths", "portal_page_views", "appeals_for_claim")
        lines.append("  evidence: " + ", ".join(f"{k}={json.dumps(ev[k])}" for k in keys if k in ev))
    return lines


def _reset_line(reset: Optional[dict[str, Any]]) -> str:
    if not reset:
        return "reset     (no reset.json)"
    stages = ", ".join(f"{s['name']} {float(s.get('seconds', 0)):.1f}" for s in reset.get("stages", []))
    ok = "ok" if reset.get("ok") else f"FAILED {reset.get('error')}"
    return f"reset     {reset.get('method')} {float(reset.get('total_seconds', 0)):.1f} s {ok} [{stages}]"


def episode_report(ep_dir: str | Path, *, turns: int = 6) -> str:
    """Everything a researcher needs to judge one recorded episode, from its artifacts only."""
    ep_dir = Path(ep_dir)
    e = load_episode(ep_dir)
    m, v, steps, reset = e["manifest"], e["verdict"] or {}, e["steps"], e["reset"]
    run = run_meta_for(ep_dir)
    spec_list = (m.get("oracle") or {}).get("effects", []) + (m.get("oracle") or {}).get("invariants", [])
    spec = {c["id"]: c for c in spec_list}
    details = v.get("details") or {}
    failed = set(v.get("failed") or [])

    L: list[str] = ["== forkloop episode report =="]
    L += _provenance_lines(ep_dir, run, v)
    attempt = m.get("attempt")
    sel = "" if m.get("selected") is None else ("  selected" if m.get("selected") else "  superseded")
    L.append(f"task      {m.get('world')} / {m.get('family')} seed {m.get('seed')} split {m.get('split')}"
             + (f"  attempt {attempt}" if attempt else "") + sel)
    b = m.get("budget") or {}
    L.append(f"budget    {b.get('max_steps')} actions / {b.get('max_seconds')} s (task); difficulty "
             + json.dumps(m.get("difficulty") or {}, sort_keys=True))
    L += ["", "instruction (the only task text the policy saw):", f"  {m.get('instruction')}"]
    L += ["", "expected (controller-only ground truth from manifest.json; never sent to the policy):"]
    for k, val in sorted((m.get("expected") or {}).items()):
        L.append(f"  {k:<18} {json.dumps(val, ensure_ascii=False)}")

    L.append("")
    if not v:
        L.append("outcome   NO VERDICT (no recorded verification result; interruption or missing artifact)")
    else:
        L.append(f"outcome   reward {v.get('reward')}  reason {v.get('reason_code')}  ended by {v.get('end_reason')} "
                 f"after {v.get('n_steps', len(steps))} steps ({v.get('n_invalid', 0)} invalid) in "
                 f"{float(v.get('wall_seconds') or 0):.1f} s; milestones {v.get('milestones')}")
        effects = [c["id"] for c in (m.get("oracle") or {}).get("effects", [])]
        invariants = [c["id"] for c in (m.get("oracle") or {}).get("invariants", [])]
        known = set(effects) | set(invariants)
        extra_ids = [cid for cid in details if cid not in known and cid != "ui_milestones"]
        for title, ids in (("effects (what the task required)", effects),
                           ("invariants (what must not have happened)", invariants + extra_ids)):
            ok = sum(1 for cid in ids if isinstance(details.get(cid), dict) and details[cid].get("passed"))
            L.append(f"{title}: {ok}/{len(ids)} passed")
            for cid in ids:
                d = details.get(cid)
                if not isinstance(d, dict):
                    L.append(f"  ????  {cid:<26} not evaluated" + ("  (listed as failed)" if cid in failed else ""))
                    continue
                L.append(_check_line(cid, d, spec.get(cid, {})))
                L += _check_evidence(d, spec.get(cid, {}))
        invariant_spec = {cid: spec.get(cid, {}) for cid in invariants + extra_ids}
        digest = _read_json(ep_dir / "baseline-digest.json") or {}
        tables = digest.get("tables")
        scope = (f" ({len(tables)} tables checksummed, baseline-digest.json)" if isinstance(tables, dict)
                 else " (checksum table scope not recorded)")
        L.append("side effects  " + _side_effect_summary(details, invariant_spec) + scope)
        L.append("scope     listed tables and explicit exemptions only; audit matching is a provenance tripwire, "
                 "not proof of UI-only writes or absence of all side effects")
        L += _milestone_lines(details)

    vs = _value_steps(ep_dir, steps, v, spec)
    L += ["", "steps that typed a value the oracle compared:"] + (vs or ["  (none)"])
    term = [s for s in steps if (s.get("action") or {}).get("type") == "done"]
    if term:
        s = term[-1]
        L.append(f"  step {s['i']:>3}  {_action_text(s)}  [{_shots(ep_dir, s)}]")
    if steps:
        first, last = steps[0], steps[-1]
        L.append(f"screenshots  {len(list((ep_dir / 'shots').glob('*.png')))} PNG(s) under {ep_dir / 'shots'}; "
                 f"first {first.get('shot_before')}, last {last.get('shot_after')}")
    L += ["", _reset_line(reset)]
    tok = episode_tokens(e)
    if tok.get("in") or tok.get("out"):
        acc = "accounting.json" if (ep_dir / "accounting.json").is_file() else "max cumulative step counter"
        L.append(f"tokens    in {tok['in']} out {tok['out']} ({acc})")
    if turns and steps:
        L += ["", f"last {min(turns, len(steps))} model turns (raw policy text; the action the env applied):"]
        for s in steps[-turns:]:
            L.append(f"  [{s['i']:>3}] {_action_text(s):<40} {_model_text(s)[:400]}")
    return "\n".join(L)


def _episode_row(ep_dir: Path) -> dict[str, Any]:
    e = load_episode(ep_dir)
    m, v, steps = e["manifest"], e["verdict"] or {}, e["steps"]
    details = v.get("details") or {}
    invariants = {c["id"]: c for c in (m.get("oracle") or {}).get("invariants", [])}
    safety = side_effect_failures(details, invariants)
    um = details.get("ui_milestones") if isinstance(details.get("ui_milestones"), dict) else {}
    return {"dir": ep_dir, "family": m.get("family"), "seed": m.get("seed"), "attempt": m.get("attempt", 1),
            "reward": v.get("reward"), "reason": v.get("reason_code", "NO_VERDICT"), "end": v.get("end_reason", "-"),
            "steps": v.get("n_steps", len(steps)), "wall": v.get("wall_seconds"),
            "safety_failed": safety, "safety_missing": _missing_checks(details, invariants),
            "safety_spec": bool(invariants), "highest": (um or {}).get("highest") or "-"}


def run_report(run_dir: str | Path, *, all_attempts: bool = False) -> str:
    """One line per episode plus the success and reason-code totals of a run directory."""
    run_dir = Path(run_dir)
    run = _read_json(run_dir / "run.json") or {}
    dirs = iter_episode_dirs(run_dir, include_superseded=all_attempts)
    rows = [_episode_row(d) for d in dirs]
    L = ["== forkloop run report ==", f"run       {run_dir}"]
    backend = run.get("backend") or "unknown"
    L.append(f"backend   {backend}: {BACKEND_NOTES.get(backend, 'backend not recorded in run.json')}")
    L.append(f"model     {run.get('model') or run.get('policy') or '(not recorded)'}"
             + (f"  started {run['started_at']}" if run.get("started_at") else ""))
    if run.get("evidence_kind"):
        L.append(f"evidence  {run['evidence_kind']}: {run.get('evidence_note') or '(no description recorded)'}")
    L.append("episodes  " + (f"{len(rows)} (all attempts)" if all_attempts else f"{len(rows)} selected attempt(s)")
             + "  -- every row is read from a recorded verdict.json; nothing was re-run")
    L.append("")
    L.append(f"  {'family':<24} {'seed':>6} {'att':>3} {'reward':>6} {'reason':<22} {'end':<12} {'steps':>5} "
             f"{'wall_s':>7} {'highest milestone':<20} side-effect failures")
    for r in sorted(rows, key=lambda r: (str(r["family"]), int(r["seed"] or 0), int(r["attempt"] or 1))):
        wall = f"{float(r['wall']):.0f}" if r["wall"] is not None else "-"
        L.append(f"  {str(r['family']):<24} {str(r['seed']):>6} {str(r['attempt']):>3} {str(r['reward']):>6} "
                 f"{str(r['reason']):<22} {str(r['end']):<12} {str(r['steps']):>5} {wall:>7} {r['highest']:<20} "
                 + (", ".join(r["safety_failed"]) or "-")
                 + (" [invariant evidence unavailable]" if r["safety_missing"] or not r["safety_spec"] else ""))
    k = sum(1 for r in rows if (r["reward"] or 0) >= 1.0)
    available = sum(r["reward"] is not None for r in rows)
    p, lo, hi = wilson(k, available)
    L.append("")
    L.append(f"success   {k}/{available} recorded outcomes"
             + (f" = {p * 100:.1f}% (Wilson 95% [{lo * 100:.1f}, {hi * 100:.1f}])" if available else "; rate unavailable"))
    L.append(f"coverage  {len(rows) - available} episode(s) without a recorded reward; excluded from rate, not successes "
             "or measured policy failures")
    L.append("meaning   this directory's recorded cohort only, not a general success rate; constructed controls "
             "and simulations do not measure live policy reliability")
    L.append("reasons   " + ", ".join(f"{c}={n}" for c, n in sorted(Counter(str(r["reason"]) for r in rows).items())))
    unsafe = [r for r in rows if r["safety_failed"]]
    L.append(f"side effects  {len(unsafe)} episode(s) with selected side-effect check failures; "
             f"{sum(bool(r['safety_missing']) or not r['safety_spec'] for r in rows)} with incomplete invariant evidence")
    L.append("")
    L.append("next      forkloop report <episode dir> for the check-by-check evidence of one episode; "
             "forkloop metrics --run <run> for cost and token totals")
    return "\n".join(L)


def report(path: str | Path, *, turns: int = 6, failed: bool = False, all_episodes: bool = False,
           all_attempts: bool = False) -> str:
    """``forkloop report PATH``: an episode directory (has manifest.json) or a run directory
    (has episodes/). ``failed``/``all_episodes`` append episode reports to a run report."""
    path = Path(path)
    if (path / "manifest.json").is_file():
        return episode_report(path, turns=turns)
    if not (path / "episodes").is_dir():
        raise SystemExit(f"{path} is neither an episode directory (manifest.json) nor a run directory (episodes/)")
    parts = [run_report(path, all_attempts=all_attempts)]
    if failed or all_episodes:
        for d in iter_episode_dirs(path, include_superseded=all_attempts):
            v = _read_json(d / "verdict.json") or {}
            if all_episodes or float(v.get("reward", 0) or 0) < 1.0:
                parts.append("")
                parts.append(episode_report(d, turns=turns))
    return "\n".join(parts)


__all__ = ["episode_report", "run_report", "report", "run_meta_for"]
