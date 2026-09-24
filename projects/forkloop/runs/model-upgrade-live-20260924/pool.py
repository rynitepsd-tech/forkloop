"""Pool the live model-upgrade A/B under the pre-registered rules (docs/protocol-model-upgrade-live.md).

Written before any cell ran. Arm A = gpt-5.6-luna, arm B = gpt-6-luna. Each seed's pair uses scored
cells only: the first scored cell per (seed, arm) in directory order (compare-a, compare-b, then any
retry-* directories, each holding exactly one pre-registered infrastructure retry). No scored cell is
ever replaced.
    ../../venv/bin/python pool.py > pooled.json
"""
from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRICE = {"A": (0.2, 1.2), "B": (0.1, 0.5)}  # USD per 1M (input, output) at <= 272K prompt tokens; cache reads 0.1x
SEEDS = list(range(100328, 100352))
AUTH = re.compile(r"AUTH-[A-Z0-9]{6,10}")
UNSCORED = (None, "INFRA_ERROR", "ORACLE_ERROR")


def dirs() -> list[str]:
    retries = sorted(p.name for p in HERE.glob("retry-*") if (p / "cells").is_dir())
    return [d for d in ("compare-a", "compare-b") if (HERE / d / "cells").is_dir()] + retries


def cell_row(d: str, cell: dict) -> dict:
    ep = HERE / d / cell["episode"] if cell.get("episode") else None
    has = lambda name: ep is not None and (ep / name).exists()  # noqa: E731
    verdict = json.loads((ep / "verdict.json").read_text()) if has("verdict.json") else {}
    steps = [json.loads(x) for x in (ep / "steps.jsonl").read_text().splitlines()] if has("steps.jsonl") else []
    reset = json.loads((ep / "reset.json").read_text()) if has("reset.json") else (cell.get("reset_report") or {})
    usage = cell.get("recorded_policy_usage") or {}
    p_in, p_out = PRICE[cell["arm"]]
    details = verdict.get("details") or {}
    auth = details.get("appeal_auth_number") or {}
    milestones = details.get("ui_milestones") or {}
    stages = {s["name"]: s for s in reset.get("stages", [])}
    # first authorization-like value the agent wrote in its own text (reasoning line or typed text)
    first_auth = None
    for s in steps:
        text = (s.get("raw_action") or "") + " " + json.dumps(s.get("action") or {})
        m = AUTH.search(text.upper())
        if m:
            first_auth = m.group(0)
            break
    scored = cell.get("status") == "completed" and verdict.get("reason_code") not in UNSCORED
    return {"dir": d, "cell": cell["id"], "arm": cell["arm"], "seed": cell["seed"], "status": cell.get("status"),
            "scored": scored, "error": (cell.get("error") or "")[:160] or None,
            "reward": verdict.get("reward"), "reason": verdict.get("reason_code"),
            "steps": len(steps), "wall_s": round(steps[-1]["t_wall"], 1) if steps else None,
            "reset_s": reset.get("total_seconds"),
            "feasibility_ok": (stages.get("feasibility") or {}).get("ok"),
            "feasibility_checks": (stages.get("feasibility") or {}).get("note"),
            "initial_screen_ok": (stages.get("initial_screen") or {}).get("ok"),
            "tokens_in": usage.get("in", 0), "tokens_cache_read": usage.get("cache_read", 0), "tokens_out": usage.get("out", 0),
            "model_usd": round((usage.get("in", 0) * p_in + usage.get("cache_read", 0) * p_in * .1
                                + usage.get("out", 0) * p_out) / 1e6, 4),
            "highest_milestone": milestones.get("highest"), "rungs": milestones.get("rungs"),
            "typed_auth": auth.get("actual"), "expected_auth": auth.get("expected"),
            "first_auth_in_text": first_auth,
            "first_auth_exact": (first_auth == auth.get("expected")) if first_auth and auth.get("expected") else None,
            "portal_yes_db_no": bool((milestones.get("rungs") or {}).get("appeal_submitted")) and verdict.get("reward") != 1.0,
            "initial_observation_sha256": cell.get("initial_observation_sha256"),
            "baseline": {t: h for t, h in ((cell.get("baseline_digest") or {}).get("tables") or {}).items()}}


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def outcome(a: dict, b: dict) -> str:
    sa, sb = a["reward"] == 1.0, b["reward"] == 1.0
    return "both" if sa and sb else "A_only" if sa else "B_only" if sb else "neither"


