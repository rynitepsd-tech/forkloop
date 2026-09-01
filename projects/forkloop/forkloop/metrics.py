"""Run-level metrics with Wilson 95% intervals (docs/contracts.md §12)."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from .trajectories import iter_episode_dirs, load_episode


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Return (p, lo, hi)."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def _rate(k: int, n: int) -> dict[str, float]:
    p, lo, hi = wilson(k, n)
    return {"value": round(p, 4), "lo": round(lo, 4), "hi": round(hi, 4), "k": k, "n": n}


def summarize_episodes(episodes: list[dict[str, Any]], *, vm_hour_usd: float = 0.134,
                       token_prices_per_m: tuple[float, float] = (0.0, 0.0)) -> dict[str, Any]:
    n = len(episodes)
    succ = [e for e in episodes if e["verdict"] and e["verdict"].get("reward", 0) >= 1.0]
    reasons = Counter((e["verdict"] or {}).get("reason_code", "NO_VERDICT") for e in episodes)
    steps = [len(e["steps"]) for e in episodes]
    walls = [float((e["verdict"] or {}).get("wall_seconds", 0.0)) for e in episodes if e["verdict"]]
    n_actions = sum(steps)
    n_invalid = sum(1 for e in episodes for s in e["steps"] if not s.get("valid", True))
    tok_in = sum(int((s.get("tokens") or {}).get("in", 0)) for e in episodes for s in e["steps"])
    tok_out = sum(int((s.get("tokens") or {}).get("out", 0)) for e in episodes for s in e["steps"])
    vm_cost = sum(walls) / 3600 * vm_hour_usd
    tok_cost = tok_in / 1e6 * token_prices_per_m[0] + tok_out / 1e6 * token_prices_per_m[1]
    total_cost = vm_cost + tok_cost
    milestones = [float((e["verdict"] or {}).get("milestones", 0.0)) for e in episodes if e["verdict"]]
    resets = [e["reset"]["total_seconds"] for e in episodes if e.get("reset") and e["reset"].get("ok")]
    return {
        "n_episodes": n,
        "success_rate": _rate(len(succ), n),
        "milestone_score": round(statistics.fmean(milestones), 4) if milestones else 0.0,
        "median_steps": statistics.median(steps) if steps else 0,
        "median_wall_s": round(statistics.median(walls), 2) if walls else 0.0,
        "median_reset_s": round(statistics.median(resets), 3) if resets else None,
        "cost_per_success_usd": round(total_cost / len(succ), 4) if succ else None,
        "cost_total_usd": round(total_cost, 4),
        "invalid_action_rate": _rate(n_invalid, n_actions),
        "wrong_record_rate": _rate(reasons.get("WRONG_RECORD", 0), n),
        "duplicate_side_effect_rate": _rate(reasons.get("DUPLICATE_SIDE_EFFECT", 0), n),
        "collateral_edit_rate": _rate(reasons.get("COLLATERAL_EDIT", 0), n),
        "reason_codes": dict(sorted(reasons.items())),
        "tokens": {"in": tok_in, "out": tok_out},
        "by_family": _by(episodes, "family"),
        "by_split": _by(episodes, "split"),
    }


def _by(episodes: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in episodes:
        groups.setdefault(str(e["manifest"].get(key)), []).append(e)
    out = {}
    for g, eps in sorted(groups.items()):
        k = sum(1 for e in eps if e["verdict"] and e["verdict"].get("reward", 0) >= 1.0)
        out[g] = _rate(k, len(eps))
    return out


def summarize_run(run_dir: str | Path, **kw: Any) -> dict[str, Any]:
    eps = [load_episode(p) for p in iter_episode_dirs(run_dir)]
    s = summarize_episodes(eps, **kw)
    s["run_dir"] = str(run_dir)
    return s


def format_table(summary: dict[str, Any]) -> str:
    def r(x: Optional[dict[str, Any]]) -> str:
        if not x:
            return "n/a"
        return f"{x['value'] * 100:.1f}% [{x['lo'] * 100:.1f}, {x['hi'] * 100:.1f}] (n={x['n']})"

    rows = [
        ("episodes", str(summary["n_episodes"])),
        ("success", r(summary["success_rate"])),
        ("milestones", f"{summary['milestone_score']:.3f}"),
        ("median steps", str(summary["median_steps"])),
        ("median wall (s)", str(summary["median_wall_s"])),
        ("median reset (s)", str(summary["median_reset_s"])),
        ("invalid-action rate", r(summary["invalid_action_rate"])),
        ("wrong-record rate", r(summary["wrong_record_rate"])),
        ("duplicate side-effect rate", r(summary["duplicate_side_effect_rate"])),
        ("collateral-edit rate", r(summary["collateral_edit_rate"])),
        ("cost / success (USD)", str(summary["cost_per_success_usd"])),
    ]
    w = max(len(a) for a, _ in rows)
    lines = [f"{a.ljust(w)}  {b}" for a, b in rows]
    lines.append("reason codes: " + ", ".join(f"{k}={v}" for k, v in summary["reason_codes"].items()))
    return "\n".join(lines)


__all__ = ["wilson", "summarize_run", "summarize_episodes", "format_table"]
