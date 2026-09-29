"""Matched-collection-cost selection for the learning experiment (``forkloop budget``).

Every arm spends collection effort in *units*:

* ``demo`` arm — one teacher attempt from the initial state (verified or not);
* ``forkloop`` arm — one student attempt (the agent under repair; needed to find its failures) plus,
  if it failed, one checkpoint repair (all its branches, restores and replays, failed ones too);
* ``restart`` arm — the same student attempt plus one full-restart repair of it (same ``k``).

A unit's cost is ``teacher_usd + world_hours * world_usd_per_hour + student_steps * student_usd_per_step``
with rates declared in the protocol. Units are taken in a fixed order (task seed order, then attempt
order) until the budget is spent; the dataset is the verified paths inside the included units. Nested
budgets (B/4, B/2, B) give the data-scaling curve. Nothing here looks at evaluation results.
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


def demo_units(store: Store, experiment_id: str) -> list[Unit]:
    ch = _charges_by_ref(store)
    units = []
    for a in store.attempts(experiment_id=experiment_id):
        if a["info"].get("role") != "teacher" or a["status"] not in (FINISHED, "infra_error"):
            continue
        c = ch.get(a["attempt_id"], [])
        ok = a["status"] == FINISHED and (a["reward"] or 0) >= 1.0
        units.append(Unit("demo", _task_key(a["task_id"]), a["task_id"], a["attempt_id"], None,
                          [a["attempt_id"]] if ok else [], teacher_usd=_sum(c, "model_tokens", "usd"),
                          world_hours=_sum(c, "attempt_wall_seconds") / 3600, teacher_steps=a["n_steps"] or 0))
    return sorted(units, key=lambda u: (u.key, u.attempt_id))


def repair_units(store: Store, attempt_experiment: str, repair_experiment: str, mode: str, arm: str) -> list[Unit]:
    """Student attempts of ``attempt_experiment`` (all of them: finding failures costs), plus the
    ``mode`` repair of each failure recorded under ``repair_experiment``."""
    ch = _charges_by_ref(store)
    reps = {r["attempt_id"]: r for r in store.repairs(experiment_id=repair_experiment) if r["mode"] == mode}
    units = []
    for a in store.attempts(experiment_id=attempt_experiment):
        if a["info"].get("role") != "student" or a["status"] not in (FINISHED, "infra_error"):
            continue
        c = ch.get(a["attempt_id"], [])
        u = Unit(arm, _task_key(a["task_id"]), a["task_id"], a["attempt_id"], None, [],
                 world_hours=_sum(c, "attempt_wall_seconds") / 3600, student_steps=a["n_steps"] or 0)
        rep = reps.get(a["attempt_id"])
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


def select(units: list[Unit], budget_usd: float, rates: Rates) -> tuple[list[Unit], float]:
    chosen, spent = [], 0.0
    for u in units:
        c = u.cost(rates)
        if spent + c > budget_usd + 1e-9:
            break
        chosen.append(u)
        spent += c
    return chosen, spent


def summarize(units: list[Unit], rates: Rates) -> dict[str, Any]:
    return {"units": len(units), "verified_paths": sum(len(u.verified_sources) for u in units),
            "cost_usd": round(sum(u.cost(rates) for u in units), 4),
            "teacher_usd": round(sum(u.teacher_usd for u in units), 4),
            "world_hours": round(sum(u.world_hours for u in units), 4),
            "student_steps": sum(u.student_steps for u in units), "teacher_steps": sum(u.teacher_steps for u in units)}


__all__ = ["Rates", "Unit", "demo_units", "repair_units", "select", "summarize"]
