"""Gymnasium-style environment (docs/contracts.md §11).

``make()`` → ``Env``; ``await env.reset(seed)`` → ``(Observation, info)``;
``await env.step(action)`` → ``(obs, reward, terminated, truncated, info)``.
Reward is 0 on every non-terminal step and the oracle's verdict at the end.
``info`` never carries expected values, seeding, or the oracle spec.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .actions import Action, InvalidAction
from .backends.base import Backend, BackendError, Machine, apply_action
from .dbaccess import DbAccess
from .observe import wait_stable
from .oracle import SAFETY_REASONS, Baseline, Oracle, Verdict
from .pool import Worker, WorkerPool
from .reset import ResetController, ResetError
from .tasks import TaskInstance
from .trajectories import EpisodeRecorder, Recorder
from .types import Observation
from .world import World, load_world


@dataclass
class EnvCheckpoint:
    """A snapshot of the machine plus the env's own state, for search."""

    snapshot_id: str
    step: int
    history: list[str]
    invalid: int
    started_at: float
    screenshot: bytes
    budget_steps: int = 0
    elapsed_s: float = 0.0
    previous_screenshot: bytes = b""


@dataclass
class EpisodeState:
    task: TaskInstance
    worker: Worker
    machine: Machine
    dbs: dict[str, DbAccess]
    baseline: Baseline
    step: int = 0
    budget_steps: int = 0  # steps that count against max_steps (everything except wait)
    invalid: int = 0
    history: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    previous_shot: bytes = b""
    last_shot: bytes = b""
    terminated: bool = False
    truncated: bool = False
    verdict: Optional[Verdict] = None
    recorder: Optional[EpisodeRecorder] = None
    end_reason: str = ""
    infra_errors: int = 0  # consecutive backend/transport failures while applying actions


