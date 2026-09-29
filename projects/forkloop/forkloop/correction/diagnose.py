"""Choose restart points for a failed attempt from recorded evidence (``forkloop failures``).

This is controller-side analysis. It may read the task's hidden expected values and the verifier
details (privileged), because it only decides *where* to restore; nothing it computes is shown to
the teacher or written into any policy input. Evidence used, in priority order:

1. **origin** — the first step where the policy wrote or typed a near-miss of a hidden expected
   value (e.g. an authorization number with one wrong character): the error was made there,
   so restart at the last clean checkpoint at or before it;
2. **damage** — the first checkpoint whose verifier status is ``damaged`` (a wrong value
   persisted, a duplicate, a collateral edit): restart at the last clean checkpoint before it;
3. **stall** — the first step of a run of repeated actions: restart before the loop began;
4. **latest** — the latest clean checkpoint (shortest replay, most preserved progress);
5. **start** — step 0, which is a full restart (the fallback, and the full-restart baseline).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from ..trajectories import load_episode


@dataclass
class RestartPoint:
    ckpt_id: str
    step: int
    reason: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"ckpt_id": self.ckpt_id, "step": self.step, "reason": self.reason, "evidence": self.evidence}


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _hidden_strings(expected: dict[str, Any]) -> list[str]:
    out = []
    for v in expected.values():
        vals = v if isinstance(v, list) else [v]
        for x in vals:
            if isinstance(x, str) and len(x) >= 5 and re.search(r"\d", x):
                out.append(x)
    return out


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{3,}")


def near_miss_origin(steps: list[dict[str, Any]], expected: dict[str, Any], *, max_distance: int = 2,
                     instruction: str = "") -> Optional[dict]:
    """First step whose typed text or memory write contains a near miss (1..max_distance edits)
    of a *hidden* expected value — one the agent must read from the application because the
    instruction does not state it (an authorization number) — or a planted decoy value.
    Identifiers the instruction states (claim numbers, a new member id) are excluded: mentioning a
    neighbouring claim number on screen is not an error (2026-09-29 false positives)."""
    targets = [t for t in _hidden_strings({k: v for k, v in expected.items() if not str(k).startswith("decoy")})
               if t not in instruction]
    decoys = set(_hidden_strings({k: v for k, v in expected.items() if str(k).startswith("decoy")}))
    for s in steps:
        texts = []
        a = s.get("action") or {}
        if a.get("type") == "type" and a.get("text"):
            texts.append(("typed", a["text"]))
        for fact in (s.get("agent") or {}).get("memory_written", []) or []:
            texts.append(("memory", fact))
        for kind, text in texts:
            for tok in _TOKEN_RE.findall(text):
                if tok in decoys:
                    return {"step": s["i"], "kind": kind, "why": "decoy value"}
                for t in targets:
                    if tok.upper() != t.upper() and abs(len(tok) - len(t)) <= max_distance and \
                            0 < _levenshtein(tok.upper(), t.upper()) <= max_distance:
                        return {"step": s["i"], "kind": kind, "why": f"near miss ({_levenshtein(tok.upper(), t.upper())} edits)"}
    return None


def stall_start(steps: list[dict[str, Any]], *, repeats: int = 3) -> Optional[int]:
    run, start, prev = 0, None, None
    for s in steps:
        key = s.get("raw_action") if not s.get("action") else str(s["action"])
        if key == prev:
            run += 1
            if run + 1 >= repeats:
                return start
        else:
            run, start, prev = 0, s["i"], key
    return None


def _last_clean_at_or_before(ckpts: list[dict], step: int) -> Optional[dict]:
    cands = [c for c in ckpts if c["step"] <= step and c["status"] == "clean" and _restorable(c)]
    return max(cands, key=lambda c: c["step"]) if cands else None


def _restorable(c: dict) -> bool:
    return not c.get("deleted_at") or c["strategy"] in ("replay", "reset")


def restart_points(attempt: dict[str, Any], ckpts: list[dict[str, Any]], task: Any, *,
                   max_points: int = 3, include_start: bool = True) -> list[RestartPoint]:
    ep = load_episode(Path(attempt["run_dir"]))
    steps = ep["steps"]
    verdict = ep.get("verdict") or {}
    end = len(steps)
    out: list[RestartPoint] = []

    def add(c: Optional[dict], reason: str, evidence: dict) -> None:
        if c is not None and all(p.ckpt_id != c["ckpt_id"] for p in out):
            out.append(RestartPoint(c["ckpt_id"], c["step"], reason, evidence))

    origin = near_miss_origin(steps, getattr(task, "expected", {}) or {}, instruction=getattr(task, "instruction", ""))
    if origin is not None:
        add(_last_clean_at_or_before(ckpts, origin["step"]), "origin", origin)
    damaged = [c for c in ckpts if c["status"] == "damaged"]
    if damaged:
        first = min(damaged, key=lambda c: c["step"])
        add(_last_clean_at_or_before(ckpts, first["step"] - 1), "damage", {"first_damaged_step": first["step"],
                                                                           "why": first["oracle"].get("why")})
    st = stall_start(steps)
    if st is not None:
        add(_last_clean_at_or_before(ckpts, st), "stall", {"stall_start": st})
    add(_last_clean_at_or_before(ckpts, end), "latest", {"end_step": end, "verdict": verdict.get("reason_code")})
    points = out[: max_points - (1 if include_start else 0)] if include_start else out[:max_points]
    if include_start:
        start = next((c for c in ckpts if c["step"] == 0), None)
        if start is not None and all(p.ckpt_id != start["ckpt_id"] for p in points):
            points.append(RestartPoint(start["ckpt_id"], 0, "start", {}))
    return points


def failures(store: Any, *, experiment_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Scored failed attempts (reward 0, finished). Unscored/interrupted attempts are listed apart."""
    where = {"experiment_id": experiment_id} if experiment_id else {}
    return [a for a in store.attempts(**where) if a["status"] == "finished" and (a["reward"] or 0) < 1.0]


__all__ = ["restart_points", "RestartPoint", "failures", "near_miss_origin", "stall_start"]
