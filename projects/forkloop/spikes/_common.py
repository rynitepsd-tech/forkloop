"""Shared helpers for the day-1 Solari spikes.

Standalone: needs only SOLARI_API_KEY (and the solari-sandbox package). Every
desktop a spike creates carries metadata {"forkloop": "spike"} so a crashed
run can be cleaned up with ``python spikes/_common.py --reap``.

Every call here exists in solari-sandbox 0.2.0 with the signature used;
see docs/contracts.md §2 for the verified mapping.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from solari_core.desktop import Desktop
from solari_core.errors import ConcurrencyLimitError, NoCapacityError, PlanError
from solari_core.types import CommandResult
from solari_sandbox import SandboxClient

BASE_URL = os.environ.get("SOLARI_BASE_URL", "https://api.getsolari.com")
SPIKE_META = {"forkloop": "spike"}
RESULTS = Path(__file__).with_name("results.jsonl")
OUT_DIR = Path(__file__).with_name("out")
RESOLUTION = "1280x720"
CPU, MEM_MB = 2, 4096
# The SDK's default per-call timeout is 300 s. After a revert the old control
# socket can be dead while the local channel still says "connected"; a short
# timeout makes that visible fast so reattach() can redial.
CALL_TIMEOUT_MS = 30_000


# --- client / desktop lifecycle -------------------------------------------------


def make_client() -> SandboxClient:
    key = os.environ.get("SOLARI_API_KEY")
    if not key:
        sys.exit("SOLARI_API_KEY is not set (get one at https://console.getsolari.com)")
    return SandboxClient(api_key=key, base_url=BASE_URL, call_timeout_ms=CALL_TIMEOUT_MS)


async def create_desktop(
    client: SandboxClient,
    *,
    from_snapshot: str | None = None,
    record: bool | None = None,
    timeout_min: int = 20,
    extra_meta: dict[str, str] | None = None,
) -> Desktop:
    """create_desktop on the unified /sandboxes route (kind=desktop). This is the
    only route that accepts from_snapshot; DesktopClient.create does not."""
    return await client.create_desktop(
        # A snapshot already implies its template; the gateway may reject both together.
        template=None if from_snapshot else "default",
        resolution=RESOLUTION,
        cpu=CPU,
        mem_mb=MEM_MB,
        timeout_ms=timeout_min * 60_000,
        record=record,
        from_snapshot=from_snapshot,
        metadata={**SPIKE_META, **(extra_meta or {})},
        lifecycle={"onTimeout": "kill"},
    )


async def wait_ready(d: Desktop, timeout_s: float = 180.0, interval: float = 1.0) -> float:
    """connect() + poll health() until .ready. Returns seconds waited."""
    t0 = time.perf_counter()
    last: Exception | None = None
    while time.perf_counter() - t0 < timeout_s:
        try:
            if not d.connected:
                await d.connect()
            if (await d.health()).ready:
                return time.perf_counter() - t0
        except Exception as exc:  # noqa: BLE001 - guest may not accept connections yet
            last = exc
        await asyncio.sleep(interval)
    raise TimeoutError(f"desktop {d.id} not ready after {timeout_s:.0f}s (last: {last!r})")


async def reattach(d: Desktop, timeout_s: float = 120.0) -> float:
    """Re-dial the guest after revert(). The VM resumes from a memory image and
    the old WebSocket is gone even if the channel still reports connected, and
    reconnect() is a no-op while it does — so drop the channel explicitly and
    connect() again. The guest accepts a control connection only briefly after
    a restore, hence the loop. Returns seconds until health().ready."""
    t0 = time.perf_counter()
    last: Exception | None = None
    while time.perf_counter() - t0 < timeout_s:
        try:
            await d.close()
            await d.connect()
            if (await d.health()).ready:
                return time.perf_counter() - t0
        except Exception as exc:  # noqa: BLE001
            last = exc
        await asyncio.sleep(0.5)
    raise TimeoutError(f"desktop {d.id} not back after revert in {timeout_s:.0f}s (last: {last!r})")


async def sh(d: Desktop, script: str, *, user: str | None = None) -> CommandResult:
    """commands.run is argv, not a shell — wrap in `sh -c` for pipes/globs/&&."""
    return await d.commands.run("sh", args=["-c", script], user=user)


async def kill_quietly(d: Desktop | None) -> None:
    """kill() destroys the VM (close() would only drop the local channel)."""
    if d is None:
        return
    try:
        await d.kill()
    except Exception as exc:  # noqa: BLE001
        print(f"  (kill {d.id} failed: {exc}; will be reaped by metadata next run)")


async def delete_snapshot_quietly(client: SandboxClient, snapshot_id: str | None) -> None:
    if not snapshot_id:
        return
    try:
        await client.delete_snapshot(snapshot_id)  # refused while a child VM is alive
    except Exception as exc:  # noqa: BLE001
        print(f"  (delete_snapshot {snapshot_id} failed: {exc})")


async def reap_orphans(client: SandboxClient) -> list[str]:
    """Kill every desktop tagged {"forkloop": "spike"} (DELETE is idempotent)."""
    killed: list[str] = []
    async for view in client.list_all(kind="desktop", metadata=SPIKE_META):
        try:
            await client.kill(view.sandboxId)
            killed.append(view.sandboxId)
        except Exception as exc:  # noqa: BLE001
            print(f"  (reap {view.sandboxId} failed: {exc})")
    return killed


# --- measurement ------------------------------------------------------------------


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


async def stable_screenshot(
    d: Desktop, *, timeout_s: float = 15.0, interval: float = 0.5
) -> tuple[bytes, str, float, bool]:
    """Screenshot until two consecutive frames hash identical.
    Returns (png, hash, seconds, stable)."""
    t0 = time.perf_counter()
    prev = ""
    png = b""
    while True:
        png = await d.screenshot(format="png")
        h = sha256(png)
        if h == prev:
            return png, h, time.perf_counter() - t0, True
        if time.perf_counter() - t0 >= timeout_s:
            return png, h, time.perf_counter() - t0, False
        prev = h
        await asyncio.sleep(interval)


def percentile(xs: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile, p in [0, 100]."""
    if not xs:
        return math.nan
    s = sorted(xs)
    k = (len(s) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    return s[f] if f == c else s[f] + (s[c] - s[f]) * (k - f)


def summarize(xs: Sequence[float]) -> dict[str, float]:
    if not xs:
        return {"n": 0, "min": math.nan, "p50": math.nan, "p95": math.nan, "p99": math.nan, "max": math.nan, "mean": math.nan}
    return {
        "n": len(xs), "min": min(xs), "p50": percentile(xs, 50), "p95": percentile(xs, 95),
        "p99": percentile(xs, 99), "max": max(xs), "mean": statistics.fmean(xs),
    }


# --- reporting --------------------------------------------------------------------


def log_result(spike: int, metric: str, value: Any, unit: str, notes: str = "") -> None:
    row = {
        "spike": spike,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metric": metric,
        "value": value,
        "unit": unit,
        "notes": notes,
    }
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def print_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    cells = [[_fmt(c) for c in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in cells:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def _fmt(v: Any) -> str:
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def save_png(name: str, png: bytes) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    p = OUT_DIR / name
    p.write_bytes(png)
    return p


def explain(exc: BaseException) -> str:
    if isinstance(exc, PlanError):
        return f"402 FeatureRequiresPlan — desktops need a paid plan (Starter $20/mo or above): {exc}"
    if isinstance(exc, ConcurrencyLimitError):
        return ("429 ConcurrencyLimitExceeded — live-VM cap hit (Free 1, Starter 2, Pro 10). "
                f"Kill leftovers with `python spikes/_common.py --reap` or lower --forks: {exc}")
    if isinstance(exc, NoCapacityError):
        return f"503 — no desktop host has capacity right now; retry in a minute: {exc}"
    return f"{type(exc).__name__}: {exc}"


# --- entrypoint plumbing ------------------------------------------------------------


def base_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeout-min", type=int, default=20, help="VM idle timeout (minutes); onTimeout=kill")
    p.add_argument("--no-reap", action="store_true", help="do not kill leftover spike desktops first")
    return p


def run(spike: int, parser: argparse.ArgumentParser, main: Callable[[argparse.Namespace, SandboxClient], Awaitable[None]]) -> None:
    args = parser.parse_args()

    async def _go() -> int:
        client = make_client()
        try:
            if not args.no_reap:
                reaped = await reap_orphans(client)
                if reaped:
                    print(f"reaped {len(reaped)} leftover spike desktop(s): {', '.join(reaped)}")
            await main(args, client)
            return 0
        except Exception as exc:  # noqa: BLE001 - SolariError, TimeoutError from wait_ready, ...
            msg = explain(exc)
            print(f"spike {spike} FAILED: {msg}")
            log_result(spike, "error", msg, "text")
            return 1
        finally:
            await client.aclose()

    sys.exit(asyncio.run(_go()))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="spike helpers; --reap kills leftover spike desktops")
    ap.add_argument("--reap", action="store_true")
    if ap.parse_args().reap:
        async def _reap() -> None:
            async with make_client() as c:
                print("killed:", await reap_orphans(c) or "nothing")
        asyncio.run(_reap())
    else:
        ap.print_help()
