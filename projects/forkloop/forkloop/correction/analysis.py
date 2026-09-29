"""Paired analysis of the final evaluation (``forkloop analyze``).

Inputs are evaluation attempts in a store: role ``eval:<label>``, one cell per (task, model). A model
label maps to an arm and a training run (seed) through ``arms``: ``{label: (arm, run)}``.

* **Outcome** per cell: 1 (verifier reward 1), 0 (scored failure), or unscored (infrastructure,
  interrupted, oracle error) after the predeclared retries. Unscored cells are reported and excluded
  pairwise: a task enters an A-vs-B comparison only if every run of both arms scored it.
* **Arm success** = mean over families of the family mean over tasks of the mean over the arm's runs
  (families equally weighted).
* **Paired difference** A − B with a 95% percentile bootstrap that resamples tasks within family and,
  independently, training runs within each arm (10,000 replicates, fixed seed).
* **Secondary**: an exact sign test on tasks where the run-averaged outcomes differ; per-family tables;
  reason-code rates (wrong record, duplicate side effect), steps and latency.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from typing import Any, Iterable, Optional

from .store import FINISHED, Store


def outcomes(store: "Store | list[Store]", experiment_id: str, arms: dict[str, tuple[str, int]],
             task_filter: Any = None) -> dict[str, Any]:
    """{arm: {run: {task_id: 0/1/None}}} plus per-cell metadata. The latest attempt of a cell counts
    only if earlier ones were unscored (the predeclared replacement rule)."""
    table: dict[str, dict[int, dict[str, Optional[int]]]] = defaultdict(lambda: defaultdict(dict))
    meta: dict[tuple[str, int, str], dict] = {}
    by_cell: dict[str, list[dict]] = defaultdict(list)
    stores = store if isinstance(store, list) else [store]
    for a in (x for st in stores for x in st.attempts(experiment_id=experiment_id)):
        role = a["info"].get("role", "")
        if not role.startswith("eval:"):
            continue
        label = role[len("eval:"):]
        if label not in arms or (task_filter is not None and not task_filter(a["task_id"])):
            continue
        by_cell[a["cell"]].append(a)
    for cell, atts in by_cell.items():
        atts.sort(key=lambda a: a["started_at"])
        scored = [a for a in atts if a["status"] == FINISHED]
        a = scored[0] if scored else atts[-1]
        label = a["info"]["role"][len("eval:"):]
        arm, run = arms[label]
        y = (1 if (a["reward"] or 0) >= 1.0 else 0) if a["status"] == FINISHED else None
        table[arm][run][a["task_id"]] = y
        meta[(arm, run, a["task_id"])] = {"reason": a["reason_code"], "steps": a["n_steps"], "status": a["status"],
                                          "attempts": len(atts), "wall_s": a["info"].get("wall_s")}
    return {"table": {k: dict(v) for k, v in table.items()}, "meta": meta}


def _family(task_id: str) -> str:
    return task_id.rsplit("-", 2)[0]


def _task_means(table: dict[int, dict[str, Optional[int]]], tasks: Iterable[str], runs: list[int]) -> dict[str, float]:
    return {t: sum(table[r][t] for r in runs) / len(runs) for t in tasks}


def _balanced(means: dict[str, float]) -> float:
    fams: dict[str, list[float]] = defaultdict(list)
    for t, m in means.items():
        fams[_family(t)].append(m)
    return sum(sum(v) / len(v) for v in fams.values()) / len(fams) if fams else float("nan")


def arm_success(res: dict[str, Any], arm: str) -> dict[str, Any]:
    table = res["table"][arm]
    runs = sorted(table)
    tasks = sorted({t for r in runs for t in table[r]})
    ok_tasks = [t for t in tasks if all(table[r].get(t) is not None for r in runs)]
    means = _task_means(table, ok_tasks, runs)
    fam: dict[str, list[float]] = defaultdict(list)
    for t, m in means.items():
        fam[_family(t)].append(m)
    per_run = {r: _balanced({t: float(table[r][t]) for t in ok_tasks}) for r in runs}
    return {"arm": arm, "runs": runs, "tasks": len(tasks), "scored_tasks": len(ok_tasks),
            "unscored_cells": sum(1 for r in runs for t in tasks if table[r].get(t) is None),
            "success_balanced": _balanced(means), "per_run_balanced": per_run,
            "per_family": {f: {"tasks": len(v), "success": sum(v) / len(v)} for f, v in sorted(fam.items())}}


def paired(res: dict[str, Any], a: str, b: str, *, n_boot: int = 10000, seed: int = 20260929) -> dict[str, Any]:
    ta, tb = res["table"][a], res["table"][b]
    ra, rb = sorted(ta), sorted(tb)
    tasks = sorted(({t for r in ra for t in ta[r]} & {t for r in rb for t in tb[r]}))
    tasks = [t for t in tasks if all(ta[r].get(t) is not None for r in ra) and all(tb[r].get(t) is not None for r in rb)]
    ma, mb = _task_means(ta, tasks, ra), _task_means(tb, tasks, rb)
    diff = _balanced(ma) - _balanced(mb)
    fams: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        fams[_family(t)].append(t)
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sa = [rng.choice(ra) for _ in ra]
        sb = [rng.choice(rb) for _ in rb]
        fam_diffs = []
        for f, ts in fams.items():
            pick = [rng.choice(ts) for _ in ts]
            da = sum(sum(ta[r][t] for r in sa) / len(sa) for t in pick) / len(pick)
            db = sum(sum(tb[r][t] for r in sb) / len(sb) for t in pick) / len(pick)
            fam_diffs.append(da - db)
        boots.append(sum(fam_diffs) / len(fam_diffs))
    boots.sort()
    lo, hi = boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]
    pos = sum(1 for t in tasks if ma[t] > mb[t])
    neg = sum(1 for t in tasks if ma[t] < mb[t])
    return {"a": a, "b": b, "tasks": len(tasks), "difference_balanced": diff, "ci95": [lo, hi],
            "tasks_a_better": pos, "tasks_b_better": neg, "sign_test_p": sign_test(pos, neg),
            "per_family": {f: sum(ma[t] - mb[t] for t in ts) / len(ts) for f, ts in sorted(fams.items())}}


def sign_test(pos: int, neg: int) -> float:
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def reason_rates(res: dict[str, Any], arm: str) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    n = 0
    steps = []
    for (ar, run, t), m in res["meta"].items():
        if ar != arm or m["status"] != FINISHED:
            continue
        n += 1
        counts[m["reason"] or "?"] += 1
        steps.append(m["steps"] or 0)
    return {"scored_cells": n, "reasons": {k: v / n for k, v in sorted(counts.items())} if n else {},
            "mean_steps": sum(steps) / len(steps) if steps else None}


__all__ = ["outcomes", "arm_success", "paired", "sign_test", "reason_rates"]


def checkpoint_tradeoffs(stores: "Store | list[Store]", experiment_id: Optional[str] = None) -> dict[str, Any]:
    """Checkpoint overhead, restore cost vs replay distance, restore fidelity, and recovery success by
    the evidence that chose the restart point (docs/correction.md)."""
    stores = stores if isinstance(stores, list) else [stores]
    cap: dict[str, list[float]] = defaultdict(list)
    restores: list[dict] = []
    by_reason: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])   # verified, scored, branches
    by_step_bucket: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for st in stores:
        where = {"experiment_id": experiment_id} if experiment_id else {}
        for c in st.charges(**where):
            if c["kind"] == "checkpoint_seconds":
                cap[c["evidence"].get("strategy", "?")].append(c["amount"])
        for rep in st.repairs(**where):
            reasons = {p["ckpt_id"]: p["reason"] for p in rep["config"].get("restart_points", [])}
            for b in st.branches(repair_id=rep["repair_id"]):
                ck = st.checkpoint(b["ckpt_id"])
                for r in b["restore"].get("attempts", []):
                    restores.append({"strategy": r.get("strategy"), "seconds": r.get("seconds"), "ok": r.get("ok"),
                                     "replayed": r.get("replayed_steps", 0),
                                     "tables_equal": (r.get("fidelity") or {}).get("tables_equal"),
                                     "screen_distance": (r.get("fidelity") or {}).get("screen_distance")})
                key = f"{rep['mode']}:{reasons.get(b['ckpt_id'], '?')}"
                row = by_reason[key]
                row[2] += 1
                if b["status"] == FINISHED:
                    row[1] += 1
                    row[0] += int((b["reward"] or 0) >= 1)
                bucket = "0" if ck["step"] == 0 else "1-9" if ck["step"] < 10 else "10-29" if ck["step"] < 30 else "30+"
                bb = by_step_bucket[f"{rep['mode']}:{bucket}"]
                bb[1] += int(b["status"] == FINISHED)
                bb[0] += int(b["status"] == FINISHED and (b["reward"] or 0) >= 1)
    def stats(v: list[float]) -> dict[str, Any]:
        v = sorted(x for x in v if x is not None)
        if not v:
            return {"n": 0}
        return {"n": len(v), "p50": v[len(v) // 2], "p90": v[min(len(v) - 1, int(0.9 * len(v)))], "mean": sum(v) / len(v)}
    per_strategy = defaultdict(list)
    for r in restores:
        per_strategy[r["strategy"]].append(r)
    replay = [r for r in restores if r["strategy"] == "replay" and r["replayed"] and r["seconds"]]
    slope = (sum(r["seconds"] for r in replay) / sum(r["replayed"] for r in replay)) if replay else None
    return {
        "capture_seconds": {k: stats(v) for k, v in cap.items()},
        "restore": {k: {"seconds": stats([r["seconds"] for r in v]), "ok_rate": sum(1 for r in v if r["ok"]) / len(v),
                        "tables_equal_rate": sum(1 for r in v if r["tables_equal"]) / len(v), "n": len(v)}
                    for k, v in per_strategy.items()},
        "replay_seconds_per_step": slope,
        "recovery_by_reason": {k: {"verified": a, "scored": b, "branches": c} for k, (a, b, c) in sorted(by_reason.items())},
        "recovery_by_restart_step": {k: {"verified": a, "scored": b} for k, (a, b) in sorted(by_step_bucket.items())},
    }
