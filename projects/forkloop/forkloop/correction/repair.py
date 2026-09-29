"""Repair a failed attempt from its checkpoints with verified alternative continuations
(``forkloop repair``).

For each restart point (``diagnose.restart_points``, or step 0 only in ``full_restart`` mode),
``k`` branches run concurrently. Each branch restores the world *and* the policy side
independently: a fresh machine restored from the checkpoint, and a fresh teacher policy that
adopts only the checkpoint's declared agent state (explicit memory). The teacher sees exactly
the actor channel — instruction, screenshots, the action history and that memory — and no
verifier feedback (``feedback: none``). Every branch runs to its own termination and is scored
by the ordinary verifier on its own machine; failed, unscored and restore-failed branches are
kept. Charges are cumulative and never rewound.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..env import act_with_deadline
from ..tasks import TaskInstance
from ..trajectories import EpisodeRecorder
from .checkpoint import Blobs, policy_identity
from .diagnose import restart_points
from .record import charge_policy
from .restore import open_branch
from .store import FINISHED, INFRA_ERROR, RESTORE_FAILED, Store, stable_id

MODES = ("checkpoint", "full_restart")


@dataclass
class RepairConfig:
    mode: str = "checkpoint"
    k: int = 3                         # continuations per restart point
    max_restart_points: int = 2        # including step 0 as the last resort
    stop_on_success: bool = True       # later restart points only if no branch verified
    concurrency: int = 3
    branch_max_steps: Optional[int] = None   # depth budget after the checkpoint (None: task budget)
    budget_override: dict[str, Any] = field(default_factory=dict)
    feedback: str = "none"             # declared: the teacher gets no verifier feedback
    settle: str = "stable"
    max_screen_distance: float = 0.10
    restore_retries: int = 1
    history_k: int = 8                  # must match the attempt's env so the policy input is the same

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if self.feedback != "none":
            raise ValueError("only feedback='none' is implemented; hidden values are never exposed to the teacher")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepairResult:
    repair_id: str
    attempt_id: str
    mode: str
    verified_branches: list[str]
    branches: list[dict[str, Any]]
    restart_points: list[dict[str, Any]]
    status: str


def task_for(store: Store, task_id: str, world: Any) -> TaskInstance:
    """Regenerate a task from its id and check it is byte-identical to what was stored."""
    import hashlib
    rows = store._rows("SELECT * FROM tasks WHERE task_id=?", (task_id,))
    if not rows:
        raise KeyError(task_id)
    r = rows[0]
    task = world.generate(r["family"], int(r["seed"]), r["split"])
    sha = hashlib.sha256(json.dumps(task.to_dict(), sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
    if sha != r["manifest_sha256"] or task.task_id != task_id:
        raise ValueError(f"task {task_id} regenerated differently; the world version changed since recording")
    return task


async def _run_branch(*, store: Store, world: Any, backend: Any, task: TaskInstance, attempt: dict, ckpt: dict,
                      repair_id: str, idx: int, teacher_factory: Callable[[], Any], cfg: RepairConfig,
                      experiment_id: Optional[str]) -> dict[str, Any]:
    branch_id = stable_id("br", repair_id, ckpt["ckpt_id"], idx)
    run_dir = store.root / "branches" / branch_id
    store.start_branch(branch_id=branch_id, repair_id=repair_id, ckpt_id=ckpt["ckpt_id"], idx=idx, run_dir=str(run_dir))
    teacher = teacher_factory()
    budget = dict(cfg.budget_override)
    if cfg.branch_max_steps:
        budget["max_steps"] = int(ckpt["budget_steps"]) + int(cfg.branch_max_steps)
    restores, env = [], None
    status, reward, reason, n_steps = INFRA_ERROR, None, None, 0
    info: dict[str, Any] = {"teacher": policy_identity(teacher)}
    t_start = time.monotonic()
    try:
        # Snapshot restores are retried with backoff, then fall back to a replay restore of the same
        # checkpoint (Solari answered "Snapshot not found" persistently for some listed snapshots on
        # 2026-09-29). Every try is recorded; a fallback is visible in the branch's restore log.
        plan = [ckpt] * (cfg.restore_retries + 1)
        if ckpt["strategy"] == "snapshot" and int(ckpt["step"]) > 0:
            plan.append({**ckpt, "strategy": "replay", "world_ref": f"replay:{ckpt['step']}"})
        for attempt_no, ck_try in enumerate(plan):
            if env is not None:
                await env.close()
            if attempt_no:
                await asyncio.sleep(min(30.0, 5.0 * attempt_no))
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)
            rec = EpisodeRecorder(run_dir, task, episode_id=branch_id,
                                  extra={"branch_of": attempt["attempt_id"], "ckpt_id": ckpt["ckpt_id"],
                                         "ckpt_step": ckpt["step"], "repair_id": repair_id, "branch_idx": idx,
                                         "restore_strategy": ck_try["strategy"]})
            env, report = await open_branch(world=world, backend=backend, task=task, ckpt=ck_try, attempt=attempt,
                                            recorder=rec, run_id=f"{branch_id}-r{attempt_no}", settle=cfg.settle,
                                            max_screen_distance=cfg.max_screen_distance, budget_override=budget,
                                            history_k=cfg.history_k)
            restores.append(report.to_dict())
            store.charge("restore_seconds", report.seconds, "s", ref=branch_id, experiment_id=experiment_id,
                         evidence={"strategy": report.strategy, "ok": report.ok, "replayed": report.replayed_steps})
            if report.replayed_steps:
                store.charge("replay_steps", report.replayed_steps, "steps", ref=branch_id, experiment_id=experiment_id)
            if report.ok:
                break
        if not restores[-1]["ok"]:
            status = RESTORE_FAILED
            return {"branch_id": branch_id, "status": status, "reward": None, "reason_code": None, "restores": restores}
        if callable(getattr(teacher, "reset", None)):
            teacher.reset()
        teacher.load_agent_state(ckpt["agent_state"])
        obs = env._obs()
        while True:
            agent_before = teacher.agent_state() if callable(getattr(teacher, "agent_state", None)) else {}
            t0 = time.monotonic()
            action, meta = await act_with_deadline(env, teacher, obs)
            meta = dict(meta or {})
            meta.setdefault("model_latency_s", time.monotonic() - t0)
            meta["agent"] = {"memory_before": list(agent_before.get("memory", [])),
                             "memory_written": meta.get("memory_written", [])}
            meta["search"] = {"branch_id": branch_id, "ckpt_step": ckpt["step"], "repair_id": repair_id}
            obs, _, term, trunc, _ = await env.step(action, meta=meta)
            if term or trunc:
                v = await env.verify()
                reward, reason = v.reward, v.reason_code
                status = INFRA_ERROR if reason in ("INFRA_ERROR", "ORACLE_ERROR") else FINISHED
                n_steps = env.ep.step - int(ckpt["step"])
                info["end_reason"] = env.ep.end_reason
                break
    except Exception as e:  # noqa: BLE001 - infrastructure: kept, unscored
        info["error"] = f"{type(e).__name__}: {str(e)[:400]}"
    finally:
        if env is not None:
            try:
                await env.close()
            except Exception as e:  # noqa: BLE001
                info["cleanup_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        wall = time.monotonic() - t_start
        store.charge("branch_wall_seconds", wall, "s", ref=branch_id, experiment_id=experiment_id)
        info["model"] = charge_policy(store, teacher, ref=branch_id, experiment_id=experiment_id, role="teacher")
        info["wall_s"] = round(wall, 3)
        store.finish_branch(branch_id, status=status, reward=reward, reason_code=reason, n_steps=n_steps,
                            restore={"attempts": restores}, info=info)
        close = getattr(teacher, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception:  # noqa: BLE001
                pass
    return {"branch_id": branch_id, "status": status, "reward": reward, "reason_code": reason, "restores": restores}


async def repair_attempt(store: Store, world: Any, backend: Any, attempt_id: str, *,
                         teacher_factory: Callable[[], Any], cfg: Optional[RepairConfig] = None,
                         experiment_id: Optional[str] = None, repair_no: int = 1,
                         points: Optional[list[dict]] = None, sem: Optional[asyncio.Semaphore] = None) -> RepairResult:
    cfg = cfg or RepairConfig()
    attempt = store.attempt(attempt_id)
    if attempt["status"] != FINISHED or (attempt["reward"] or 0) >= 1.0:
        raise ValueError(f"attempt {attempt_id} is not a scored failure (status={attempt['status']}, reward={attempt['reward']})")
    task = task_for(store, attempt["task_id"], world)
    ckpts = store.checkpoints(attempt_id)
    probe = teacher_factory()
    teacher_id = store.put_policy("teacher", policy_identity(probe))
    close = getattr(probe, "aclose", None)
    if callable(close):
        await close()
    repair_id = stable_id("rep", attempt_id, cfg.mode, teacher_id, json.dumps(cfg.to_dict(), sort_keys=True), repair_no)
    if points is None:
        if cfg.mode == "full_restart":
            start = next(c for c in ckpts if c["step"] == 0)
            chosen = [{"ckpt_id": start["ckpt_id"], "step": 0, "reason": "full_restart", "evidence": {}}]
        else:
            chosen = [p.to_dict() for p in restart_points(attempt, ckpts, task, max_points=cfg.max_restart_points)]
    else:
        chosen = points
    store.start_repair(repair_id=repair_id, attempt_id=attempt_id, mode=cfg.mode, teacher_policy_id=teacher_id,
                       config={**cfg.to_dict(), "restart_points": chosen}, experiment_id=experiment_id)
    by_id = {c["ckpt_id"]: c for c in ckpts}
    sem = sem or asyncio.Semaphore(max(1, cfg.concurrency))  # a shared semaphore bounds machines across repairs
    results: list[dict] = []
    verified: list[str] = []
    try:
        for point in chosen:
            ckpt = by_id[point["ckpt_id"]]

            async def one(i: int) -> dict:
                async with sem:
                    return await _run_branch(store=store, world=world, backend=backend, task=task, attempt=attempt,
                                             ckpt=ckpt, repair_id=repair_id, idx=i, teacher_factory=teacher_factory,
                                             cfg=cfg, experiment_id=experiment_id)

            outs = await asyncio.gather(*(one(i) for i in range(cfg.k)))
            for o in outs:
                o["ckpt_step"] = ckpt["step"]
                o["restart_reason"] = point["reason"]
            results += outs
            verified += [o["branch_id"] for o in outs if o["status"] == FINISHED and (o["reward"] or 0) >= 1.0]
            if verified and cfg.stop_on_success:
                break
        status = "verified" if verified else "unrepaired"
    except BaseException:
        store.finish_repair(repair_id, status="interrupted", result={"branches": results})
        raise
    store.finish_repair(repair_id, status=status, result={"branches": results, "verified": verified})
    return RepairResult(repair_id, attempt_id, cfg.mode, verified, results, chosen, status)


#: Branch outcomes that are infrastructure, not the teacher's: they make the whole repair unscored.
UNSCORED_BRANCH = ("infra_error", "restore_failed", "interrupted", "running")


def repair_is_clean(store: Store, repair: dict) -> bool:
    """A repair is scored only if it finished and none of its branches is unscored. One lost branch
    would otherwise shrink its ``k`` tries (or skip to a later restart point), so it is replaced whole,
    as an unscored attempt is."""
    if repair["status"] not in ("verified", "unrepaired"):
        return False
    return not any(b["status"] in UNSCORED_BRANCH for b in store.branches(repair_id=repair["repair_id"]))


def counted_repair(store: Store, attempt_id: str, *, experiment_id: Optional[str], mode: str) -> tuple[Optional[dict], int]:
    """The registered replacement rule applied to repairs: the first clean repair of this attempt (in
    start order) counts. Returns ``(that repair or None, number of repairs tried)``."""
    where = {"attempt_id": attempt_id, **({"experiment_id": experiment_id} if experiment_id is not None else {})}
    prior = [r for r in store.repairs(**where) if r["mode"] == mode]
    for r in prior:
        if repair_is_clean(store, r):
            return r, _tries(prior)
    return None, _tries(prior)


#: A void repair annotated with this reason was run while the provider refused every request (e.g. the
#: account's credit was exhausted): it says nothing about the teacher and is not a replacement try.
PROVIDER_OUTAGE = "provider_outage"


def _tries(repairs: list[dict]) -> int:
    return sum(1 for r in repairs if (r.get("result") or {}).get("void_reason") != PROVIDER_OUTAGE)


__all__ = ["repair_attempt", "RepairConfig", "RepairResult", "task_for", "repair_is_clean", "counted_repair",
           "UNSCORED_BRANCH", "PROVIDER_OUTAGE"]
