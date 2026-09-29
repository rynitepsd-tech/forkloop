"""Matched-collection-cost selection for the learning experiment (``forkloop budget``).

Every arm spends collection effort in *units*:

* ``demo`` arm — one teacher attempt from the initial state (verified or not);
* ``forkloop`` arm — one student attempt (the agent under repair; needed to find its failures) plus,
  if it failed, one checkpoint repair (all its branches, restores and replays, failed ones too);
* ``restart`` arm — the same student attempt plus one full-restart repair of it (same ``k``).

A unit's cost is ``teacher_usd + world_hours * world_usd_per_hour + student_steps * student_usd_per_step``
with rates declared in the protocol. Units are taken in a fixed order (families round-robin, each in
seed order) until the budget is spent; the dataset is the verified paths inside the included units.
Nested budgets (B/4, B/2, B) give the data-scaling curve. Nothing here looks at evaluation results.

**Unscored work** (infrastructure: unscored attempts, repairs with an unscored branch) is not a cost of
any arm by default: each arm is charged for its scored attempts and, per failure, its *counted*
repair (the first one with no unscored branch; ``repair.counted_repair``), exactly as unscored cells
are replaced rather than counted in the evaluation. ``count_unscored=True`` reproduces the earlier
accounting (every attempt with status finished/infra_error and the latest repair, whatever its
branches). A failure whose repairs are still missing is *pending*: ``select`` refuses to cut the
budget past it, and one with ``1 + infra_retries`` unscored repairs is excluded and reported.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .store import FINISHED, Store


@dataclass
class Rates:
    world_usd_per_hour: float
    student_usd_per_step: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"world_usd_per_hour": self.world_usd_per_hour, "student_usd_per_step": self.student_usd_per_step,
                "note": self.note}


@dataclass
class Unit:
    arm: str
    key: str                              # ordering key: task seed order
    task_id: str
    attempt_id: str
    repair_id: Optional[str]
    verified_sources: list[str]           # branch ids (repairs) or the attempt id (demos)
    teacher_usd: float = 0.0
    world_hours: float = 0.0
    student_steps: int = 0
    teacher_steps: int = 0
    parts: dict[str, float] = field(default_factory=dict)
    pending: bool = False                 # a failure whose counted repair does not exist yet
    excluded: Optional[str] = None        # reason it can never count (e.g. repairs exhausted)

    def cost(self, r: Rates) -> float:
        return self.teacher_usd + self.world_hours * r.world_usd_per_hour + self.student_steps * r.student_usd_per_step

    def to_dict(self, r: Rates) -> dict[str, Any]:
        return {"arm": self.arm, "task_id": self.task_id, "attempt_id": self.attempt_id, "repair_id": self.repair_id,
                "verified_sources": self.verified_sources, "teacher_usd": round(self.teacher_usd, 5),
                "world_hours": round(self.world_hours, 5), "student_steps": self.student_steps,
                "teacher_steps": self.teacher_steps, "cost_usd": round(self.cost(r), 5)}


def _charges_by_ref(store: Store) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in store.charges():
        if c["ref"]:
            out.setdefault(c["ref"], []).append(c)
    return out


def _sum(charges: list[dict], kind: str, field: str = "amount") -> float:
    return sum((c[field] or 0.0) for c in charges if c["kind"] == kind)


def _task_key(task_id: str) -> str:
    fam, split, seed = task_id.rsplit("-", 2)
    return f"{int(seed):09d}:{fam}"


def demo_units(store: Store, experiment_id: str, *, count_unscored: bool = False) -> list[Unit]:
    ch = _charges_by_ref(store)
    units = []
    counted = (FINISHED, "infra_error") if count_unscored else (FINISHED,)
    for a in store.attempts(experiment_id=experiment_id):
        if a["info"].get("role") != "teacher" or a["status"] not in counted:
            continue
        c = ch.get(a["attempt_id"], [])
        ok = a["status"] == FINISHED and (a["reward"] or 0) >= 1.0
        units.append(Unit("demo", _task_key(a["task_id"]), a["task_id"], a["attempt_id"], None,
                          [a["attempt_id"]] if ok else [], teacher_usd=_sum(c, "model_tokens", "usd"),
                          world_hours=_sum(c, "attempt_wall_seconds") / 3600, teacher_steps=a["n_steps"] or 0))
    return sorted(units, key=lambda u: (u.key, u.attempt_id))


def repair_units(store: Store, attempt_experiment: str, repair_experiment: str, mode: str, arm: str, *,
                 count_unscored: bool = False, infra_retries: int = 2) -> list[Unit]:
    """Student attempts of ``attempt_experiment`` (all scored ones: finding failures costs), plus the
    counted ``mode`` repair of each failure recorded under ``repair_experiment``."""
    from .repair import counted_repair

    ch = _charges_by_ref(store)
    latest = {r["attempt_id"]: r for r in store.repairs(experiment_id=repair_experiment) if r["mode"] == mode}
    units = []
    for a in store.attempts(experiment_id=attempt_experiment):
        if a["info"].get("role") != "student" or a["status"] not in ((FINISHED, "infra_error") if count_unscored else (FINISHED,)):
            continue
        c = ch.get(a["attempt_id"], [])
        wall = _sum(c, "attempt_wall_seconds")
        if mode == "full_restart":
            # A restart-only pipeline would not capture mid-episode checkpoints: do not charge their overhead.
            ck_ids = {ck["ckpt_id"] for ck in store.checkpoints(a["attempt_id"]) if ck["step"] > 0}
            wall -= sum(x["amount"] for ref in ck_ids for x in ch.get(ref, []) if x["kind"] == "checkpoint_seconds")
        u = Unit(arm, _task_key(a["task_id"]), a["task_id"], a["attempt_id"], None, [],
                 world_hours=max(0.0, wall) / 3600, student_steps=a["n_steps"] or 0)
        failed = a["status"] == FINISHED and (a["reward"] or 0) < 1.0
        if count_unscored:
            rep = latest.get(a["attempt_id"])
        elif failed:
            rep, tried = counted_repair(store, a["attempt_id"], experiment_id=repair_experiment, mode=mode)
            if rep is None and tried >= 1 + infra_retries:
                u.excluded = f"{tried} unscored repairs"
            elif rep is None:
                u.pending = True
        else:
            rep = None
        if rep is not None:
            u.repair_id = rep["repair_id"]
            for b in store.branches(repair_id=rep["repair_id"]):
                bc = ch.get(b["branch_id"], [])
                u.teacher_usd += _sum(bc, "model_tokens", "usd")
                u.world_hours += _sum(bc, "branch_wall_seconds") / 3600
                u.teacher_steps += b["n_steps"] or 0
                if b["status"] == FINISHED and (b["reward"] or 0) >= 1.0:
                    u.verified_sources.append(b["branch_id"])
        units.append(u)
    return sorted(units, key=lambda u: (u.key, u.attempt_id))


def interleave(units: list[Unit]) -> list[Unit]:
    """Round-robin across families (each family in seed order), so a truncated budget keeps every
    family represented (review 2026-09-29: seed order starved compose_claims)."""
    fams: dict[str, list[Unit]] = {}
    for u in sorted(units, key=lambda u: (u.key, u.attempt_id)):
        fams.setdefault(u.task_id.rsplit("-", 2)[0], []).append(u)
    out, i = [], 0
    names = sorted(fams)
    while any(i < len(fams[f]) for f in names):
        for f in names:
            if i < len(fams[f]):
                out.append(fams[f][i])
        i += 1
    return out


class PendingUnit(RuntimeError):
    """The selection reached a failure whose repair is not settled yet: the dataset would be wrong."""


def select(units: list[Unit], budget_usd: float, rates: Rates) -> tuple[list[Unit], float]:
    chosen, spent = [], 0.0
    for u in interleave(units):
        if u.excluded:
            continue
        if u.pending:
            raise PendingUnit(f"{u.arm}: {u.attempt_id} ({u.task_id}) has no scored repair yet; "
                              f"${spent:.2f} of ${budget_usd:.2f} selected")
        c = u.cost(rates)
        if spent + c > budget_usd + 1e-9:
            break
        chosen.append(u)
        spent += c
    return chosen, spent


def summarize(units: list[Unit], rates: Rates) -> dict[str, Any]:
    """Totals over settled units; ``pending`` and ``excluded`` count the others."""
    extra = {"pending": sum(u.pending for u in units), "excluded": sum(bool(u.excluded) for u in units)}
    units = [u for u in units if not u.pending and not u.excluded]
    return {**extra, "units": len(units), "verified_paths": sum(len(u.verified_sources) for u in units),
            "cost_usd": round(sum(u.cost(rates) for u in units), 4),
            "teacher_usd": round(sum(u.teacher_usd for u in units), 4),
            "world_hours": round(sum(u.world_hours for u in units), 4),
            "student_steps": sum(u.student_steps for u in units), "teacher_steps": sum(u.teacher_steps for u in units)}


__all__ = ["Rates", "Unit", "PendingUnit", "demo_units", "repair_units", "select", "summarize", "interleave"]
