"""Spike 1 — revert latency and state fidelity.

Question: how long does revert() take wall-clock (API call → guest healthy →
screen stable), and does the screen come back to the post-snapshot state?

Method: open mousepad, type a marker, snapshot. Then N× {type junk; revert;
reattach; wait for two identical screenshot hashes}. "State came back" means
the stable post-revert hash is one of the hashes seen right after the
snapshot. A second, best-effort check reads the editor text via the clipboard.

Run:  SOLARI_API_KEY=... python spikes/spike_01_revert_latency.py [--iterations 20]
"""

from __future__ import annotations

import asyncio
import time

from _common import (
    base_parser, create_desktop, delete_snapshot_quietly, kill_quietly, log_result,
    print_table, reattach, run, save_png, sh, sha256, stable_screenshot, summarize, wait_ready,
)

MARKER = "FORKLOOP-MARKER-7f3a"
# GTK reads gtk-cursor-blink from settings.ini at startup. A blinking caret makes
# consecutive screenshots differ, so turn it off before mousepad launches.
NO_BLINK = (
    "mkdir -p ~/.config/gtk-3.0 && printf '[Settings]\\ngtk-cursor-blink=false\\n' "
    "> ~/.config/gtk-3.0/settings.ini; xfconf-query -c xsettings -p /Net/CursorBlink "
    "-n -t bool -s false >/dev/null 2>&1; true"
)


async def editor_text(d) -> str:
    """Best effort: select-all + copy, then read the clipboard. Empty on failure."""
    try:
        await d.keyboard.hotkey("ctrl", "a")
        await d.keyboard.hotkey("ctrl", "c")
        await asyncio.sleep(0.3)
        return await d.clipboard.get()
    except Exception:  # noqa: BLE001
        return ""


async def main(args, client) -> None:
    d = None
    snap = None
    try:
        d = await create_desktop(client, timeout_min=args.timeout_min)
        print("desktop:", d.id, "| watch:", d.streamUrl)
        print(f"ready in {await wait_ready(d):.1f}s")
        await sh(d, NO_BLINK)
        await d.open("mousepad")
        await asyncio.sleep(4)
        # Mousepad maps top-left; (320, 300) is inside its text area.
        await d.mouse.click(320, 300)
        await d.keyboard.type(MARKER)
        await asyncio.sleep(1)

        t0 = time.perf_counter()
        snap = await d.snapshot("spike01-marker")
        snap_s = time.perf_counter() - t0
        print(f"snapshot {snap} in {snap_s:.1f}s")

        # Every hash the post-snapshot screen shows over ~3 s (more than one if
        # something still animates). The revert check compares against this set.
        baseline: set[str] = set()
        for _ in range(6):
            baseline.add(sha256(await d.screenshot(format="png")))
            await asyncio.sleep(0.5)
        print(f"baseline hashes: {sorted(baseline)}")

        totals, api_ts, ready_ts, stable_ts = [], [], [], []
        back = stable_n = text_back = 0
        for i in range(args.iterations):
            await d.keyboard.type(f" junk{i}")
            await asyncio.sleep(0.5)
            t0 = time.perf_counter()
            await d.revert(snap)
            t_api = time.perf_counter() - t0
            t_ready = await reattach(d)
            png, h, t_stable, stable = await stable_screenshot(d)
            total = time.perf_counter() - t0
            ok = h in baseline
            totals.append(total); api_ts.append(t_api); ready_ts.append(t_ready); stable_ts.append(t_stable)
            back += ok; stable_n += stable
            if i == 0:
                print("saved", save_png("spike01_after_first_revert.png", png))
            print(f"  revert {i + 1:2d}/{args.iterations}: api {t_api:5.2f}s  ready +{t_ready:5.2f}s  "
                  f"stable +{t_stable:4.1f}s  total {total:5.2f}s  state_back={ok} stable={stable}")
        # Text check once at the end (it mutates the selection, so not per-iteration).
        text = await editor_text(d)
        text_back = int(MARKER in text and "junk" not in text)

        s = summarize(totals)
        print_table(
            ["metric", "n", "p50", "p95", "p99", "max", "mean"],
            [["revert_total_s", s["n"], s["p50"], s["p95"], s["p99"], s["max"], s["mean"]],
             ["revert_api_s", *[summarize(api_ts)[k] for k in ("n", "p50", "p95", "p99", "max", "mean")]],
             ["ready_after_api_s", *[summarize(ready_ts)[k] for k in ("n", "p50", "p95", "p99", "max", "mean")]],
             ["screen_stable_s", *[summarize(stable_ts)[k] for k in ("n", "p50", "p95", "p99", "max", "mean")]]],
        )
        print(f"state_back {back}/{args.iterations}  screen_stable {stable_n}/{args.iterations}  "
              f"marker_text_back={bool(text_back)}  snapshot_s={snap_s:.1f}")
        n = f"n={args.iterations}"
        log_result(1, "snapshot_s", round(snap_s, 3), "s")
        log_result(1, "revert_wall_p50", round(s["p50"], 3), "s", n)
        log_result(1, "revert_wall_p95", round(s["p95"], 3), "s", n)
        log_result(1, "revert_wall_p99", round(s["p99"], 3), "s", n)
        log_result(1, "revert_api_p50", round(summarize(api_ts)["p50"], 3), "s", n)
        log_result(1, "state_back_rate", back / args.iterations, "fraction", "screenshot hash in post-snapshot set")
        log_result(1, "screen_stable_rate", stable_n / args.iterations, "fraction", "two identical hashes within 15s")
        log_result(1, "marker_text_back", bool(text_back), "bool", "clipboard read after final revert")
    finally:
        await kill_quietly(d)
        await delete_snapshot_quietly(client, snap)


if __name__ == "__main__":
    p = base_parser(__doc__)
    p.add_argument("--iterations", type=int, default=20)
    run(1, p, main)
