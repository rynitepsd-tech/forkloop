"""Spike 4 — does a snapshot capture RAM (processes, windows, tmpfs, stream)?

Question: is revert() a full machine restore (memory + disk) or disk-only?
If memory is not restored, the golden snapshot must be taken with apps
stopped and reset() must relaunch them.

Method: open a mousepad window, start `sleep 3600`, write a file on tmpfs
(/dev/shm). Snapshot. Kill the process, close the window, delete the file.
Revert. Probe each again, and try a WebSocket connect to streamUrl.

Run:  SOLARI_API_KEY=... python spikes/spike_04_memory_survives_revert.py
"""

from __future__ import annotations

import asyncio
import base64
import os

from _common import (
    base_parser, create_desktop, delete_snapshot_quietly, kill_quietly, log_result,
    print_table, reattach, run, sh, wait_ready,
)

TMPFS = "/dev/shm/forkloop/mem.txt"


async def probe(d) -> dict[str, bool]:
    proc = (await sh(d, "pgrep -f 'sleep 3600' >/dev/null && echo 1 || echo 0")).stdout.strip() == "1"
    win = (await sh(d, "DISPLAY=${DISPLAY:-:0} xdotool search --classname mousepad 2>/dev/null | head -1")).stdout.strip() != ""
    tmp = (await sh(d, f"test -f {TMPFS} && echo 1 || echo 0")).stdout.strip() == "1"
    return {"process_sleep_3600": proc, "window_mousepad": win, "tmpfs_file": tmp}


async def stream_alive(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Try to open streamUrl as a WebSocket (RFB greets with 'RFB 003.008')."""
    if not url:
        return False, "no streamUrl"
    try:
        import websockets  # optional dependency
    except ImportError:
        websockets = None
    if websockets is not None:
        try:
            async with websockets.connect(url, open_timeout=timeout) as ws:
                try:
                    greeting = await asyncio.wait_for(ws.recv(), 2.0)
                    return True, f"open, greeting={greeting[:12]!r}"
                except asyncio.TimeoutError:
                    return True, "open, no greeting within 2s"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
    # Fallback: raw HTTP upgrade with httpx; 101 means the endpoint is serving.
    import httpx
    http_url = "https://" + url.split("://", 1)[1] if "://" in url else url
    headers = {"Connection": "Upgrade", "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
               "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode()}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(http_url, headers=headers)
            return r.status_code == 101, f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return "101" in str(exc), f"{type(exc).__name__}: {exc}"


async def main(args, client) -> None:
    d = None
    snap = None
    try:
        d = await create_desktop(client, timeout_min=args.timeout_min)
        print("desktop:", d.id, "| stream:", d.streamUrl)
        print(f"ready in {await wait_ready(d):.1f}s")
        await d.open("mousepad")
        await asyncio.sleep(4)
        pid = await d.process.start("sleep", args=["3600"])
        fs = (await sh(d, f"mkdir -p $(dirname {TMPFS}) && echo alive > {TMPFS} && df -T /dev/shm | tail -1")).stdout.strip()
        print(f"sleep pid {pid}; /dev/shm: {fs}")
        before = await probe(d)
        stream_before = await stream_alive(d.streamUrl)

        snap = await d.snapshot("spike04-live-state")
        print("snapshot", snap)

        await sh(d, "pkill -f 'sleep 3600'; pkill -x mousepad; rm -rf /dev/shm/forkloop; true")
        await asyncio.sleep(1)
        killed = await probe(d)

        await d.revert(snap)
        print(f"reattached {await reattach(d):.1f}s after revert")
        after = await probe(d)
        stream_after = await stream_alive(d.streamUrl)

        rows = [[k, before[k], killed[k], after[k]] for k in before]
        rows.append(["stream_ws_connect", stream_before[0], "-", stream_after[0]])
        print_table(["check", "before_snapshot", "after_kill", "after_revert"], rows)
        print(f"stream before: {stream_before[1]}\nstream after : {stream_after[1]}")
        memory_restored = all(after.values())
        print(f"memory_restored={memory_restored}  (all three in-RAM artefacts came back)")
        for k in before:
            log_result(4, f"{k}_after_revert", after[k], "bool", f"before={before[k]} after_kill={killed[k]}")
        log_result(4, "stream_alive_after_revert", stream_after[0], "bool", stream_after[1])
        log_result(4, "memory_restored", memory_restored, "bool",
                   "False → golden snapshot must be taken with apps stopped; reset relaunches them")
    finally:
        await kill_quietly(d)
        await delete_snapshot_quietly(client, snap)


if __name__ == "__main__":
    run(4, base_parser(__doc__), main)
