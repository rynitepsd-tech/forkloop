"""Worker pool over backend machines. Respects the plan's concurrency cap,
retries capacity/concurrency errors with backoff, and reaps orphans.

Two reset modes:

* ``revert`` — each worker is a long-lived machine; ``restore()`` is one
  ``revert(golden)`` call on the same machine id.
* ``fork``   — ``restore()`` kills the worker's machine (if any) and creates a
  fresh one with ``create(from_snapshot=golden)``.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .backends.base import Backend, CapacityError, ConcurrencyError, Machine
from .world import World


@dataclass
class Worker:
    pool: "WorkerPool"
    index: int
    machine: Optional[Machine] = None
    generation: int = 0
    busy: bool = False
    stats: dict[str, Any] = field(default_factory=lambda: {"restores": 0, "restore_seconds": []})

    async def restore(self) -> Machine:
        t0 = time.monotonic()
        if self.pool.mode == "revert":
            if self.machine is None or not await self.machine.healthy():
                self.machine = await self.pool._create(from_snapshot=self.pool.golden)
                if self.pool.golden is None:
                    sid, built_here = await self.pool._ensure_golden(self.machine)
                    self.pool.golden = sid
                    if not built_here:  # another worker built it first; bring this machine to the same state
                        await self._revert_or_fall_back(sid)
            else:
                await self._revert_or_fall_back(self.pool.golden)  # type: ignore[arg-type]
        elif self.pool.mode == "fork":
            if self.machine is not None:
                await self.machine.kill()
                self.machine = None
            if self.pool.golden is None:
                self.machine = await self.pool._create(from_snapshot=None)
                sid, built_here = await self.pool._ensure_golden(self.machine)
                self.pool.golden = sid
                if not built_here:
                    await self.machine.kill()
                    self.machine = await self.pool._create(from_snapshot=sid)
            else:
                self.machine = await self.pool._create(from_snapshot=self.pool.golden)
        else:
            raise ValueError(f"unknown pool mode {self.pool.mode!r}")
        self.generation += 1
        self.stats["restores"] += 1
        self.stats["restore_seconds"].append(time.monotonic() - t0)
        return self.machine

    async def _revert_or_fall_back(self, snapshot_id: str) -> None:
        """``revert()`` this worker's machine, or switch the whole pool to fork mode.

        Some accounts refuse ``revert()`` outright (HTTP 409 ``Not revertable``),
        and a refused revert can leave the machine unreachable — so on failure the
        machine is discarded and replaced with a fresh ``create(from_snapshot)``.
        The switch is pool-wide, logged in ``events``, and happens at most once.
        """
        try:
            await self.machine.revert(snapshot_id)  # type: ignore[union-attr]
            self.pool.revert_supported = True
            return
        except Exception as e:  # noqa: BLE001
            if not self.pool.fallback_to_fork:
                raise
            self.pool.revert_supported = False
            self.pool.mode = "fork"
            self.pool.events.append({"t": time.time(), "event": "revert_unsupported_fell_back_to_fork",
                                     "error": f"{type(e).__name__}: {e}"})
        # The machine may have been destroyed by the failed revert; replace it either way.
        if self.machine is not None:
            try:
                await self.machine.kill()
            except Exception:  # noqa: BLE001
                pass
            self.machine = None
        self.machine = await self.pool._create(from_snapshot=snapshot_id)

    async def snapshot(self, name: Optional[str] = None) -> str:
        assert self.machine is not None
        return await self.machine.snapshot(name)

    async def kill(self) -> None:
        if self.machine is not None:
            await self.machine.kill()
            self.machine = None


class WorkerPool:
    def __init__(self, backend: Backend, world: World, *, size: Optional[int] = None, mode: str = "revert",
                 golden_snapshot: Optional[str] = None, run_id: Optional[str] = None,
                 cpu: Optional[int] = None, mem_mb: Optional[int] = None, record: Optional[bool] = None,
                 timeout_ms: int = 30 * 60_000, max_retries: int = 10, disk_gb: Optional[int] = None,
                 fallback_to_fork: bool = True, create_timeout_s: float = 240.0) -> None:
        if mode not in ("revert", "fork"):
            raise ValueError("mode must be 'revert' or 'fork'")
        self.backend = backend
        self.world = world
        self.mode = mode
        #: Switch the pool to fork mode the first time ``revert()`` is refused.
        self.fallback_to_fork = fallback_to_fork
        #: Give up on a single create call after this long and retry (the SDK has no timeout of its own).
        self.create_timeout_s = create_timeout_s
        #: None until a revert has been attempted; then True/False for this account.
        self.revert_supported: Optional[bool] = None
        self.size = max(1, min(size or backend.concurrency_cap, backend.concurrency_cap))
        self.golden = golden_snapshot or world.golden_snapshot_id()
        self.run_id = run_id or ("run-" + uuid.uuid4().hex[:8])
        res = world.config.extra.get("resources", {}) if hasattr(world.config, "extra") else {}
        self.cpu = cpu or int(res.get("cpu", 2))
        self.mem_mb = mem_mb or int(res.get("mem_mb", 4096))
        self.disk_gb = disk_gb or (int(res["disk_gb"]) if res.get("disk_gb") else None)
        self.record, self.timeout_ms = record, timeout_ms
        self.max_retries = max_retries
        self.workers: list[Worker] = [Worker(self, i) for i in range(self.size)]
        self._free: asyncio.Queue[Worker] = asyncio.Queue()
        self._started = False
        self._build_lock = asyncio.Lock()
        self.events: list[dict[str, Any]] = []

    # ------------------------------------------------------------- lifecycle
    async def start(self, *, reap: bool = True, warm: bool = False) -> None:
        if self._started:
            return
        if reap:
            await self.reap_orphans()
        for w in self.workers:
            self._free.put_nowait(w)
        self._started = True
        if warm:
            for w in self.workers:
                await w.restore()

    async def acquire(self) -> Worker:
        if not self._started:
            await self.start()
        w = await self._free.get()
        w.busy = True
        return w

    async def release(self, worker: Worker, *, healthy: bool = True) -> None:
        worker.busy = False
        if not healthy and worker.machine is not None:
            try:
                await worker.machine.kill()
            finally:
                worker.machine = None
        self._free.put_nowait(worker)

    async def close(self) -> None:
        await asyncio.gather(*(w.kill() for w in self.workers), return_exceptions=True)
        self._started = False

    async def __aenter__(self) -> "WorkerPool":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------- internals
    async def _create(self, *, from_snapshot: Optional[str]) -> Machine:
        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                # Solari's create call has been seen to hang for many minutes with no answer
                # (2026-09-02); treat that like a capacity error and try again.
                return await asyncio.wait_for(self.backend.create(
                    template=self.world.config.template, from_snapshot=from_snapshot,
                    resolution=self.world.config.resolution, cpu=self.cpu, mem_mb=self.mem_mb,
                    record=self.record, metadata={"forkloop": "1", "run_id": self.run_id, "world": self.world.name},
                    timeout_ms=self.timeout_ms, disk_gb=self.disk_gb), timeout=self.create_timeout_s)
            except (ConcurrencyError, CapacityError, asyncio.TimeoutError) as e:
                err = str(e) or f"create timed out after {self.create_timeout_s:.0f}s"
                self.events.append({"t": time.time(), "event": "create_retry", "attempt": attempt, "error": err})
                if attempt == self.max_retries:
                    raise
                if isinstance(e, ConcurrencyError):
                    # The cap is usually held by a machine of ours that an earlier process left
                    # behind (a killed run, or a listing that lagged at start-up). Re-list and
                    # kill orphans before waiting; a stale listing at pool start is not fatal.
                    killed = await self.reap_orphans()
                    if killed:
                        continue
                await asyncio.sleep(delay + random.random() * 0.5)
                delay = min(delay * 2, 60.0)
        raise RuntimeError("unreachable")

    async def _ensure_golden(self, machine: Machine) -> tuple[str, bool]:
        """No golden snapshot configured: build the world on this machine and snapshot it.

        Returns ``(snapshot_id, built_on_this_machine)``. Only the fake backend does
        this implicitly; on Solari the world build is an explicit, logged step
        (``forkloop build-world``) because it takes minutes.
        """
        async with self._build_lock:
            if self.golden:
                return self.golden, False
            if self.backend.name != "fake":
                raise RuntimeError(
                    f"no golden snapshot for world {self.world.name}: set ${self.world.config.golden_snapshot_env} "
                    "or run `forkloop build-world` first")
            sid = await self.world.build(machine, log=lambda s: None)
            self.golden = sid
            self.events.append({"t": time.time(), "event": "golden_built", "snapshot": sid})
            return sid, True

    async def reap_orphans(self, *, older_than_s: float = 0.0) -> list[str]:
        """Kill machines tagged forkloop=1 that are not owned by this pool."""
        mine = {w.machine.id for w in self.workers if w.machine is not None}
        killed: list[str] = []
        try:
            infos = await self.backend.list_machines(metadata={"forkloop": "1"})
        except Exception as e:  # noqa: BLE001
            self.events.append({"t": time.time(), "event": "reap_failed", "error": str(e)})
            return killed
        for info in infos:
            if info.id in mine or info.state not in ("running", "starting", "paused"):
                continue
            if info.metadata.get("run_id") == self.run_id:
                continue
            try:
                await self.backend.kill_machine(info.id)
                killed.append(info.id)
            except Exception as e:  # noqa: BLE001
                self.events.append({"t": time.time(), "event": "reap_kill_failed", "id": info.id, "error": str(e)})
        if killed:
            self.events.append({"t": time.time(), "event": "reaped", "ids": killed})
        return killed

    def stats(self) -> dict[str, Any]:
        return {"size": self.size, "mode": self.mode, "golden": self.golden, "run_id": self.run_id,
                "revert_supported": self.revert_supported,
                "workers": [{"index": w.index, "machine": w.machine.id if w.machine else None,
                             "restores": w.stats["restores"]} for w in self.workers],
                "events": self.events[-50:]}


__all__ = ["WorkerPool", "Worker"]
