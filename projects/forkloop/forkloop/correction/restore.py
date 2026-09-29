"""Restore a bound checkpoint into an independent branch environment.

Every branch gets its own machine (branch independence), its own recorder directory and its
own policy instance. The world is restored by the checkpoint's strategy:

* ``reset`` (step 0): reset the task on a fresh machine;
* ``snapshot``: create a fresh machine from the provider snapshot (``fork`` from the checkpoint),
  and rebuild the episode state (task, the attempt's own baseline, counters, history);
* ``replay``: reset the task on a fresh machine, then re-execute the attempt's recorded action
  prefix through the agent channel, waiting for the screen to settle after each action.

Then a fidelity check compares the restored world's digest with the checkpoint's. A branch whose
restore does not match is recorded as ``restore_failed`` and never scored as the policy's result.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..actions import Action
from ..backends.base import apply_action
from ..env import EpisodeState, Env
from ..observe import wait_stable
from ..oracle import Baseline
from ..pool import WorkerPool
from ..trajectories import EpisodeRecorder, load_episode
from .digest import WorldDigest, compare, world_digest


@dataclass
class RestoreReport:
    strategy: str
    ok: bool
    seconds: float
    fidelity: dict[str, Any] = field(default_factory=dict)
    replayed_steps: int = 0
    baseline_equal: Optional[bool] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy, "ok": self.ok, "seconds": round(self.seconds, 3), "fidelity": self.fidelity,
                "replayed_steps": self.replayed_steps, "baseline_equal": self.baseline_equal, "error": self.error}


def prefix_actions(attempt_dir: str | Path, upto_step: int) -> list[tuple[int, Optional[Action], dict]]:
    """The attempt's executed actions for steps < upto_step (invalid actions are kept as None)."""
    ep = load_episode(Path(attempt_dir))
    out = []
    for s in ep["steps"]:
        if s["i"] >= upto_step:
            break
        a = Action.parse(s["action"]) if s.get("action") else None
        out.append((s["i"], a, s))
    if len(out) != upto_step:
        raise ValueError(f"attempt has {len(out)} recorded steps before step {upto_step}; cannot replay")
    return out


async def _settle(machine: Any, action: Optional[Action], settle: str, settle_s: float) -> bytes:
    if action is not None and action.type == "wait":
        await asyncio.sleep(min(float(action.seconds or 0), 3.0))
    if settle == "stable":
        try:
            shot, _ = await wait_stable(machine, timeout_s=8.0, interval_s=0.3)
            return shot
        except Exception:  # noqa: BLE001
            pass
    await asyncio.sleep(settle_s)
    return await machine.screenshot()


async def open_branch(*, world: Any, backend: Any, task: Any, ckpt: dict[str, Any], attempt: dict[str, Any],
                      recorder: Optional[EpisodeRecorder], run_id: str, settle: str = "stable", settle_s: float = 0.6,
                      max_screen_distance: float = 0.05, budget_override: Optional[dict] = None,
                      history_k: int = 8) -> tuple[Env, RestoreReport]:
    """Build a branch Env positioned at ``ckpt``. The caller owns ``env.close()``."""
    t0 = time.monotonic()
    strategy = ckpt["strategy"]
    golden = ckpt["world_ref"] if strategy == "snapshot" else None
    from .runner import RUNNER_ID
    pool = WorkerPool(backend, world, size=1, mode="fork", golden_snapshot=golden, run_id=run_id,
                      reap_orphans_enabled=False, metadata={"runner": RUNNER_ID})
    env = Env(world, backend, family=task.family, split=task.split, pool=pool, recorder=None,
              budget_override=budget_override, history_k=history_k, stable_after_action=(settle == "stable"))
    env._own_pool = True
    report = RestoreReport(strategy=strategy, ok=False, seconds=0.0)
    attempt_dir = Path(attempt["run_dir"])
    base_dict = json.loads((attempt_dir / "baseline.json").read_text())
    step = int(ckpt["step"])
    try:
        if strategy == "snapshot":
            worker = await pool.acquire()
            try:
                machine = await worker.restore()
            except BaseException:
                await pool.release(worker, healthy=False)
                raise
            shot = await machine.screenshot() if "gui" in machine.capabilities else b""
            env.ep = EpisodeState(task=task, worker=worker, machine=machine, dbs=world.databases(machine),
                                  baseline=Baseline.from_dict(base_dict), step=step, recorder=recorder,
                                  last_shot=shot)
            if recorder is not None:
                recorder.record_reset({"method": "snapshot_fork", "snapshot": golden})
        else:
            env.recorder = None
            await env.reset(task.seed, task=task)
            env.ep.recorder = recorder
            if recorder is not None:
                recorder.record_reset({**(env.last_reset_report or {}), "restore": strategy})
            fresh = env.ep.baseline.to_dict()
            report.baseline_equal = fresh["tables"] == base_dict["tables"]
            # replay: re-execute the recorded prefix through the agent channel (controller-driven)
            for i, action, rec in prefix_actions(attempt_dir, step):
                before = env.ep.last_shot
                if action is not None and not action.is_terminal:
                    await apply_action(env.ep.machine, action)
                    env.ep.last_shot = await _settle(env.ep.machine, action, settle, settle_s)
                env.ep.previous_shot = before
                if recorder is not None:
                    recorder.record_step(i, shot_before=before, shot_after=env.ep.last_shot, action=action,
                                         raw_action=rec.get("raw_action", ""), valid=action is not None,
                                         policy_note="replayed from " + attempt["attempt_id"],
                                         search={"replayed": True, "source_attempt": attempt["attempt_id"]},
                                         agent=rec.get("agent"))
                report.replayed_steps += 1
            env.ep.step = step
        ep = env.ep
        ep.history = list(ckpt["history"])
        ep.budget_steps = int(ckpt["budget_steps"])
        ep.invalid = int(ckpt["invalid"])
        ep.started_at = time.monotonic() - float(ckpt["elapsed_s"])
        if strategy == "snapshot" and step > 0:
            # the observation's previous screenshot is the attempt's own screen before step-1's action
            prev = Path(attempt_dir) / "shots" / f"{step - 1:03d}_before.png"
            ep.previous_shot = prev.read_bytes() if prev.exists() else b""
        digest = await world_digest(world, ep.dbs, ep.last_shot)
        fid = compare(WorldDigest.from_dict(ckpt["digest"]), digest, max_screen_distance=max_screen_distance)
        report.fidelity = fid.to_dict()
        report.ok = fid.ok and report.baseline_equal is not False
        if not report.ok:
            report.error = "fidelity check failed" if report.baseline_equal is not False else "reset baseline differs"
    except Exception as e:  # noqa: BLE001
        report.error = f"{type(e).__name__}: {str(e)[:400]}"
        report.ok = False
    report.seconds = time.monotonic() - t0
    return env, report


__all__ = ["open_branch", "RestoreReport", "prefix_actions"]
