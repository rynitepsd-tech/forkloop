"""Re-check the support-email items on a fork of the live golden (the machine kind the harness uses).

    python scripts/solari_verify_fork.py

Creates one from_snapshot desktop from $FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1, prints disk / cpu / RAM,
tries snapshot() (email item 2) and revert(golden) (item 1), reports whether the machine survived, kills it.
Never touches the golden itself. Uses the spike helpers so the output matches docs/spikes.md.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spikes"))
from _common import create_desktop, explain, kill_quietly, make_client, sh, wait_ready  # noqa: E402


async def main() -> int:
    golden = os.environ["FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1"]
    client = make_client()
    d = None
    rows = []
    try:
        t = time.monotonic()
        d = await create_desktop(client, from_snapshot=golden, timeout_min=15)
        await wait_ready(d)
        rows.append(("fork of golden ready", f"{time.monotonic() - t:.1f} s id={d.id[:24]}…"))
        out = await sh(d, "nproc; free -m | awk 'NR==2{print $2\" MB\"}'; df -h / | tail -1")
        rows.append(("nproc / RAM / disk on fork", " | ".join(out.stdout.split("\n"))))
        try:
            t = time.monotonic()
            sid = await d.snapshot("verify-fork-snap")
            rows.append(("snapshot() on fork", f"OK {sid} in {time.monotonic() - t:.1f} s"))
            try:
                await client.delete_snapshot(sid)
                rows.append(("delete that snapshot", "OK"))
            except Exception as e:  # noqa: BLE001
                rows.append(("delete that snapshot", explain(e)))
        except Exception as e:  # noqa: BLE001
            rows.append(("snapshot() on fork", explain(e)))
        try:
            t = time.monotonic()
            await d.revert(golden)
            rows.append(("revert(golden) on fork", f"accepted in {time.monotonic() - t:.1f} s"))
        except Exception as e:  # noqa: BLE001
            rows.append(("revert(golden) on fork", explain(e)))
        try:
            out = await asyncio.wait_for(sh(d, "echo alive"), timeout=60)
            rows.append(("machine after revert call", f"alive: {out.stdout.strip()!r}"))
        except Exception as e:  # noqa: BLE001
            rows.append(("machine after revert call", f"NOT reachable: {explain(e)}"))
        try:
            found = [v.sandboxId async for v in client.list_all(kind="desktop")]
            rows.append(("still listed by the API", str(d.id in found)))
        except Exception as e:  # noqa: BLE001
            rows.append(("still listed by the API", explain(e)))
    finally:
        await kill_quietly(d)
    w = max(len(a) for a, _ in rows)
    print("\n== fork-of-golden verification ==")
    for a, b in rows:
        print(f"{a.ljust(w)}  {b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
