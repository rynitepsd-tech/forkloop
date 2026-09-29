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


def _pool_tasks(pool: str, families: str, per_family: int, skip: int) -> list[Any]:
    """Tasks from a named pool (forkloop/splits.py): train/dev in seed order without held-out structures,
    val/final_test from their frozen, hash-checked lists. ``skip`` keeps disjoint slices (pilot vs collection)."""
    import itertools

    from .. import splits
    fams = [f.strip() for f in families.split(",") if f.strip()]
    if pool in ("val", "final_test"):
        # the registered evaluation uses the frozen final_test list only, not the legacy sealed block
        tasks = splits.pool_tasks(pool, fams, include_legacy_sealed=False)
        out = []
        for f in fams:
            out += [t for t in tasks if t.family == f][skip: skip + per_family]
        return out
    out = []
    for f in fams:
        out += list(itertools.islice(splits.iter_pool(pool, f), skip, skip + per_family))
    return out


def _tasks(world: Any, families: str, split: Optional[str], seeds: Optional[str], *, allow_final: bool = False,
           pool: Optional[str] = None, per_family: int = 0, skip: int = 0) -> list[Any]:
    if pool:
        if pool == "final_test" and not allow_final:
            raise SystemExit("the final_test pool runs only through `forkloop evaluate --final`")
        return _pool_tasks(pool, families, per_family, skip)
    if not (split and seeds):
        raise SystemExit("give --pool NAME --per-family N, or --split and --seeds")
    fams = [f.strip() for f in families.split(",") if f.strip()]
    tasks = [world.generate(f, s, split) for f in fams for s in parse_seeds(seeds)]
    if not allow_final and world.name == "claims-ops-v1":
        from ..splits import final_reasons
        leaks = [(t.task_id, final_reasons(t)) for t in tasks if final_reasons(t)]
        if leaks:
            raise SystemExit(f"{len(leaks)} task(s) belong to the final test (split, sealed block or held-out "
                             f"structure), e.g. {leaks[0]}; only `forkloop evaluate --final` may run them")
    return tasks


def _guard_final(split: str, allow: bool) -> None:
    from ..splits import is_final_split
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


