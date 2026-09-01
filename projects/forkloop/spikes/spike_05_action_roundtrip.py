"""Spike 5 — action round-trip latency.

Question: how long do screenshot() and mouse.click() take over the control
channel? This bounds the step rate of the agent loop (plan: steps/s per VM).

Method: N× {screenshot; click(640, 360); screenshot} on an idle desktop,
timing each call and the whole loop. Nothing is open, so the click lands on
the desktop background.

Run:  SOLARI_API_KEY=... python spikes/spike_05_action_roundtrip.py [--iterations 50]
"""

from __future__ import annotations

import time

from _common import (
    base_parser, create_desktop, kill_quietly, log_result, print_table, run, summarize, wait_ready,
)


async def main(args, client) -> None:
    d = None
    try:
        d = await create_desktop(client, timeout_min=args.timeout_min)
        print("desktop:", d.id)
        print(f"ready in {await wait_ready(d):.1f}s")
        size = await d.display.size()
        print("display:", size)

        shots, clicks, loops, sizes = [], [], [], []
        for i in range(args.iterations):
            t0 = time.perf_counter()
            png = await d.screenshot(format="png")
            t1 = time.perf_counter()
            await d.mouse.click(640, 360)
            t2 = time.perf_counter()
            png2 = await d.screenshot(format="png")
            t3 = time.perf_counter()
            shots += [t1 - t0, t3 - t2]
            clicks.append(t2 - t1)
            loops.append(t3 - t0)
            sizes += [len(png), len(png2)]
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{args.iterations}  loop {t3 - t0:.3f}s")

        cols = ("n", "min", "p50", "p95", "p99", "max", "mean")
        stats = {"screenshot_s": summarize(shots), "click_s": summarize(clicks), "loop_s": summarize(loops)}
        print_table(["call", *cols], [[k, *[v[c] for c in cols]] for k, v in stats.items()])
        print(f"png bytes: mean {sum(sizes) / len(sizes):.0f}  steps/s at p50 ≈ {1 / stats['loop_s']['p50']:.2f}")
        n = f"n={args.iterations}"
        for k, v in stats.items():
            log_result(5, f"{k}_p50", round(v["p50"], 4), "s", n)
            log_result(5, f"{k}_p95", round(v["p95"], 4), "s", n)
        log_result(5, "png_bytes_mean", round(sum(sizes) / len(sizes)), "bytes", n)
    finally:
        await kill_quietly(d)


if __name__ == "__main__":
    p = base_parser(__doc__)
    p.add_argument("--iterations", type=int, default=50)
    run(5, p, main)
