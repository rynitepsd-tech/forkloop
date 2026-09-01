"""Spike 3 — does recording coexist with snapshot / revert / from_snapshot?

Question (reviewer claim): `record=True` may not work on a desktop created
with from_snapshot, or across a revert. Which combinations work?

Method: every step is wrapped so a failure is a table row, never a crash.
  A. create_desktop(record=True)          → record.start/stop, recordingUrl
     snapshot → revert → record.start/stop again
  B. create_desktop(from_snapshot, record=True) → record.start/stop
  C. create_desktop(from_snapshot)        (no record flag) → record.start/stop
Exact error text is printed and logged for every failing step.

Run:  SOLARI_API_KEY=... python spikes/spike_03_record_with_snapshot.py
"""

from __future__ import annotations

import asyncio

from _common import (
    base_parser, create_desktop, delete_snapshot_quietly, explain, kill_quietly,
    log_result, print_table, reattach, run, wait_ready,
)

ROWS: list[list] = []


async def attempt(step: str, coro):
    """Run one step; record ok/detail; return the value or None."""
    try:
        value = await coro
        detail = _short(value)
        ROWS.append([step, True, detail])
        print(f"  ok   {step}: {detail}")
        return value
    except Exception as exc:  # noqa: BLE001
        ROWS.append([step, False, explain(exc)])
        print(f"  FAIL {step}: {explain(exc)}")
        return None


def _short(v) -> str:
    if v is None:
        return ""
    s = str(getattr(v, "id", v))
    return s if len(s) <= 70 else s[:67] + "..."


async def record_cycle(d, label: str) -> None:
    await attempt(f"{label}.record.start", d.record.start())
    await attempt(f"{label}.type", d.keyboard.type("recording?"))
    await asyncio.sleep(3)
    await attempt(f"{label}.record.stop", d.record.stop())
    ROWS.append([f"{label}.recordingUrl", bool(d.recordingUrl), _short(d.recordingUrl)])
    print(f"  info {label}.recordingUrl = {d.recordingUrl!r}")


async def main(args, client) -> None:
    a = b = c = None
    snap = None
    try:
        print("A. plain create with record=True")
        a = await attempt("A.create(record=True)", create_desktop(client, record=True, timeout_min=args.timeout_min))
        if a is not None:
            await attempt("A.ready", wait_ready(a))
            await record_cycle(a, "A")
            snap = await attempt("A.snapshot", a.snapshot("spike03"))
            if snap:
                await attempt("A.revert", a.revert(snap))
                await attempt("A.reattach", reattach(a))
                await record_cycle(a, "A.after_revert")
            # Kill A before creating B: keeps us at one live VM (Free cap 1, Starter 2).
            await kill_quietly(a)
            a = None

        if snap:
            print("B. create from_snapshot with record=True")
            b = await attempt("B.create(from_snapshot, record=True)",
                              create_desktop(client, from_snapshot=snap, record=True, timeout_min=args.timeout_min))
            if b is not None:
                await attempt("B.ready", wait_ready(b))
                await record_cycle(b, "B")
                await kill_quietly(b)
                b = None

            print("C. create from_snapshot without record flag")
            c = await attempt("C.create(from_snapshot)",
                              create_desktop(client, from_snapshot=snap, timeout_min=args.timeout_min))
            if c is not None:
                await attempt("C.ready", wait_ready(c))
                await record_cycle(c, "C")
        else:
            print("no snapshot from A; skipping B and C")

        print()
        print_table(["step", "ok", "detail"], ROWS)
        for step, ok, detail in ROWS:
            log_result(3, step, bool(ok), "bool", str(detail))
    finally:
        for d in (a, b, c):
            await kill_quietly(d)
        await delete_snapshot_quietly(client, snap)


if __name__ == "__main__":
    run(3, base_parser(__doc__), main)
