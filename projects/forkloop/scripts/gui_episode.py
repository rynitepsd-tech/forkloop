"""A scripted GUI episode on the real desktop world — proves the agent channel end to end.

The "policy" is a deterministic keyboard-driven script derived from the task's
controller-side expected values (so it is a test fixture, not an agent): it
navigates Chrome to the claim's appeal form with ctrl+L, tabs into the form,
chooses the reason, types the authorization number and a narrative, submits,
and says done. Every step is recorded (before/after PNGs, steps.jsonl) and
the oracle judges the result exactly as it would for a model.

    FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1=snap_... python scripts/gui_episode.py --seed 1234 [--tabs 5]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from forkloop.actions import Action
from forkloop.backends.solari import SolariBackend
from forkloop.env import Env
from forkloop.pool import WorkerPool
from forkloop.trajectories import Recorder
from forkloop.world import load_world


def build_script(task, *, tabs_to_form: int = 0, wrong: bool = False) -> list[str]:
    """Click-driven script; field positions are for the portal's fixed 1280x720 layout."""
    ex = task.expected
    auth = ex["decoy_numbers"][0] if wrong else ex["auth_number"]
    url = f"http://localhost:8080/claims/{ex['claim_number']}/appeal"
    return [
        "click(640, 90)", 'key("ctrl+a")', f'type("{url}")', 'key("Return")', "wait(2.5)",
        "click(340, 382)", "wait(0.5)", 'type("Prior")', 'key("Return")',          # reason <select>
        "click(340, 464)", f'type("{auth}")',                                       # authorization number
        "click(340, 595)", 'type("Prior authorization was obtained before the service date.")',
        "click(340, 464)", 'key("Return")', "wait(2.5)",                            # Enter in a text input submits
        "done()",
    ]


async def main(args: argparse.Namespace) -> int:
    world = load_world("claims-ops-v1")
    backend = SolariBackend(kind="desktop")
    pool = WorkerPool(backend, world, size=1, mode="fork", run_id="gui-episode")
    rec = Recorder(args.runs, run_id=args.run_id, meta={"backend": "solari-desktop", "policy": "scripted-gui"})
    env = Env(world, backend, family="resolve_denial", pool=pool, recorder=rec, settle_s=0.8, stable_after_action=False)
    try:
        t0 = time.monotonic()
        obs, info = await env.reset(args.seed)
        task = env.ep.task
        print("reset:", json.dumps({k: round(v, 2) for k, v in ((s["name"], s["seconds"]) for s in info["reset"]["stages"])}),
              f"total {info['reset']['total_seconds']:.1f}s", flush=True)
        print("stream:", info.get("stream_url"), flush=True)
        script = build_script(task, tabs_to_form=args.tabs, wrong=args.wrong)
        for i, raw in enumerate(script):
            a = Action.parse(raw, width=obs.width, height=obs.height)
            obs, reward, term, trunc, info2 = await env.step(a, meta={"raw_action": raw})
            print(f"  step {i:02d} {raw[:60]:60s} err={info2.get('error')}", flush=True)
            if term or trunc:
                break
        v = await env.verify()
        print(f"verdict: reward={v.reward} reason={v.reason_code} failed={v.failed} milestones={v.milestones:.2f} "
              f"episode {time.monotonic() - t0:.1f}s", flush=True)
        print("run dir:", rec.dir)
        for ep in rec.episodes():
            print("shots:", sorted(p.name for p in (ep / "shots").iterdir())[:6], "...")
        return 0 if v.reward >= 1.0 else 1
    finally:
        await env.close()
        await pool.close()
        await backend.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--tabs", type=int, default=6, help="Tab presses from page load to the reason select")
    ap.add_argument("--wrong", action="store_true", help="type a decoy number (oracle must reject)")
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--run-id", default=None)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
