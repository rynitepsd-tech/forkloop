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
import hashlib
import datetime as dt
from typing import Any, Optional

from ..types import ExecResult, MachineInfo, SnapshotInfo
from .base import BackendError, CapacityError, ConcurrencyError, PlanGateError, parse_resolution, RevertTimeoutError

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
    async def _ready(self, timeout_s: float, *, connect: bool = False,
                     force_reconnect: bool = False) -> None:
        """One monotonic deadline covers every dial, RPC, backoff and reconnect.

        A control channel the guest dropped (``_on_close`` leaves ``_ws=None``) can
        never become healthy by polling: every ``call()`` raises "Not connected".
        Right after a restore the guest accepts one vsock control connection at a
        time and the host may be tearing down a sibling session, so the channel
        can drop more than once (2026-09-06: seed-201 fork spun 85 s on that
        error after its single redial). Redial after every transport error, and
        once after a non-transport health error. SDK reconnect() is a no-op on an
        open socket, so close first. Never swallow caller cancellation or equate
        an open WS with health.
        """
        last = None
        dial = "connect" if connect else ("reconnect" if force_reconnect else None)
        health_redials = 0
        try:
            async with asyncio.timeout(timeout_s):
                while True:
                    try:
                        if dial == "connect":
                            await self._d.connect()
                        elif dial == "reconnect":
                            await self._d.close()
                            await self._d.reconnect()
                        dial = None
                        async with asyncio.timeout(min(10.0, timeout_s)):
                            if self.kind != "desktop":
                                result = await self._d.commands.run("true", timeout_ms=10_000)
                                ready = result.exitCode == 0
                            else:
                                result = await self._d.health()
                                ready = bool(getattr(result, "ready", False))
                        if ready:
                            self.readiness_redials = health_redials
                            return
                        last = result
                    except Exception as exc:
                        last = exc
                        if self._is_connection_error(exc) or health_redials == 0:
                            health_redials += 1
                            dial = "reconnect"
                    if dial is None:  # a dial paces itself (SDK handshake retry); polls back off
                        await asyncio.sleep(0.5)
        except TimeoutError as exc:
            raise BackendError(f"machine {self.id} readiness deadline {timeout_s}s expired "
                               f"(last={last!r}, redials={health_redials})") from exc

    async def connect(self, *, wait_ready_s: float = 60.0) -> None:
        await self._ready(wait_ready_s, connect=True)

    async def wait_ready(self, timeout_s: float = 60.0) -> None:
        await self._ready(timeout_s)

    async def healthy(self) -> bool:
        try:
            if self.kind != "desktop":
                return (await self._d.commands.run("true", timeout_ms=10_000)).exitCode == 0
            h = await self._d.health()
            return bool(getattr(h, "ready", False))
        except Exception:  # noqa: BLE001
            return False

    async def snapshot(self, name: Optional[str] = None) -> str:
        from ..spending import load_solari_pricing
        pricing = load_solari_pricing(getattr(self.backend, "pricing_file", None))
        if dt.date.today() >= pricing.storage_starts_on:
            raise BackendError(
                "snapshot creation refused: Solari now bills retained storage and this SDK has no "
                "provider-enforced snapshot expiry. A compute pricing review does not bound indefinite storage; "
                "use an existing golden snapshot in fork mode (no branch snapshots).")
        try:
            return await self._d.snapshot(name)
        except Exception as e:  # noqa: BLE001
            raise _wrap_error(e)

    async def revert(self, snapshot_id: str) -> None:
        try:
            async with asyncio.timeout(self.backend.revert_ready_timeout_s):
                await self._d.revert(snapshot_id)
                await self._ready(self.backend.revert_ready_timeout_s, force_reconnect=True)
        except (TimeoutError, BackendError) as exc:
            raise RevertTimeoutError(f"machine {self.id}: revert/readiness deadline expired: {exc}") from exc
        except Exception as exc:
            raise _wrap_error(exc)

    async def kill(self) -> None:
        if not self.alive:
            return
        try:
            await self._d.kill()
        except Exception as e:  # noqa: BLE001
            # fall back to the gateway route
            try:
                await self.backend.kill_machine(self.id)
            except Exception:  # noqa: BLE001
                raise _wrap_error(e)
        self.alive = False  # failed cleanup remains retryable
        self.backend.record_resource_closed(self.id)

    async def refresh_lifetime(self, timeout_ms: int = 30 * 60_000) -> Any:
        """Re-arm the provider kill-on-timeout window (``POST /sandboxes/:id/timeout``).

        A machine reused across episodes keeps its 30-minute guard; the controller
        must re-arm it before each episode, so an abandoned machine still dies.
        """
        if not 0 < timeout_ms <= 30 * 60_000:
            raise BackendError("kill-on-timeout refresh must stay within 30 minutes")
        try:
            return await self._d.set_timeout(timeout_ms)
        except Exception as e:  # noqa: BLE001
            raise _wrap_error(e)

    # ---------------------------------------------------------- channel resilience
    #: Times the control channel dropped mid-episode and was re-dialled.
    reconnects = 0
    reconnect_timeout_s = 30.0

    @staticmethod
    def _is_connection_error(e: BaseException) -> bool:
        name = type(e).__name__
        return (name in ("ConnectionError", "ConnectionClosed", "ConnectionClosedError", "ConnectionClosedOK",
                         "WebSocketException", "InvalidState")
                or "Not connected" in str(e) or "connection is closed" in str(e).lower())

    async def _reconnect(self) -> None:
        await self._ready(self.reconnect_timeout_s, force_reconnect=True)
        self.reconnects += 1

    async def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run one channel operation; on a dropped channel reconnect and retry it once."""
        try:
            return await fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if not self.alive or not self._is_connection_error(e):
                raise
            await self._reconnect()
            return await fn(*args, **kwargs)

    # ---------------------------------------------------------- controller
    async def exec(self, cmd: str, args: Optional[list[str]] = None, *, timeout_ms: Optional[int] = None,
                   cwd: Optional[str] = None, env: Optional[dict[str, str]] = None) -> ExecResult:
        r = await self._call(self._d.commands.run, cmd, args=list(args or []), cwd=cwd, env=env, timeout_ms=timeout_ms)
        return ExecResult(int(r.exitCode), r.stdout, r.stderr)

    async def read_file(self, path: str) -> bytes:
        return await self._call(self._d.files.read, path)

    async def write_file(self, path: str, data: bytes | str, mode: Optional[int] = None) -> None:
        await self._call(self._d.files.write, path, data, mode)

    # ---------------------------------------------------------- agent side
    def _gui(self) -> None:
        if self.kind != "desktop":
            raise BackendError("this machine is a headless sandbox: no agent channel (screenshot/mouse/keyboard)")

    async def screenshot(self) -> bytes:
        self._gui()
        return await self._call(self._d.screenshot, format="png")

    async def display_size(self) -> tuple[int, int]:
        try:
            s = await self._d.display.size()
            return int(s["w"]), int(s["h"])
        except Exception:  # noqa: BLE001
            return self.size

    async def click(self, x: int, y: int, *, button: str = "left") -> None:
        self._gui()
        await self._call(self._d.mouse.click, int(x), int(y), button=button)

    async def double_click(self, x: int, y: int) -> None:
        await self._call(self._d.mouse.double_click, int(x), int(y))

    async def move(self, x: int, y: int) -> None:
        await self._call(self._d.mouse.move, int(x), int(y))

    async def scroll(self, x: int, y: int, *, direction: str, amount: int) -> None:
        # The SDK's mouse.scroll takes a button code; xdotool wheel buttons are 4 (up) / 5 (down),
        # 6 (left) / 7 (right). The SDK maps names only for left/middle/right, so drive xdotool via
        # the agent channel's key/mouse primitives: move, then repeated wheel clicks through exec is
        # NOT allowed (controller channel). We therefore use keyboard paging for vertical scroll and
        # horizontal arrows for horizontal scroll, which every browser honours.
        await self._call(self._d.mouse.move, int(x), int(y))
        key = {"down": "Page_Down", "up": "Page_Up", "left": "Left", "right": "Right"}[direction]
        if direction in ("down", "up"):
            n = max(1, round(amount / 3))
        else:
            n = amount
        for _ in range(n):
            await self._call(self._d.keyboard.press, [key])

    async def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        await self._call(self._d.mouse.drag, {"x": int(x1), "y": int(y1)}, {"x": int(x2), "y": int(y2)})

    async def type_text(self, text: str) -> None:
        self._gui()
        await self._call(self._d.keyboard.type, text)

    async def press(self, keys: list[str]) -> None:
        self._gui()
        # The guest presses a list of keys one after another; a chord must be one xdotool
        # string ("ctrl+l"). Verified on a live desktop: ["ctrl", "a"] typed the letter a.
        await self._call(self._d.keyboard.press, ["+".join(keys)] if len(keys) > 1 else list(keys))


class SolariBackend:
    name = "solari"

    def __init__(self, *, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 plan: Optional[str] = None, concurrency_cap: Optional[int] = None,
                 ready_timeout_s: float = 90.0, revert_ready_timeout_s: float = 240.0,
                 call_timeout_ms: Optional[int] = None,
                 kind: Optional[str] = None, session_ledger: Optional[str] = None,
                 pricing_file: Optional[str] = None) -> None:
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
        #: After ``revert()`` the guest can take the slow restore mode (70–160 s measured 2026-09-03);
        #: 90 s discarded a healthy machine and flipped a whole run to fork mode (runs/luna-v7-fam1-s0-9).
        self.revert_ready_timeout_s = revert_ready_timeout_s
        try:
            from solari_sandbox import SandboxClient  # type: ignore
        except ImportError as exc:
            raise BackendError("Solari SDK unavailable; install solari-sandbox>=0.2.0 and solari-desktop>=0.2.0") from exc

        self._client = SandboxClient(api_key=self.api_key, base_url=self.base_url, call_timeout_ms=call_timeout_ms)
        self.counters: dict[str, int] = {"create": 0}
        self.session_ledger = session_ledger or os.environ.get("FORKLOOP_SESSION_LEDGER")
        self.pricing_file = pricing_file or os.environ.get("FORKLOOP_SOLARI_PRICING_FILE")
        self.resources: dict[str, dict] = {}

    def record_resource_closed(self, machine_id: str) -> None:
        resource = self.resources.get(machine_id)
        if not resource or resource.get("closed"):
            return
        from ..spending import SessionLedger
        elapsed = time.time() - resource["started_at"]
        resource.update(closed=True, lifetime_seconds=elapsed,
                        estimated_compute_usd=elapsed / 3600 * resource["hourly_usd"])
        # An observed lifetime can exceed the assumed provider cap. Retain that
        # larger exposure and block further spending; never call it an invoice.
        ledger = SessionLedger(self.session_ledger)
        ledger.retain_exposure(resource["operation"], resource["estimated_compute_usd"],
                               evidence={"machine_id": machine_id, **resource})
        ledger.reconcile(resource["operation"], None, status="resource_closed_usage_pending",
                         evidence={"machine_id": machine_id, **resource})

    async def create(self, *, template: Optional[str] = None, from_snapshot: Optional[str] = None,
                     resolution: str = "1280x720", cpu: int = 2, mem_mb: int = 4096,
                     record: Optional[bool] = None, metadata: Optional[dict[str, str]] = None,
                     timeout_ms: int = 30 * 60_000, disk_gb: Optional[int] = None) -> SolariMachine:
        from ..spending import SessionLedger, load_solari_pricing
        if not self.session_ledger:
            raise BackendError("Solari creates require FORKLOOP_SESSION_LEDGER; initialize it with forkloop ledger --create")
        if self.plan != "starter":
            raise BackendError("guarded Solari execution supports SOLARI_PLAN=starter only; verify the account plan manually")
        if type(timeout_ms) is not int or not 0 < timeout_ms <= 30 * 60_000 or record:
            raise BackendError("guarded probes require <=30 minute kill-on-idle timeout and no recording")
        try:
            pricing = load_solari_pricing(getattr(self, "pricing_file", None))
            hourly = pricing.hourly(cpu, mem_mb, desktop=self.kind == "desktop")
        except ValueError as exc:
            raise BackendError(str(exc)) from exc
        ledger = SessionLedger(self.session_ledger)
        operation = ledger.reserve("solari", pricing.reservation(hourly), label="machine_create",
                                   evidence={"cpu": cpu, "mem_mb": mem_mb, "timeout_ms": timeout_ms,
                                             "hourly_usd": hourly, "storage_usd": 0,
                                             "pricing": pricing.public_info()})
        started = time.time()
        meta = {"forkloop": "1", **(metadata or {}), "spend_operation": operation,
                "forkloop_session": hashlib.sha256(str(ledger.path.resolve()).encode()).hexdigest()[:16]}
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
        except BaseException as e:  # a timeout can leave a billed remote resource
            ledger.reconcile(operation, None, status="create_uncertain",
                             evidence={"error_type": type(e).__name__, "metadata": meta})
            raise _wrap_error(e)
        self.counters["create"] += 1
        self.resources[d.id] = {"operation": operation, "started_at": started,
                                "hourly_usd": hourly, "closed": False}
        ledger.reconcile(operation, None, status="resource_running",
                         evidence={"machine_id": d.id, **self.resources[d.id]})
        m = SolariMachine(d, self, parse_resolution(resolution), meta, kind=self.kind)
        # Include failure cleanup within the post-create readiness deadline.
        deadline = asyncio.get_running_loop().time() + self.ready_timeout_s
        cleanup_s = min(5.0, self.ready_timeout_s / 4)
        try:
            await m.connect(wait_ready_s=self.ready_timeout_s - cleanup_s)
        except BaseException:
            # Cancellation must also clean up; failed cleanup stays pending in
            # the ledger for the session watchdog's gateway-only retry.
            try:
                async with asyncio.timeout_at(min(deadline, asyncio.get_running_loop().time() + cleanup_s)):
                    await m.kill()
            except Exception as exc:
                self.resources[d.id]["cleanup_error"] = type(exc).__name__
            raise
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
        self.record_resource_closed(machine_id)

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["SolariBackend", "SolariMachine", "PLAN_CAPS", "DEFAULT_BASE_URL"]
