"""Bounded best-of-N with explicit decision-state isolation and terminal adoption.

Trajectory budgets rewind to the checkpoint; total calls and resource lifetimes
never rewind. All branches are recorded, including losing and failed attempts.
"""
from __future__ import annotations

import asyncio
import copy
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .actions import Action
from .env import Env, EnvCheckpoint, EpisodeState, act_with_deadline
from .oracle import Verdict
from .policies.base import Policy, require_branchable
from .types import Observation


@dataclass
class BranchResult:
    label: str
    first_action: str
    verdict: Verdict
    steps: int
    policy_state: dict = field(default_factory=dict)
    env_state: Optional[EnvCheckpoint] = None
    _end_snapshot: Optional[str] = None


@dataclass
class SearchStats:
    branch_points: int = 0
    branches: int = 0
    wins: int = 0
    snapshots: int = 0
    reverts: int = 0
    forks: int = 0
    snapshots_deleted: int = 0
    snapshot_delete_errors: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    experiment_wall_s: float = 0.0
    experiment_tokens: dict = field(default_factory=dict)
    branch_resource_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.__dict__)


def _score(v: Verdict) -> tuple[float, float]:
    return v.reward, v.milestones


async def _rollout(env: Env, policy: Policy, obs: Observation, *, first=None, search_tag=None) -> Verdict:
    pending = first
    while True:
        if pending is not None:
            action, meta = pending
            pending = None
        else:
            t0 = time.monotonic()
            action, meta = await act_with_deadline(env, policy, obs)
            meta = dict(meta or {})
            meta.setdefault("model_latency_s", time.monotonic() - t0)
        meta = {k: v for k, v in (meta or {}).items() if k != "_policy_state"}
        if search_tag:
            meta["search"] = {**search_tag, **(meta.get("search") or {})}
        obs, _, term, trunc, _ = await env.step(action, meta=meta)
        if term or trunc:
            return await env.verify()


def _dedupe(candidates):
    seen, out = set(), []
    for action, meta in candidates:
        key = action.to_compact() if isinstance(action, Action) else f"invalid:{meta.get('raw_action')}"
        if key not in seen:
            seen.add(key)
            out.append((action, meta))
    return out


async def _delete_snapshots(env: Env, ids: list, stats: SearchStats) -> None:
    for sid in dict.fromkeys(ids):
        if not sid:
            continue
        try:
            await env.backend.delete_snapshot(sid)
            stats.snapshots_deleted += 1
        except Exception as e:
            stats.snapshot_delete_errors.append(f"{sid}: {type(e).__name__}: {str(e)[:200]}")


async def _candidates(policy, before_state, obs, n):
    """Generate alternatives from the state BEFORE the initial act, never after it.

    A proposing policy must attach each candidate's post-choice state. A policy
    without propose is sampled independently from its explicit checkpoint.
    """
    clone = policy.clone_for_branch(before_state)
    if callable(getattr(clone, "propose", None)):
        choices = await clone.propose(obs, n)
        for _, meta in choices:
            if "_policy_state" not in meta:
                raise TypeError("propose must return each candidate's _policy_state")
        return choices
    choices = []
    for _ in range(n):
        child = policy.clone_for_branch(before_state)
        action, meta = await child.act(obs)
        choices.append((action, {**(meta or {}), "_policy_state": child.snapshot_state()}))
    return choices


async def best_of_n(env: Env, policy: Policy, n: int, seed: int, *, family: Optional[str] = None,
                    branch_prob: float = 0.2, confidence_threshold: float = 0.5, max_branch_points: int = 3,
                    mode: str = "revert", rng: Optional[random.Random] = None,
                    stats: Optional[SearchStats] = None) -> Verdict:
    if n < 2:
        from .env import run_episode
        return await run_episode(env, policy, seed, family=family)
    if mode not in ("revert", "fork"):
        raise ValueError("mode must be revert or fork")
    require_branchable(policy)
    if mode == "fork" and env.backend.concurrency_cap < 2:
        raise ValueError("fork search needs a slot for the parent and at least one branch")
    rng, stats = rng or random.Random(seed), stats if stats is not None else SearchStats()
    snapshots = []
    start = time.monotonic()
    main_rec = None
    try:
        obs, _ = await env.reset(seed, family=family)
        if callable(getattr(policy, "reset", None)):
            policy.reset()
        main_rec = env.ep.recorder
        while True:
            before_state = policy.snapshot_state()
            action, meta = await act_with_deadline(env, policy, obs)
            meta = dict(meta or {})
            after_state = policy.snapshot_state()
            conf = meta.get("confidence")
            uncertain = (float(conf) < confidence_threshold) if conf is not None else rng.random() < branch_prob
            if uncertain and not (isinstance(action, Action) and action.is_terminal) and stats.branch_points < max_branch_points:
                stats.branch_points += 1
                cp = await env.checkpoint()
                snapshots.append(cp.snapshot_id)
                stats.snapshots += 1
                try:
                    alternatives = await asyncio.wait_for(
                        _candidates(policy, before_state, obs, n - 1),
                        timeout=max(0.0, env.remaining_seconds()))
                except asyncio.TimeoutError:
                    alternatives = []  # env.step verifies the elapsed trajectory deadline
                candidates = _dedupe([(action, {**meta, "_policy_state": after_state})] + alternatives)
                if len(candidates) >= 2:
                    runner = _run_branches_fork if mode == "fork" else _run_branches_revert
                    results = await runner(env, policy, cp, candidates, stats, snapshots)
                    best_idx, best, child_rec = max(results, key=lambda r: _score(r[1].verdict))
                    stats.wins += int(best.verdict.reward >= 1)
                    stats.results.append({"step": cp.step, "candidates": [r[1].first_action for r in results],
                                          "rewards": [r[1].verdict.reward for r in results], "chosen": best_idx})
                    if mode == "revert":
                        if best.env_state is None or not best._end_snapshot:
                            raise RuntimeError("winning branch has no restorable end state")
                        await env.restore(best.env_state)
                        stats.reverts += 1
                    else:
                        # The root machine remains at the fork point. This is a terminal
                        # recorded result; no further actions on that machine are permitted.
                        env.ep.step = cp.step + best.steps
                    policy.restore_state(best.policy_state)
                    env.ep.terminated, env.ep.truncated = True, False
                    env.ep.end_reason, env.ep.verdict = "search_done", best.verdict
                    if main_rec is not None:
                        main_rec.adopt(child_rec, from_step=cp.step)
                        main_rec.finish(best.verdict, extra={"end_reason": "search_done", "search": stats.to_dict()})
                    return best.verdict
                policy.restore_state(after_state)
            obs, _, term, trunc, _ = await env.step(action, meta=meta)
            if term or trunc:
                return await env.verify()
    finally:
        if env.ep is not None:
            env.swap_recorder(main_rec)
        await _delete_snapshots(env, snapshots, stats)
        stats.experiment_wall_s = time.monotonic() - start
        stats.experiment_tokens = dict(getattr(policy, "usage", {}) or {})
        if main_rec is not None:
            # Independent of the adopted/winning trajectory, also written on exceptions.
            (main_rec.dir / "accounting.json").write_text(json.dumps(stats.to_dict(), indent=2))