def _overrides(pairs: Optional[list[str]]) -> dict[str, Any]:
    """``--set model=arm-a2-s1 --set temperature=0`` → student option overrides (values parsed as YAML)."""
    import yaml
    out: dict[str, Any] = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--set expects key=value, got {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = yaml.safe_load(v)
    return out


def _preflight_served(policy: Any) -> None:
    """Refuse to start if a self-hosted student endpoint does not serve the requested model name
    (a missing adapter would otherwise turn every cell into provider errors; review 2026-09-29)."""
    opts = (policy.identity or {}).get("options", {}) if policy is not None else {}
    base, model = str(opts.get("base_url", "")), opts.get("model")
    if not base or "api.openai.com" in base or not model:
        return
    import httpx
    try:
        r = httpx.get(base.rstrip("/") + "/models", timeout=15)
        ids = {m.get("id") for m in r.json().get("data", [])}
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"cannot reach the student endpoint {base}: {type(e).__name__}: {e}")
    if model not in ids:
        raise SystemExit(f"{base} does not serve model {model!r} (serves {sorted(i for i in ids if i)})")


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

    proj = load_project(args.config, overrides=_overrides(args.set) if args.role == "student" else None)
    _guard_final(args.split or "", False)
    world = proj.world()
    backend = proj.backend(world)
    policy = proj.teacher if args.role == "teacher" else proj.student
    if policy is None:
        raise SystemExit(f"the project has no {args.role} policy")
    _preflight_served(policy)
    tasks = _tasks(world, args.families, args.split, args.seeds, pool=args.pool, per_family=args.per_family,
                   skip=args.skip)
    ckpt = proj.checkpoints
    if args.no_checkpoints:  # demonstrations from the initial state need no restart points (no overhead)
        from .checkpoint import CheckpointPolicy
        ckpt = CheckpointPolicy(strategy="replay", every=0, before_types=False, before_keys=(), oracle_status=False)

    async def run():
        try:
            return await run_attempts(store=proj.store(), world=world, backend=backend, tasks=tasks, role=args.role,
                                      policy_factory=policy.factory, ckpt=ckpt, experiment_id=args.experiment,
                                      concurrency=args.concurrency or proj.concurrency, replicate=args.replicate,
                                      infra_retries=proj.infra_retries, history_k=proj.history_k, budget=proj.budget,
                                      settle=proj.settle)
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
    fails = [a for a in failures(store, experiment_id=args.source_experiment or args.experiment)
             if a["info"].get("role") == args.role]
    if args.order == "budget":
        # the matched-cost selection order (families round-robin, each in seed order), so the repairs
        # inside a budget window finish first
        from .budget import Unit, _task_key, interleave
        fails = [a for u in interleave([Unit("", _task_key(a["task_id"]), a["task_id"], a["attempt_id"], None, [])
                                        for a in fails])
                 for a in fails if a["attempt_id"] == u.attempt_id]
    ids = args.attempt or [a["attempt_id"] for a in fails]
    if args.limit:
        ids = ids[: args.limit]

    async def run():
        try:
            return await run_repairs(store=store, world=world, backend=backend, attempt_ids=ids,
                                     teacher_factory=proj.teacher.factory, cfg=cfg, experiment_id=args.experiment,
                                     concurrency=args.concurrency or proj.concurrency, infra_retries=proj.infra_retries)
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

    proj = load_project(args.config, overrides=_overrides(args.set), require_env=False)  # the teacher is not used
    _guard_final(args.split or "", args.final)
    world = proj.world()
    backend = proj.backend(world)
    policy = proj.student
    if policy is None:
        raise SystemExit("the project has no student policy")
    _preflight_served(policy)
    tasks = _tasks(world, args.families, args.split, args.seeds, allow_final=args.final, pool=args.pool,
                   per_family=args.per_family, skip=args.skip)
    if args.shard:
        k, n = (int(x) for x in args.shard.split("/"))
        tasks = [t for i, t in enumerate(tasks) if i % n == k]   # same split for every model (server balance)
    # the student alone: no teacher, no search, one attempt per cell; step-0 bookkeeping only
    ckpt = CheckpointPolicy(strategy="replay", every=0, before_types=False, before_keys=(), oracle_status=False)
    role = f"eval:{args.label}"

    async def run():
        try:
            return await run_attempts(store=proj.store(), world=world, backend=backend, tasks=tasks, role=role,
                                      policy_factory=policy.factory, ckpt=ckpt, experiment_id=args.experiment,
                                      concurrency=args.concurrency or proj.concurrency, replicate=args.replicate,
                                      infra_retries=proj.infra_retries, history_k=proj.history_k, budget=proj.budget,
                                      settle=proj.settle)
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
    p.add_argument("--split", default=None)
    p.add_argument("--seeds", default=None, help="e.g. 1-20,25")
    p.add_argument("--pool", default=None, help="train|val|dev (forkloop/splits.py) instead of --split/--seeds")
    p.add_argument("--per-family", type=int, default=10)
    p.add_argument("--skip", type=int, default=0, help="skip the first K pool tasks per family (disjoint slices)")
    p.add_argument("--no-checkpoints", action="store_true", help="only the step-0 checkpoint (e.g. teacher demonstrations)")
    p.add_argument("--set", action="append", default=None, metavar="KEY=VALUE",
                   help="override a student option (e.g. model=<served LoRA name>); recorded in the policy identity")
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
    p.add_argument("--order", choices=["seed", "budget"], default="seed",
                   help="budget: launch in the matched-cost selection order (families round-robin)")
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
    p.add_argument("--split", default=None)
    p.add_argument("--seeds", default=None)
    p.add_argument("--pool", default=None, help="train|val|dev|final_test (final_test needs --final)")
    p.add_argument("--per-family", type=int, default=10)
    p.add_argument("--skip", type=int, default=0)
    p.add_argument("--experiment", required=True)
    p.add_argument("--label", default="student")
    p.add_argument("--replicate", type=int, default=1)
    p.add_argument("--final", action="store_true", help="allow the final test pool (pre-registered evaluation only)")
    p.add_argument("--shard", default=None, metavar="K/N", help="only tasks whose list position %% N == K")
    p.add_argument("--set", action="append", default=None, metavar="KEY=VALUE",
                   help="override a student option for this run (e.g. model=<served LoRA name>); recorded in the policy identity")
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
                # Solari answered "Not found" for existing snapshots nondeterministically (2026-09-29,
                # docs/solari-platform-notes.md); a retry usually reaches it. Only a successful delete
                # marks the checkpoint deleted; otherwise the registry lease/reaper keeps trying.
                for tries in range(6):
                    try:
                        await backend.delete_snapshot(c["world_ref"])
                        store.mark_checkpoint_deleted(c["ckpt_id"])
                        store.event("snapshot_deleted", c["ckpt_id"], snapshot=c["world_ref"], tries=tries + 1)
                        action = "deleted"
                        break
                    except Exception as e:  # noqa: BLE001
                        action = f"delete failed: {type(e).__name__}: {str(e)[:200]}"
                        await asyncio.sleep(2 + 2 * tries)
            out.append({"ckpt_id": c["ckpt_id"], "snapshot": c["world_ref"], "action": action})
            log(f"[cleanup] {c['ckpt_id']} {c['world_ref']}: {action}")
    if not dry_run and any(r["action"] == "deleted" for r in out):
        # A delete that answered success can leave the snapshot listed (Solari, 2026-09-29): verify
        # against the provider listing and delete again until it is gone.
        for rnd in range(4):
            try:
                listed = {s.id for s in await backend.list_snapshots()}
            except Exception as e:  # noqa: BLE001
                log(f"[cleanup] cannot list snapshots to verify: {type(e).__name__}: {e}")
                break
            left = [r for r in out if r["action"] == "deleted" and r.get("snapshot") in listed]
            if not left:
                break
            for r in left:
                try:
                    await backend.delete_snapshot(r["snapshot"])
                except Exception as e:  # noqa: BLE001
                    log(f"[cleanup] re-delete {r['snapshot']}: {type(e).__name__}")
                r["verify_rounds"] = rnd + 1
            await asyncio.sleep(3)
        else:
            for r in out:
                if r["action"] == "deleted" and r.get("snapshot") in listed:
                    r["action"] = "still listed after verification"
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


