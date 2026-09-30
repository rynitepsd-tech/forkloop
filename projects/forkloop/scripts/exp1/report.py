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
from forkloop.correction.analysis import arm_success, checkpoint_tradeoffs, outcomes, paired, reason_rates, sign_test
from forkloop.correction.repair import counted_repair

import report_extra as rx  # noqa: E402  (scripts/exp1/report_extra.py)

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
    st = stores[0]  # collection ran on main's store
    failed = [a for a in st.attempts(experiment_id="exp1-round1")
              if a["info"].get("role") == "student" and a["status"] == "finished" and (a["reward"] or 0) < 1]
    for exp, mode in (("exp1-round1", "checkpoint"), ("exp1-restart", "full_restart")):
        by, br, br_counted = defaultdict(Counter), Counter(), Counter()
        reps = [r for r in st.repairs(experiment_id=exp) if r["mode"] == mode]
        for r in reps:
            for b in st.branches(repair_id=r["repair_id"]):
                br["verified" if b["status"] == "finished" and (b["reward"] or 0) >= 1 else b["status"]] += 1
        for a in failed:
            rep, tried = counted_repair(st, a["attempt_id"], experiment_id=exp, mode=mode)
            key = rep["status"] if rep else ("exhausted" if tried >= 3 else "pending")
            by[fam(a["task_id"])][key] += 1
            by[fam(a["task_id"])]["void_repairs"] += tried - (1 if rep else 0)
            for b in st.branches(repair_id=rep["repair_id"]) if rep else []:
                br_counted["verified" if b["status"] == "finished" and (b["reward"] or 0) >= 1 else b["status"]] += 1
        out[f"repairs:{mode}"] = {"counted_by_family": {f: dict(c) for f, c in sorted(by.items())},
                                  "branches_counted_repairs": dict(br_counted), "branches_all": dict(br),
                                  "repairs_all": len(reps)}
    return out


def repair_modes(stores: list[Store]) -> dict:
    """Secondary, descriptive: per failure, did its counted checkpoint repair and its counted
    full-restart repair verify? Same failures, teacher and k; exact sign test on discordant failures."""
    from forkloop.correction.budget import Unit, _task_key, interleave
    st = stores[0]
    scored = [a for a in st.attempts(experiment_id="exp1-round1")
              if a["info"].get("role") == "student" and a["status"] == "finished"]
    order = [u.attempt_id for u in interleave([Unit("", _task_key(a["task_id"]), a["task_id"], a["attempt_id"], None, [])
                                               for a in scored])]
    by_id = {a["attempt_id"]: a for a in scored}
    failed_all = [by_id[i] for i in order if (by_id[i]["reward"] or 0) < 1]
    window = failed_all[:50]   # protocol note 2026-09-30 08:45: first 50 failures in selection order
    out = {"window": _modes(st, window), "outside_window_settled": _modes(st, failed_all[50:], settled_only=True)}
    return out


def _modes(st: Store, failed: list[dict], settled_only: bool = False) -> dict:
    both = Counter()
    per_fam = defaultdict(Counter)
    for a in failed:
        ck, _ = counted_repair(st, a["attempt_id"], experiment_id="exp1-round1", mode="checkpoint")
        fr, _ = counted_repair(st, a["attempt_id"], experiment_id="exp1-restart", mode="full_restart")
        if ck is None or fr is None:
            if not settled_only:
                both["not both scored"] += 1
            continue
        key = ("ckpt+" if ck["status"] == "verified" else "ckpt-") + ("/restart+" if fr["status"] == "verified" else "/restart-")
        both[key] += 1
        per_fam[fam(a["task_id"])][key] += 1
    pos, neg = both["ckpt+/restart-"], both["ckpt-/restart+"]
    return {"failures": len(failed), "paired": dict(both), "per_family": {f: dict(c) for f, c in sorted(per_fam.items())},
            "checkpoint_only": pos, "restart_only": neg, "sign_test_p": sign_test(pos, neg)}


