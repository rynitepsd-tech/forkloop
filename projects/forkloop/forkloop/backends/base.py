"""Backend / Machine protocols (docs/contracts.md §2)."""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from ..types import ExecResult, MachineInfo, SnapshotInfo


@runtime_checkable
class Machine(Protocol):
    id: str
    stream_url: Optional[str]
    backend_name: str
    capabilities: frozenset[str]   # subset of {"gui", "http", "shell"}

    # controller channel
    async def exec(self, cmd: str, args: Optional[list[str]] = None, *, timeout_ms: Optional[int] = None,
                   cwd: Optional[str] = None, env: Optional[dict[str, str]] = None) -> ExecResult: ...
    async def read_file(self, path: str) -> bytes: ...
    async def write_file(self, path: str, data: bytes | str, mode: Optional[int] = None) -> None: ...
    async def snapshot(self, name: Optional[str] = None) -> str: ...
    async def revert(self, snapshot_id: str) -> None: ...
    async def kill(self) -> None: ...
    async def healthy(self) -> bool: ...

    # agent channel
    async def screenshot(self) -> bytes: ...
    async def display_size(self) -> tuple[int, int]: ...
    async def click(self, x: int, y: int, *, button: str = "left") -> None: ...
    async def double_click(self, x: int, y: int) -> None: ...
    async def move(self, x: int, y: int) -> None: ...
    async def scroll(self, x: int, y: int, *, direction: str, amount: int) -> None: ...
    async def drag(self, x1: int, y1: int, x2: int, y2: int) -> None: ...
    async def type_text(self, text: str) -> None: ...
    async def press(self, keys: list[str]) -> None: ...


@runtime_checkable
class Backend(Protocol):
    name: str
    concurrency_cap: int

    async def create(self, *, template: Optional[str] = None, from_snapshot: Optional[str] = None,
                     resolution: str = "1280x720", cpu: int = 2, mem_mb: int = 4096,
                     record: Optional[bool] = None, metadata: Optional[dict[str, str]] = None,
                     timeout_ms: int = 30 * 60_000) -> Machine: ...
    async def list_snapshots(self) -> list[SnapshotInfo]: ...
    async def delete_snapshot(self, snapshot_id: str) -> None: ...
    async def list_machines(self, *, metadata: Optional[dict[str, str]] = None) -> list[MachineInfo]: ...
    async def kill_machine(self, machine_id: str) -> None: ...
    async def close(self) -> None: ...


class BackendError(RuntimeError):
    pass


class ConcurrencyError(BackendError):
    """The plan's concurrent-machine cap was hit (HTTP 429 on Solari)."""


class PlanGateError(BackendError):
    """The feature needs a paid plan (HTTP 402 on Solari)."""


class CapacityError(BackendError):
    """No warm hosts available right now (HTTP 503 on Solari); retry later."""


def parse_resolution(res: str) -> tuple[int, int]:
    try:
        w, h = res.lower().split("x")
        return int(w), int(h)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"bad resolution {res!r}; expected WIDTHxHEIGHT") from e


def apply_action(machine: Machine, action: Any):
    """Return the coroutine that performs ``action`` on ``machine`` (agent channel only)."""
    t = action.type
    if t == "click":
        return machine.click(action.x, action.y, button=action.button or "left")
    if t == "double_click":
        return machine.double_click(action.x, action.y)
    if t == "right_click":
        return machine.click(action.x, action.y, button="right")
    if t == "move":
        return machine.move(action.x, action.y)
    if t == "scroll":
        return machine.scroll(action.x, action.y, direction=action.direction or "down", amount=action.amount or 3)
    if t == "drag":
        return machine.drag(action.x, action.y, action.x2, action.y2)
    if t == "type":
        return machine.type_text(action.text or "")
    if t == "key":
        return machine.press(list(action.keys or ()))
    if t == "wait":
        import asyncio

        return asyncio.sleep(float(action.seconds or 0))
    if t == "done":
        import asyncio

        return asyncio.sleep(0)
    raise ValueError(f"cannot apply action type {t!r}")


__all__ = ["Machine", "Backend", "BackendError", "ConcurrencyError", "PlanGateError", "CapacityError",
           "parse_resolution", "apply_action"]
