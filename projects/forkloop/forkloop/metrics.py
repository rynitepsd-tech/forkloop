"""Run-level metrics with Wilson 95% intervals (docs/contracts.md §12)."""

from __future__ import annotations

import json
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


# USD per 1M tokens: (input, output). Cache reads are billed at 0.1x input and cache
# writes at 1.25x input. Source: docs.anthropic.com pricing, read 2026-09-02.
MODEL_PRICES_PER_M: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # OpenAI GPT-5.6 family, developers.openai.com/api/docs/pricing read 2026-09-03 (short context)
    "gpt-5.6-luna": (0.2, 1.2),
    "gpt-5.6-terra": (2.0, 12.0),
    "gpt-5.6-sol": (4.0, 20.0),
}
CACHE_READ_MULT, CACHE_WRITE_MULT = 0.1, 1.25


def episode_tokens(episode: dict[str, Any]) -> dict[str, int]:
    """Token usage of one episode.

    Policies report *cumulative* usage on every step (`steps.jsonl` `tokens` is the running
    total, so batched actions from one model call repeat the same numbers). The episode's
    usage is therefore the maximum over steps, never the sum.
    """
    keys = ("in", "out", "cache_read", "cache_write")
    out = {k: 0 for k in keys}
    for s in episode["steps"]:
        t = s.get("tokens") or {}
        for k in keys:
            out[k] = max(out[k], int(t.get(k, 0) or 0))
    return out


def token_cost_usd(tokens: dict[str, int], prices_per_m: tuple[float, float]) -> float:
    p_in, p_out = prices_per_m
    return (tokens.get("in", 0) * p_in + tokens.get("out", 0) * p_out
            + tokens.get("cache_read", 0) * p_in * CACHE_READ_MULT
            + tokens.get("cache_write", 0) * p_in * CACHE_WRITE_MULT) / 1e6


def summarize_episodes(episodes: list[dict[str, Any]], *, vm_hour_usd: float = 0.134,
                       token_prices_per_m: Optional[tuple[float, float]] = None,
                       model: Optional[str] = None) -> dict[str, Any]:
    """Summarise episodes. Token cost uses `token_prices_per_m` if given, else the price
    table entry for `model`; with neither, tokens are counted but priced at zero."""
    if token_prices_per_m is None:
        token_prices_per_m = MODEL_PRICES_PER_M.get(model or "", (0.0, 0.0))
    n = len(episodes)
    succ = [e for e in episodes if e["verdict"] and e["verdict"].get("reward", 0) >= 1.0]
    reasons = Counter((e["verdict"] or {}).get("reason_code", "NO_VERDICT") for e in episodes)
    steps = [len(e["steps"]) for e in episodes]
    walls = [float((e["verdict"] or {}).get("wall_seconds", 0.0)) for e in episodes if e["verdict"]]
    n_actions = sum(steps)
    n_invalid = sum(1 for e in episodes for s in e["steps"] if not s.get("valid", True))
    per_ep = [episode_tokens(e) for e in episodes]
    tokens = {k: sum(t[k] for t in per_ep) for k in ("in", "out", "cache_read", "cache_write")}
    vm_cost = sum(walls) / 3600 * vm_hour_usd
    tok_cost = token_cost_usd(tokens, token_prices_per_m)
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
        "cost_vm_usd": round(vm_cost, 4),
        "cost_tokens_usd": round(tok_cost, 4),
        "cost_per_episode_usd": round(total_cost / n, 4) if n else None,
        "model": model,
        "token_prices_per_m": list(token_prices_per_m),
        "invalid_action_rate": _rate(n_invalid, n_actions),
        "wrong_record_rate": _rate(reasons.get("WRONG_RECORD", 0), n),
        "duplicate_side_effect_rate": _rate(reasons.get("DUPLICATE_SIDE_EFFECT", 0), n),
        "collateral_edit_rate": _rate(reasons.get("COLLATERAL_EDIT", 0), n),
        "reason_codes": dict(sorted(reasons.items())),
        "tokens": tokens,
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
    """Summarise a run directory. The model (for token pricing) comes from `run.json` when
    the caller does not pass one."""
    run_dir = Path(run_dir)
    if kw.get("model") is None and (run_dir / "run.json").exists():
        try:
            kw["model"] = json.loads((run_dir / "run.json").read_text()).get("model")
        except (OSError, ValueError):
            pass
    selected = iter_episode_dirs(run_dir)
    every = iter_episode_dirs(run_dir, include_superseded=True)
    eps = [load_episode(p) for p in selected]
    s = summarize_episodes(eps, **kw)
    # `collect --retry-failed` keeps one selected attempt per seed (the shortest verified one) and
    # marks the rest superseded. Rates, steps and walls describe the selected attempts; cost and
    # tokens count every attempt, because every attempt was paid for.
    chosen = set(selected)
    superseded = [load_episode(p) for p in every if p not in chosen]
    s["n_attempts"] = len(every)
    s["n_superseded"] = len(superseded)
    if superseded:
        extra = summarize_episodes(superseded, **{**kw, "model": s.get("model"),
                                                  "token_prices_per_m": tuple(s["token_prices_per_m"])})
        for k in ("cost_total_usd", "cost_vm_usd", "cost_tokens_usd"):
            s[k] = round(s[k] + extra[k], 4)
        s["tokens"] = {k: s["tokens"][k] + extra["tokens"][k] for k in s["tokens"]}
        k_ok = s["success_rate"]["k"]
        s["cost_per_success_usd"] = round(s["cost_total_usd"] / k_ok, 4) if k_ok else None
        s["cost_per_episode_usd"] = round(s["cost_total_usd"] / len(every), 4)
    s["run_dir"] = str(run_dir)
    return s


def format_table(summary: dict[str, Any]) -> str:
    def r(x: Optional[dict[str, Any]]) -> str:
        if not x:
            return "n/a"
        return f"{x['value'] * 100:.1f}% [{x['lo'] * 100:.1f}, {x['hi'] * 100:.1f}] (n={x['n']})"

    rows = [
        ("episodes", str(summary["n_episodes"])
         + (f" selected of {summary['n_attempts']} attempts ({summary['n_superseded']} superseded)"
            if summary.get("n_superseded") else "")),
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
        ("cost / episode (USD)", str(summary["cost_per_episode_usd"])),
        ("  of which VM / tokens", f"{summary['cost_vm_usd']} / {summary['cost_tokens_usd']}"),
        ("tokens in / out", f"{summary['tokens']['in']} / {summary['tokens']['out']}"
         + (f" (cache read {summary['tokens']['cache_read']})" if summary['tokens'].get('cache_read') else "")),
        ("model priced as", str(summary.get("model"))),
    ]
    w = max(len(a) for a, _ in rows)
    lines = [f"{a.ljust(w)}  {b}" for a, b in rows]
    lines.append("reason codes: " + ", ".join(f"{k}={v}" for k, v in summary["reason_codes"].items()))
    return "\n".join(lines)


__all__ = ["wilson", "summarize_run", "summarize_episodes", "format_table", "episode_tokens",
           "token_cost_usd", "MODEL_PRICES_PER_M"]