class Env:
    def __init__(self, world: World, backend: Backend, *, family: Optional[str] = None, split: str = "train",
                 pool: Optional[WorkerPool] = None, recorder: Optional[Recorder] = None,
                 history_k: int = 8, settle_s: float = 0.6, stable_after_action: bool = False,
                 max_invalid: int = 10, reset_controller: Optional[ResetController] = None,
                 record_extra: Optional[dict[str, Any]] = None, budget_override: Optional[dict[str, Any]] = None) -> None:
        self.world = world
        self.backend = backend
        #: Per-run budget knobs (max_steps / max_seconds) laid over every task's own budget.
        #: A teacher that needs more clicks than the task budget allows is still a valid data
        #: source; the run records the override so results are never compared blindly.
        self.budget_override = dict(budget_override or {})
        self.family = family or (world.config.families[0] if world.config.families else None)
        self.split = split
        self.pool = pool or WorkerPool(backend, world, size=1)
        self._own_pool = pool is None
        self.recorder = recorder
        self.history_k = history_k
        self.settle_s = settle_s
        self.stable_after_action = stable_after_action
        self.max_invalid = max_invalid
        self.max_infra_errors = 3
        self.resetter = reset_controller or ResetController(world)
        self.record_extra = record_extra or {}
        self.width, self.height = world.size
        self.ep: Optional[EpisodeState] = None
        self.last_reset_report: Optional[dict[str, Any]] = None

    # ------------------------------------------------------------ helpers
    def _obs(self) -> Observation:
        assert self.ep is not None
        return Observation(screenshot=self.ep.last_shot, instruction=self.ep.task.instruction, step=self.ep.step,
                           history=list(self.ep.history[-self.history_k:]) if self.history_k else [],
                           width=self.width, height=self.height, previous_screenshot=self.ep.previous_shot)

    def _info(self, **extra: Any) -> dict[str, Any]:
        assert self.ep is not None
        d = {**self.ep.task.public_info, "step": self.ep.step, "invalid": self.ep.invalid,
             "elapsed_s": round(time.monotonic() - self.ep.started_at, 3), "machine": self.ep.machine.id,
             "stream_url": getattr(self.ep.machine, "stream_url", None)}
        d.update(extra)
        return d

    # -------------------------------------------------------------- reset
    async def reset(self, seed: int, *, family: Optional[str] = None, task: Optional[TaskInstance] = None,
                    episode_id: Optional[str] = None) -> tuple[Observation, dict[str, Any]]:
        if self.ep is not None:
            await self._end_episode("abandoned" if not (self.ep.terminated or self.ep.truncated) else "next_reset")
        fam = family or self.family
        if task is None:
            if fam is None:
                raise ValueError("no family given and the world declares none")
            task = self.world.generate(fam, seed, self.split)
        worker = await self.pool.acquire()
        try:
            outcome = await self.resetter.reset(worker, task)
        except BaseException as e:
            await self.pool.release(worker, healthy=False)
            if isinstance(e, ResetError):
                self.last_reset_report = e.report.to_dict() if e.report else None
            raise
        self.last_reset_report = outcome.report.to_dict()
        rec = self.recorder.episode(task, episode_id=episode_id, extra=self.record_extra) if self.recorder else None
        if rec:
            rec.record_reset(self.last_reset_report)
        self.ep = EpisodeState(task=task, worker=worker, machine=outcome.machine, dbs=outcome.dbs,
                               baseline=outcome.baseline, last_shot=outcome.screenshot, recorder=rec)
        return self._obs(), self._info(reset=self.last_reset_report)

    # --------------------------------------------------------------- step
    def remaining_seconds(self) -> float:
        if self.ep is None:
            raise RuntimeError("call reset() first")
        budget = {**self.ep.task.budget, **self.budget_override}
        return max(0.0, float(budget.get("max_seconds", 600)) - (time.monotonic() - self.ep.started_at))

    async def step(self, action: Any, *, meta: Optional[dict[str, Any]] = None) -> tuple[Observation, float, bool, bool, dict[str, Any]]:
        ep = self.ep
        if ep is None:
            raise RuntimeError("call reset() first")
        if ep.terminated or ep.truncated:
            raise RuntimeError("episode is over; call reset()")
        if self.remaining_seconds() <= 0:
            ep.truncated, ep.end_reason = True, "max_seconds"
            verdict = await self.verify()
            return self._obs(), verdict.reward, False, True, self._info(end_reason=ep.end_reason)
        meta = dict(meta or {})
        raw = meta.get("raw_action") or (action.to_compact() if isinstance(action, Action) else str(action))
        shot_before = ep.last_shot
        parsed: Optional[Action] = None
        error: Optional[str] = None
        if action is None:
            error = meta.get("error") or "policy produced no action"
        else:
            try:
                parsed = Action.parse(action, width=self.width, height=self.height)
            except InvalidAction as e:
                error = str(e)
        if parsed is None:
            ep.invalid += 1
        else:
            try:
                await apply_action(ep.machine, parsed)
            except Exception as e:  # noqa: BLE001
                if _is_infrastructure_error(e):
                    error = f"{BACKEND_FAILURE_PREFIX} {type(e).__name__}: {e}"
                    # A dropped connection or dead machine is not the policy's mistake:
                    # count it separately so it cannot end the episode as INVALID_ACTION_LIMIT.
                    ep.infra_errors += 1
                else:
                    error = f"apply failed: {type(e).__name__}: {e}"
                    ep.invalid += 1
            else:
                ep.infra_errors = 0
        # observe
        if parsed is not None and not parsed.is_terminal:
            if self.stable_after_action:
                try:
                    ep.last_shot, _ = await wait_stable(ep.machine, timeout_s=8.0, interval_s=0.3)
                except Exception:  # noqa: BLE001
                    ep.last_shot = await ep.machine.screenshot()
            else:
                if self.settle_s:
                    await asyncio.sleep(self.settle_s)
                ep.last_shot = await ep.machine.screenshot()
        ep.previous_shot = shot_before
        shot_after = ep.last_shot
        ep.history.append(raw if parsed is None else parsed.to_compact())
        i = ep.step
        ep.step += 1
        if parsed is None or parsed.type != "wait":
            ep.budget_steps += 1  # waits are free: they change nothing and a real agent needs many of them
        if ep.recorder:
            ep.recorder.record_step(i, shot_before=shot_before, shot_after=shot_after, action=parsed, raw_action=raw,
                                    valid=parsed is not None and error is None,
                                    model_latency_s=float(meta.get("model_latency_s", 0.0)),
                                    tokens=meta.get("tokens"), policy_note=str(meta.get("note", "") or ""),
                                    search=meta.get("search"), error=error)
        # termination
        budget = {**ep.task.budget, **self.budget_override}
        reward = 0.0
        if parsed is not None and parsed.is_terminal:
            ep.terminated = True
            ep.end_reason = "done"
        elif ep.budget_steps >= int(budget.get("max_steps", 60)):
            ep.truncated = True
            ep.end_reason = "max_steps"
        elif time.monotonic() - ep.started_at >= float(budget.get("max_seconds", 600)):
            ep.truncated = True
            ep.end_reason = "max_seconds"
        elif ep.invalid >= self.max_invalid:
            ep.truncated = True
            ep.end_reason = "invalid_actions"
        elif ep.infra_errors >= self.max_infra_errors:
            ep.truncated = True
            ep.end_reason = "infrastructure_error"
        if ep.terminated or ep.truncated:
            verdict = await self.verify()
            reward = verdict.reward
        return self._obs(), reward, ep.terminated, ep.truncated, self._info(error=error, end_reason=ep.end_reason or None)

    # ------------------------------------------------------------- verify
    async def verify(self) -> Verdict:
        ep = self.ep
        if ep is None:
            raise RuntimeError("no episode")
        if ep.verdict is not None:
            return ep.verdict
        ctx = self.world.oracle_context(ep.dbs, ep.baseline)
        try:
            verdict = await Oracle(ctx).evaluate(ep.task.oracle)
        except Exception as e:  # noqa: BLE001
            verdict = Verdict.error(f"{type(e).__name__}: {e}")
        if ep.end_reason == "infrastructure_error" and verdict.reward < 1.0:
            # The backend cut the episode short, so missing work is not the policy's failure.
            # A safety violation the verifier observed cleanly is, and stays the reason.
            observed = {d.get("reason_code") for c, d in verdict.details.items()
                        if c in verdict.failed and isinstance(d, dict) and "error" not in d}
            if not observed & SAFETY_REASONS:
                verdict.reason_code = "INFRA_ERROR"
        if ep.end_reason == "invalid_actions" and verdict.reward < 1.0 and verdict.reason_code == "OK":
            verdict.reason_code = "INVALID_ACTION_LIMIT"
        if ep.truncated and verdict.reward < 1.0 and verdict.reason_code in ("OK",):
            verdict.reason_code = "BUDGET_EXCEEDED"
        # UI milestones (which screens the agent reached, from the audit trails) ride along in the
        # details for analysis; they never touch reward, milestones or the reason code.
        try:
            ms = await self.world.ui_milestones(ep.dbs, ep.baseline, ep.task)
        except Exception as e:  # noqa: BLE001 - diagnostics only
            ms = {"error": f"{type(e).__name__}: {e}"}
        if ms:
            verdict.details["ui_milestones"] = ms
        ep.verdict = verdict
        if ep.recorder:
            await self._save_diagnostics(ep)
            ep.recorder.finish(verdict, extra={"end_reason": ep.end_reason, "invalid_actions": ep.invalid})
        return verdict

    async def _save_diagnostics(self, ep: EpisodeState) -> None:
        """Keep the world's diagnostic logs (browser, kernel) next to the episode. Never fails the episode."""
        try:
            files = await self.world.diagnostics(ep.machine)
        except Exception as e:  # noqa: BLE001
            files = {"diagnostics_error.txt": f"{type(e).__name__}: {e}"}
        if not files or ep.recorder is None:
            return
        d = ep.recorder.dir / "diagnostics"
        d.mkdir(exist_ok=True)
        for name, text in files.items():
            (d / name).write_text(text or "", encoding="utf-8", errors="replace")

    # ------------------------------------------------------------- search
    async def checkpoint(self) -> EnvCheckpoint:
        ep = self.ep
        assert ep is not None
        sid = await ep.machine.snapshot(f"cp-{ep.task.task_id}-{ep.step}")
        return EnvCheckpoint(snapshot_id=sid, step=ep.step, history=list(ep.history), invalid=ep.invalid,
                             started_at=ep.started_at, screenshot=ep.last_shot, budget_steps=ep.budget_steps,
                             elapsed_s=time.monotonic() - ep.started_at, previous_screenshot=ep.previous_shot)

    async def restore(self, cp: EnvCheckpoint) -> Observation:
        ep = self.ep
        assert ep is not None
        await ep.machine.revert(cp.snapshot_id)
        ep.step, ep.history, ep.invalid = cp.step, list(cp.history), cp.invalid
        ep.budget_steps = cp.budget_steps
        ep.started_at = time.monotonic() - cp.elapsed_s
        ep.terminated = ep.truncated = False
        ep.verdict = None
        ep.end_reason = ""
        ep.previous_shot = cp.previous_screenshot
        ep.last_shot = cp.screenshot or await ep.machine.screenshot()
        return self._obs()

    def swap_recorder(self, rec: Optional[EpisodeRecorder]) -> Optional[EpisodeRecorder]:
        assert self.ep is not None
        old = self.ep.recorder
        self.ep.recorder = rec
        return old

    # -------------------------------------------------------------- close
    async def _end_episode(self, reason: str) -> None:
        ep = self.ep
        if ep is None:
            return
        if ep.verdict is None and ep.recorder is not None:
            ep.recorder.finish(Verdict(0.0, 0.0, "NOT_DONE", ["abandoned"], {}), extra={"end_reason": reason})
        await self.pool.release(ep.worker, healthy=True)
        self.ep = None

    async def close(self) -> None:
        await self._end_episode("closed")
        if self._own_pool:
            await self.pool.close()

    async def __aenter__(self) -> "Env":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


