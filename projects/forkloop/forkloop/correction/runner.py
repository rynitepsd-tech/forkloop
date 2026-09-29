"""Resumable batch execution of attempts and repairs.

A *cell* is one planned unit of evidence: ``<task_id>/<role>/r<replicate>`` for an attempt.
Before anything runs, the store is consulted:

* a cell with a ``finished`` attempt is skipped (never re-run to fish for a better outcome);
* a cell whose attempts all ended ``infra_error`` / ``interrupted`` / ``restore_failed`` gets a
  new attempt number, up to ``1 + infra_retries`` attempts in total (the predeclared
  replacement rule); earlier attempts stay in the store and in every report;
* rows left ``running`` by a dead process are marked ``interrupted`` first.

Every attempt runs on a fresh machine (``fork`` pool of size 1), so cells never share state.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from ..env import Env
from ..pool import WorkerPool
from .checkpoint import CheckpointPolicy
from .record import AttemptResult, record_attempt
from .repair import RepairConfig, repair_attempt
from .store import FINISHED, Store, stable_id

RETRYABLE = ("infra_error", "interrupted", "restore_failed")


async def make_policy(factory: Callable[[], Any]) -> Any:
    p = factory()
    return await p if inspect.isawaitable(p) else p


@dataclass
class CellPlan:
    cell: str
    task: Any
    attempt_no: int
    skip_reason: Optional[str] = None


def plan_cells(store: Store, tasks: Iterable[Any], *, role: str, experiment_id: str, replicate: int = 1,
               infra_retries: int = 2) -> list[CellPlan]:
    plans = []
    for task in tasks:
        cell = f"{task.task_id}/{role}/r{replicate}"
        prior = store.attempts(experiment_id=experiment_id, cell=cell)
        if any(a["status"] == FINISHED for a in prior):
            plans.append(CellPlan(cell, task, 0, "finished"))
        elif any(a["status"] == "running" for a in prior):
            # rows of dead runners were just marked interrupted; a running row belongs to a live runner
            plans.append(CellPlan(cell, task, 0, "running in another live runner"))
        elif len(prior) >= 1 + infra_retries:
            plans.append(CellPlan(cell, task, 0, f"exhausted after {len(prior)} unscored attempts"))
        else:
            plans.append(CellPlan(cell, task, len(prior) + 1))
    return plans


async def run_attempts(*, store: Store, world: Any, backend: Any, tasks: Iterable[Any], role: str,
                       policy_factory: Callable[[], Any], ckpt: CheckpointPolicy, experiment_id: str,
                       concurrency: int = 2, replicate: int = 1, infra_retries: int = 2, history_k: int = 8,
                       budget: Optional[dict] = None, log: Callable[[str], None] = print,
                       settle: str = "stable") -> list[AttemptResult]:
    reap_dead_runners(store, log=log)
    plans = plan_cells(store, tasks, role=role, experiment_id=experiment_id, replicate=replicate,
                       infra_retries=infra_retries)
    todo = [p for p in plans if not p.skip_reason]
    log(f"[runner] {experiment_id} {role}: {len(todo)} to run, {len(plans) - len(todo)} skipped")
    sem = asyncio.Semaphore(max(1, concurrency))
    results: list[AttemptResult] = []

    async def one(p: CellPlan) -> None:
        async with sem:
            env = Env(world, backend, family=p.task.family, split=p.task.split, history_k=history_k,
                      pool=WorkerPool(backend, world, size=1, mode="fork", run_id=stable_id("run", p.cell, p.attempt_no),
                                      reap_orphans_enabled=False, metadata={"runner": RUNNER_ID}), budget_override=budget,
                      stable_after_action=(settle == "stable"))
            env._own_pool = True  # the cell's pool (and its machine) dies with the cell
            policy = await make_policy(policy_factory)
            t0 = time.monotonic()
            try:
                attempt_id = stable_id("att", experiment_id, p.cell, p.attempt_no)
                r = await record_attempt(env, policy, p.task, store=store, role=role, ckpt=ckpt, attempt_id=attempt_id,
                                         experiment_id=experiment_id, cell=p.cell, attempt_no=p.attempt_no)
                results.append(r)
                log(f"[runner] {p.cell} #{p.attempt_no}: {r.status} reward={r.reward} {r.reason_code} "
                    f"steps={r.n_steps} {time.monotonic() - t0:.0f}s")
            finally:
                try:
                    await env.close()
                except Exception as e:  # noqa: BLE001
                    log(f"[runner] cleanup failed for {p.cell}: {type(e).__name__}: {e}")
                close = getattr(policy, "aclose", None)
                if callable(close):
                    try:
                        await close()
                    except Exception:  # noqa: BLE001
                        pass

    hb = asyncio.create_task(heartbeat_loop(store))
    try:
        await asyncio.gather(*(one(p) for p in todo))
    finally:
        hb.cancel()
    return results


RUNNER_ID = f"{__import__('socket').gethostname()}-{__import__('os').getpid()}-{int(time.time())}"
HEARTBEAT_S = 30.0
STALE_S = 180.0


def heartbeat(store: Store) -> None:
    d = store.root / "runners"
    d.mkdir(exist_ok=True)
    (d / f"{RUNNER_ID}.hb").write_text(str(time.time()))


async def heartbeat_loop(store: Store) -> None:
    while True:
        heartbeat(store)
        await asyncio.sleep(HEARTBEAT_S)


def live_runner_ids(root) -> set[str]:
    from pathlib import Path
    alive = set()
    for hb in (Path(root) / "runners").glob("*.hb"):
        try:
            if time.time() - float(hb.read_text()) < STALE_S:
                alive.add(hb.stem)
        except (OSError, ValueError):
            pass
    return alive


async def reap_orphan_machines(store: Store, backend: Any, *, log: Callable[[str], None] = print,
                               dry_run: bool = False) -> list[str]:
    """Kill machines tagged with a runner of this store whose heartbeat stopped (a killed runner's
    worlds). Machines without a runner tag, or whose runner is alive, are never touched."""
    alive = live_runner_ids(store.root)
    known = {hb.stem for hb in (store.root / "runners").glob("*.hb")}
    killed = []
    for m in await backend.list_machines(metadata={"forkloop": "1"}):
        owner = (m.metadata or {}).get("runner")
        if owner and owner in known and owner not in alive and m.state in ("running", "starting", "paused", "created"):
            if not dry_run:
                await backend.kill_machine(m.id)
            killed.append(m.id)
    if killed:
        log(f"[reaper] {'would kill' if dry_run else 'killed'} {len(killed)} machines of dead runners")
    return killed


def reap_dead_runners(store: Store, *, log: Callable[[str], None] = print) -> list[str]:
    """Mark ``running`` rows as ``interrupted`` only when the runner that owns them stopped heartbeating
    (several runners may share one store, e.g. student and teacher collection in parallel)."""
    heartbeat(store)
    alive = set()
    for hb in (store.root / "runners").glob("*.hb"):
        try:
            if time.time() - float(hb.read_text()) < STALE_S:
                alive.add(hb.stem)
        except (OSError, ValueError):
            pass
    ids = store.mark_interrupted(alive_runners=alive)
    if ids:
        log(f"[runner] marked {len(ids)} rows interrupted (their runner stopped): {ids[:5]}")
    return ids


async def run_repairs(*, store: Store, world: Any, backend: Any, attempt_ids: Iterable[str],
                      teacher_factory: Callable[[], Any], cfg: RepairConfig, experiment_id: str,
                      concurrency: int = 2, log: Callable[[str], None] = print) -> list[Any]:
    sem = asyncio.Semaphore(max(1, concurrency))
    out = []

    def sync_factory():
        p = teacher_factory()
        if inspect.isawaitable(p):
            raise TypeError("repair teachers must be constructed synchronously")
        return p

    async def one(aid: str) -> None:
        prior = [r for r in store.repairs(attempt_id=aid, experiment_id=experiment_id) if r["mode"] == cfg.mode]
        if any(r["status"] in ("verified", "unrepaired") for r in prior):
            log(f"[runner] repair {aid} {cfg.mode}: done before, skipped")
            return
        try:
            r = await repair_attempt(store, world, backend, aid, teacher_factory=sync_factory, cfg=cfg,
                                     experiment_id=experiment_id, repair_no=len(prior) + 1, sem=sem)
            out.append(r)
            log(f"[runner] repair {aid} {cfg.mode}: {r.status} verified={len(r.verified_branches)} "
                f"branches={len(r.branches)} points={[p['step'] for p in r.restart_points]}")
        except Exception as e:  # noqa: BLE001 - one repair's failure must not stop the batch
            log(f"[runner] repair {aid} failed: {type(e).__name__}: {str(e)[:300]}")

    reap_dead_runners(store, log=log)
    hb = asyncio.create_task(heartbeat_loop(store))
    try:
        await asyncio.gather(*(one(a) for a in attempt_ids))
    finally:
        hb.cancel()
    return out


__all__ = ["run_attempts", "run_repairs", "plan_cells", "make_policy", "RETRYABLE"]
