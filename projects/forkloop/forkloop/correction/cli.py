"""CLI for the failure-to-correction loop (wired into ``forkloop`` by ``forkloop.cli``).

    forkloop record   --config P --role student --families F --split S --seeds 1-20 --experiment E
    forkloop failures --config P --experiment E                 failed attempts + chosen restart points
    forkloop repair   --config P --experiment E [--mode checkpoint|full_restart] [--attempt ID ...]
    forkloop dataset  --config P --out DIR [--experiment E]     verified, immutable, with lineage
    forkloop evaluate --config P --experiment E --families F --split S --seeds ...   student alone
    forkloop status   --config P [--experiment E]               counts, unscored cells, costs
    forkloop inspect  --config P --out report.html [--experiment E]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path
from typing import Any, Optional


def parse_seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    if len(out) != len(set(out)):
        raise ValueError("duplicate seeds")
    return out


def _tasks(world: Any, families: str, split: str, seeds: str) -> list[Any]:
    fams = [f.strip() for f in families.split(",") if f.strip()]
    return [world.generate(f, s, split) for f in fams for s in parse_seeds(seeds)]


def _guard_final(split: str, allow: bool) -> None:
    try:
        from ..splits import is_final_split
    except Exception:  # noqa: BLE001 - split policy module optional in early versions
        def is_final_split(s: str) -> bool:
            return s in ("final_test",)
    if is_final_split(split) and not allow:
        raise SystemExit(f"split {split!r} is the final test pool: only `forkloop evaluate --final` may run it")


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def summarize_attempts(rows: list[dict]) -> dict[str, Any]:
    scored = [r for r in rows if r["status"] == "finished"]
    wins = sum(1 for r in scored if (r["reward"] or 0) >= 1.0)
    reasons: dict[str, int] = {}
    for r in scored:
        reasons[r["reason_code"] or "?"] = reasons.get(r["reason_code"] or "?", 0) + 1
    unscored: dict[str, int] = {}
    for r in rows:
        if r["status"] != "finished":
            unscored[r["status"]] = unscored.get(r["status"], 0) + 1
    lo, hi = _wilson(wins, len(scored))
    return {"cells": len({r["cell"] for r in rows}), "attempts": len(rows), "scored": len(scored), "successes": wins,
            "success_rate": (wins / len(scored)) if scored else None, "wilson95": [round(lo, 4), round(hi, 4)],
            "reasons": reasons, "unscored": unscored,
            "mean_steps": (sum(r["n_steps"] or 0 for r in scored) / len(scored)) if scored else None}


# ----------------------------------------------------------------------------- commands


def cmd_record(args: argparse.Namespace) -> int:
    from .project import load_project
    from .runner import run_attempts

    proj = load_project(args.config)
    _guard_final(args.split, False)
    world = proj.world()
    backend = proj.backend(world)
    policy = proj.teacher if args.role == "teacher" else proj.student
    if policy is None:
        raise SystemExit(f"the project has no {args.role} policy")
    tasks = _tasks(world, args.families, args.split, args.seeds)
    ckpt = proj.checkpoints

    async def run():
        try:
            return await run_attempts(store=proj.store(), world=world, backend=backend, tasks=tasks, role=args.role,
                                      policy_factory=policy.factory, ckpt=ckpt, experiment_id=args.experiment,
                                      concurrency=args.concurrency or proj.concurrency, replicate=args.replicate,
                                      infra_retries=proj.infra_retries, history_k=proj.history_k, budget=proj.budget)
        finally:
            await backend.close()

    asyncio.run(run())
    rows = proj.store().attempts(experiment_id=args.experiment)
    print(json.dumps(summarize_attempts([r for r in rows if r["info"].get("role") == args.role]), indent=2))
    return 0


def cmd_failures(args: argparse.Namespace) -> int:
    from .diagnose import failures, restart_points
    from .project import load_project
    from .repair import task_for

    proj = load_project(args.config, require_env=False)
    store, world = proj.store(), proj.world()
    out = []
    for a in failures(store, experiment_id=args.experiment):
        if args.role and a["info"].get("role") != args.role:
            continue
        task = task_for(store, a["task_id"], world)
        pts = restart_points(a, store.checkpoints(a["attempt_id"]), task, max_points=proj.repair.max_restart_points)
        out.append({"attempt_id": a["attempt_id"], "task_id": a["task_id"], "reason": a["reason_code"],
                    "steps": a["n_steps"], "restart_points": [p.to_dict() for p in pts]})
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        for f in out:
            pts = ", ".join(f"step {p['step']} ({p['reason']})" for p in f["restart_points"])
            print(f"{f['attempt_id']}  {f['task_id']:<45} {f['reason']:<22} {f['steps']:>3} steps  restart: {pts}")
        print(f"{len(out)} failed attempts")
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from .diagnose import failures
    from .project import load_project
    from .runner import run_repairs

    proj = load_project(args.config)
    if proj.teacher is None:
        raise SystemExit("the project has no teacher policy")
    world = proj.world()
    backend = proj.backend(world)
    store = proj.store()
    cfg = replace(proj.repair, mode=args.mode, **({"k": args.k} if args.k else {}))
    ids = args.attempt or [a["attempt_id"] for a in failures(store, experiment_id=args.source_experiment or args.experiment)
                           if a["info"].get("role") == args.role]
    if args.limit:
        ids = ids[: args.limit]

    async def run():
        try:
            return await run_repairs(store=store, world=world, backend=backend, attempt_ids=ids,
                                     teacher_factory=proj.teacher.factory, cfg=cfg, experiment_id=args.experiment,
                                     concurrency=args.concurrency or proj.concurrency)
        finally:
            await backend.close()

    res = asyncio.run(run())
    print(json.dumps({"repairs": len(res), "verified": sum(1 for r in res if r.status == "verified")}, indent=2))
    return 0


def cmd_dataset(args: argparse.Namespace) -> int:
    from .dataset import export_dataset
    from .project import load_project

    proj = load_project(args.config, require_env=False)
    manifest = export_dataset(proj.store(), proj.world(), args.out, experiment_id=args.experiment,
                              include_corrections=not args.no_corrections, include_demos=not args.no_demos,
                              name=args.name)
    print(json.dumps({k: manifest[k] for k in ("dataset_id", "counts", "splits", "audit", "files")}, indent=2))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from .checkpoint import CheckpointPolicy
    from .project import load_project
    from .runner import run_attempts

    proj = load_project(args.config)
    _guard_final(args.split, args.final)
    world = proj.world()
    backend = proj.backend(world)
    policy = proj.student
    if policy is None:
        raise SystemExit("the project has no student policy")
    tasks = _tasks(world, args.families, args.split, args.seeds)
    # the student alone: no teacher, no search, one attempt per cell; step-0 bookkeeping only
    ckpt = CheckpointPolicy(strategy="replay", every=0, before_types=False, before_keys=(), oracle_status=False)
    role = f"eval:{args.label}"

    async def run():
        try:
            return await run_attempts(store=proj.store(), world=world, backend=backend, tasks=tasks, role=role,
                                      policy_factory=policy.factory, ckpt=ckpt, experiment_id=args.experiment,
                                      concurrency=args.concurrency or proj.concurrency, replicate=args.replicate,
                                      infra_retries=proj.infra_retries, history_k=proj.history_k, budget=proj.budget)
        finally:
            await backend.close()

    asyncio.run(run())
    rows = [r for r in proj.store().attempts(experiment_id=args.experiment) if r["info"].get("role") == role]
    print(json.dumps(summarize_attempts(rows), indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from .project import load_project

    proj = load_project(args.config, require_env=False)
    store = proj.store()
    rows = store.attempts(**({"experiment_id": args.experiment} if args.experiment else {}))
    by_role: dict[str, list] = {}
    for r in rows:
        by_role.setdefault(f"{r['experiment_id']}:{r['info'].get('role')}", []).append(r)
    reps = store.repairs(**({"experiment_id": args.experiment} if args.experiment else {}))
    branches = [b for rep in reps for b in store.branches(repair_id=rep["repair_id"])]
    out = {"attempts": {k: summarize_attempts(v) for k, v in sorted(by_role.items())},
           "repairs": {s: sum(1 for r in reps if r["status"] == s) for s in sorted({r["status"] for r in reps})},
           "branches": {s: sum(1 for b in branches if b["status"] == s) for s in sorted({b["status"] for b in branches})},
           "costs": store.cost_summary(args.experiment), "datasets": [
               {k: d[k] for k in ("dataset_id", "n_records", "path")} for d in store.datasets()]}
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    from .inspect_html import write_report
    from .project import load_project

    proj = load_project(args.config, require_env=False)
    path = write_report(proj.store(), proj.world(), Path(args.out), experiment_id=args.experiment,
                        attempt_id=args.attempt, max_attempts=args.max_attempts)
    print(path)
    return 0


def add_commands(sub: Any) -> None:
    add_cleanup_command(sub)

    def cfg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", required=True, help="project YAML (see forkloop/correction/project.py)")

    p = sub.add_parser("record", help="record attempts with bound checkpoints")
    cfg(p)
    p.add_argument("--role", choices=["student", "teacher"], default="student")
    p.add_argument("--families", required=True)
    p.add_argument("--split", required=True)
    p.add_argument("--seeds", required=True, help="e.g. 1-20,25")
    p.add_argument("--experiment", required=True)
    p.add_argument("--replicate", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=None)
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("failures", help="list failed attempts and the restart points evidence selects")
    cfg(p)
    p.add_argument("--experiment", default=None)
    p.add_argument("--role", default="student")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_failures)

    p = sub.add_parser("repair", help="run verified alternative continuations from checkpoints")
    cfg(p)
    p.add_argument("--experiment", required=True, help="experiment id the repairs are recorded under")
    p.add_argument("--source-experiment", default=None, help="where the failed attempts are (default: --experiment)")
    p.add_argument("--attempt", action="append", default=None)
    p.add_argument("--role", default="student")
    p.add_argument("--mode", choices=["checkpoint", "full_restart"], default="checkpoint")
    p.add_argument("--k", type=int, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=None)
    p.set_defaults(fn=cmd_repair)

    p = sub.add_parser("dataset", help="export verified experience as an immutable dataset with lineage")
    cfg(p)
    p.add_argument("--out", required=True)
    p.add_argument("--experiment", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--no-demos", action="store_true")
    p.add_argument("--no-corrections", action="store_true")
    p.set_defaults(fn=cmd_dataset)

    p = sub.add_parser("evaluate", help="run the student alone (no teacher, no search) and score it")
    cfg(p)
    p.add_argument("--families", required=True)
    p.add_argument("--split", required=True)
    p.add_argument("--seeds", required=True)
    p.add_argument("--experiment", required=True)
    p.add_argument("--label", default="student")
    p.add_argument("--replicate", type=int, default=1)
    p.add_argument("--final", action="store_true", help="allow the final test pool (pre-registered evaluation only)")
    p.add_argument("--concurrency", type=int, default=None)
    p.set_defaults(fn=cmd_evaluate)

    p = sub.add_parser("status", help="counts, unscored cells and cumulative costs from the store")
    cfg(p)
    p.add_argument("--experiment", default=None)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("inspect", help="static HTML report: attempts, checkpoints, branches, verdicts, datasets")
    cfg(p)
    p.add_argument("--out", required=True)
    p.add_argument("--experiment", default=None)
    p.add_argument("--attempt", default=None)
    p.add_argument("--max-attempts", type=int, default=50)
    p.set_defaults(fn=cmd_inspect)


__all__ = ["add_commands", "parse_seeds", "summarize_attempts"]


async def cleanup_snapshots(store: Any, backend: Any, *, experiment_id: Optional[str] = None, dry_run: bool = False,
                            log: Any = print) -> list[dict]:
    """Delete provider snapshots behind checkpoints that are no longer needed: the attempt succeeded,
    or it is not a failure pending repair (every repair of it finished). Retained checkpoints and
    replay/reset checkpoints are left alone. The store keeps the checkpoint row (``deleted_at``)."""
    out = []
    for a in store.attempts(**({"experiment_id": experiment_id} if experiment_id else {})):
        reps = store.repairs(attempt_id=a["attempt_id"])
        pending = a["status"] == "running" or any(r["status"] == "running" for r in reps)
        failure_unrepaired = a["status"] == "finished" and (a["reward"] or 0) < 1 and not reps
        for c in store.checkpoints(a["attempt_id"]):
            if c["strategy"] != "snapshot" or c.get("deleted_at") or c.get("retained"):
                continue
            if pending or failure_unrepaired:
                out.append({"ckpt_id": c["ckpt_id"], "action": "kept (repair pending)"})
                continue
            action = "would delete" if dry_run else "deleted"
            if not dry_run:
                try:
                    await backend.delete_snapshot(c["world_ref"])
                    store.mark_checkpoint_deleted(c["ckpt_id"])
                    store.event("snapshot_deleted", c["ckpt_id"], snapshot=c["world_ref"])
                except Exception as e:  # noqa: BLE001
                    action = f"delete failed: {type(e).__name__}: {str(e)[:200]}"
            out.append({"ckpt_id": c["ckpt_id"], "snapshot": c["world_ref"], "action": action})
            log(f"[cleanup] {c['ckpt_id']} {c['world_ref']}: {action}")
    return out


def cmd_cleanup(args: argparse.Namespace) -> int:
    from .project import load_project

    proj = load_project(args.config, require_env=False)
    world = proj.world()
    backend = proj.backend(world)

    async def run():
        try:
            return await cleanup_snapshots(proj.store(), backend, experiment_id=args.experiment, dry_run=args.dry_run)
        finally:
            await backend.close()

    res = asyncio.run(run())
    print(json.dumps({"checkpoints": len(res), "deleted": sum(1 for r in res if r["action"] == "deleted")}, indent=2))
    return 0


def add_cleanup_command(sub: Any) -> None:
    p = sub.add_parser("cleanup", help="delete provider snapshots behind checkpoints no repair still needs")
    p.add_argument("--config", required=True)
    p.add_argument("--experiment", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_cleanup)
