"""Offline reading / grounding / format probe of a candidate student (development evidence only).

Runs frozen recorded states (``train/probe_select.py``) against an OpenAI-compatible endpoint
with thinking disabled (``chat_template_kwargs.enable_thinking=false``) and greedy decoding.
No world is needed. Probes:

* ``read`` (read states): the current screenshot and "what is the authorization number shown?";
  exact string match against the manifest's expected number.
* ``read_x<S>`` (optional, e.g. ``read_x1.5``, ``read_x2``): the same question on the screenshot
  upscaled by S on the client (PIL LANCZOS), i.e. the proposed observation-builder ``image_scale``.
  ``read_proc_x<S>``: the native PNG, upscaled by the image processor inside vLLM through the
  per-request ``mm_processor_kwargs`` minimum-pixel bound (bicubic).
* ``ground_norm1000`` / ``ground_pixel`` (click states): the current screenshot and the teacher's
  own reasoning line for that step as the target description; the model answers ``click(x, y)``
  in 0-1000 normalized coordinates or in 1280x720 pixels. Scored as distance to the teacher's
  click: **agreement with the teacher**, not correctness (another point on the same element is
  just as good, and the teacher is sometimes wrong).
* ``agent_*`` (all states): the real serving prompt rendered by ``StudentPolicy.build_request``
  (instruction, last 8 compact actions, previous + current screenshots) and parsed by
  ``StudentPolicy.parse_choice``. ``agent_compact_norm1000`` and ``agent_compact_pixel`` use the
  default compact prompt; ``agent_memory_norm1000`` uses ``prompts/agent_memory_v1.md`` with
  ``memory=True`` (empty memory: the teacher wrote none). Reported: strict format validity (the
  last line is exactly one compact action), lenient parse validity, next-click agreement with the
  teacher on click states, and whether the reply (or a ``Memory:`` line) carries the exact
  authorization number on read states.

    python -m train.probe_student --base-url http://127.0.0.1:8000/v1 --model holo-3.1-9b \\
        --states /lambda/nfs/forkloop-usw3/datasets/probe_states_20260928 --out runs/probe-holo31-9b
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx  # noqa: E402

AUTH_RE = re.compile(r"AUTH-[A-Z0-9]+")
CLICK_RE = re.compile(r"click\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")
READ_PROMPT = ("This screenshot shows a document. What is the authorization number printed in the document? "
               "Reply with the authorization number only, exactly as printed.")
GROUND_PROMPTS = {
    "ground_norm1000": ("You are operating this screen. Next step: {target}\n"
                        "Where should the mouse click to perform this step? Reply with only click(x, y), where x and y "
                        "are integers from 0 to 1000 giving the position relative to the image width and height "
                        "(0, 0 is the top-left corner; 1000, 1000 is the bottom-right corner)."),
    "ground_pixel": ("You are operating this screen. Next step: {target}\n"
                     "Where should the mouse click to perform this step? Reply with only click(x, y), where x and y "
                     "are pixel positions in this 1280x720 screenshot (x from 0 to 1279, y from 0 to 719)."),
}
AGENT_VARIANTS = ("agent_compact_norm1000", "agent_compact_pixel", "agent_memory_norm1000")
THRESH_PX = 20.0


def load_states(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "states.jsonl").read_text().splitlines() if line.strip()]


def data_url(path: Path, scale: float = 1.0) -> str:
    import base64
    import io
    if scale == 1:
        raw = path.read_bytes()
    else:  # pre-upscale on the client (PIL LANCZOS), the proposed observation-builder ``image_scale``
        from PIL import Image
        im = Image.open(path).convert("RGB")
        buf = io.BytesIO()
        im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS).save(buf, format="PNG")
        raw = buf.getvalue()
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def proc_kwargs(scale: float, size: tuple[int, int] = (1280, 720)) -> dict:
    """vLLM per-request processor kwargs that make the Qwen3.5 image processor itself upscale a
    1280x720 screenshot by ``scale`` (bicubic) through its minimum-pixel bound."""
    return {"size": {"shortest_edge": round(size[0] * scale) * round(size[1] * scale), "longest_edge": 16777216}}


def read_probe(name: str) -> tuple[float, bool] | None:
    """``read`` -> (1, False); ``read_x1.5`` -> (1.5, False); ``read_proc_x2`` -> (2, True)."""
    m = re.fullmatch(r"read(_proc)?(?:_x([0-9.]+))?", name)
    if not m:
        return None
    return float(m.group(2) or 1), bool(m.group(1))


def strict_format(text: str) -> bool:
    """Exactly one compact action and it is the last non-empty line."""
    from forkloop.policies.action_parse import parse_compact
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return False
    action, _ = parse_compact(lines[-1])
    if action is None or not re.match(r"^\w+\(.*\)$", lines[-1]):
        return False
    return sum(bool(re.match(r"^(click|double_click|right_click|move|drag|scroll|type|key|wait|done)\(", ln))
               for ln in lines) == 1


def make_policy(variant: str, model: str, max_tokens: int):
    from forkloop.policies.student import StudentPolicy
    extra = {"chat_template_kwargs": {"enable_thinking": False}}
    kw = dict(prompt_style="compact", prev_screenshot=True, history_k=8, max_tokens=max_tokens, temperature=0.0,
              extra_body=extra)
    if variant == "agent_compact_norm1000":
        return StudentPolicy("http://127.0.0.1:1/v1", model, coord_space="norm1000", **kw)
    if variant == "agent_compact_pixel":
        return StudentPolicy("http://127.0.0.1:1/v1", model, coord_space="image", **kw)
    prompt = (_ROOT / "forkloop/policies/prompts/agent_memory_v1.md").read_text(encoding="utf-8")
    return StudentPolicy("http://127.0.0.1:1/v1", model, coord_space="norm1000", memory=True, system_prompt=prompt, **kw)


def build_jobs(states: list[dict], root: Path, model: str, probes: list[str]) -> list[dict]:
    from forkloop.types import Observation
    jobs = []
    base = {"model": model, "temperature": 0.0, "chat_template_kwargs": {"enable_thinking": False}}
    for s in states:
        cur = root / s["images"][-1]
        for name in probes:
            rp = read_probe(name)
            if rp is None or s["kind"] != "read":
                continue
            scale, in_processor = rp
            body = dict(base, max_tokens=64, messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url(cur, 1 if in_processor else scale)}},
                {"type": "text", "text": READ_PROMPT}]}])
            if in_processor and scale != 1:
                body["mm_processor_kwargs"] = proc_kwargs(scale)
            jobs.append({"probe": name, "state": s, "body": body})
        if s["kind"] == "click":
            for name, tmpl in GROUND_PROMPTS.items():
                if name not in probes:
                    continue
                text = tmpl.format(target=s["score"]["teacher_reasoning"])
                body = dict(base, max_tokens=32, messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_url(cur)}}, {"type": "text", "text": text}]}])
                jobs.append({"probe": name, "state": s, "body": body})
        for variant in AGENT_VARIANTS:
            if variant not in probes:
                continue
            pol = make_policy(variant, model, 256)
            prev = (root / s["images"][0]).read_bytes() if len(s["images"]) == 2 else b""
            obs = Observation(cur.read_bytes(), s["instruction"], s["step"], s["history"], 1280, 720, prev)
            body, ctx = pol.build_request(obs)
            jobs.append({"probe": variant, "state": s, "body": body, "ctx": ctx, "policy": pol})
    return jobs


def score(job: dict, data: dict) -> dict:
    s = job["state"]
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    out = {"probe": job["probe"], "id": s["id"], "kind": s["kind"], "reply": text,
           "reasoning_field": msg.get("reasoning_content") or msg.get("reasoning"),
           "finish_reason": choice.get("finish_reason"), "usage": data.get("usage")}
    expected = s["score"]["expected_auth"]
    ta = s["score"]["teacher_action"] or {}
    if read_probe(job["probe"]):
        found = AUTH_RE.findall(text.upper())
        out.update(expected=expected, found=found, exact=bool(found) and found[0] == expected,
                   contains=expected in text)
    elif job["probe"].startswith("ground_"):
        m = CLICK_RE.search(text)
        out["valid"] = bool(m)
        if m:
            x, y = float(m.group(1)), float(m.group(2))
            if job["probe"] == "ground_norm1000":
                x, y = x / 1000 * 1280, y / 1000 * 720
            d = math.hypot(x - ta["x"], y - ta["y"])
            out.update(pred_px=[round(x, 1), round(y, 1)], teacher_px=[ta["x"], ta["y"]], dist_px=d, agree=d <= THRESH_PX)
    else:
        from forkloop.policies.action_parse import to_compact
        pol = job["policy"]
        action, meta = pol.parse_choice(choice, job["ctx"])
        parsed = meta.get("parsed")
        out.update(strict=strict_format(text), lenient=action is not None,
                   action=to_compact(parsed) if parsed else None)
        if s["kind"] == "click" and parsed and parsed.get("type") == "click":
            d = math.hypot(parsed["x"] - ta["x"], parsed["y"] - ta["y"])
            out.update(dist_px=d, agree=d <= THRESH_PX)
        elif s["kind"] == "click":
            out.update(dist_px=None, agree=False)
        if s["kind"] == "read":
            from forkloop.policies.student import memory_from_reply
            mem = memory_from_reply(text)
            out.update(expected=expected, auth_in_reply=expected in text,
                       auth_in_memory=any(expected in f for f in mem), memory=mem)
    return out


async def run_jobs(base_url: str, jobs: list[dict], concurrency: int, timeout: float) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    results: list[dict | None] = [None] * len(jobs)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        async def one(i: int, job: dict) -> None:
            async with sem:
                t0 = time.perf_counter()
                r = await client.post("/chat/completions", json=job["body"])
                dt = time.perf_counter() - t0
                if r.status_code != 200:
                    results[i] = {"probe": job["probe"], "id": job["state"]["id"], "error": r.text[:500]}
                    return
                res = score(job, r.json())
                res["latency_s"] = dt
                results[i] = res
        await asyncio.gather(*(one(i, j) for i, j in enumerate(jobs)))
    return [r for r in results if r is not None]


def summarize(rows: list[dict]) -> dict:
    out: dict = {}
    for probe in sorted({r["probe"] for r in rows}):
        rs = [r for r in rows if r["probe"] == probe]
        errs = sum("error" in r for r in rs)
        rs = [r for r in rs if "error" not in r]
        d: dict = {"n": len(rs), "errors": errs}
        if read_probe(probe):
            d.update(exact=sum(r["exact"] for r in rs), contains=sum(r["contains"] for r in rs),
                     prompt_tokens_mean=statistics.mean(r["usage"]["prompt_tokens"] for r in rs) if rs else None)
        elif probe.startswith("ground_"):
            dist = [r["dist_px"] for r in rs if r.get("dist_px") is not None]
            d.update(valid=sum(r["valid"] for r in rs), agree_20px=sum(bool(r.get("agree")) for r in rs),
                     within_40px=sum(x <= 40 for x in dist),
                     median_dist_px=statistics.median(dist) if dist else None)
        else:
            clicks = [r for r in rs if r["kind"] == "click"]
            reads = [r for r in rs if r["kind"] == "read"]
            dist = [r["dist_px"] for r in clicks if r.get("dist_px") is not None]
            d.update(strict_format=sum(r["strict"] for r in rs), lenient_parse=sum(r["lenient"] for r in rs),
                     click_states=len(clicks), predicted_click=len(dist),
                     next_click_agree_20px=sum(bool(r.get("agree")) for r in clicks),
                     median_dist_px=statistics.median(dist) if dist else None,
                     read_states=len(reads), auth_in_reply=sum(r["auth_in_reply"] for r in reads),
                     auth_in_memory=sum(r["auth_in_memory"] for r in reads))
        lat = [r["latency_s"] for r in rs if "latency_s" in r]
        d["latency_mean_s"] = statistics.mean(lat) if lat else None
        out[probe] = d
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    p.add_argument("--model", required=True)
    p.add_argument("--states", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--probes", default="read,ground_norm1000,ground_pixel," + ",".join(AGENT_VARIANTS))
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--timeout", type=float, default=600)
    a = p.parse_args(argv)
    root = Path(a.states)
    states = load_states(root)
    jobs = build_jobs(states, root, a.model, [x.strip() for x in a.probes.split(",") if x.strip()])
    rows = asyncio.run(run_jobs(a.base_url, jobs, a.concurrency, a.timeout))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "rows.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta = json.loads((root / "states.meta.json").read_text()) if (root / "states.meta.json").exists() else {}
    summary = {"model": a.model, "base_url": a.base_url, "states": str(root), "states_sha256": meta.get("states_sha256"),
               "decoding": "greedy (temperature 0), thinking disabled", "threshold_px": THRESH_PX,
               "label": "development evidence; click numbers are agreement with the teacher, not correctness",
               "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "summary": summarize(rows)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