def cmd_reap_machines(args: argparse.Namespace) -> int:
    from .project import load_project
    from .runner import reap_orphan_machines

    proj = load_project(args.config, require_env=False)
    world = proj.world()
    backend = proj.backend(world)

    async def run():
        try:
            return await reap_orphan_machines(proj.store(), backend, dry_run=args.dry_run)
        finally:
            await backend.close()

    print(json.dumps({"killed": asyncio.run(run())}, indent=2))
    return 0


def cmd_demo_loop(args: argparse.Namespace) -> int:
    """The whole loop offline on the toy world: record -> failures -> repair -> dataset -> evidence."""
    import os
    import shutil

    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit(f"{out} exists; choose a new directory")
    out.mkdir(parents=True)
    here = Path(__file__).resolve().parents[2]
    shutil.copy(here / "examples" / "loop_agents.py", out / "loop_agents.py")
    (out / "project.yaml").write_text(
        "# OFFLINE SIMULATION: toy world on the in-process fake backend (no browser, no model).\n"
        "version: 1\nworld: toy-counter\nbackend: fake\nstore: store/forkloop.sqlite\nhistory_k: 50\nsettle: fixed\n"
        "checkpoints: {strategy: replay, every: 1}\n"
        "student: {name: toy student, factory: 'loop_agents:student', revision: examples/loop_agents.py}\n"
        "teacher: {name: toy teacher, factory: 'loop_agents:teacher', revision: examples/loop_agents.py}\n"
        "repair: {k: 2, max_restart_points: 2, concurrency: 2, settle: fixed}\nconcurrency: 2\n")
    os.environ.setdefault("FORKLOOP_POOL_LOG", "0")
    cwd = os.getcwd()
    os.chdir(out)
    try:
        from ..cli import main as cli_main
        cfg = ["--config", "project.yaml"]
        steps = [["record", *cfg, "--families", "reach_target", "--split", "train", "--seeds", "1-4", "--experiment", "demo"],
                 ["failures", *cfg, "--experiment", "demo"],
                 ["repair", *cfg, "--experiment", "demo"],
                 ["dataset", *cfg, "--out", "dataset", "--experiment", "demo", "--name", "toy corrections"]]
        for argv in steps:
            print(f"\n$ forkloop {' '.join(argv)}")
            rc = cli_main(argv)
            if rc:
                return rc
        from .evidence import write_evidence
        from .project import load_project
        store = load_project("project.yaml", require_env=False).store()
        path = write_evidence({"toy": store}, Path("evidence"), title="Toy loop evidence (offline simulation)",
                              datasets=[Path("dataset")],
                              intro="<p class='note'>OFFLINE SIMULATION on the toy-counter world and the in-process fake "
                                    "backend: synthetic screens, scripted agents. It shows the mechanics of the loop, not "
                                    "model or application evidence.</p>")
        print(f"\nEvidence: {out / path.relative_to(out) if path.is_absolute() else out / path}")
    finally:
        os.chdir(cwd)
    return 0


