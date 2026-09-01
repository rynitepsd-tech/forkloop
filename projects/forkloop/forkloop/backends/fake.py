"""In-process fake backend with real snapshot / revert / fork semantics.

Each machine is a directory on the local disk. ``snapshot()`` copies it,
``revert()`` restores it, ``create(from_snapshot=...)`` clones it. Commands run
locally with VM paths rewritten under the machine root, so ``sqlite3`` /
``python3`` seeding and oracle queries behave exactly as they do in the VM.

The GUI is simulated by an optional :class:`GuiSim` supplied by a world (the
toy world uses one). Without it, screenshots are a labelled blank frame.

Latencies can be injected (``latency={"revert": 0.5}``) so the reset
benchmark and pool logic can be exercised offline. Numbers produced this way
are never Solari numbers and the benchmark labels them as fake.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

from ..types import ExecResult, MachineInfo, SnapshotInfo
from .base import BackendError, ConcurrencyError, parse_resolution

VM_PREFIXES = ("/var/", "/etc/", "/home/", "/opt/", "/tmp/", "/root/", "/srv/", "/usr/local/forkloop/")
_PATH_RE = re.compile(r"(?<![\w./-])(" + "|".join(re.escape(p) for p in VM_PREFIXES) + r")")


class GuiSim(Protocol):
    """World-supplied GUI simulation for the fake backend."""

    def render(self, root: Path, size: tuple[int, int]) -> bytes: ...
    def apply(self, root: Path, action: dict[str, Any], size: tuple[int, int]) -> None: ...


def _blank_png(size: tuple[int, int], lines: list[str]) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, (245, 245, 245))
    d = ImageDraw.Draw(img)
    y = 20
    for ln in lines:
        d.text((20, y), ln, fill=(20, 20, 20))
        y += 18
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class FakeMachine:
    id: str
    root: Path
    backend: "FakeBackend"
    size: tuple[int, int]
    gui: Optional[GuiSim] = None
    stream_url: Optional[str] = None
    backend_name: str = "fake"
    capabilities: frozenset[str] = frozenset({"shell", "gui"})
    alive: bool = True
    metadata: dict[str, str] = field(default_factory=dict)
    action_log: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------ controller
    def rewrite(self, s: str) -> str:
        return _PATH_RE.sub(lambda m: str(self.root) + m.group(1), s)

    async def exec(self, cmd: str, args: Optional[list[str]] = None, *, timeout_ms: Optional[int] = None,
                   cwd: Optional[str] = None, env: Optional[dict[str, str]] = None) -> ExecResult:
        self._check_alive()
        argv = [cmd, *[self.rewrite(a) for a in (args or [])]]
        # make sure rewritten target directories exist so tools can create files
        for a in argv[1:]:
            if a.startswith(str(self.root)) and not a.endswith("/") and "/" in a:
                Path(a).parent.mkdir(parents=True, exist_ok=True)
        full_env = {**os.environ, **(env or {}), "FORKLOOP_FAKE_ROOT": str(self.root)}
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=self.rewrite(cwd) if cwd else str(self.root),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=full_env)
        except FileNotFoundError as e:
            return ExecResult(127, "", f"{cmd}: not found ({e})")
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=(timeout_ms or 300_000) / 1000)
        except asyncio.TimeoutError:
            proc.kill()
            return ExecResult(124, "", "timeout")
        return ExecResult(proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))

    def _local(self, path: str) -> Path:
        if not path.startswith("/"):
            raise BackendError("VM paths must be absolute")
        rewritten = self.rewrite(path)
        if rewritten == path:  # not under a VM prefix: keep it inside the root anyway
            rewritten = str(self.root) + path
        return Path(rewritten)

    async def read_file(self, path: str) -> bytes:
        self._check_alive()
        return self._local(path).read_bytes()

    async def write_file(self, path: str, data: bytes | str, mode: Optional[int] = None) -> None:
        self._check_alive()
        p = self._local(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
        if mode is not None:
            p.chmod(mode)

    async def snapshot(self, name: Optional[str] = None) -> str:
        self._check_alive()
        await self.backend._sleep("snapshot")
        sid = "snap_" + uuid.uuid4().hex[:12]
        dst = self.backend.snap_dir / sid
        shutil.copytree(self.root, dst, symlinks=True)
        self.backend.snapshots[sid] = SnapshotInfo(id=sid, name=name, parent=self.id, size_bytes=_dir_size(dst),
                                                   created_at=_now_iso(), kind="desktop", template="fake")
        return sid

    async def revert(self, snapshot_id: str) -> None:
        self._check_alive()
        src = self.backend.snap_dir / snapshot_id
        if not src.exists():
            raise BackendError(f"unknown snapshot {snapshot_id}")
        await self.backend._sleep("revert")
        tmp = self.root.with_name(self.root.name + ".old")
        if tmp.exists():
            shutil.rmtree(tmp)
        self.root.rename(tmp)
        shutil.copytree(src, self.root, symlinks=True)
        shutil.rmtree(tmp, ignore_errors=True)
        self.action_log.append({"type": "_revert", "snapshot": snapshot_id})

    async def kill(self) -> None:
        if not self.alive:
            return
        self.alive = False
        self.backend._release(self)
        shutil.rmtree(self.root, ignore_errors=True)

    async def healthy(self) -> bool:
        return self.alive

    def _check_alive(self) -> None:
        if not self.alive:
            raise BackendError(f"machine {self.id} is gone")

    # ------------------------------------------------------------ agent side
    async def screenshot(self) -> bytes:
        self._check_alive()
        await self.backend._sleep("screenshot")
        if self.gui is not None:
            return self.gui.render(self.root, self.size)
        return _blank_png(self.size, [f"forkloop fake backend  machine={self.id}",
                                      f"actions so far: {len([a for a in self.action_log if not a['type'].startswith('_')])}",
                                      "no GUI simulation attached to this world"])

    async def display_size(self) -> tuple[int, int]:
        return self.size

    async def _act(self, action: dict[str, Any]) -> None:
        self._check_alive()
        await self.backend._sleep("action")
        self.action_log.append(action)
        if self.gui is not None:
            self.gui.apply(self.root, action, self.size)

    async def click(self, x: int, y: int, *, button: str = "left") -> None:
        await self._act({"type": "click", "x": x, "y": y, "button": button})

    async def double_click(self, x: int, y: int) -> None:
        await self._act({"type": "double_click", "x": x, "y": y})

    async def move(self, x: int, y: int) -> None:
        await self._act({"type": "move", "x": x, "y": y})

    async def scroll(self, x: int, y: int, *, direction: str, amount: int) -> None:
        await self._act({"type": "scroll", "x": x, "y": y, "direction": direction, "amount": amount})

    async def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        await self._act({"type": "drag", "x": x1, "y": y1, "x2": x2, "y2": y2})

    async def type_text(self, text: str) -> None:
        await self._act({"type": "type", "text": text})

    async def press(self, keys: list[str]) -> None:
        await self._act({"type": "key", "keys": list(keys)})


class FakeBackend:
    name = "fake"

    def __init__(self, *, base_dir: Optional[str | Path] = None, concurrency_cap: int = 2,
                 latency: Optional[dict[str, float]] = None, gui_factory: Any = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path(tempfile.mkdtemp(prefix="forkloop-fake-"))
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.snap_dir = self.base_dir / "snapshots"
        self.snap_dir.mkdir(exist_ok=True)
        self.concurrency_cap = concurrency_cap
        self.latency = latency or {}
        self.gui_factory = gui_factory
        self.machines: dict[str, FakeMachine] = {}
        self.snapshots: dict[str, SnapshotInfo] = {}
        self.counters: dict[str, int] = {"create": 0, "snapshot": 0, "revert": 0}

    async def _sleep(self, op: str) -> None:
        s = self.latency.get(op, 0.0)
        if s:
            await asyncio.sleep(s)

    def _release(self, m: FakeMachine) -> None:
        self.machines.pop(m.id, None)

    async def create(self, *, template: Optional[str] = None, from_snapshot: Optional[str] = None,
                     resolution: str = "1280x720", cpu: int = 2, mem_mb: int = 4096,
                     record: Optional[bool] = None, metadata: Optional[dict[str, str]] = None,
                     timeout_ms: int = 30 * 60_000, disk_gb: Optional[int] = None) -> FakeMachine:
        live = [m for m in self.machines.values() if m.alive]
        if len(live) >= self.concurrency_cap:
            raise ConcurrencyError(f"fake concurrency cap {self.concurrency_cap} reached")
        await self._sleep("create")
        mid = "fake_" + uuid.uuid4().hex[:10]
        root = self.base_dir / mid
        if from_snapshot:
            src = self.snap_dir / from_snapshot
            if not src.exists():
                raise BackendError(f"unknown snapshot {from_snapshot}")
            shutil.copytree(src, root, symlinks=True)
        else:
            root.mkdir(parents=True)
            (root / "etc").mkdir()
            (root / "etc" / "forkloop-template").write_text(template or "default")
        m = FakeMachine(id=mid, root=root, backend=self, size=parse_resolution(resolution),
                        gui=self.gui_factory() if self.gui_factory else None,
                        metadata=dict(metadata or {}))
        self.machines[mid] = m
        self.counters["create"] += 1
        return m

    async def list_snapshots(self) -> list[SnapshotInfo]:
        return list(self.snapshots.values())

    async def delete_snapshot(self, snapshot_id: str) -> None:
        self.snapshots.pop(snapshot_id, None)
        shutil.rmtree(self.snap_dir / snapshot_id, ignore_errors=True)

    async def list_machines(self, *, metadata: Optional[dict[str, str]] = None) -> list[MachineInfo]:
        out = []
        for m in self.machines.values():
            if metadata and any(m.metadata.get(k) != v for k, v in metadata.items()):
                continue
            out.append(MachineInfo(id=m.id, state="running" if m.alive else "gone", metadata=dict(m.metadata),
                                   created_at=_iso(m.created_at)))
        return out

    async def kill_machine(self, machine_id: str) -> None:
        m = self.machines.get(machine_id)
        if m:
            await m.kill()

    async def close(self) -> None:
        for m in list(self.machines.values()):
            await m.kill()

    def cleanup(self) -> None:
        shutil.rmtree(self.base_dir, ignore_errors=True)


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _now_iso() -> str:
    return _iso(time.time())


def _iso(t: float) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(t, tz=_dt.timezone.utc).isoformat(timespec="seconds")


__all__ = ["FakeBackend", "FakeMachine", "GuiSim"]
