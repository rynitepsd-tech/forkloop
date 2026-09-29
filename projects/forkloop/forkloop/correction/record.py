"""Record an attempt with bound checkpoints (``forkloop record``).

The attempt row is written ``running`` before the world is reset. Checkpoints are captured
before the policy's action is applied at each declared boundary (the world has not changed yet,
and the policy state is the one it had before deciding). The episode ends with the ordinary
verifier; an infrastructure failure leaves the attempt ``infra_error`` and unscored.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..env import Env, act_with_deadline
from ..reset import ResetError
from ..trajectories import EpisodeRecorder
from .checkpoint import Blobs, CheckpointPolicy, capture, oracle_now, policy_identity, snapshot_policy
from .store import FINISHED, INFRA_ERROR, Store, stable_id


class DirRecorder:
    """Recorder adapter: every episode goes to ``<base>/<episode_id>/`` (no run-id layer)."""

    def __init__(self, base: str | Path) -> None:
        self.dir = Path(base)
        self.dir.mkdir(parents=True, exist_ok=True)

    def episode(self, task: Any, *, episode_id: Optional[str] = None, extra: Optional[dict] = None) -> EpisodeRecorder:
        assert episode_id, "correction runs always name their episodes"
        return EpisodeRecorder(self.dir / episode_id, task, episode_id=episode_id, extra=extra)


def model_usd(policy: Any) -> Optional[float]:
    """Estimated USD for a hosted OpenAI policy's cumulative usage (None for self-hosted models)."""
    try:
        from ..policies.student import GUARDED_OPENAI_PRICES
    except Exception:  # noqa: BLE001
        return None
    model = getattr(policy, "model", None)
    base = str(getattr(policy, "base_url", ""))
    if "api.openai.com" not in base or model not in GUARDED_OPENAI_PRICES:
        return None
    p_in, p_out, _, _ = GUARDED_OPENAI_PRICES[model]
    u = getattr(policy, "usage", {}) or {}
    return (u.get("in", 0) * p_in + u.get("cache_read", 0) * p_in * 0.1 + u.get("cache_write", 0) * p_in * 1.25
            + u.get("out", 0) * p_out) / 1e6


def charge_policy(store: Store, policy: Any, *, ref: str, experiment_id: Optional[str], role: str) -> dict:
    u = dict(getattr(policy, "usage", {}) or {})
    usd = model_usd(policy)
    tokens = sum(v for v in u.values() if isinstance(v, (int, float)))
    store.charge("model_tokens", tokens, "tokens", usd=usd, ref=ref, experiment_id=experiment_id,
                 evidence={"usage": u, "model": getattr(policy, "model", None), "role": role,
                           "requests": getattr(policy, "n_requests", None)})
    return {"usage": u, "usd": usd}


@dataclass
class AttemptResult:
    attempt_id: str
    status: str
    reward: Optional[float]
    reason_code: Optional[str]
    n_steps: int
    run_dir: str
    checkpoints: list[dict] = field(default_factory=list)


async def record_attempt(env: Env, policy: Any, task: Any, *, store: Store, role: str = "student",
                         ckpt: Optional[CheckpointPolicy] = None, attempt_id: Optional[str] = None,
                         experiment_id: Optional[str] = None, cell: Optional[str] = None,
                         attempt_no: int = 1) -> AttemptResult:
    """Run one attempt of ``task`` with ``policy`` in ``env``, recording bound checkpoints."""
    ckpt = ckpt or CheckpointPolicy()
    blobs = Blobs(store.root / "blobs")
    store.put_task(task)
    policy_id = store.put_policy(role, policy_identity(policy))
    attempt_id = attempt_id or stable_id("att", experiment_id or "adhoc", cell or task.task_id, policy_id, attempt_no,
                                         time.time() if not (experiment_id and cell) else "")
    run_dir = store.root / "attempts" / attempt_id
    store.start_attempt(attempt_id=attempt_id, task_id=task.task_id, policy_id=policy_id, run_dir=str(run_dir),
                        strategy=ckpt.strategy, experiment_id=experiment_id, cell=cell,
                        info={"role": role, "attempt_no": attempt_no, "checkpoint_policy": ckpt.to_dict()})
    env.recorder = DirRecorder(store.root / "attempts")
    t_start = time.monotonic()
    ckpts: list[dict] = []
    status, reward, reason, n_steps = INFRA_ERROR, None, None, 0
    info: dict[str, Any] = {}
    try:
        try:
            obs, reset_info = await env.reset(task.seed, task=task, episode_id=attempt_id)
        except ResetError as e:
            info["reset_error"] = str(e)[:500]
            info["reset"] = e.report.to_dict() if e.report else None
            raise
        (run_dir / "baseline.json").write_text(json.dumps(env.ep.baseline.to_dict(), default=str))
        if callable(getattr(policy, "reset", None)):
            policy.reset()
        reference = (await oracle_now(env)).details
        (run_dir / "reference_verdict.json").write_text(json.dumps(reference, default=str))
        snapshots, last_ckpt = 0, None
        while True:
            step = env.ep.step
            full, agent = snapshot_policy(policy)
            t0 = time.monotonic()
            action, meta = await act_with_deadline(env, policy, obs)
            meta = dict(meta or {})
            meta.setdefault("model_latency_s", time.monotonic() - t0)
            meta["agent"] = {"memory_before": list(agent.get("memory", [])), "memory_written": meta.get("memory_written", [])}
            kind = ckpt.boundary(step, action, last_ckpt)
            if kind:
                t_ck = time.monotonic()
                c = await capture(env, store=store, blobs=blobs, attempt_id=attempt_id, step=step, boundary=kind,
                                  policy_state=full, agent_state=agent, cfg=ckpt, snapshots_taken=snapshots,
                                  reference=reference, experiment_id=experiment_id)
                # Checkpoint overhead is the controller's cost, not the agent's: pause the episode
                # clock (a Solari snapshot takes ~70 s). It is reported in charges instead.
                env.ep.started_at += time.monotonic() - t_ck
                snapshots += int(c["strategy"] == "snapshot")
                last_ckpt = step
                ckpts.append({k: c[k] for k in ("ckpt_id", "strategy", "status")} | {"step": step})
            obs, _, term, trunc, step_info = await env.step(action, meta=meta)
            if term or trunc:
                verdict = await env.verify()
                reward, reason = verdict.reward, verdict.reason_code
                status = INFRA_ERROR if reason in ("INFRA_ERROR", "ORACLE_ERROR") else FINISHED
                n_steps = env.ep.step
                info["end_reason"] = env.ep.end_reason
                break
    except Exception as e:  # noqa: BLE001 - infrastructure: the attempt is kept, unscored
        info["error"] = f"{type(e).__name__}: {str(e)[:500]}"
        n_steps = env.ep.step if env.ep is not None else 0
    finally:
        wall = time.monotonic() - t_start
        store.charge("attempt_wall_seconds", wall, "s", ref=attempt_id, experiment_id=experiment_id,
                     evidence={"role": role, "task_id": task.task_id})
        info["model"] = charge_policy(store, policy, ref=attempt_id, experiment_id=experiment_id, role=role)
        info["wall_s"] = round(wall, 3)
        info["checkpoints"] = ckpts
        try:
            store.finish_attempt(attempt_id, status=status, reward=reward, reason_code=reason, n_steps=n_steps, info=info)
        except ValueError:
            pass  # already finished by a concurrent cleanup; the first outcome stands
    return AttemptResult(attempt_id, status, reward, reason, n_steps, str(run_dir), ckpts)


__all__ = ["record_attempt", "AttemptResult", "DirRecorder", "charge_policy", "model_usd"]
