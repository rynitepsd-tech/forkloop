"""Throughput/latency of a student endpoint with the exact serving request shape.

Requests are rendered by ``forkloop.policies.student.StudentPolicy.build_request`` (compact
prompt, 1280-px screenshots as PNG data URLs, thinking disabled through
``chat_template_kwargs``) from recorded probe states (``train/probe_select.py``). ``--images 1``
sends the current screenshot only, ``--images 2`` previous + current. Every request gets a
unique one-pixel perturbation of its screenshots so neither the multimodal encoder cache nor
prefix caching can reuse image tokens across requests (the text system prompt stays cacheable,
as it is in real episodes). Output length is fixed with ``max_tokens`` + ``ignore_eos`` so
runs are comparable.

    python -m train.serve.bench --base-url http://127.0.0.1:8000/v1 --model holo-3.1-9b \\
        --states /lambda/nfs/forkloop-usw3/datasets/probe_states_20260928 --images 1 2 \\
        --concurrency 1 8 32 --out runs/bench-holo.json
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402


def load_states(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "states.jsonl").read_text().splitlines() if line.strip()]


def perturbed_png(path: Path, k: int, scale: float = 1.0) -> bytes:
    im = Image.open(path).convert("RGB")
    if scale != 1:  # client-side upscale (PIL LANCZOS), as the proposed observation ``image_scale`` would
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    x, y = k % im.width, (k // im.width) % im.height
    r, g, b = im.getpixel((x, y))
    im.putpixel((x, y), ((r + 1 + k) % 256, g, b))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def build_bodies(states: list[dict], root: Path, *, model: str, n: int, images: int, max_tokens: int,
                 seed_offset: int = 0, image_scale: float = 1.0, proc_scale: float = 1.0) -> list[dict]:
    from forkloop.policies.student import StudentPolicy
    from forkloop.types import Observation

    extra = {"chat_template_kwargs": {"enable_thinking": False}, "ignore_eos": True}
    if proc_scale != 1:  # upscale inside vLLM's image processor instead (minimum-pixel bound)
        extra["mm_processor_kwargs"] = {"size": {"shortest_edge": round(1280 * proc_scale) * round(720 * proc_scale),
                                                 "longest_edge": 16777216}}
    pol = StudentPolicy("http://127.0.0.1:1/v1", model, prompt_style="compact", coord_space="norm1000",
                        max_tokens=max_tokens, prev_screenshot=True, image_max_side=0, extra_body=extra)
    bodies = []
    for k in range(n):
        s = states[k % len(states)]
        cur = perturbed_png(root / s["images"][-1], seed_offset + k, image_scale)
        if images == 2:
            prev_path = root / s["images"][0]
            prev = perturbed_png(prev_path, seed_offset + k + 7, image_scale)
            obs = Observation(cur, s["instruction"], max(1, s["step"]), s["history"], 1280, 720, prev)
        else:
            obs = Observation(cur, s["instruction"], 0, [], 1280, 720)
        body, _ = pol.build_request(obs)
        bodies.append(body)
    asyncio.run(pol.aclose())
    return bodies


async def run_level(base_url: str, bodies: list[dict], concurrency: int, timeout: float) -> dict:
    sem = asyncio.Semaphore(concurrency)
    lat: list[float] = []
    usage = {"prompt": [], "completion": []}
    errors = 0
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                 limits=httpx.Limits(max_connections=concurrency + 4)) as client:
        async def one(body: dict) -> None:
            nonlocal errors
            async with sem:
                t0 = time.perf_counter()
                r = await client.post("/chat/completions", json=body)
                dt = time.perf_counter() - t0
                if r.status_code != 200:
                    errors += 1
                    return
                u = r.json().get("usage") or {}
                usage["prompt"].append(u.get("prompt_tokens", 0))
                usage["completion"].append(u.get("completion_tokens", 0))
                lat.append(dt)

        t0 = time.perf_counter()
        await asyncio.gather(*(one(b) for b in bodies))
        wall = time.perf_counter() - t0
    lat.sort()

    def q(p: float) -> float | None:
        return lat[min(len(lat) - 1, int(round(p * (len(lat) - 1))))] if lat else None

    return {"concurrency": concurrency, "requests": len(bodies), "ok": len(lat), "errors": errors,
            "wall_s": wall, "req_per_s": len(lat) / wall if wall else None,
            "latency_p50_s": q(0.5), "latency_p90_s": q(0.9), "latency_mean_s": statistics.mean(lat) if lat else None,
            "prompt_tokens_mean": statistics.mean(usage["prompt"]) if usage["prompt"] else None,
            "completion_tokens_mean": statistics.mean(usage["completion"]) if usage["completion"] else None,
            "output_tok_per_s": sum(usage["completion"]) / wall if wall else None}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    p.add_argument("--model", required=True)
    p.add_argument("--states", required=True)
    p.add_argument("--images", type=int, nargs="+", default=[1, 2])
    p.add_argument("--concurrency", type=int, nargs="+", default=[1, 8, 32])
    p.add_argument("--requests-per-level", type=int, default=0, help="default max(16, 4 x concurrency)")
    p.add_argument("--max-tokens", type=int, default=64)
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--image-scale", type=float, default=1.0, help="client-side LANCZOS upscale of every screenshot")
    p.add_argument("--proc-scale", type=float, default=1.0, help="upscale inside vLLM's processor (mm_processor_kwargs)")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    root = Path(a.states)
    states = load_states(root)
    results = []
    # warm-up: compile/capture paths for both image counts
    for images in a.images:
        asyncio.run(run_level(a.base_url, build_bodies(states, root, model=a.model, n=2, images=images,
                                                       max_tokens=a.max_tokens, seed_offset=10**6,
                                                       image_scale=a.image_scale, proc_scale=a.proc_scale),
                              1, a.timeout))
    offset = 0
    for images in a.images:
        for c in a.concurrency:
            n = a.requests_per_level or max(16, 4 * c)
            bodies = build_bodies(states, root, model=a.model, n=n, images=images, max_tokens=a.max_tokens,
                                  seed_offset=offset, image_scale=a.image_scale, proc_scale=a.proc_scale)
            offset += n
            r = asyncio.run(run_level(a.base_url, bodies, c, a.timeout))
            r["images"] = images
            results.append(r)
            print(json.dumps(r), flush=True)
    out = {"model": a.model, "base_url": a.base_url, "max_tokens": a.max_tokens, "ignore_eos": True,
           "image_scale": a.image_scale, "proc_scale": a.proc_scale,
           "states": str(root), "results": results, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
