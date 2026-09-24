"""Recompute the reading model-upgrade outcomes from results.json without another request.

Written and hashed into docs/protocol-reading-model-upgrade.md before any inference.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from math import comb
from pathlib import Path
import runpy
import statistics

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "runs/reading-model-upgrade-20260924.py"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mcnemar_exact(b, c):
    """Exact two-sided McNemar: binomial test on the discordant pairs at p = 0.5."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def wrong_pattern(expected, typed):
    """Pre-registered descriptive classes for a wrong typed authorization."""
    if len(typed) == len(expected) - 1:
        for i in range(len(expected)):
            if expected[:i] + expected[i + 1:] == typed:
                repeated = (i > 0 and expected[i - 1] == expected[i]) or (i + 1 < len(expected) and expected[i + 1] == expected[i])
                return "dropped repeated character" if repeated else "dropped character"
    if len(typed) == len(expected):
        diffs = [(e, t) for e, t in zip(expected, typed) if e != t]
        if diffs and all(e.isalpha() != t.isalpha() and e.isalnum() and t.isalnum() for e, t in diffs):
            return "letter/digit confusion"
        if diffs:
            return "substitution"
    return "other"


def main(out):
    ns = runpy.run_path(str(RUNNER))
    protocol = json.loads((out / "protocol.json").read_text())
    results = json.loads((out / "results.json").read_text())
    ledger = json.loads((out / "ledger.json").read_text())
    assert sha(out / "protocol.json") == results["protocol_sha256"]
    _, cases, labels = ns["verify_dataset"](ns["PACKAGE"])
    expected = [(row["case_id"], row["arm"]) for row in protocol["cells"]]
    actual = [(row["case_id"], row["arm"]) for row in results["cells"]]
    assert actual == expected and len(set(actual)) == 80
    rows = {(row["case_id"], row["arm"]): row for row in results["cells"]}
    operations = {}
    for operation in ledger["operations"]:
        evidence = json.loads(operation["evidence"])
        response_id = evidence.get("response_id")
        if response_id:
            assert response_id not in operations
            operations[response_id] = operation
    paired = {kind: Counter() for kind in ("authorization", "navigation")}
    summaries = {arm: {"model": model, "statuses": Counter(), "kinds": {}, "input_tokens": 0,
                       "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "usage_cost_usd": 0,
                       "latencies_seconds": [], "parse_failures": 0, "truncations": 0, "retried_cells": 0,
                       "wrong_values": []}
                 for arm, model in protocol["arms"].items()}
    outcomes = []
    for case in cases:
        case_id = case["case_id"]
        label = labels[case_id]
        kind = label["kind"]
        outcome = {"case_id": case_id, "seed": label["seed"], "kind": kind}
        if kind == "authorization":
            outcome["expected_authorization"] = label["expected_authorization"]
        available = []
        for arm in ("A", "B"):
            row = rows[(case_id, arm)]
            summary = summaries[arm]
            summary["statuses"][row["status"]] += 1
            summary["retried_cells"] += bool(row.get("retried"))
            bucket = summary["kinds"].setdefault(kind, Counter())
            bucket["planned"] += 1
            if row["status"] != "completed":
                outcome[arm] = {"status": row["status"], "error": row.get("error")}
                available.append(False)
                continue
            assert row["calls"] == 1 and row["response"]["model"] == row["model"] == protocol["arms"][arm]
            measured = ns["metrics"](row["action"], row["meta"], label)
            assert measured == row["metrics"]
            bucket["completed"] += 1
            if kind == "authorization":
                hit = measured["exact_authorization_type"]
                bucket["wrong_type"] += measured["wrong_authorization_type"]
                bucket["type_attempts"] += measured["authorization_type_attempt"]
                bucket["other_or_no_action"] += not measured["authorization_type_attempt"]
                if measured["wrong_authorization_type"]:
                    summary["wrong_values"].append({"seed": label["seed"], "expected": label["expected_authorization"],
                                                    "typed": row["action"]["text"],
                                                    "edit_distance": measured["wrong_type_edit_distance"],
                                                    "pattern": wrong_pattern(label["expected_authorization"], row["action"]["text"])})
            else:
                hit = measured["teacher_action_agreement"]
            bucket["hits"] += hit
            summary["parse_failures"] += measured["parse_failure"]
            summary["truncations"] += measured["truncated"]
            usage = row["response"]["usage"]
            summary["input_tokens"] += usage["prompt_tokens"]
            summary["cached_input_tokens"] += (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            summary["output_tokens"] += usage["completion_tokens"]
            summary["reasoning_tokens"] += (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
            operation = operations[row["response"]["id"]]
            assert operation["actual"] is not None
            summary["usage_cost_usd"] += operation["actual"]
            summary["latencies_seconds"].append(row["meta"]["model_latency_s"])
            outcome[arm] = {"status": "completed", "hit": hit, "action": row["action"],
                            "thoughts": row["meta"].get("thoughts"), "metrics": measured,
                            "input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"],
                            "usage_cost_usd": operation["actual"], "retried": bool(row.get("retried"))}
            available.append(True)
        paired[kind]["planned_pairs"] += 1
        if all(available):
            left, right = rows[(case_id, "A")], rows[(case_id, "B")]
            assert left["without_model_sha256"] == right["without_model_sha256"]
            assert left["image_sha256"] == right["image_sha256"]
            paired[kind]["complete_pairs"] += 1
            hits = outcome["A"]["hit"], outcome["B"]["hit"]
            paired[kind][{(True, True): "both", (True, False): "A_only", (False, True): "B_only", (False, False): "neither"}[hits]] += 1
        else:
            paired[kind]["incomplete_pairs"] += 1
        outcomes.append(outcome)
    tests = {}
    for kind, counts in paired.items():
        b, c = counts["A_only"], counts["B_only"]
        tests[kind] = {"A_only": b, "B_only": c, "exact_two_sided_mcnemar_p": mcnemar_exact(b, c)}
    tests["authorization"]["primary"] = True
    tests["navigation"]["primary"] = False
    auth = {arm: summaries[arm]["kinds"].get("authorization", Counter()).get("hits", 0) for arm in ("A", "B")}
    p = tests["authorization"]["exact_two_sided_mcnemar_p"]
    verdict = ("gpt-6-luna significantly worse" if p < 0.05 and tests["authorization"]["A_only"] > tests["authorization"]["B_only"]
               else "gpt-6-luna significantly better" if p < 0.05
               else "no significant difference")
    go = auth["B"] <= 14 and auth["A"] >= 16 and auth["A"] - auth["B"] >= 5
    for summary in summaries.values():
        latencies = summary.pop("latencies_seconds")
        summary["latency_median_seconds"] = statistics.median(latencies) if latencies else None
        summary["wrong_pattern_counts"] = Counter(v["pattern"] for v in summary["wrong_values"])
    complete = all(row["status"] == "completed" for row in results["cells"])
    analysis = {
        "protocol_sha256": sha(out / "protocol.json"), "results_sha256": sha(out / "results.json"),
        "analyzer_sha256": sha(Path(__file__)), "complete": complete, "arms": protocol["arms"],
        "planned_requests": 80, "completed_requests": sum(row["status"] == "completed" for row in results["cells"]),
        "stop_reason": results["stop_reason"], "summaries": summaries, "paired": paired, "tests": tests,
        "primary_exact_typing": {"A": auth["A"], "B": auth["B"], "of": 20}, "primary_verdict": verdict,
        "phase3_go": go, "phase3_rule": "GO iff gpt-6-luna <= 14/20 and gpt-5.6-luna >= 16/20 (gap >= 5)",
        "spending": ledger["services"], "cases": outcomes,
        "limits": ["States reached and selected from gpt-5.6-luna's own successful episodes (on-distribution for arm A).",
                   "Parsed typing does not establish field selection, persisted entry, or task completion.",
                   "Navigation agreement is imitation of gpt-5.6-luna's recorded action, not correctness.",
                   "One response per arm/state; no repeated-sampling reliability estimate.",
                   "Letters rendered by the pre-0.2.1 generator; live held-out letters differ.",
                   "Same provider-reported model aliases, not independently verified immutable weights."],
    }
    (out / "analysis.json").write_text(json.dumps(analysis, indent=2) + "\n")
    print(json.dumps({key: value for key, value in analysis.items() if key not in ("cases", "limits", "summaries")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    main(parser.parse_args().out)
