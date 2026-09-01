"""Fork-based best-of-N at uncertain steps — the Rewind mechanic as a data engine.

Two modes:

* ``revert`` (width 1, any plan): at a branch point take a snapshot, try each
  candidate action in turn by reverting the same machine, roll each branch out
  to the end, verify, keep the best.
* ``fork`` (needs ≥ 2 concurrent machines): create ``create(from_snapshot=cp)``
  workers and roll the candidates out in parallel.

The main trajectory adopts the winning branch's steps; every branch is kept on
disk under ``branches/`` with its own verdict for analysis.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .actions import Action
from .env import Env, EnvCheckpoint
from .oracle import Verdict
from .policies.base import Policy, propose_or_repeat
from .types import Observation


@dataclass
class BranchResult:
    label: str
    first_action: str
    verdict: Verdict
    steps: int


@dataclass
class SearchStats:
    branch_points: int = 0
    branches: int = 0
    wins: int = 0
    snapshots: int = 0
    reverts: int = 0
    forks: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _score(v: Verdict) -> tuple[float, float]:
    return (v.reward, v.milestones)


async def _rollout(env: Env, policy: Policy, obs: Observation, *, first: Optional[tuple[Optional[Action], dict[str, Any]]] = None,
                   search_tag: Optional[dict[str, Any]] = None) -> Verdict:
    """Play the current episode to the end from ``obs`` (greedy after the first action)."""
    pending = first
    while True:
        if pending is not None:
            action, meta = pending
            pending = None
        else:
            t0 = time.monotonic()
            action, meta = await policy.act(obs)
            meta = dict(meta or {})
            meta.setdefault("model_latency_s", time.monotonic() - t0)
        meta = dict(meta or {})
        if search_tag:
            meta["search"] = {**search_tag, **(meta.get("search") or {})}
        obs, reward, term, trunc, info = await env.step(action, meta=meta)
        if term or trunc:
            return await env.verify()


async def best_of_n(env: Env, policy: Policy, n: int, seed: int, *, family: Optional[str] = None,
                    branch_prob: float = 0.2, confidence_threshold: float = 0.5, max_branch_points: int = 3,
                    mode: str = "revert", rng: Optional[random.Random] = None,
                    stats: Optional[SearchStats] = None) -> Verdict:
    """Run one episode with best-of-``n`` branching at uncertain steps.

    A step is a branch point when the policy reports ``confidence`` below the
    threshold, or with probability ``branch_prob`` otherwise, up to
    ``max_branch_points`` per episode. Returns the final verdict of the
    trajectory the env's recorder ends up holding (the winner).
    """
    if n < 2:
        from .env import run_episode

        return await run_episode(env, policy, seed, family=family)
    if mode not in ("revert", "fork"):
        raise ValueError("mode must be revert or fork")
    rng = rng or random.Random(seed)
    stats = stats if stats is not None else SearchStats()
    obs, info = await env.reset(seed, family=family)
    branch_points = 0
    while True:
        t0 = time.monotonic()
        action, meta = await policy.act(obs)
        meta = dict(meta or {})
        meta.setdefault("model_latency_s", time.monotonic() - t0)
        conf = meta.get("confidence")
        uncertain = (conf is not None and float(conf) < confidence_threshold) or (conf is None and rng.random() < branch_prob)
        terminal = isinstance(action, Action) and action.is_terminal
        if uncertain and not terminal and branch_points < max_branch_points:
            branch_points += 1
            stats.branch_points += 1
            cp = await env.checkpoint()
            stats.snapshots += 1
            candidates = [(action, meta)] + await propose_or_repeat(policy, obs, n - 1)
            candidates = _dedupe(candidates)
            if len(candidates) < 2:
                obs, reward, term, trunc, info = await env.step(action, meta=meta)
                if term or trunc:
                    return await env.verify()
                continue
            if mode == "fork":
                results = await _run_branches_fork(env, policy, cp, candidates, obs, stats)
            else:
                results = await _run_branches_revert(env, policy, cp, candidates, obs, stats)
            best = max(results, key=lambda r: _score(r[1].verdict))
            best_idx, best_res, best_rec = best
            stats.wins += 1 if best_res.verdict.reward >= 1.0 else 0
            stats.results.append({"step": cp.step, "candidates": [r[1].first_action for r in results],
                                  "rewards": [r[1].verdict.reward for r in results], "chosen": best_idx})
            main_rec = env.ep.recorder if env.ep else None
            if main_rec is not None and best_rec is not None:
                main_rec.adopt(best_rec, from_step=cp.step)
            # Leave the machine in the winning branch's final state and finish.
            if mode == "revert":
                # the last rollout may not be the winner; replay the winner's end state by reverting to its end snapshot
                end_sid = best_res_end_snapshot(results, best_idx)
                if end_sid and env.ep is not None:
                    await env.ep.machine.revert(end_sid)
                    stats.reverts += 1
            env.ep.terminated = True  # type: ignore[union-attr]
            env.ep.end_reason = "search_done"  # type: ignore[union-attr]
            env.ep.verdict = best_res.verdict  # type: ignore[union-attr]
            env.ep.step = cp.step + best_res.steps  # type: ignore[union-attr]
            if main_rec is not None:
                main_rec.finish(best_res.verdict, extra={"end_reason": "search_done", "search": stats.to_dict()})
            return best_res.verdict
        obs, reward, term, trunc, info = await env.step(action, meta=meta)
        if term or trunc:
            return await env.verify()


def _dedupe(cands: list[tuple[Optional[Action], dict[str, Any]]]) -> list[tuple[Optional[Action], dict[str, Any]]]:
    seen: set[str] = set()
    out = []
    for a, m in cands:
        key = a.to_compact() if isinstance(a, Action) else f"invalid:{m.get('raw_action')}"
        if key in seen:
            continue
        seen.add(key)
        out.append((a, m))
    return out


_END_SNAPSHOTS: dict[int, dict[int, str]] = {}


def best_res_end_snapshot(results: list[tuple[int, BranchResult, Any]], idx: int) -> Optional[str]:
    for i, res, rec in results:
        if i == idx:
            return getattr(res, "_end_snapshot", None)
    return None


async def _run_branches_revert(env: Env, policy: Policy, cp: EnvCheckpoint, candidates: list, obs: Observation,
                               stats: SearchStats) -> list[tuple[int, BranchResult, Any]]:
    results = []
    main_rec = env.ep.recorder if env.ep else None
    for i, (a, m) in enumerate(candidates):
        obs_i = await env.restore(cp)
        stats.reverts += 1
        child = main_rec.fork(f"s{cp.step:03d}_b{i}") if main_rec else None
        env.swap_recorder(child)
        meta = dict(m)
        v = await _rollout(env, policy, obs_i, first=(a, meta), search_tag={"branch": i, "of": len(candidates), "step": cp.step})
        stats.branches += 1
        env.swap_recorder(main_rec)
        steps = (env.ep.step - cp.step) if env.ep else 0
        res = BranchResult(label=f"b{i}", first_action=a.to_compact() if isinstance(a, Action) else str(m.get("raw_action")),
                           verdict=v, steps=steps)
        # remember the end state so the winner can be restored after the loop
        try:
            res._end_snapshot = await env.ep.machine.snapshot(f"end-{cp.step}-{i}")  # type: ignore[attr-defined,union-attr]
            stats.snapshots += 1
        except Exception:  # noqa: BLE001
            res._end_snapshot = None  # type: ignore[attr-defined]
        results.append((i, res, child))
    return results


async def _run_branches_fork(env: Env, policy: Policy, cp: EnvCheckpoint, candidates: list, obs: Observation,
                             stats: SearchStats) -> list[tuple[int, BranchResult, Any]]:
    """Run candidates on forked machines in parallel (bounded by the backend cap)."""
    from .env import Env as _Env
    from .pool import WorkerPool

    main_rec = env.ep.recorder if env.ep else None
    cap = max(1, env.backend.concurrency_cap - 1)  # the main machine holds one slot
    sem = asyncio.Semaphore(cap)
    results: list[tuple[int, BranchResult, Any]] = []

    async def one(i: int, a: Optional[Action], m: dict[str, Any]) -> None:
        async with sem:
            pool = WorkerPool(env.backend, env.world, size=1, mode="fork", golden_snapshot=cp.snapshot_id,
                              run_id=env.pool.run_id)
            sub = _Env(env.world, env.backend, family=env.family, split=env.split, pool=pool, recorder=None,
                       history_k=env.history_k, settle_s=env.settle_s, reset_controller=env.resetter)
            # A fork already contains the seeded, post-checkpoint state: skip seeding/health by attaching directly.
            worker = await pool.acquire()
            machine = await worker.restore()
            stats.forks += 1
            from .env import EpisodeState

            sub.ep = EpisodeState(task=env.ep.task, worker=worker, machine=machine, dbs=env.world.databases(machine),
                                  baseline=env.ep.baseline, step=cp.step, invalid=cp.invalid,
                                  history=list(cp.history), started_at=cp.started_at,
                                  last_shot=cp.screenshot or await machine.screenshot(),
                                  recorder=main_rec.fork(f"s{cp.step:03d}_b{i}") if main_rec else None)
            try:
                v = await _rollout(sub, policy, sub._obs(), first=(a, dict(m)),
                                   search_tag={"branch": i, "of": len(candidates), "step": cp.step})
                stats.branches += 1
                res = BranchResult(label=f"b{i}", first_action=a.to_compact() if isinstance(a, Action) else str(m.get("raw_action")),
                                   verdict=v, steps=sub.ep.step - cp.step)
                res._end_snapshot = None  # type: ignore[attr-defined]
                results.append((i, res, sub.ep.recorder))
            finally:
                await sub.close()

    await asyncio.gather(*(one(i, a, m) for i, (a, m) in enumerate(candidates)))
    results.sort(key=lambda r: r[0])
    return results


__all__ = ["best_of_n", "SearchStats", "BranchResult"]
