"""exp1 results: collection, checkpoint tradeoffs, training runs, final evaluation (paired).

    python scripts/exp1/report.py --store PATH [--store PATH2] --budget-dir DIR --adapters DIR \
        --out-json docs/results-exp1.json --out-md docs/results-exp1.md [--final]

Without ``--final`` the evaluation section is omitted (collection-phase reporting only), so nobody
reads final-test outcomes before every planned cell has run.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from forkloop.correction import Store
from forkloop.correction.analysis import arm_success, checkpoint_tradeoffs, outcomes, paired, reason_rates

ARMS = {"A0": ("A0", 0), "sw": ("S_W", 0),
        **{f"{a}-s{s}": (a, s) for a in ("A1", "A2", "A3") for s in (1, 2, 3)}}


def fam(task_id: str) -> str:
    return task_id.rsplit("-", 2)[0]


def collection(stores: list[Store]) -> dict:
    out = {}
    for exp, role in (("exp1-warmstart", "teacher"), ("exp1-demos", "teacher"), ("exp1-round1", "student")):
        rows = [a for st in stores for a in st.attempts(experiment_id=exp) if a["info"].get("role") == role]
        by = defaultdict(Counter)
        for a in rows:
            key = "verified" if a["status"] == "finished" and (a["reward"] or 0) >= 1 else (
                "failed" if a["status"] == "finished" else "unscored")
            by[fam(a["task_id"])][key] += 1
        out[exp] = {f: dict(c) for f, c in sorted(by.items())}
    for exp, mode in (("exp1-round1", "checkpoint"), ("exp1-restart", "full_restart")):
        reps = [r for st in stores for r in st.repairs(experiment_id=exp) if r["mode"] == mode]
        by = defaultdict(Counter)
        for r in reps:
            att = next(st.attempt(r["attempt_id"]) for st in stores if st.attempts(attempt_id=r["attempt_id"]))
            by[fam(att["task_id"])][r["status"]] += 1
        br = Counter()
        for st in stores:
            for r in reps:
                for b in st.branches(repair_id=r["repair_id"]):
                    br["verified" if b["status"] == "finished" and (b["reward"] or 0) >= 1 else b["status"]] += 1
        out[f"repairs:{mode}"] = {"by_family": {f: dict(c) for f, c in sorted(by.items())}, "branches": dict(br)}
    return out


def training(adapters: Path) -> dict:
    out = {}
    for d in sorted(adapters.iterdir()) if adapters.exists() else []:
        log = d / "train_log.jsonl"
        if not log.exists():
            continue
        rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        args = json.loads((d / "final" / "training_args.json").read_text()) if (d / "final" / "training_args.json").exists() else {}
        out[d.name] = {"steps": rows[-1]["step"] if rows else 0, "final_loss": rows[-1]["loss"] if rows else None,
                       "first_loss": rows[0]["loss"] if rows else None,
                       "gpu_hours": round(rows[-1]["elapsed_s"] / 3600, 3) if rows else None,
                       "examples_seen": rows[-1].get("examples_seen") if rows else None,
                       "datasets": args.get("dataset_ids") or args.get("datasets"), "seed": args.get("seed")}
    return out


def main(a: argparse.Namespace) -> None:
    stores = [Store(p) for p in a.store]
    rep: dict = {"collection": collection(stores), "checkpoints": checkpoint_tradeoffs(stores),
                 "training": training(Path(a.adapters)) if a.adapters else {}}
    if a.budget_dir and (Path(a.budget_dir) / "budget-report.json").exists():
        b = json.loads((Path(a.budget_dir) / "budget-report.json").read_text())
        rep["budget"] = {"budget_usd": b["budget_usd"], "rates": b["rates"], "totals": b["totals"],
                         "arms": {k: {kk: v[kk] for kk in ("units", "verified_paths", "cost_usd", "teacher_usd",
                                                           "world_hours", "student_steps", "teacher_steps", "records",
                                                           "dataset_id", "by_origin")} for k, v in b["arms"].items()}}
    if a.final:
        res = outcomes(stores, "exp1-eval", ARMS, task_filter=lambda t: "-final_test-" in t)  # registered list only
        arms_present = sorted(res["table"])
        rep["evaluation"] = {"arms": {arm: arm_success(res, arm) | {"reasons": reason_rates(res, arm)} for arm in arms_present},
                             "paired": [paired(res, x, y) for x, y in (("A2", "A0"), ("A2", "A1"), ("A2", "A3"),
                                                                        ("A1", "A0"), ("A3", "A0"), ("S_W", "A0"))
                                        if x in res["table"] and y in res["table"]]}
        names = {"A0": "untrained", "S_W": "warm start", "A1": "demonstrations", "A2": "Forkloop corrections",
                 "A3": "full-restart repairs"}
        ev = rep["evaluation"]
        rep["video_lines"] = [f"{names.get(arm, arm)} ({arm}): {v['success_balanced']:.0%} of held-out tasks"
                              for arm, v in ev["arms"].items() if arm in ("A0", "A1", "A2", "A3")]
        rep["video_lines"] += [f"{p['a']} − {p['b']}: {p['difference_balanced']:+.2f} "
                               f"(95% CI {p['ci95'][0]:+.2f} to {p['ci95'][1]:+.2f})"
                               for p in ev["paired"] if p["a"] == "A2" and p["b"] in ("A0", "A1")]
        rep["video_footer"] = ("150 registered final-test tasks, student alone, family-balanced, "
                               "3 training runs per arm; generated by scripts/exp1/report.py")
    Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out_json).write_text(json.dumps(rep, indent=2, default=str))
    md = ["# exp1 results (generated)", "", f"Source: `scripts/exp1/report.py` over {', '.join(a.store)}.", ""]
    md.append("## Collection\n")
    for exp, v in rep["collection"].items():
        md.append(f"- **{exp}**: `{json.dumps(v)}`")
    if "budget" in rep:
        md.append(f"\n## Matched collection cost\n\nBudget B = ${rep['budget']['budget_usd']:.2f} (smallest arm total). Rates: `{json.dumps(rep['budget']['rates'])}`\n")
        md.append("| dataset | units | verified paths | records | cost $ | teacher $ | world h | student steps |")
        md.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for k, v in sorted(rep["budget"]["arms"].items()):
            md.append(f"| {k} | {v['units']} | {v['verified_paths']} | {v['records']} | {v['cost_usd']:.2f} | {v['teacher_usd']:.2f} | {v['world_hours']:.2f} | {v['student_steps']} |")
    md.append(f"\n## Checkpoints\n\n```json\n{json.dumps(rep['checkpoints'], indent=1)[:4000]}\n```")
    if rep["training"]:
        md.append("\n## Training runs\n\n| run | steps | first loss | final loss | GPU h |\n| --- | ---: | ---: | ---: | ---: |")
        for k, v in rep["training"].items():
            md.append(f"| {k} | {v['steps']} | {v['first_loss']:.3f} | {v['final_loss']:.3f} | {v['gpu_hours']} |")
    if "evaluation" in rep:
        md.append("\n## Final evaluation (student alone, final_test)\n")
        md.append("| arm | runs | scored tasks | unscored cells | success (family-balanced) | per family |")
        md.append("| --- | --- | ---: | ---: | ---: | --- |")
        for arm, v in rep["evaluation"]["arms"].items():
            pf = "; ".join(f"{f}: {x['success']:.2f}" for f, x in v["per_family"].items())
            md.append(f"| {arm} | {v['runs']} | {v['scored_tasks']} | {v['unscored_cells']} | {v['success_balanced']:.3f} | {pf} |")
        md.append("\n| comparison | tasks | difference | 95% CI | A better | B better | sign-test p |")
        md.append("| --- | ---: | ---: | --- | ---: | ---: | ---: |")
        for p in rep["evaluation"]["paired"]:
            md.append(f"| {p['a']} − {p['b']} | {p['tasks']} | {p['difference_balanced']:+.3f} | "
                      f"[{p['ci95'][0]:+.3f}, {p['ci95'][1]:+.3f}] | {p['tasks_a_better']} | {p['tasks_b_better']} | {p['sign_test_p']:.3g} |")
    Path(a.out_md).write_text("\n".join(md) + "\n")
    print(a.out_md)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", action="append", required=True)
    ap.add_argument("--budget-dir", default=None)
    ap.add_argument("--adapters", default=None)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--final", action="store_true")
    main(ap.parse_args())