def per_arm(cells: list[dict]) -> dict:
    out = {}
    for arm in "AB":
        rows = [c for c in cells if c["arm"] == arm]
        order = ["openemr_login", "openemr_chart", "openemr_document", "portal_claim", "portal_appeal_form", "appeal_submitted"]
        out[arm] = {
            "cells": len(rows),
            "staircase": {r: sum(bool((c["rungs"] or {}).get(r)) for c in rows) for r in order},
            "correct_authorization": sum(c["reward"] == 1.0 for c in rows),
            "portal_yes_db_no": sum(c["portal_yes_db_no"] for c in rows),
            "portal_yes_db_no_wrong_authorization": sum(c["portal_yes_db_no"] and c["reason"] == "WRONG_VALUE"
                                                        and c["typed_auth"] != c["expected_auth"] for c in rows),
            "first_auth_in_text": {"exact": sum(c["first_auth_exact"] is True for c in rows),
                                   "wrong": sum(c["first_auth_exact"] is False for c in rows),
                                   "none": sum(c["first_auth_in_text"] is None for c in rows)},
            "reasons": {r: sum(c["reason"] == r for c in rows) for r in sorted({c["reason"] for c in rows}, key=str)},
            "median_steps": statistics.median([c["steps"] for c in rows]) if rows else None,
            "median_wall_s": statistics.median([c["wall_s"] for c in rows if c["wall_s"] is not None]) if rows else None,
            "median_model_usd": statistics.median([c["model_usd"] for c in rows]) if rows else None,
            "total_model_usd": round(sum(c["model_usd"] for c in rows), 4),
            "median_reset_s": statistics.median([c["reset_s"] for c in rows if c["reset_s"]]) if rows else None,
        }
    return out


def main() -> None:
    cells = []
    for d in dirs():
        for f in sorted((HERE / d / "cells").glob("*.json")):
            cells.append(cell_row(d, json.loads(f.read_text())))
    pairs, used = {}, set()
    for seed in SEEDS:
        pick = {}
        for arm in "AB":
            first = next((c for c in cells if c["seed"] == seed and c["arm"] == arm and c["scored"]), None)
            if first:
                pick[arm] = first
                used.add((first["dir"], first["cell"]))
        if len(pick) == 2:
            a, b = pick["A"], pick["B"]
            pick["baseline_tables_equal"] = a["baseline"] == b["baseline"] and bool(a["baseline"])
            pick["initial_observation_equal"] = a["initial_observation_sha256"] == b["initial_observation_sha256"]
            pairs[seed] = pick
    for c in cells:
        c["counted"] = (c["dir"], c["cell"]) in used
    counted = [c for c in cells if c["counted"]]
    test_pairs = {s: p for s, p in pairs.items() if p["baseline_tables_equal"]}
    outs = [outcome(p["A"], p["B"]) for p in test_pairs.values()]
    b, c = outs.count("A_only"), outs.count("B_only")
    p = mcnemar_exact(b, c)
    report = {
        "primary": {"pairs": len(test_pairs), "outcomes": {k: outs.count(k) for k in ("both", "A_only", "B_only", "neither")},
                    "success": {arm: f"{sum(q[arm]['reward'] == 1.0 for q in test_pairs.values())}/{len(test_pairs)}" for arm in "AB"},
                    "mcnemar_exact_two_sided_p": p,
                    "verdict": ("gpt-5.6-luna better" if p < 0.05 and b > c else "gpt-6-luna better" if p < 0.05 and c > b
                                else "no significant difference")},
        "pairs_excluded_for_reset_inequivalence": sorted(set(pairs) - set(test_pairs)),
        "missing_seeds": [s for s in SEEDS if s not in pairs],
        "secondary_counted_cells": per_arm(counted),
        "validity": {"counted_cells": len(counted),
                     "feasibility_ok": sum(c["feasibility_ok"] is True for c in counted),
                     "initial_screen_ok": sum(c["initial_screen_ok"] is True for c in counted),
                     "unscored_or_failed_cells": [f"{c['dir']}/{c['cell']} seed {c['seed']} {c['status']}: {c['error']}"
                                                  for c in cells if not c["scored"]]},
        "pairs": {s: {"A": f"{q['A']['dir']}/{q['A']['cell']}", "B": f"{q['B']['dir']}/{q['B']['cell']}",
                      "outcome": outcome(q["A"], q["B"]), "baseline_tables_equal": q["baseline_tables_equal"],
                      "initial_observation_equal": q["initial_observation_equal"]} for s, q in pairs.items()},
        "cells": [{k: v for k, v in c.items() if k != "baseline"} for c in cells],
    }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