def training(adapters: Path) -> dict:
    out = {}
    runs = [adapters] if (adapters / "train_log.jsonl").exists() else (sorted(adapters.iterdir()) if adapters.exists() else [])
    for d in runs:
        log = d / "train_log.jsonl"
        if not log.exists():
            continue
        rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        args = json.loads((d / "final" / "training_args.json").read_text()) if (d / "final" / "training_args.json").exists() else {}
        out[d.name] = {"steps": rows[-1]["step"] if rows else 0, "final_loss": rows[-1]["loss"] if rows else None,
                       "first_loss": rows[0]["loss"] if rows else None,
                       "gpu_hours": round(rows[-1]["elapsed_s"] / 3600, 3) if rows else None,
                       "examples_seen": rows[-1].get("examples_seen") if rows else None,
                       "datasets": [x.get("dataset_id") for x in (args.get("datasets") or [])],
                       "seed": (args.get("optim") or {}).get("seed")}
    return out


def main(a: argparse.Namespace) -> None:
    stores = [Store(p) for p in a.store]
    adapters = [Path(x) for x in (a.adapters or [])]
    rep: dict = {"collection": collection(stores), "repair_modes": repair_modes(stores),
                 "checkpoints": {e: checkpoint_tradeoffs(stores[0], e) for e in ("exp1-round1", "exp1-restart")},
                 "training": {k: v for d in adapters for k, v in training(d).items()}}
    bd = Path(a.budget_dir) if a.budget_dir else None
    rep["review2"] = {"all_work_cost": rx.all_work(stores[0]), "all_work_selection": rx.all_work_selection(stores[0]),
                      "repair_mode_views": rx.repair_mode_views(stores[0], bd), "branch_views": rx.branch_views(stores[0]),
                      "training_provenance": rx.training_provenance([d.parent if (d / "final").exists() else d for d in adapters])}
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
        rep["evaluation"]["extras"] = rx.eval_extras(stores, ARMS, ["main (shard 0/2)", "aux (shard 1/2)"][:len(stores)])
        names = {"A0": "untrained", "S_W": "warm start", "A1": "demonstrations", "A2": "Forkloop corrections",
                 "A3": "full-restart repairs"}
        ev = rep["evaluation"]
        rep["video_lines"] = [f"{names.get(arm, arm)} ({arm}): {v['success_balanced']:.0%} (family-balanced)"
                              for arm, v in ev["arms"].items() if arm in ("A0", "S_W", "A1", "A2", "A3")]
        rep["video_lines"] += [f"{p['a']} − {p['b']}: {p['difference_balanced']:+.2f} "
                               f"(95% CI {p['ci95'][0]:+.2f} to {p['ci95'][1]:+.2f})"
                               for p in ev["paired"] if p["a"] == "A2" and p["b"] in ("A0", "A1", "A3")]
        a2a1 = next((p for p in ev["paired"] if p["a"] == "A2" and p["b"] == "A1"), None)
        if a2a1:
            rep["video_conclusion"] = ("Corrections beat demonstrations at matched cost." if a2a1["ci95"][0] > 0 else
                                       "Corrections did not beat demonstrations at matched cost (registered criterion not met)."
                                       if a2a1["ci95"][1] >= 0 else "Demonstrations beat corrections at matched cost.")
        rep["video_footer"] = ("150 registered final-test tasks, student alone, family-balanced success; "
                               "3 training runs per arm, one collected dataset per arm; scripts/exp1/report.py")
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
    md.append("\n## Checkpoint vs full-restart repairs (secondary, descriptive, per failure)\n")
    for part, rm in rep["repair_modes"].items():
        md.append(f"- **{part}**: {rm['failures']} failures; both modes scored on "
                  f"{sum(v for k, v in rm['paired'].items() if k != 'not both scored')}. Repaired only from the "
                  f"checkpoint: {rm['checkpoint_only']}; only from the start: {rm['restart_only']}; exact sign test "
                  f"p = {rm['sign_test_p']:.3g}. `{json.dumps(rm['paired'])}`")
    r2 = rep["review2"]
    md.append("\n## All-work cost per arm by cause (review 2, B1; registered rates)\n")
    md.append("The matched-cost datasets above charge counted (clean) work only (protocol 15:28). Everything spent:\n")
    for arm, v in r2["all_work_cost"].items():
        md.append(f"- **{arm}** total ${v['total_usd']:.2f}: " + "; ".join(f"{k} {x['n']} (${x['usd']:.2f})" for k, x in v["by_cause"].items()))
    sel = r2["all_work_selection"]
    md.append(f"\nUnder all-work accounting B would be ${sel['budget_usd']:.2f}, selecting (descriptive; not trained): "
              + "; ".join(f"{k} {v['units']} units, {v['verified_paths']} verified paths from {v['tasks_with_paths']} tasks"
                          for k, v in sel["selection"].items()))
    md.append("\n## Repair-mode views (exploratory)\n")
    for scope, v in r2["repair_mode_views"].items():
        md.append(f"- **{scope}** ({v['failures']} failures; split {json.dumps(v['checkpoint success split'])}; "
                  f"{len(v['unsettled'])} unsettled):")
        for k in ("counted repairs, any restart point", "counted repairs, evidence point only (k = 3 vs 3)",
                  "first repair started, unscored branches as failures"):
            x = v[k]
            md.append(f"  - {k}: both scored {x['both_scored']}, checkpoint only {x['ckpt_only']}, restart only "
                      f"{x['restart_only']}, both {x['both']}, neither {x['neither']}, sign test p = {x['sign_test_p']:.3g}")
    md.append(f"\n```json\n{json.dumps(r2['branch_views'], indent=1)[:6000]}\n```")
    md.append(f"\n## Checkpoints (collection only)\n\n```json\n{json.dumps(rep['checkpoints'], indent=1)[:6000]}\n```")
    if rep["training"]:
        prov = r2["training_provenance"]
        md.append("\n## Training runs\n\n| run | seed | host | steps | first loss | final loss | GPU h | datasets |\n| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |")
        for k, v in rep["training"].items():
            pv = prov.get(k, {})
            md.append(f"| {k} | {v['seed']} | {pv.get('host', '')} | {v['steps']} | {v['first_loss']:.3f} | {v['final_loss']:.3f} | "
                      f"{v['gpu_hours']} | {', '.join(v['datasets'] or [])} |")
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
        md.append("\nIntervals for trained-arm contrasts are conditional on one collected dataset per arm (review 2, M4).")
        ex = rep["evaluation"]["extras"]
        for name, v in ex["sensitivity"].items():
            md.append(f"\n**Sensitivity — {name}:** " + "; ".join(f"{a} {x:.3f}" for a, x in v["arms"].items()))
            md.append("| comparison | tasks | difference | 95% CI | sign-test p |\n| --- | ---: | ---: | --- | ---: |")
            for p in v["paired"]:
                md.append(f"| {p['a']} − {p['b']} | {p['tasks']} | {p['difference_balanced']:+.3f} | "
                          f"[{p['ci95'][0]:+.3f}, {p['ci95'][1]:+.3f}] | {p['sign_test_p']:.3g} |")
        md.append(f"\n```json\n{json.dumps({k: ex[k] for k in ('per_shard_success', 'unscored_cells_by_cause', 'attempts_per_cell', 'end_reasons', 'model_latency_s')}, indent=1)}\n```")
    Path(a.out_md).write_text("\n".join(md) + "\n")
    print(a.out_md)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", action="append", required=True)
    ap.add_argument("--budget-dir", default=None)
    ap.add_argument("--adapters", action="append", help="adapter runs dir, or one run dir (repeatable)")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--final", action="store_true")
    main(ap.parse_args())
