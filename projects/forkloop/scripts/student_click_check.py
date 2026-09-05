"""Hand-check one student click on a fresh fork of the golden (the step-2 gate before any bake-off).

Forks the golden, resets a family-3 episode (the portal claims list), asks the served
student for ONE click on a named control, and logs the four values that catch a
coordinate-space bug: the image size the policy sent, the raw model output, the
parsed coordinate in the model's space, and the rescaled screen coordinate. Writes
the full-size screenshot with a crosshair at the rescaled point next to the JSON log
so a human can confirm the point lands on the control.

    PYTHONPATH=. python scripts/student_click_check.py --student-url http://127.0.0.1:8001/v1 \
        --model microsoft/Fara1.5-4B --prompt-style fara --seed 200 \
        --control "the 'Patients' link in the top navigation bar" --out runs/logs/click-check

The machine is killed afterwards (one fork, a few cents). Nothing here is training data.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import time
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

from forkloop.backends.solari import SolariBackend
from forkloop.env import Env
from forkloop.policies.student import StudentPolicy
from forkloop.pool import WorkerPool
from forkloop.world import load_world


def draw_point(png: bytes, xy: tuple[int, int], out: Path) -> None:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    d = ImageDraw.Draw(im)
    x, y = xy
    d.ellipse((x - 12, y - 12, x + 12, y + 12), outline=(255, 0, 0), width=3)
    d.line((x - 24, y, x + 24, y), fill=(255, 0, 0), width=2)
    d.line((x, y - 24, x, y + 24), fill=(255, 0, 0), width=2)
    im.save(out)


async def main(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    world = load_world("claims-ops-v1")
    backend = SolariBackend(kind="desktop")
    pool = WorkerPool(backend, world, size=1, mode="fork")
    env = Env(world, backend, family="resolve_denial", split=args.split, pool=pool)
    pol = StudentPolicy(args.student_url, args.model, prompt_style=args.prompt_style, history_k=8,
                        image_max_side=args.image_max_side, coord_space=args.coord_space,
                        extra_body=json.loads(args.extra_body) if args.extra_body else None)
    log: dict = {"model": args.model, "prompt_style": args.prompt_style, "coord_space": pol.coord_space,
                 "image_max_side": args.image_max_side, "seed": args.seed, "control": args.control}
    try:
        await pool.start()
        t0 = time.monotonic()
        obs, info = await env.reset(args.seed)
        log["reset_s"] = round(time.monotonic() - t0, 1)
        log["screen_size"] = [obs.width, obs.height]
        (out / "screen.png").write_bytes(obs.screenshot)
        probe = replace(obs, instruction=f"Click {args.control}. Do nothing else.", history=[])
        body, ctx = pol.build_request(probe)
        log["image_size_sent"] = list(ctx["model_size"])
        log["coord_size"] = list(ctx["coord_size"])
        action, meta = await pol.act(probe)
        log["raw_model_output"] = meta.get("raw_action")
        log["thoughts"] = (meta.get("thoughts") or "")[:500]
        log["model_latency_s"] = round(meta.get("model_latency_s", 0.0), 2)
        log["tokens"] = meta.get("tokens")
        log["note"] = meta.get("note")
        log["rescaled_screen_action"] = meta.get("parsed")
        # the parsed coordinate in the model's own space, before rescaling
        from forkloop.policies import action_parse as ap
        d, err = ap.parse_any(meta.get("raw_action") or "", style=args.prompt_style)
        log["parsed_model_space"] = d
        log["parse_error"] = err
        if meta.get("parsed") and "x" in meta["parsed"]:
            xy = (int(meta["parsed"]["x"]), int(meta["parsed"]["y"]))
            draw_point(obs.screenshot, xy, out / "screen_with_point.png")
            log["crosshair_png"] = str(out / "screen_with_point.png")
        (out / "click_check.json").write_text(json.dumps(log, indent=2))
        print(json.dumps(log, indent=2))
        return 0 if action is not None else 1
    finally:
        await pol.aclose()
        await env.close()
        await pool.close()
        await backend.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--student-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--prompt-style", default="fara")
    p.add_argument("--coord-space", default="auto")
    p.add_argument("--image-max-side", type=int, default=1280)
    p.add_argument("--extra-body", default=None, help="JSON merged into the request body (e.g. '{\"enable_thinking\": false}')")
    p.add_argument("--seed", type=int, default=200)
    p.add_argument("--split", default="train")
    p.add_argument("--control", default="the 'Patients' link in the top navigation bar")
    p.add_argument("--out", default="runs/logs/click-check")
    raise SystemExit(asyncio.run(main(p.parse_args())))