def cmd_evidence(args: argparse.Namespace) -> int:
    from .evidence import write_evidence
    from .project import load_project

    stores = {}
    for c in args.config:
        proj = load_project(c, require_env=False)
        stores[Path(c).stem] = proj.store()
    path = write_evidence(stores, Path(args.out), title=args.title, example_store=args.example_store,
                          example_repair=args.repair, datasets=[Path(d) for d in (args.dataset or [])])
    print(path)
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    """Matched-collection-cost datasets per arm (docs/protocol-learning-experiment.md)."""
    from .budget import Rates, demo_units, repair_units, select, summarize
    from .dataset import export_dataset
    from .project import load_project

    proj = load_project(args.config, require_env=False)
    store, world = proj.store(), proj.world()
    rates = Rates(args.world_usd_per_hour, args.student_usd_per_step, note=args.rates_note)
    cu = {"count_unscored": args.count_unscored_cost}
    arms = {"A1": demo_units(store, args.demo_experiment, **cu),
            "A2": repair_units(store, args.attempt_experiment, args.checkpoint_experiment, "checkpoint", "A2",
                               infra_retries=proj.infra_retries, **cu),
            "A3": repair_units(store, args.attempt_experiment, args.restart_experiment, "full_restart", "A3",
                               infra_retries=proj.infra_retries, **cu)}
    totals = {k: summarize(v, rates) for k, v in arms.items()}
    settled = [t["cost_usd"] for t in totals.values() if not t["pending"]]
    if not settled:
        raise SystemExit(f"every arm has pending units: {json.dumps(totals)}")
    budget = min(settled)
    short = {k: t for k, t in totals.items() if t["pending"] and t["cost_usd"] < budget}
    if short:
        raise SystemExit(f"budget undetermined: arms with pending units below B=${budget:.2f}: {json.dumps(short)}")
    if args.dry_run:  # selection only (raises PendingUnit if a selected failure is unsettled); exports nothing
        for frac in [float(x) for x in args.fractions.split(",")]:
            for arm, units in arms.items():
                chosen, spent = select(units, budget * frac, rates)
                print(f"{arm}-b{int(round(frac * 100)):03d}: units={len(chosen)} spent=${spent:.2f} "
                      f"verified_paths={sum(len(u.verified_sources) for u in chosen)}")
        print(json.dumps({"budget_usd": budget, "totals": totals}, indent=2))
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"rates": rates.to_dict(), "totals": totals, "budget_usd": budget, "arms": {},
                              "accounting": "count_unscored" if args.count_unscored_cost else "counted_repairs"}
    for frac in [float(x) for x in args.fractions.split(",")]:
        for arm, units in arms.items():
            chosen, spent = select(units, budget * frac, rates)
            tag = f"{arm}-b{int(round(frac * 100)):03d}"
            srcs = [x for u in chosen for x in u.verified_sources]
            if arm == "A1":
                m = export_dataset(store, world, out / tag, include_corrections=False, attempt_ids=srcs, name=f"exp1 {tag}")
            else:
                reps = [u.repair_id for u in chosen if u.repair_id]
                m = export_dataset(store, world, out / tag, include_demos=False, repair_ids=reps, name=f"exp1 {tag}")
            report["arms"][tag] = {**summarize(chosen, rates), "spent_usd": round(spent, 4), "dataset_id": m["dataset_id"],
                                   "records": m["counts"]["records"], "by_origin": m["counts"]["by_origin"],
                                   "selected": [u.to_dict(rates) for u in chosen]}
            print(f"{tag}: units={len(chosen)} spent=${spent:.2f} verified_paths={len(srcs)} records={m['counts']['records']}")
    (out / "budget-report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({"budget_usd": budget, "totals": totals}, indent=2))
    return 0


def add_cleanup_command(sub: Any) -> None:
    p = sub.add_parser("budget", help="matched-collection-cost datasets for the demonstration, Forkloop and restart arms")
    p.add_argument("--config", required=True)
    p.add_argument("--demo-experiment", required=True)
    p.add_argument("--attempt-experiment", required=True)
    p.add_argument("--checkpoint-experiment", required=True)
    p.add_argument("--restart-experiment", required=True)
    p.add_argument("--world-usd-per-hour", type=float, default=0.35)
    p.add_argument("--student-usd-per-step", type=float, default=0.001)
    p.add_argument("--rates-note", default="main box $22.32/h / 64 worlds; 7 A100 replicas ≈ $19.53/h at ≈ 6 steps/s")
    p.add_argument("--fractions", default="0.25,0.5,1.0")
    p.add_argument("--dry-run", action="store_true", help="selection only; fails while a selected failure is unsettled")
    p.add_argument("--count-unscored-cost", action="store_true",
                   help="charge unscored attempts and the latest repair whatever its branches (earlier accounting)")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_budget)
    p = sub.add_parser("evidence", help="shareable evidence bundle: one repaired failure end to end, datasets, lineage")
    p.add_argument("--config", action="append", required=True, help="project YAML (repeatable)")
    p.add_argument("--out", required=True)
    p.add_argument("--title", default="Forkloop evidence")
    p.add_argument("--dataset", action="append", default=None)
    p.add_argument("--repair", default=None, help="repair id to feature (default: the most informative verified one)")
    p.add_argument("--example-store", default=None, help="config stem whose store holds the featured repair")
    p.set_defaults(fn=cmd_evidence)
    p = sub.add_parser("demo-loop", help="the whole correction loop offline on the toy world (no account, no model)")
    p.add_argument("--out", default="runs/demo-loop")
    p.set_defaults(fn=cmd_demo_loop)
    p = sub.add_parser("reap-machines", help="kill machines whose runner (on this store) stopped heartbeating")
    p.add_argument("--config", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_reap_machines)
    p = sub.add_parser("cleanup", help="delete provider snapshots behind checkpoints no repair still needs")
    p.add_argument("--config", required=True)
    p.add_argument("--experiment", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_cleanup)
