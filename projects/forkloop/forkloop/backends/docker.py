"""Docker backend: claims-ops-v1 (or any world image built the same way) in local containers.

One machine = one container started from the golden image (``worlds/claims_ops_v1/docker/``). The
contract (docs/contracts.md §2) maps onto the docker CLI plus a small in-container helper:

| Contract | Docker |
| --- | --- |
| ``Backend.create(from_snapshot=img)`` | ``docker run -d --pull never img`` (golden image or a committed checkpoint), then wait for ``/run/forkloop/ready`` |
| ``Machine.exec/read_file/write_file`` | the in-container agent (``agent.py``) over one persistent ``docker exec -i`` pipe, as root |
| ``Machine.screenshot`` / mouse / keyboard | the same pipe: XGetImage of Xvfb ``:0`` → PNG; ``xdotool`` for input |
| ``Machine.snapshot`` | DB flush, then ``docker commit --pause`` → image ``<repo>:snap-<hex>`` |
| ``Machine.revert(img)`` | ``docker rm -f`` + ``docker run`` of ``img`` under the same container name (the machine id) |
| ``Machine.kill`` / ``Backend.kill_machine`` | ``docker rm -f`` |
| ``Backend.list_machines`` / ``list_snapshots`` | ``docker ps`` / ``docker images`` filtered by ``forkloop`` labels |

**Snapshots are filesystem checkpoints, not running-process checkpoints** (``snapshot_semantics =
"filesystem"``). Restoring one boots the container again: every service restarts, Chrome starts afresh
from its profile on disk (persistent cookies survive, tabs, in-memory state, scroll positions, unsaved
form input and the OpenEMR PHP session cookie do not). A Solari snapshot is a memory snapshot; a Docker
one is closer to a power cycle of a machine whose databases were flushed first. The golden image is
built so that this is exactly the golden state (``build_image.sh``: Chrome closed cleanly after logging
in, so the portal's 30-day session cookie is on disk).

Configuration (environment, like the other backends):
``FORKLOOP_DOCKER_IMAGE`` golden image (default ``forkloop/claims-ops-v1:1``);
``FORKLOOP_DOCKER_HOST`` docker daemon (``DOCKER_HOST`` syntax, e.g. ``ssh://user@box``; default local);
``FORKLOOP_DOCKER_CONCURRENCY`` running-world cap on that daemon (default 8);
``FORKLOOP_DOCKER_OWNER`` value of the ``forkloop_owner`` label (default ``forkloop``);
``FORKLOOP_DOCKER_SECCOMP`` ``unconfined`` (default; Chrome's own namespace sandbox needs it) or a profile path;
``FORKLOOP_DOCKER_NETWORK`` docker network (default: docker's default bridge);
``FORKLOOP_DOCKER_SNAPSHOT_DB`` ``flush`` (default) | ``stop`` | ``none`` — see ``DockerMachine.snapshot``;
``FORKLOOP_DOCKER_READY_TIMEOUT_S`` boot deadline per container (default 120);
``FORKLOOP_DOCKER_PNG_LEVEL`` zlib level of screenshots (default 1);
``FORKLOOP_WORLD_CLOCK`` the world clock containers start at (ISO time or ``real``; default: the image's,
2026-09-07T09:00:00Z from image :3 on — every process in the container runs on it, libfaketime).
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import itertools
import json
import os
import re
import time
import uuid
from typing import Any, Optional

from ..types import ExecResult, MachineInfo, SnapshotInfo
from .base import BackendError, ConcurrencyError, RevertTimeoutError, parse_resolution

DEFAULT_IMAGE = "forkloop/claims-ops-v1:1"
AGENT_PATH = "/usr/local/forkloop/agent.py"
SVC_PATH = "/usr/local/forkloop/svc.sh"
LABEL_WORLD = "forkloop.world_container"
META_PREFIX = "forkloop.meta."
SNAPSHOT_SEMANTICS = "filesystem"
_SOLARI_SNAPSHOT_ID = re.compile(r"^snap_[a-z0-9]+$")
_STATE = {"running": "running", "created": "starting", "restarting": "starting", "paused": "paused",
          "exited": "stopped", "dead": "stopped", "removing": "stopped"}
_BUTTONS = {"left": 1, "middle": 2, "right": 3}
_LINE_LIMIT = 256 * 1024 * 1024  # one agent response line (a base64 PNG or file)


class AgentError(BackendError):
    """The in-container agent answered with an error."""


class ChannelClosed(BackendError):
    """The ``docker exec -i`` pipe to the agent is gone (container stopped or removed)."""


# --------------------------------------------------------------------------- docker CLI
class DockerCLI:
    """``docker`` invocations through asyncio subprocesses (no SDK dependency)."""

    def __init__(self, host: Optional[str] = None, binary: str = "docker") -> None:
        self.host = host
        self.binary = binary

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.host:
            env["DOCKER_HOST"] = self.host
        return env

    async def run(self, *args: str, timeout: float = 120.0, input: Optional[bytes] = None,
                  check: bool = True) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary, *args, stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self.env())
        except FileNotFoundError as e:
            raise BackendError(f"docker CLI not found ({e}); install Docker or set FORKLOOP_DOCKER_HOST") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(input), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise BackendError(f"docker {' '.join(args[:3])} timed out after {timeout:.0f}s") from None
        rc = proc.returncode or 0
        o, e = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        if check and rc != 0:
            raise BackendError(f"docker {' '.join(args[:3])} failed ({rc}): {e.strip()[:500]}")
        return rc, o, e

    async def spawn(self, *args: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            self.binary, *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=self.env(), limit=_LINE_LIMIT)


# --------------------------------------------------------------------------- agent channel
class _Agent:
    """One persistent ``docker exec -i <container> python3 -u agent.py``: JSON lines in, JSON lines out,
    matched by id, so concurrent calls on one machine are fine."""

    def __init__(self, cli: DockerCLI, container: str) -> None:
        self.cli = cli
        self.container = container
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.pending: dict[int, asyncio.Future] = {}
        self.ids = itertools.count(1)
        self.closed = False
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._tasks: list[asyncio.Task] = []
        self._wlock: Optional[asyncio.Lock] = None
        self.stderr_tail = b""

    async def start(self, timeout: float = 20.0) -> None:
        self.loop = asyncio.get_running_loop()
        self._wlock = asyncio.Lock()
        self.proc = await self.cli.spawn("exec", "-i", self.container, "python3", "-u", AGENT_PATH)
        self._tasks = [asyncio.create_task(self._read_loop()), asyncio.create_task(self._drain_stderr())]
        await self.call("ping", timeout)

    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    resp = json.loads(line)
                except ValueError:
                    continue
                fut = self.pending.pop(resp.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(resp)
        except Exception as e:  # noqa: BLE001 - a broken pipe ends the channel either way
            self.stderr_tail += f"\nreader: {type(e).__name__}: {e}".encode()
        finally:
            self.closed = True
            err = ChannelClosed(f"agent channel to {self.container} closed "
                                f"({self.stderr_tail.decode('utf-8', 'replace').strip()[-300:] or 'no stderr'})")
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(err)
            self.pending.clear()

    async def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while True:
            chunk = await self.proc.stderr.read(4096)
            if not chunk:
                return
            self.stderr_tail = (self.stderr_tail + chunk)[-2000:]

    async def call(self, op: str, timeout: float, /, **kw: Any) -> dict[str, Any]:
        """Send one request; ``timeout`` bounds the wait for its answer (``kw`` are the request fields)."""
        if self.closed or self.proc is None or self.proc.stdin is None:
            raise ChannelClosed(f"agent channel to {self.container} is closed")
        rid = next(self.ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        data = (json.dumps({"id": rid, "op": op, **kw}, separators=(",", ":")) + "\n").encode()
        try:
            assert self._wlock is not None
            async with self._wlock:
                self.proc.stdin.write(data)
                await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
            self.pending.pop(rid, None)
            self.closed = True
            raise ChannelClosed(f"agent channel to {self.container} broke: {e}") from e
        try:
            resp = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(rid, None)
            raise BackendError(f"agent {op} on {self.container} timed out after {timeout:.0f}s") from None
        if not resp.get("ok"):
            raise AgentError(str(resp.get("error") or "agent error"))
        return resp

    async def close(self) -> None:
        self.closed = True
        proc = self.proc
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
        for t in self._tasks:
            t.cancel()
        self._tasks = []


# --------------------------------------------------------------------------- machine
class DockerMachine:
    backend_name = "docker"
    capabilities = frozenset({"shell", "gui", "http"})
    snapshot_semantics = SNAPSHOT_SEMANTICS
    stream_url: Optional[str] = None

    def __init__(self, backend: "DockerBackend", name: str, image: str, size: tuple[int, int],
                 metadata: dict[str, str], *, cpu: Optional[int] = None, mem_mb: Optional[int] = None) -> None:
        self.backend = backend
        self.id = name
        self.image = image          # the image the current container was started from
        self.size = size
        self.metadata = metadata
        self.cpu, self.mem_mb = cpu, mem_mb
        self.alive = True
        self.created_at = time.time()
        self._agent: Optional[_Agent] = None
        self._agent_lock: Optional[asyncio.Lock] = None
        self._agent_lock_loop: Optional[asyncio.AbstractEventLoop] = None
        #: per-boot timings: controller side (docker rm / run / wait for ready) and the image's own
        #: /run/forkloop/boot.json stages
        self.last_boot: dict[str, Any] = {}
        self._rm_seconds: Optional[float] = None
        self._run_seconds = 0.0

    # ---------------------------------------------------------------- container lifecycle
    def _run_args(self, image: str) -> list[str]:
        b = self.backend
        args = ["run", "-d", "--name", self.id, "--pull", "never", "--shm-size", "512m", "--stop-timeout", "10",
                "--label", "forkloop=1", "--label", f"forkloop_owner={b.owner}", "--label", f"{LABEL_WORLD}=1",
                "--label", f"forkloop.image={image}", "--label", f"forkloop.snapshot_semantics={SNAPSHOT_SEMANTICS}"]
        for k, v in sorted(self.metadata.items()):
            args += ["--label", f"{META_PREFIX}{k}={v}"]
        if b.seccomp:
            args += ["--security-opt", f"seccomp={b.seccomp}"]
        if b.network:
            args += ["--network", b.network]
        if b.limits and self.cpu:
            args += ["--cpus", str(self.cpu)]
        if b.limits and self.mem_mb:
            args += ["--memory", f"{int(self.mem_mb)}m"]
        if self.size != (1280, 720):
            args += ["-e", f"FORKLOOP_SCREEN={self.size[0]}x{self.size[1]}"]
        if b.world_clock:
            args += ["-e", f"FORKLOOP_WORLD_CLOCK={b.world_clock}"]
        return [*args, image]

    async def _start_container(self, image: str) -> None:
        t0 = time.monotonic()
        await self.backend.cli.run(*self._run_args(image), timeout=120)
        self._run_seconds = time.monotonic() - t0
        self.image = image
        self.alive = True

    async def _wait_ready(self, timeout_s: float) -> None:
        t0 = time.monotonic()
        try:
            agent = await self._channel()
            await agent.call("ready", timeout_s + 5, timeout=timeout_s)
        except BackendError as e:
            # keep the evidence: the container is removed by whoever handles this error
            _, logs, _ = await self.backend.cli.run("logs", "--tail", "40", self.id, timeout=30, check=False)
            self.backend.boot_failures.append({"machine": self.id, "image": self.image, "at": _now_iso(),
                                               "seconds": round(time.monotonic() - t0, 2), "error": str(e)[:500],
                                               "log_tail": logs[-4000:]})
            raise BackendError(f"container {self.id} ({self.image}) not ready after {time.monotonic() - t0:.0f}s: {e}; "
                               f"boot log tail: {logs.strip()[-600:]!r}") from e
        self.last_boot = {"docker_rm_seconds": round(self._rm_seconds, 3) if self._rm_seconds is not None else None,
                          "docker_run_seconds": round(self._run_seconds, 3),
                          "ready_seconds": round(time.monotonic() - t0, 3)}
        self._rm_seconds = None
        try:
            self.last_boot["boot"] = json.loads(await self.read_file("/run/forkloop/boot.json"))
        except Exception:  # noqa: BLE001 - diagnostics only
            pass

    async def _channel(self) -> _Agent:
        loop = asyncio.get_running_loop()
        if self._agent_lock is None or self._agent_lock_loop is not loop:
            self._agent_lock, self._agent_lock_loop = asyncio.Lock(), loop
        async with self._agent_lock:
            a = self._agent
            if a is not None and not a.closed and a.loop is asyncio.get_running_loop():
                return a
            if a is not None:
                await a.close()
            if not self.alive:
                raise BackendError(f"machine {self.id} is gone")
            a = _Agent(self.backend.cli, self.id)
            try:
                await a.start()
            except BaseException:
                await a.close()
                raise
            self._agent = a
            return a

    async def _call(self, op: str, wait: float, /, **kw: Any) -> dict[str, Any]:
        """One agent request (``wait`` bounds the answer); a dropped pipe, not an agent-side error, is
        re-opened once."""
        agent = await self._channel()
        try:
            return await agent.call(op, wait, **kw)
        except ChannelClosed:
            if not self.alive:
                raise
            agent = await self._channel()
            return await agent.call(op, wait, **kw)

    async def _close_agent(self) -> None:
        if self._agent is not None:
            await self._agent.close()
            self._agent = None

    # ---------------------------------------------------------------- controller channel
    async def exec(self, cmd: str, args: Optional[list[str]] = None, *, timeout_ms: Optional[int] = None,
                   cwd: Optional[str] = None, env: Optional[dict[str, str]] = None) -> ExecResult:
        timeout = (timeout_ms or self.backend.exec_timeout_ms) / 1000
        r = await self._call("exec", timeout + 15, argv=[cmd, *[str(a) for a in (args or [])]], cwd=cwd,
                             env=env, timeout=timeout)
        return ExecResult(int(r["exit"]), base64.b64decode(r["stdout"]).decode("utf-8", "replace"),
                          base64.b64decode(r["stderr"]).decode("utf-8", "replace"))

    async def read_file(self, path: str) -> bytes:
        try:
            r = await self._call("read", 120, path=path)
        except AgentError as e:
            if str(e).startswith("FileNotFoundError"):
                raise FileNotFoundError(path) from e
            raise
        return base64.b64decode(r["data"])

    async def write_file(self, path: str, data: bytes | str, mode: Optional[int] = None) -> None:
        raw = data.encode() if isinstance(data, str) else bytes(data)
        await self._call("write", 120, path=path, data=base64.b64encode(raw).decode(), mode=mode)

    async def snapshot(self, name: Optional[str] = None) -> str:
        """``docker commit`` of this container: a FILESYSTEM checkpoint (``snapshot_semantics``).

        Database consistency (``FORKLOOP_DOCKER_SNAPSHOT_DB``):
        ``flush`` (default) — ``FLUSH TABLES`` in MariaDB and ``sync``, then a *paused* commit: every
        process is frozen while the layer is copied, so the image is a crash-consistent point in time
        (committed InnoDB transactions are in the redo log; the portal's SQLite rollback journal is
        replayed on next open) and MyISAM/Aria tables are flushed. ``stop`` — clean MariaDB shutdown,
        paused commit, MariaDB started again (no recovery at restore; costs a DB restart). ``none`` — the
        paused commit alone. Restoring any of them restarts every service and Chrome (see module doc).
        """
        mode = self.backend.snapshot_db
        if mode not in ("flush", "stop", "none"):
            raise BackendError(f"FORKLOOP_DOCKER_SNAPSHOT_DB must be flush, stop or none (got {mode!r})")
        tag = f"{self.backend.snapshot_repo}:snap-{uuid.uuid4().hex[:12]}"
        t0 = time.monotonic()
        if mode == "flush":
            r = await self.exec("sh", ["-c", "mariadb -e 'FLUSH TABLES' && sync"], timeout_ms=60_000)
            if r.exit_code != 0:
                raise BackendError(f"snapshot: FLUSH TABLES failed: {r.stderr.strip()[:300]}")
        elif mode == "stop":
            r = await self.exec(SVC_PATH, ["stop", "mariadb"], timeout_ms=120_000)
            if r.exit_code != 0:
                raise BackendError(f"snapshot: stopping MariaDB failed: {r.stderr.strip()[:300]}")
            await self.exec("sync", [], timeout_ms=60_000)
        changes = {"forkloop": "1", "forkloop.kind": "checkpoint", "forkloop.snapshot": "1",
                   "forkloop.snapshot_name": name or "", "forkloop.parent": self.image, "forkloop.machine": self.id,
                   "forkloop.snapshot_semantics": SNAPSHOT_SEMANTICS, "forkloop.snapshot_db": mode,
                   "forkloop.created_at": _now_iso(),
                   # container-only labels must not leak into containers started from the image
                   LABEL_WORLD: "", "forkloop.image": ""}
        args = ["commit", "--pause=true"]
        for k, v in changes.items():
            args += ["--change", f"LABEL {k}={json.dumps(v)}"]
        try:
            await self.backend.cli.run(*args, self.id, tag, timeout=600)
        finally:
            if mode == "stop":
                r = await self.exec(SVC_PATH, ["start", "mariadb"], timeout_ms=120_000)
                if r.exit_code != 0:
                    raise BackendError(f"snapshot: restarting MariaDB failed: {r.stderr.strip()[:300]}")
        self.backend.counters["snapshot"] += 1
        self.backend.last_snapshot_seconds = time.monotonic() - t0
        return tag

    async def revert(self, snapshot_id: str) -> None:
        """Replace the container with a fresh one from ``snapshot_id`` under the same name (= machine id).
        A boot that misses the ready deadline raises :class:`RevertTimeoutError` (the pool replaces the
        machine and stays in revert mode)."""
        image = self.backend.resolve_image(snapshot_id)
        await self._close_agent()
        t0 = time.monotonic()
        await self.backend.cli.run("rm", "-f", self.id, timeout=120)
        self._rm_seconds = time.monotonic() - t0
        await self._start_container(image)
        try:
            await self._wait_ready(self.backend.revert_ready_timeout_s)
        except BackendError as e:
            raise RevertTimeoutError(str(e)) from e
        self.backend.counters["revert"] += 1

    async def kill(self) -> None:
        if not self.alive:
            return
        await self._close_agent()
        rc, _, err = await self.backend.cli.run("rm", "-f", self.id, timeout=120, check=False)
        if rc != 0 and "No such container" not in err:
            raise BackendError(f"docker rm -f {self.id} failed: {err.strip()[:300]}")
        self.alive = False
        self.backend.machines.pop(self.id, None)

    async def healthy(self) -> bool:
        if not self.alive:
            return False
        try:
            await self._call("ping", 10)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---------------------------------------------------------------- agent channel
    async def screenshot(self) -> bytes:
        r = await self._call("shot", 30, level=self.backend.png_level)
        return base64.b64decode(r["png"])

    async def display_size(self) -> tuple[int, int]:
        return self.size

    async def _xdo(self, *args: Any) -> None:
        r = await self._call("xdo", 120, args=[str(a) for a in args])
        if int(r["exit"]) != 0:
            raise BackendError(f"xdotool {' '.join(str(a) for a in args[:4])} failed ({r['exit']}): "
                               f"{base64.b64decode(r['stderr']).decode('utf-8', 'replace').strip()[:200]}")

    async def click(self, x: int, y: int, *, button: str = "left") -> None:
        b = _BUTTONS.get(button)
        if b is None:
            raise ValueError(f"unknown mouse button {button!r}")
        # --delay 0: xdotool otherwise sleeps its default 100 ms *after* a single click (measured: click p50
        # 109.5 ms vs move 6.5 ms); the press/release itself is unchanged.
        await self._xdo("mousemove", int(x), int(y), "click", "--delay", 0, b)

    async def double_click(self, x: int, y: int) -> None:
        await self._xdo("mousemove", int(x), int(y), "click", "--repeat", 2, "--delay", 80, 1)

    async def move(self, x: int, y: int) -> None:
        await self._xdo("mousemove", int(x), int(y))

    async def scroll(self, x: int, y: int, *, direction: str, amount: int) -> None:
        # Same semantics as SolariMachine.scroll (keyboard paging, not wheel clicks) so an episode
        # behaves identically on both backends: vertical = Page_Down/Page_Up, round(amount/3) times
        # (at least once); horizontal = Left/Right `amount` times.
        key = {"down": "Page_Down", "up": "Page_Up", "left": "Left", "right": "Right"}.get(direction)
        if key is None:
            raise ValueError(f"unknown scroll direction {direction!r}")
        n = max(1, round(amount / 3)) if direction in ("down", "up") else int(amount)
        await self._xdo("mousemove", int(x), int(y))
        if n > 0:
            await self._xdo("key", *([key] * n))

    async def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        mx, my = (int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2
        await self._xdo("mousemove", int(x1), int(y1), "mousedown", 1, "sleep", 0.05,
                        "mousemove", mx, my, "sleep", 0.05, "mousemove", int(x2), int(y2), "sleep", 0.05, "mouseup", 1)

    async def type_text(self, text: str) -> None:
        if text:
            await self._xdo("type", "--delay", 12, "--", text)

    async def press(self, keys: list[str]) -> None:
        # A chord is one xdotool string ("ctrl+a"), as on the Solari guest (see SolariMachine.press).
        keys = [str(k) for k in keys if str(k)]
        if not keys:
            return
        await self._xdo("key", "+".join(keys) if len(keys) > 1 else keys[0])


# --------------------------------------------------------------------------- backend
class DockerBackend:
    name = "docker"
    snapshot_semantics = SNAPSHOT_SEMANTICS

    def __init__(self, *, image: Optional[str] = None, host: Optional[str] = None,
                 concurrency_cap: Optional[int] = None, owner: Optional[str] = None,
                 seccomp: Optional[str] = None, network: Optional[str] = None, snapshot_db: Optional[str] = None,
                 ready_timeout_s: Optional[float] = None, revert_ready_timeout_s: Optional[float] = None,
                 png_level: Optional[int] = None, limits: bool = True, exec_timeout_ms: int = 600_000,
                 world_clock: Optional[str] = None,
                 cli: Optional[DockerCLI] = None) -> None:
        env = os.environ.get
        self.image = image or env("FORKLOOP_DOCKER_IMAGE") or DEFAULT_IMAGE
        self.cli = cli or DockerCLI(host or env("FORKLOOP_DOCKER_HOST") or None)
        self.concurrency_cap = int(concurrency_cap or env("FORKLOOP_DOCKER_CONCURRENCY") or 8)
        self.owner = owner or env("FORKLOOP_DOCKER_OWNER") or "forkloop"
        sec = seccomp if seccomp is not None else env("FORKLOOP_DOCKER_SECCOMP", "unconfined")
        self.seccomp = "" if sec in ("", "default") else sec
        self.network = network if network is not None else env("FORKLOOP_DOCKER_NETWORK", "")
        self.snapshot_db = (snapshot_db or env("FORKLOOP_DOCKER_SNAPSHOT_DB") or "flush").lower()
        self.ready_timeout_s = float(ready_timeout_s or env("FORKLOOP_DOCKER_READY_TIMEOUT_S") or 120)
        self.revert_ready_timeout_s = float(revert_ready_timeout_s or self.ready_timeout_s)
        self.png_level = int(png_level if png_level is not None else env("FORKLOOP_DOCKER_PNG_LEVEL", 1))
        self.limits = limits and env("FORKLOOP_DOCKER_LIMITS", "1") != "0"
        self.exec_timeout_ms = exec_timeout_ms
        #: the world clock every container starts at (images from :3 on fake it with libfaketime; the image's
        #: own default is 2026-09-07T09:00:00Z); only passed on when set here or in FORKLOOP_WORLD_CLOCK
        self.world_clock = world_clock if world_clock is not None else env("FORKLOOP_WORLD_CLOCK", "")
        repo = self.image.rsplit(":", 1)[0] if ":" in self.image.split("/")[-1] else self.image
        self.snapshot_repo = repo
        self.machines: dict[str, DockerMachine] = {}
        self.counters: dict[str, int] = {"create": 0, "snapshot": 0, "revert": 0}
        self.last_snapshot_seconds: Optional[float] = None
        self._create_lock: Optional[asyncio.Lock] = None
        self._starting = 0   # `docker run` calls in flight (not yet visible to `docker ps`)
        #: containers that did not reach /run/forkloop/ready: error + `docker logs` tail (diagnostics)
        self.boot_failures: list[dict[str, Any]] = []

    @classmethod
    def for_world(cls, world: Any, **kw: Any) -> "DockerBackend":
        """Backend for ``world`` whose golden snapshot is the Docker image. The pool reads the golden id
        from the world's env var (``FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1``); when that is unset it is set
        to the image here. A Solari id left there is refused by :meth:`resolve_image` with a clear message."""
        b = cls(**kw)
        env_name = getattr(getattr(world, "config", None), "golden_snapshot_env", "")
        if env_name and not os.environ.get(env_name):
            os.environ[env_name] = b.image
        return b

    def resolve_image(self, snapshot: Optional[str]) -> str:
        if not snapshot:
            return self.image
        if _SOLARI_SNAPSHOT_ID.match(snapshot):
            raise BackendError(
                f"{snapshot!r} is a Solari snapshot id; the docker backend restores Docker images. Unset the "
                f"world's golden-snapshot variable (e.g. FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1) or set it to the "
                f"image ({self.image}); FORKLOOP_DOCKER_IMAGE picks the default image.")
        return snapshot

    async def _running_worlds(self) -> int:
        _, out, _ = await self.cli.run("ps", "-q", "--filter", f"label={LABEL_WORLD}=1", timeout=60)
        return len([ln for ln in out.split() if ln.strip()])

    async def create(self, *, template: Optional[str] = None, from_snapshot: Optional[str] = None,
                     resolution: str = "1280x720", cpu: int = 2, mem_mb: int = 4096,
                     record: Optional[bool] = None, metadata: Optional[dict[str, str]] = None,
                     timeout_ms: int = 30 * 60_000, disk_gb: Optional[int] = None) -> DockerMachine:
        """Start a container from ``from_snapshot`` (a Docker image ref; default the golden image) and wait
        until the world is up. ``template``, ``record``, ``timeout_ms`` and ``disk_gb`` are Solari
        concepts with no Docker meaning and are ignored."""
        image = self.resolve_image(from_snapshot)
        size = parse_resolution(resolution)
        meta = {"forkloop": "1", **{str(k): str(v) for k, v in (metadata or {}).items()}}
        if self._create_lock is None:
            self._create_lock = asyncio.Lock()
        # The lock covers only the cap check and a slot reservation: holding it across `docker run`
        # serialised container starts (measured 2026-09-29: 32 simultaneous creates spread over 30 s).
        async with self._create_lock:
            n = await self._running_worlds() + self._starting
            if n >= self.concurrency_cap:
                raise ConcurrencyError(f"docker world cap {self.concurrency_cap} reached ({n} running or starting; "
                                       "FORKLOOP_DOCKER_CONCURRENCY raises it)")
            self._starting += 1
        m = DockerMachine(self, "flw-" + uuid.uuid4().hex[:12], image, size, meta, cpu=cpu, mem_mb=mem_mb)
        try:
            self.machines[m.id] = m
            await m._start_container(image)
        except BaseException:
            self.machines.pop(m.id, None)
            await self.cli.run("rm", "-f", m.id, timeout=120, check=False)
            raise
        finally:
            self._starting -= 1
        try:
            await m._wait_ready(self.ready_timeout_s)
        except BaseException:
            try:
                await asyncio.shield(m.kill())
            except Exception:  # noqa: BLE001
                pass
            raise
        self.counters["create"] += 1
        return m

    async def list_snapshots(self) -> list[SnapshotInfo]:
        _, out, _ = await self.cli.run("images", "-q", "--no-trunc", "--filter", "label=forkloop=1", timeout=60)
        ids = sorted(set(out.split()))
        if not ids:
            return []
        _, raw, _ = await self.cli.run("image", "inspect", *ids, timeout=60)
        snaps: list[SnapshotInfo] = []
        for img in json.loads(raw or "[]"):
            labels = (img.get("Config") or {}).get("Labels") or {}
            kind = labels.get("forkloop.kind", "")
            if kind not in ("golden", "checkpoint"):
                continue
            ref = (img.get("RepoTags") or [img.get("Id", "")])[0]
            snaps.append(SnapshotInfo(id=ref, name=labels.get("forkloop.snapshot_name") or ref,
                                      parent=labels.get("forkloop.parent") or labels.get("forkloop.base") or None,
                                      size_bytes=int(img.get("Size") or 0), created_at=str(img.get("Created", "")),
                                      kind=kind, template=labels.get("forkloop.world", "")))
        return snaps

    async def delete_snapshot(self, snapshot_id: str) -> None:
        if snapshot_id == self.image:
            raise BackendError(f"refusing to delete the golden image {snapshot_id} (docker rmi it by hand)")
        await self.cli.run("rmi", snapshot_id, timeout=120)

    async def list_machines(self, *, metadata: Optional[dict[str, str]] = None) -> list[MachineInfo]:
        filters = ["--filter", f"label={LABEL_WORLD}=1"]
        for k, v in (metadata or {}).items():
            filters += ["--filter", f"label={META_PREFIX}{k}={v}"]
        _, out, _ = await self.cli.run("ps", "-a", "-q", "--no-trunc", *filters, timeout=60)
        ids = out.split()
        if not ids:
            return []
        rc, raw, _ = await self.cli.run("inspect", *ids, timeout=60, check=False)  # a container may vanish meanwhile
        infos: list[MachineInfo] = []
        for c in json.loads(raw or "[]"):
            labels = (c.get("Config") or {}).get("Labels") or {}
            meta = {k[len(META_PREFIX):]: v for k, v in labels.items() if k.startswith(META_PREFIX)}
            status = ((c.get("State") or {}).get("Status") or "").lower()
            infos.append(MachineInfo(id=str(c.get("Name", "")).lstrip("/"), state=_STATE.get(status, status),
                                     metadata=meta, created_at=str(c.get("Created", ""))))
        return infos

    async def kill_machine(self, machine_id: str) -> None:
        m = self.machines.get(machine_id)
        if m is not None:
            await m.kill()
            return
        rc, _, err = await self.cli.run("rm", "-f", machine_id, timeout=120, check=False)
        if rc != 0 and "No such container" not in err:
            raise BackendError(f"docker rm -f {machine_id} failed: {err.strip()[:300]}")

    async def close(self) -> None:
        """Remove every container this backend started that is still alive (containers hold CPU and
        memory on the host; the pool normally kills its own first). ``FORKLOOP_DOCKER_KEEP=1`` keeps them."""
        keep = os.environ.get("FORKLOOP_DOCKER_KEEP") == "1"
        errors = []
        for m in list(self.machines.values()):
            try:
                if keep:
                    await m._close_agent()
                else:
                    await m.kill()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{m.id}: {e}")
        if errors:
            raise BackendError("docker cleanup failed: " + "; ".join(errors))


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


__all__ = ["DockerBackend", "DockerMachine", "DockerCLI", "DEFAULT_IMAGE", "SNAPSHOT_SEMANTICS", "AgentError",
           "ChannelClosed"]