def make(world: str | World, backend: Backend, **kw: Any) -> Env:
    w = load_world(world) if isinstance(world, str) else world
    return Env(w, backend, **kw)


async def run_episode(env: Env, policy: Any, seed: int, *, family: Optional[str] = None,
                      on_step: Optional[Any] = None) -> Verdict:
    """Drive one full episode with a policy. Returns the verdict."""
    obs, info = await env.reset(seed, family=family)
    if callable(getattr(policy, "reset", None)):
        policy.reset()
    while True:
        t0 = time.monotonic()
        action, meta = await act_with_deadline(env, policy, obs)
        meta = dict(meta or {})
        meta.setdefault("model_latency_s", time.monotonic() - t0)
        obs, reward, term, trunc, info = await env.step(action, meta=meta)
        if on_step:
            on_step(obs, reward, term, trunc, info)
        if term or trunc:
            return await env.verify()


async def act_with_deadline(env: Env, policy: Any, obs: Observation):
    """A trajectory wall budget also bounds time spent waiting for the policy.

    Cancellation never cancels/refunds the external reservation for a request
    whose server may already be computing a response.
    """
    remaining = env.remaining_seconds()
    if remaining <= 0:
        return None, {"note": "trajectory wall budget exhausted"}
    deadline = asyncio.timeout(remaining)
    try:
        async with deadline:
            return await policy.act(obs)
    except TimeoutError:
        if not deadline.expired():
            raise  # Provider timeouts are infrastructure errors, not elapsed task budgets.
        return None, {"note": "policy call exceeded trajectory wall budget", "tokens": dict(getattr(policy, "usage", {}) or {})}


__all__ = ["Env", "EnvCheckpoint", "make", "run_episode", "Observation"]


#: Step-error prefix for backend/transport failures (unscored); "apply failed:" marks an action
#: the backend rejected because of its content, which is the policy's invalid action.
BACKEND_FAILURE_PREFIX = "backend failed:"


def _is_infrastructure_error(e: BaseException) -> bool:
    """Backend/transport failures (dead machine, dropped channel, timeouts), as opposed to an
    action the backend rejected because of its content (e.g. ValueError for an empty type())."""
    if isinstance(e, (BackendError, ConnectionError, TimeoutError, asyncio.TimeoutError, OSError)):
        return True
    try:
        from solari_core import errors as se  # type: ignore
    except Exception:  # pragma: no cover - SDK absent
        return False
    return isinstance(e, (se.GatewayError, se.ConnectionError, se.TimeoutError))

