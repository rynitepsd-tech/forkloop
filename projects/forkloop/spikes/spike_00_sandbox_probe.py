"""Spike 0 — headless probe for accounts where desktops are plan-gated (402).

Same snapshot API, no screen: measures create-from-template, snapshot(),
revert() (running and paused), pause/resume, and create(from_snapshot), and
reports guest facts (OS, root, init, tools, disk, egress). Everything it
creates is killed and every snapshot deleted in ``finally``.

Run:  SOLARI_API_KEY=... python spikes/spike_00_sandbox_probe.py [--cpu 2 --mem-mb 4096]

Measured 2026-09-01 on a Free-plan key: revert() → 409 "Not revertable" on
running AND paused sandboxes, and a failed revert on a running sandbox left it
"Not found". from_snapshot ≈ 18 s. See docs/spikes.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

from _common import log_result, print_table

BASE = "https://api.getsolari.com"
META = {"forkloop": "spike", "spike": "0"}


async def sh(sb, cmd: str, timeout_ms: int = 60_000):
    r = await sb.commands.run("sh", args=["-c", cmd], timeout_ms=timeout_ms)
    return r.exitCode, (r.stdout + r.stderr).strip()


async def reattach(sb, timeout_s: float = 60.0) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        try:
            await sb.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await sb.connect()
            code, _ = await sh(sb, "true", 10_000)
            if code == 0:
                return time.monotonic() - t0
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.3)
    raise TimeoutError("machine not reachable")


async def main(args: argparse.Namespace) -> int:
    from solari_sandbox import SandboxClient
    from solari_core.errors import GatewayError

    rows: list[tuple[str, str]] = []
    snaps: list[str] = []
    sb = sb2 = None
    async with SandboxClient(api_key=os.environ["SOLARI_API_KEY"], base_url=BASE) as c:
        try:
            t = time.monotonic()
            sb = await c.create(template="base", cpu=args.cpu, mem_mb=args.mem_mb, timeout_ms=15 * 60_000,
                                metadata=META, lifecycle={"onTimeout": "kill"})
            await sb.connect()
            dt = time.monotonic() - t
            rows.append(("create(base) → connect", f"{dt:.2f} s")); log_result(0, "create_base_s", round(dt, 2), "s")
            facts = {}
            for k, cmd in {"os": ". /etc/os-release; echo $PRETTY_NAME", "uid": "id -u", "init": "cat /proc/1/comm",
                           "systemd": "systemctl --version 2>/dev/null | head -1", "python": "python3 --version",
                           "tools": "for t in sqlite3 curl mysql apt-get sudo ps docker; do command -v $t >/dev/null && printf '%s ' $t; done; echo",
                           "disk": "df -h / | tail -1", "egress": "curl -sS -m 8 -o /dev/null -w '%{http_code}' https://github.com"}.items():
                _, out = await sh(sb, cmd)
                facts[k] = out.replace("\n", " ")[:120]
                rows.append((f"guest {k}", facts[k]))
            log_result(0, "guest_facts", facts, "", "")
            await sh(sb, "mkdir -p /var/lib/forkloop && echo golden > /var/lib/forkloop/marker && mkdir -p /dev/shm/fl && echo mem > /dev/shm/fl/x && (nohup sleep 3600 >/dev/null 2>&1 &)")
            t = time.monotonic(); snap = await sb.snapshot("spike0"); snaps.append(snap); dt = time.monotonic() - t
            view = await c.get_snapshot(snap)
            rows.append(("snapshot()", f"{dt:.1f} s ({view.sizeBytes / 1e9:.2f} GB)")); log_result(0, "snapshot_s", round(dt, 2), "s", f"sizeBytes={view.sizeBytes}")
            await sh(sb, "echo dirty > /var/lib/forkloop/marker")
            # revert on a running machine
            t = time.monotonic()
            try:
                await sb.revert(snap)
                dt = await reattach(sb) + (time.monotonic() - t)
                _, out = await sh(sb, "cat /var/lib/forkloop/marker; cat /dev/shm/fl/x 2>&1; pgrep -c sleep 2>&1")
                rows.append(("revert(running) → reachable", f"{dt:.2f} s state=[{out.replace(chr(10), ' | ')}]"))
                log_result(0, "revert_running_s", round(dt, 2), "s", out.replace("\n", "|"))
            except GatewayError as e:
                rows.append(("revert(running)", f"HTTP {e.status}: {e}")); log_result(0, "revert_running", f"{e.status} {e}", "", "")
                try:
                    st = (await c.get(sb.sandboxId)).state
                except GatewayError as e2:
                    st = f"get → {e2.status} {e2}"
                rows.append(("state after failed revert", st)); log_result(0, "state_after_failed_revert", st, "", "")
                if "Not found" in st:
                    sb = None
            # revert on a paused machine (fresh machine if the first one is gone)
            if sb is None:
                sb = await c.create(template="base", cpu=args.cpu, mem_mb=args.mem_mb, timeout_ms=15 * 60_000, metadata=META, lifecycle={"onTimeout": "pause"})
                await sb.connect(); await sh(sb, "mkdir -p /var/lib/forkloop && echo golden > /var/lib/forkloop/marker")
                snap = await sb.snapshot("spike0b"); snaps.append(snap)
            t = time.monotonic(); await sb.pause(); dt = time.monotonic() - t
            rows.append(("pause()", f"{dt:.2f} s")); log_result(0, "pause_s", round(dt, 2), "s")
            t = time.monotonic()
            try:
                await sb.revert(snap)
                rows.append(("revert(paused)", f"OK api {time.monotonic() - t:.2f} s")); log_result(0, "revert_paused", "OK", "", "")
            except GatewayError as e:
                rows.append(("revert(paused)", f"HTTP {e.status}: {e}")); log_result(0, "revert_paused", f"{e.status} {e}", "", "")
            t = time.monotonic()
            try:
                await sb.resume(); dt = await reattach(sb) + (time.monotonic() - t)
                rows.append(("resume() → reachable", f"{dt:.2f} s")); log_result(0, "resume_reattach_s", round(dt, 2), "s")
            except Exception as e:  # noqa: BLE001
                rows.append(("resume()", f"{type(e).__name__}: {e}"))
            # from_snapshot (Free cap = 1 → kill the original first)
            if sb is not None:
                await sb.kill(); sb = None
            t = time.monotonic()
            sb2 = await c.create(from_snapshot=snaps[0], cpu=args.cpu, mem_mb=args.mem_mb, timeout_ms=10 * 60_000, metadata=META, lifecycle={"onTimeout": "kill"})
            await sb2.connect(); _, out = await sh(sb2, "cat /var/lib/forkloop/marker; cat /dev/shm/fl/x 2>&1; pgrep -c sleep 2>&1")
            dt = time.monotonic() - t
            rows.append(("create(from_snapshot) → first command", f"{dt:.2f} s state=[{out.replace(chr(10), ' | ')}]"))
            log_result(0, "from_snapshot_create_s", round(dt, 2), "s", out.replace("\n", "|"))
        finally:
            for m in (sb, sb2):
                if m is not None:
                    try:
                        await m.kill()
                    except Exception:  # noqa: BLE001
                        pass
            for s in snaps:
                try:
                    await c.delete_snapshot(s)
                except Exception:  # noqa: BLE001
                    pass
    print_table(("measurement", "result"), rows)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cpu", type=int, default=2)
    ap.add_argument("--mem-mb", type=int, default=4096)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
