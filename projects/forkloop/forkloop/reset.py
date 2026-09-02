"""Snapshot / revert / from_snapshot lifecycle with health gating (§11).

``ResetController.reset`` executes the fixed reset protocol and returns a
stage-by-stage :class:`ResetReport` so every reset is also a benchmark sample.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from .dbaccess import DbAccess
from .observe import ScreenNotStable, wait_stable
from .oracle import Baseline
from .seed import apply_seeding
from .tasks import TaskInstance
from .types import ResetReport, StageTiming
from .world import World

if TYPE_CHECKING:  # pragma: no cover
    from .backends.base import Machine
    from .pool import Worker


class ResetError(RuntimeError):
    def __init__(self, msg: str, report: Optional[ResetReport] = None) -> None:
        super().__init__(msg)
        self.report = report


@dataclass
class ResetOutcome:
    machine: "Machine"
    dbs: dict[str, DbAccess]
    baseline: Baseline
    screenshot: bytes
    report: ResetReport


class ResetController:
    def __init__(self, world: World, *, stable_timeout_s: float = 15.0, stable_interval_s: float = 0.4,
                 skip_screen: bool = False) -> None:
        self.world = world
        self.stable_timeout_s = stable_timeout_s
        self.stable_interval_s = stable_interval_s
        self.skip_screen = skip_screen

    async def reset(self, worker: "Worker", task: TaskInstance) -> ResetOutcome:
        report = ResetReport(method=worker.pool.mode)
        t_all = time.monotonic()

        async def stage(name: str, coro):
            t0 = time.monotonic()
            try:
                result = await coro
                report.stages.append(StageTiming(name, time.monotonic() - t0, True))
                return result
            except Exception as e:  # noqa: BLE001
                report.stages.append(StageTiming(name, time.monotonic() - t0, False, f"{type(e).__name__}: {e}"))
                report.ok = False
                report.error = f"{name}: {type(e).__name__}: {e}"
                report.total_seconds = time.monotonic() - t_all
                raise ResetError(report.error, report) from e

        # 1. restore the golden world state
        machine = await stage("restore", worker.restore())
        # The pool may have switched modes mid-restore (e.g. revert refused → fork),
        # so label the report with what actually happened, not what was asked for.
        report.method = worker.pool.mode
        dbs = self.world.databases(machine)
        # 2. seed
        await stage("seed", apply_seeding(machine, dbs, task.seeding))
        await stage("before_episode", self.world.before_episode(machine))
        # 3. health
        health = await stage("health", self.world.health(machine, dbs))
        if not health.ok:
            report.stages[-1].ok = False
            report.stages[-1].note = str(health.checks)
            report.ok = False
            report.error = f"health: {health.checks}"
            report.total_seconds = time.monotonic() - t_all
            raise ResetError(report.error, report)
        # 4. baseline
        baseline = await stage("baseline", Baseline.capture(dbs, self.world.checksum_tables(), self.world.primary_keys(),
                                                            self.world.watermark_tables(),
                                                            ignore_columns=self.world.ignore_columns()))
        # 5. initial screen
        await stage("initial_screen", self.world.open_initial_screen(machine, task.initial_screen))
        if self.skip_screen or "gui" not in machine.capabilities:
            shot = b""
            report.stages.append(StageTiming("stable_screen", 0.0, True, "skipped"))
        else:
            shot, _ = await stage("stable_screen", wait_stable(machine, timeout_s=self.stable_timeout_s,
                                                               interval_s=self.stable_interval_s))
        report.total_seconds = time.monotonic() - t_all
        return ResetOutcome(machine=machine, dbs=dbs, baseline=baseline, screenshot=shot, report=report)


__all__ = ["ResetController", "ResetOutcome", "ResetError", "ScreenNotStable"]