async def _run_branches_revert(env, policy, cp, candidates, stats, snapshots):
    results, main_rec = [], env.ep.recorder
    try:
        for i, (action, meta) in enumerate(candidates):
            obs = await env.restore(cp)
            stats.reverts += 1
            child = main_rec.fork(f"s{cp.step:03d}_b{i}") if main_rec else None
            env.swap_recorder(child)
            branch_policy = policy.clone_for_branch(meta["_policy_state"])
            verdict = await _rollout(env, branch_policy, obs, first=(action, meta), search_tag={"branch": i})
            stats.branches += 1
            end = await env.checkpoint()
            snapshots.append(end.snapshot_id)
            stats.snapshots += 1
            result = BranchResult(f"b{i}", action.to_compact() if isinstance(action, Action) else str(meta.get("raw_action")),
                                  verdict, env.ep.step - cp.step, branch_policy.snapshot_state(), end, end.snapshot_id)
            results.append((i, result, child))
    finally:
        env.swap_recorder(main_rec)
    return results


async def _run_branches_fork(env, policy, cp, candidates, stats, snapshots):
    from .pool import WorkerPool
    main_rec, parent = env.ep.recorder, env.ep
    sem = asyncio.Semaphore(max(1, env.backend.concurrency_cap - 1))

    async def one(i, action, meta):
        async with sem:
            t0 = time.monotonic()
            pool = WorkerPool(env.backend, env.world, size=1, mode="fork", golden_snapshot=cp.snapshot_id,
                              run_id=env.pool.run_id, reap_orphans_enabled=False)
            sub = Env(env.world, env.backend, family=env.family, split=env.split, pool=pool,
                      history_k=env.history_k, settle_s=env.settle_s, reset_controller=env.resetter,
                      stable_after_action=env.stable_after_action, max_invalid=env.max_invalid,
                      budget_override=copy.deepcopy(env.budget_override), record_extra=copy.deepcopy(env.record_extra))
            try:
                worker = await pool.acquire()
                machine = await worker.restore()
                stats.forks += 1
                sub.ep = EpisodeState(task=copy.deepcopy(parent.task), worker=worker, machine=machine,
                    dbs=env.world.databases(machine), baseline=copy.deepcopy(parent.baseline), step=cp.step,
                    budget_steps=cp.budget_steps, invalid=cp.invalid, history=list(cp.history),
                    started_at=time.monotonic() - cp.elapsed_s, last_shot=cp.screenshot,
                    previous_shot=cp.previous_screenshot,
                    recorder=main_rec.fork(f"s{cp.step:03d}_b{i}") if main_rec else None)
                branch_policy = policy.clone_for_branch(meta["_policy_state"])
                verdict = await _rollout(sub, branch_policy, sub._obs(), first=(action, meta), search_tag={"branch": i})
                stats.branches += 1
                result = BranchResult(f"b{i}", action.to_compact() if isinstance(action, Action) else str(meta.get("raw_action")),
                                      verdict, sub.ep.step - cp.step, branch_policy.snapshot_state())
                return i, result, sub.ep.recorder
            finally:
                try:
                    await sub.close()
                finally:
                    await pool.close()
                    stats.branch_resource_seconds += time.monotonic() - t0

    outcomes = await asyncio.gather(*(one(i, a, m) for i, (a, m) in enumerate(candidates)), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome  # siblings have finished cleanup before propagating the failure
    return sorted(outcomes, key=lambda r: r[0])


__all__ = ["best_of_n", "SearchStats", "BranchResult"]
