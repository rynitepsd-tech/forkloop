"""exp1 report additions required by independent review 2 (runs/review2/review-2.md).

Collection (always): all-work cost per arm by cause (B1) and what an all-work accounting would select;
repair-mode views over the first 50 failures and over A2's own budget window, with the step-0 fallback
separated and an evidence-point-only comparison (B2), the first-repair sensitivity (M1a), void rates by
branch length (M1c), time-limit endings by mode and restart depth (M5); training provenance (M7).
Evaluation (only with ``final=True``): unscored cells by arm × shard × cause with key-name failures
separate, attempts per cell, sensitivities (unscored = failure; key-name failure = policy failure;
A3 without seed 3), per-shard success, end reasons and model latency by arm × shard (M2, M3, M4, M6).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from forkloop.correction import Store
from forkloop.correction.analysis import arm_success, outcomes, paired
from forkloop.correction.budget import Rates, Unit, _task_key, demo_units, interleave, repair_units, select, summarize
from forkloop.correction.repair import UNSCORED_BRANCH, counted_repair
from forkloop.correction.store import FINISHED

RATES = Rates(0.35, 0.001)
KEY_NAME = re.compile(r"xdotool key .{0,40}failed|Invalid key sequence|You specified the wrong")
TRANSPORT = re.compile(r"request failed: (ReadError|RemoteProtocolError|ConnectError|WriteError|\w*Timeout)")
MODES = (("A2", "exp1-round1", "checkpoint"), ("A3", "exp1-restart", "full_restart"))


def _text(run_dir: Optional[str]) -> str:
    f = Path(run_dir or "") / "steps.jsonl"
    return f.read_text() if run_dir and f.exists() else ""


def branch_cause(b: dict) -> str:
    if b["status"] == FINISHED:
        return "scored"
    if b["status"] in ("interrupted", "running"):
        return "operator stop / interrupted"
    if b["status"] == "restore_failed":
        return "restore failure"
    t = _text(b.get("run_dir"))
    if "429 Too Many Requests" in t or "insufficient_quota" in t:
        return "provider refusal (429)"
    if KEY_NAME.search(t):
        return "key name (xdotool)"
    if TRANSPORT.search(t):
        return "provider transport"
    err = str((b.get("info") or {}).get("error") or "")
    return ("exception: " + err.split(":")[0]) if err else "other infrastructure"


def repair_cause(st: Store, r: dict) -> str:
    vr = (r.get("result") or {}).get("void_reason")
    if vr:
        return {"provider_outage": "provider refusal (429)", "provider_transport": "provider transport"}.get(vr, vr)
    causes = [branch_cause(b) for b in st.branches(repair_id=r["repair_id"]) if b["status"] in UNSCORED_BRANCH]
    if r["status"] not in ("verified", "unrepaired"):
        causes.append("operator stop / interrupted")
    return Counter(causes).most_common(1)[0][0] if causes else "clean"


def _charges(st: Store) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for c in st.charges():
        if c["ref"]:
            out[c["ref"]].append(c)
    return out


def _usd(cs: list[dict], wall_kind: str, steps: int = 0) -> float:
    return (sum((c["usd"] or 0) for c in cs if c["kind"] == "model_tokens")
            + sum(c["amount"] for c in cs if c["kind"] == wall_kind) / 3600 * RATES.world_usd_per_hour
            + steps * RATES.student_usd_per_step)


def all_work(st: Store) -> dict:
    """Cost of every piece of collection work per arm, by cause (rates as registered)."""
    ch = _charges(st)
    out: dict[str, Any] = {}
    rows: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for a in st.attempts(experiment_id="exp1-demos"):
        if a["info"].get("role") != "teacher":
            continue
        k = "demonstration attempts, scored" if a["status"] == FINISHED else f"demonstration attempts, {a['status']}"
        rows[k][0] += 1
        rows[k][1] += _usd(ch.get(a["attempt_id"], []), "attempt_wall_seconds")
    out["A1"] = rows
    for arm, exp, mode in MODES:
        rows = defaultdict(lambda: [0, 0.0])
        for a in st.attempts(experiment_id="exp1-round1"):
            if a["info"].get("role") != "student":
                continue
            cs = ch.get(a["attempt_id"], [])
            if mode == "full_restart":   # as budget.repair_units: no mid-episode checkpoint overhead for A3
                ck = {c["ckpt_id"] for c in st.checkpoints(a["attempt_id"]) if c["step"] > 0}
                cs = [c for c in cs if not (c["kind"] == "attempt_wall_seconds")] + [
                    {**c, "amount": c["amount"] - sum(x["amount"] for ref in ck for x in ch.get(ref, [])
                                                       if x["kind"] == "checkpoint_seconds")}
                    for c in cs if c["kind"] == "attempt_wall_seconds"]
            k = "student attempts, scored" if a["status"] == FINISHED else f"student attempts, {a['status']}"
            rows[k][0] += 1
            rows[k][1] += _usd(cs, "attempt_wall_seconds", a["n_steps"] or 0)
            if a["status"] == FINISHED and (a["reward"] or 0) < 1:
                counted, _ = counted_repair(st, a["attempt_id"], experiment_id=exp, mode=mode)
                for r in st.repairs(attempt_id=a["attempt_id"], experiment_id=exp):
                    if r["mode"] != mode:
                        continue
                    k = "repairs, counted" if counted and r["repair_id"] == counted["repair_id"] else \
                        f"repairs, void: {repair_cause(st, r)}"
                    rows[k][0] += 1
                    rows[k][1] += sum(_usd(ch.get(b["branch_id"], []), "branch_wall_seconds")
                                      for b in st.branches(repair_id=r["repair_id"]))
        out[arm] = rows
    return {arm: {"by_cause": {k: {"n": n, "usd": round(c, 2)} for k, (n, c) in sorted(rows.items())},
                  "total_usd": round(sum(c for _, c in rows.values()), 2)} for arm, rows in out.items()}


def all_work_selection(st: Store) -> dict:
    """Matched cost under the all-work accounting: B = the smallest all-work arm total; data only from
    counted repairs (descriptive: no model was trained on these selections)."""
    arms = {"A1": demo_units(st, "exp1-demos", count_unscored=True),
            "A2": repair_units(st, "exp1-round1", "exp1-round1", "checkpoint", "A2", count_unscored=True),
            "A3": repair_units(st, "exp1-round1", "exp1-restart", "full_restart", "A3", count_unscored=True)}
    totals = {k: summarize(v, RATES) for k, v in arms.items()}
    b = min(t["cost_usd"] for t in totals.values())
    sel = {}
    for arm, units in arms.items():
        chosen, spent = select(units, b, RATES)
        sel[arm] = {"units": len(chosen), "spent_usd": round(spent, 2),
                    "verified_paths": sum(len(u.verified_sources) for u in chosen),
                    "tasks_with_paths": len({u.task_id for u in chosen if u.verified_sources})}
    return {"budget_usd": round(b, 2), "totals": totals, "selection": sel}


def _failure_order(st: Store) -> list[dict]:
    scored = [a for a in st.attempts(experiment_id="exp1-round1")
              if a["info"].get("role") == "student" and a["status"] == FINISHED]
    by_id = {a["attempt_id"]: a for a in scored}
    order = [u.attempt_id for u in interleave([Unit("", _task_key(a["task_id"]), a["task_id"], a["attempt_id"], None, [])
                                               for a in scored])]
    return [by_id[i] for i in order if (by_id[i]["reward"] or 0) < 1]


def _verified_steps(st: Store, rep: dict) -> list[int]:
    return [st.checkpoint(b["ckpt_id"])["step"] for b in st.branches(repair_id=rep["repair_id"])
            if b["status"] == FINISHED and (b["reward"] or 0) >= 1]


def _pairs(rows: list[tuple[Optional[bool], Optional[bool]]]) -> dict:
    from forkloop.correction.analysis import sign_test
    both = [(x, y) for x, y in rows if x is not None and y is not None]
    pos = sum(1 for x, y in both if x and not y)
    neg = sum(1 for x, y in both if y and not x)
    return {"both_scored": len(both), "ckpt_only": pos, "restart_only": neg,
            "both": sum(1 for x, y in both if x and y), "neither": sum(1 for x, y in both if not x and not y),
            "sign_test_p": sign_test(pos, neg)}


def repair_mode_views(st: Store, budget_dir: Optional[Path]) -> dict:
    fails = _failure_order(st)
    scopes = {"first 50 failures (selection order; window chosen after the first batch — exploratory)": fails[:50]}
    if budget_dir and (budget_dir / "budget-report.json").exists():
        rep = json.loads((budget_dir / "budget-report.json").read_text())
        ids = [u["attempt_id"] for u in rep["arms"]["A2-b100"]["selected"] if u.get("repair_id")]
        scopes["failures inside A2's budget window"] = [a for a in fails if a["attempt_id"] in set(ids)]
    out = {}
    for name, fs in scopes.items():
        any_rows, ev_rows, first_rows, unsettled = [], [], [], []
        split = Counter()
        for a in fs:
            ck, _ = counted_repair(st, a["attempt_id"], experiment_id="exp1-round1", mode="checkpoint")
            fr, _ = counted_repair(st, a["attempt_id"], experiment_id="exp1-restart", mode="full_restart")
            ck_any = None if ck is None else ck["status"] == "verified"
            ck_ev = None if ck is None else any(s > 0 for s in _verified_steps(st, ck))
            fr_ok = None if fr is None else fr["status"] == "verified"
            any_rows.append((ck_any, fr_ok))
            ev_rows.append((ck_ev, fr_ok))
            if ck_any:
                split["checkpoint successes from an evidence point" if ck_ev else "checkpoint successes only from the step-0 fallback"] += 1
            if ck is None or fr is None:
                unsettled.append({"task": a["task_id"], "missing": [m for m, r in (("checkpoint", ck), ("full_restart", fr)) if r is None]})
            # M1(a): first repair started (not a provider-outage repair), unscored branches = failures
            firsts = []
            for exp, mode in (("exp1-round1", "checkpoint"), ("exp1-restart", "full_restart")):
                reps = [r for r in st.repairs(attempt_id=a["attempt_id"], experiment_id=exp) if r["mode"] == mode
                        and (r.get("result") or {}).get("void_reason") != "provider_outage"]
                firsts.append(None if not reps else bool(_verified_steps(st, reps[0])))
            first_rows.append(tuple(firsts))
        out[name] = {"failures": len(fs), "counted repairs, any restart point": _pairs(any_rows),
                     "counted repairs, evidence point only (k = 3 vs 3)": _pairs(ev_rows),
                     "first repair started, unscored branches as failures": _pairs(first_rows),
                     "checkpoint success split": dict(split), "unsettled": unsettled}
    return out


def branch_views(st: Store) -> dict:
    """Branch-level verified rates by mode and restart point; void (unscored) share by branch length;
    time-limit endings by mode and restart depth."""
    rate = defaultdict(lambda: Counter())
    void_len = defaultdict(lambda: Counter())
    ends = defaultdict(lambda: Counter())
    for arm, exp, mode in MODES:
        for r in st.repairs(experiment_id=exp):
            if r["mode"] != mode:
                continue
            reasons = {p["ckpt_id"]: p["reason"] for p in r["config"].get("restart_points", [])}
            for b in st.branches(repair_id=r["repair_id"]):
                ck = st.checkpoint(b["ckpt_id"])
                point = "step 0" if ck["step"] == 0 else f"evidence ({reasons.get(b['ckpt_id'], '?')})"
                c = rate[f"{mode} · {point}"]
                c["branches"] += 1
                c["scored"] += b["status"] == FINISHED
                c["verified"] += b["status"] == FINISHED and (b["reward"] or 0) >= 1
                if b["status"] in (FINISHED, "infra_error"):
                    n = b["n_steps"] or 0
                    bucket = "0-29" if n < 30 else "30-59" if n < 60 else "60-89" if n < 90 else "90+"
                    void_len[f"{mode} · {bucket} steps"]["void" if b["status"] != FINISHED else "scored"] += 1
                if b["status"] == FINISHED:
                    depth = "0" if ck["step"] == 0 else "1-29" if ck["step"] < 30 else "30-69" if ck["step"] < 70 else "70+"
                    ends[f"{mode} · restart step {depth}"][(b.get("info") or {}).get("end_reason") or "?"] += 1
    return {"branch_rates": {k: dict(v) for k, v in sorted(rate.items())},
            "void_share_by_length": {k: {**dict(v), "void_share": round(v["void"] / max(1, v["void"] + v["scored"]), 3)}
                                     for k, v in sorted(void_len.items())},
            "end_reasons_by_depth": {k: dict(v) for k, v in sorted(ends.items())}}


def training_provenance(adapter_dirs: list[Path]) -> dict:
    out = {}
    for root in adapter_dirs:
        for d in sorted(p for p in root.iterdir() if (p / "final" / "training_args.json").exists()):
            a = json.loads((d / "final" / "training_args.json").read_text())
            argv = " ".join(a.get("argv") or [])
            f = d / "final" / "adapter_model.safetensors"
            out[d.name] = {"seed": (a.get("optim") or {}).get("seed"), "versions": a.get("versions"),
                           "datasets": [x.get("dataset_id") for x in a.get("datasets") or []],
                           "host": "forkloop-dev (1×H100)" if "/home/ubuntu/exp1/" in argv else "forkloop-main (A100-80)",
                           "adapter_sha256": hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None}
    return out


# ------------------------------------------------------------------ evaluation (final only)

def _key_name_failure(a: dict) -> bool:
    return a["status"] != FINISHED and bool(KEY_NAME.search(_text(a.get("run_dir"))))


def _cause(m: dict) -> str:
    if m["status"] == FINISHED:
        return "scored"
    t = _text(m.get("run_dir"))
    if KEY_NAME.search(t):
        return "key name (xdotool)"
    err = str(m.get("error") or "")
    if "ConcurrencyError" in err:
        return "world cap"
    if TRANSPORT.search(t) or "request failed" in t:
        return "model server / transport"
    return m["status"] + (": " + err.split(":")[0] if err else "")


def eval_extras(stores: list[Store], arms: dict[str, tuple[str, int]], shard_names: list[str]) -> dict:
    flt = lambda t: "-final_test-" in t  # noqa: E731
    out: dict[str, Any] = {}
    res = outcomes(stores, "exp1-eval", arms, task_filter=flt)
    unscored = defaultdict(Counter)
    attempts = defaultdict(Counter)
    for i, st in enumerate(stores):
        r = outcomes(st, "exp1-eval", arms, task_filter=flt)
        for (arm, run, task), m in r["meta"].items():
            label = f"{arm}{'' if arm in ('A0', 'S_W') else f'-s{run}'} · {shard_names[i]}"
            attempts[label][m["attempts"]] += 1
            if r["table"][arm][run][task] is None:
                unscored[label][_cause(m)] += 1
        out.setdefault("per_shard_success", {})[shard_names[i]] = {a: arm_success(r, a)["success_balanced"]
                                                                   for a in sorted(r["table"])}
    out["unscored_cells_by_cause"] = {k: dict(v) for k, v in sorted(unscored.items())}
    out["attempts_per_cell"] = {k: dict(sorted(v.items())) for k, v in sorted(attempts.items())}
    comps = (("A2", "A0"), ("A2", "A1"), ("A2", "A3"), ("A1", "A0"), ("A3", "A0"), ("S_W", "A0"))
    sens = {"unscored cells scored as failures": outcomes(stores, "exp1-eval", arms, task_filter=flt, unscored_as_failure=True),
            "key-name failures scored as policy failures": outcomes(stores, "exp1-eval", arms, task_filter=flt,
                                                                     policy_failure=_key_name_failure)}
    no_s3 = {k: v for k, v in arms.items() if k != "A3-s3"}
    sens["A3 without seed 3 (trained on the H100 box)"] = outcomes(stores, "exp1-eval", no_s3, task_filter=flt)
    out["sensitivity"] = {name: {"arms": {a: round(arm_success(r, a)["success_balanced"], 4) for a in sorted(r["table"])},
                                 "paired": [paired(r, x, y) for x, y in comps if x in r["table"] and y in r["table"]]}
                          for name, r in sens.items()}
    ends = defaultdict(Counter)
    lat = defaultdict(list)
    for i, st in enumerate(stores):
        r = outcomes(st, "exp1-eval", arms, task_filter=flt)
        for (arm, run, task), m in r["meta"].items():
            ends[f"{arm} · {shard_names[i]}"][m.get("end_reason") or m["status"]] += 1
            for line in _text(m.get("run_dir")).splitlines():
                try:
                    v = json.loads(line).get("model_latency_s")
                except ValueError:
                    v = None
                if v is not None:
                    lat[f"{arm} · {shard_names[i]}"].append(v)
    out["end_reasons"] = {k: dict(v) for k, v in sorted(ends.items())}
    out["model_latency_s"] = {k: {"n": len(v), "p50": sorted(v)[len(v) // 2], "p90": sorted(v)[int(0.9 * len(v))]}
                              for k, v in sorted(lat.items()) if v}
    return out
