"""Solari backend: desktops created through the unified ``/sandboxes`` route so
``from_snapshot`` works (``SandboxClient.create_desktop``; ``DesktopClient.create``
has no such parameter — verified against solari-sandbox 0.2.0).

Everything the contract calls ``Machine`` maps one-to-one onto the SDK's
``Desktop`` handle; see docs/contracts.md §2 for the table.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

from ..types import ExecResult, MachineInfo, SnapshotInfo
from .base import BackendError, CapacityError, ConcurrencyError, PlanGateError, parse_resolution

DEFAULT_BASE_URL = "https://api.getsolari.com"
PLAN_CAPS = {"free": 1, "starter": 2, "pro": 10, "professional": 10, "enterprise": 50}


def _wrap_error(e: Exception) -> Exception:
    try:
        from solari_core import errors as se  # type: ignore
    except Exception:  # pragma: no cover
        return e
    if isinstance(e, se.PlanError):
        return PlanGateError(f"Solari plan gate (402): {e}. Desktops need a paid plan (Starter+).")
    if isinstance(e, se.ConcurrencyLimitError):
        return ConcurrencyError(f"Solari concurrency cap (429): {e}")
    if isinstance(e, se.NoCapacityError):
        return CapacityError(f"Solari has no warm desktop hosts right now (503): {e}")
    return e


class SolariMachine:
    backend_name = "solari"

    def __init__(self, desktop: Any, backend: "SolariBackend", size: tuple[int, int],
                 metadata: dict[str, str], kind: str = "desktop") -> None:
        self._d = desktop
        self.backend = backend
        self.id: str = desktop.id
        self.stream_url: Optional[str] = getattr(desktop, "streamUrl", None) or None
        self.size = size
        self.metadata = metadata
        self.kind = kind
        # headless sandboxes have no agent channel: the controller loop still works (seed, health, oracle, revert)
        self.capabilities = frozenset({"shell", "gui", "http"}) if kind == "desktop" else frozenset({"shell", "http"})
        self.alive = True

    # ---------------------------------------------------------- lifecycle
    async def connect(self, *, wait_ready_s: float = 60.0) -> None:
        await self._d.connect()
        await self.wait_ready(wait_ready_s)

    async def wait_ready(self, timeout_s: float = 60.0) -> None:
        t0 = time.monotonic()
        last = None
        while time.monotonic() - t0 < timeout_s:
            try:
                if self.kind != "desktop":
                    # sandboxes have no health(); a successful command is "ready"
                    r = await self._d.commands.run("true", timeout_ms=10_000)
                    if r.exitCode == 0:
                        return
                    last = r
                else:
                    h = await self._d.health()
                    if getattr(h, "ready", False):
                        return
                    last = h
            except Exception as e:  # noqa: BLE001 - transient right after restore
                last = e
                try:
                    await self._d.reconnect()
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(0.5)
        raise BackendError(f"desktop {self.id} not ready after {timeout_s}s (last={last!r})")

    async def healthy(self) -> bool:
        try:
            if self.kind != "desktop":
                return (await self._d.commands.run("true", timeout_ms=10_000)).exitCode == 0
            h = await self._d.health()
            return bool(getattr(h, "ready", False))
        except Exception:  # noqa: BLE001
            return False

    async def snapshot(self, name: Optional[str] = None) -> str:
        try:
            return await self._d.snapshot(name)
        except Exception as e:  # noqa: BLE001
            raise _wrap_error(e)

    async def revert(self, snapshot_id: str) -> None:
        try:
            await self._d.revert(snapshot_id)
        except Exception as e:  # noqa: BLE001
            raise _wrap_error(e)
        # Right after a restore the guest accepts only one control connection for a
        # brief window (see solari_core/transport.py). The old channel is dead, so
        # close it and dial again until a command succeeds.
        t0 = time.monotonic()
        while True:
            try:
                await self._d.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                await self._d.connect()
                await self.wait_ready(5.0)
                return
            except Exception as e:  # noqa: BLE001
                if time.monotonic() - t0 > self.backend.ready_timeout_s:
                    raise BackendError(f"machine {self.id} not reachable after revert: {e}") from e
                await asyncio.sleep(0.3)

    async def kill(self) -> None:
        if not self.alive:
            return
        self.alive = False
        try:
            await self._d.kill()
        except Exception as e:  # noqa: BLE001
            # fall back to the gateway route
            try:
                await self.backend.kill_machine(self.id)
            except Exception:  # noqa: BLE001
                raise _wrap_error(e)

    # ---------------------------------------------------------- controller
    async def exec(self, cmd: str, args: Optional[list[str]] = None, *, timeout_ms: Optional[int] = None,
                   cwd: Optional[str] = None, env: Optional[dict[str, str]] = None) -> ExecResult:
        r = await self._d.commands.run(cmd, args=list(args or []), cwd=cwd, env=env, timeout_ms=timeout_ms)
        return ExecResult(int(r.exitCode), r.stdout, r.stderr)

    async def read_file(self, path: str) -> bytes:
        return await self._d.files.read(path)

    async def write_file(self, path: str, data: bytes | str, mode: Optional[int] = None) -> None:
        await self._d.files.write(path, data, mode)

    # ---------------------------------------------------------- agent side
    def _gui(self) -> None:
        if self.kind != "desktop":
            raise BackendError("this machine is a headless sandbox: no agent channel (screenshot/mouse/keyboard)")

    async def screenshot(self) -> bytes:
        self._gui()
        return await self._d.screenshot(format="png")

    async def display_size(self) -> tuple[int, int]:
        try:
            s = await self._d.display.size()
            return int(s["w"]), int(s["h"])
        except Exception:  # noqa: BLE001
            return self.size

    async def click(self, x: int, y: int, *, button: str = "left") -> None:
        self._gui()
        await self._d.mouse.click(int(x), int(y), button=button)

    async def double_click(self, x: int, y: int) -> None:
        await self._d.mouse.double_click(int(x), int(y))

    async def move(self, x: int, y: int) -> None:
        await self._d.mouse.move(int(x), int(y))

    async def scroll(self, x: int, y: int, *, direction: str, amount: int) -> None:
        # The SDK's mouse.scroll takes a button code; xdotool wheel buttons are 4 (up) / 5 (down),
        # 6 (left) / 7 (right). The SDK maps names only for left/middle/right, so drive xdotool via
        # the agent channel's key/mouse primitives: move, then repeated wheel clicks through exec is
        # NOT allowed (controller channel). We therefore use keyboard paging for vertical scroll and
        # horizontal arrows for horizontal scroll, which every browser honours.
        await self._d.mouse.move(int(x), int(y))
        key = {"down": "Page_Down", "up": "Page_Up", "left": "Left", "right": "Right"}[direction]
        if direction in ("down", "up"):
            n = max(1, round(amount / 3))
        else:
            n = amount
        for _ in range(n):
            await self._d.keyboard.press([key])

    async def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        await self._d.mouse.drag({"x": int(x1), "y": int(y1)}, {"x": int(x2), "y": int(y2)})

    async def type_text(self, text: str) -> None:
        self._gui()
        await self._d.keyboard.type(text)

    async def press(self, keys: list[str]) -> None:
        self._gui()
        # The guest presses a list of keys one after another; a chord must be one xdotool
        # string ("ctrl+l"). Verified on a live desktop: ["ctrl", "a"] typed the letter a.
        await self._d.keyboard.press(["+".join(keys)] if len(keys) > 1 else list(keys))


class SolariBackend:
    name = "solari"

    def __init__(self, *, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 plan: Optional[str] = None, concurrency_cap: Optional[int] = None,
                 ready_timeout_s: float = 90.0, call_timeout_ms: Optional[int] = None,
                 kind: Optional[str] = None) -> None:
        #: "desktop" (GUI, paid plans) or "sandbox" (headless; Free plan). Env FORKLOOP_SOLARI_KIND.
        self.kind = (kind or os.environ.get("FORKLOOP_SOLARI_KIND", "desktop")).lower()
        if self.kind not in ("desktop", "sandbox"):
            raise BackendError("kind must be 'desktop' or 'sandbox'")
        self.api_key = api_key or os.environ.get("SOLARI_API_KEY", "")
        if not self.api_key:
            raise BackendError("SOLARI_API_KEY is not set")
        self.base_url = base_url or os.environ.get("SOLARI_BASE_URL", DEFAULT_BASE_URL)
        self.plan = (plan or os.environ.get("SOLARI_PLAN", "starter")).lower()
        self.concurrency_cap = concurrency_cap or int(os.environ.get("FORKLOOP_CONCURRENCY", 0)) or PLAN_CAPS.get(self.plan, 2)
        self.ready_timeout_s = ready_timeout_s
        from solari_sandbox import SandboxClient  # type: ignore

        self._client = SandboxClient(api_key=self.api_key, base_url=self.base_url, call_timeout_ms=call_timeout_ms)
        self.counters: dict[str, int] = {"create": 0}

    async def create(self, *, template: Optional[str] = None, from_snapshot: Optional[str] = None,
                     resolution: str = "1280x720", cpu: int = 2, mem_mb: int = 4096,
                     record: Optional[bool] = None, metadata: Optional[dict[str, str]] = None,
                     timeout_ms: int = 30 * 60_000, disk_gb: Optional[int] = None) -> SolariMachine:
        meta = {"forkloop": "1", **(metadata or {})}
        try:
            if self.kind == "sandbox":
                d = await self._client.create(
                    template=None if from_snapshot else (template if template and template != "default" else "base"),
                    from_snapshot=from_snapshot, cpu=cpu, mem_mb=mem_mb, disk_gb=None if from_snapshot else disk_gb,
                    metadata=meta, timeout_ms=timeout_ms, lifecycle={"onTimeout": "kill"})
            else:
                d = await self._client.create_desktop(
                    template=None if from_snapshot else (template or "default"),
                    from_snapshot=from_snapshot, resolution=resolution, cpu=cpu, mem_mb=mem_mb,
                    disk_gb=None if from_snapshot else disk_gb, record=record, metadata=meta, timeout_ms=timeout_ms,
                    lifecycle={"onTimeout": "kill"})
        except Exception as e:  # noqa: BLE001
            raise _wrap_error(e)
        self.counters["create"] += 1
        m = SolariMachine(d, self, parse_resolution(resolution), meta, kind=self.kind)
        await m.connect(wait_ready_s=self.ready_timeout_s)
        return m

    async def attach(self, machine_id: str, *, resolution: str = "1280x720") -> SolariMachine:
        """Re-attach to a running machine by id (e.g. resume a failed world build)."""
        try:
            view = await self._client.get(machine_id)
            if self.kind == "sandbox":
                d = await self._client.connect(machine_id)
            else:
                # Mirror SandboxClient.create_desktop's handle construction for an existing session:
                # the SDK has no connect_desktop(), only connect() → Sandbox (no GUI surface).
                from urllib.parse import quote as _q

                from solari_core.desktop import Desktop, DesktopConfig  # type: ignore
                from solari_core.types import CreateDesktopResponse  # type: ignore

                origin = self._client._t.ws_origin()
                session = CreateDesktopResponse(sessionId=view.sandboxId, controlUrl=f"{origin}/control/{_q(machine_id, safe='')}",
                                                streamUrl="", expiresAt=view.expiresAt)
                base = self._client._handle_config()
                cfg = DesktopConfig(headers=base.headers, hooks=base.hooks)
                if base.callTimeoutMs is not None:
                    cfg.callTimeoutMs = base.callTimeoutMs
                d = Desktop(session, cfg)
        except Exception as e:  # noqa: BLE001
            raise _wrap_error(e)
        m = SolariMachine(d, self, parse_resolution(resolution), dict(view.metadata or {}), kind=self.kind)
        await m.connect(wait_ready_s=self.ready_timeout_s)
        return m

    async def list_snapshots(self) -> list[SnapshotInfo]:
        views = await self._client.list_snapshots()
        return [SnapshotInfo(id=v.id, name=v.name, parent=v.parent, size_bytes=v.sizeBytes, created_at=v.createdAt,
                             kind=v.kind, template=v.template) for v in views]

    async def delete_snapshot(self, snapshot_id: str) -> None:
        await self._client.delete_snapshot(snapshot_id)

    async def list_machines(self, *, metadata: Optional[dict[str, str]] = None) -> list[MachineInfo]:
        out: list[MachineInfo] = []
        async for v in self._client.list_all(metadata=metadata, kind=self.kind):
            out.append(MachineInfo(id=v.sandboxId, state=v.state, metadata=dict(v.metadata or {}), created_at=""))
        return out

    async def kill_machine(self, machine_id: str) -> None:
        await self._client.kill(machine_id)

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["SolariBackend", "SolariMachine", "PLAN_CAPS", "DEFAULT_BASE_URL"]
