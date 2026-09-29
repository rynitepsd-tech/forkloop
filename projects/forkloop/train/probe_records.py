"""Query a served student on training/eval records through the real serving request.

For every ``forkloop.dataset.v1`` record, the request is ``StudentPolicy.build_request`` for the
record's observation (memory, history, previous + current screenshot), exactly as in
``train/parity.py``. Two measurements per record and per served model name (base and LoRA
adapters can be compared on one vLLM server, ``--model base --model smoke``):

* greedy prediction: strict format (reasoning, ``Memory:`` lines, one compact action as the last
  line), exact action match with the target (compact text in the student frame), click distance in
  screen pixels, ``Memory:`` facts equal to the target's;
* target log-likelihood: the target reply appended as an open assistant turn
  (``continue_final_message``) with ``prompt_logprobs``; the summed log-probability of the target
  tokens (and the end-of-turn token is not included), i.e. how much the model moved toward it.

    python -m train.probe_records --dataset data/smoke-ds --model holo-3.1-9b --model smoke \\
        --system-prompt-file forkloop/policies/prompts/agent_memory_v1.md --out runs/smoke-probe
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx  # noqa: E402

from train.parity import observation_for  # noqa: E402
from train.train_lora import load_dataset_records, template_kwargs_for, verify_dataset_dir  # noqa: E402


def build(record: dict, model: str, *, system_template: str | None, coord_space: str, history_k: int,
          max_tokens: int, base_url: str, image_scale: float = 1.0, image_max_side: int = 1280):
    from forkloop.correction.dataset import render_target
    from forkloop.policies.observation import coordinate_size
    from forkloop.policies.student import StudentPolicy

    obs, memory = observation_for(record, 1280)
    pol = StudentPolicy(base_url, model, prompt_style="compact", coord_space=coord_space, history_k=history_k,
                        prev_screenshot=True, memory=memory is not None, system_prompt=system_template,
                        image_scale=image_scale, image_max_side=image_max_side,
                        max_tokens=max_tokens, temperature=0.0,
                        extra_body={"chat_template_kwargs": template_kwargs_for("auto", "compact")})
    if memory is not None:
        pol._memory = list(memory)
    body, ctx = pol.build_request(obs)
    screen = tuple(record["input"].get("screen") or (1280, 720))
    target = render_target(record["target"], screen=screen, coords=coordinate_size(coord_space, "compact", screen, screen))
    return pol, body, ctx, target


async def one(client: httpx.AsyncClient, record: dict, model: str, a) -> dict:
    from forkloop.policies.action_parse import parse_compact, to_compact
    from forkloop.policies.student import memory_from_reply
    from train.probe_student import strict_format

    pol, body, ctx, target = build(record, model, system_template=a.system_template, coord_space=a.coord_space,
                                   history_k=a.history_k, max_tokens=a.max_tokens, base_url=a.base_url,
                                   image_scale=a.image_scale, image_max_side=a.image_max_side)
    r = await client.post("/chat/completions", json=body)
    r.raise_for_status()
    choice = r.json()["choices"][0]
    text = choice["message"].get("content") or ""
    action, meta = pol.parse_choice(choice, ctx)
    await pol.aclose()
    tgt_action = record["target"]["action"]
    pred = meta.get("parsed")
    t_line = target.splitlines()[-1]
    out = {"record_id": record.get("record_id"), "step": record["input"]["step"], "model": model, "reply": text,
           "target": target, "strict": strict_format(text), "valid": action is not None,
           "action_exact": bool(text.strip()) and text.strip().splitlines()[-1].strip() == t_line,
           "type_match": bool(pred) and pred.get("type") == tgt_action.get("type"),
           "memory_pred": memory_from_reply(text), "memory_target": list(record["target"].get("memory_written") or [])}
    out["memory_match"] = out["memory_pred"] == out["memory_target"]
    if pred and tgt_action.get("type") in ("click", "double_click", "right_click") and pred.get("type") == tgt_action["type"]:
        out["click_dist_px"] = math.hypot(pred["x"] - tgt_action["x"], pred["y"] - tgt_action["y"])
    # target log-likelihood (teacher forcing through vLLM's own prompt processing)
    tf = dict(body, messages=body["messages"] + [{"role": "assistant", "content": target}],
              add_generation_prompt=False, continue_final_message=True, prompt_logprobs=0, max_tokens=1,
              return_token_ids=True)
    r2 = await client.post("/chat/completions", json=tf)
    if r2.status_code == 200:
        d2 = r2.json()
        plp = d2.get("prompt_logprobs") or []
        ids = d2.get("prompt_token_ids") or []
        n_prompt = len((await client.post("/chat/completions", json=dict(body, max_tokens=1, return_token_ids=True)))
                       .json().get("prompt_token_ids") or [])
        lps = []
        for pos in range(n_prompt, len(plp)):
            entry = plp[pos] or {}
            tok = str(ids[pos]) if pos < len(ids) else None
            v = entry.get(tok) if tok is not None else None
            if v is None and entry:
                v = next(iter(entry.values()))
            if isinstance(v, dict):
                lps.append(float(v.get("logprob")))
        out.update(target_tokens=len(lps), target_logprob_sum=sum(lps) if lps else None,
                   target_logprob_mean=(sum(lps) / len(lps)) if lps else None)
    else:
        out["target_logprob_error"] = r2.text[:300]
    return out


async def run(records: list[dict], a) -> list[dict]:
    sem = asyncio.Semaphore(a.concurrency)
    async with httpx.AsyncClient(base_url=a.base_url, timeout=600) as client:
        async def guarded(rec, model):
            async with sem:
                return await one(client, rec, model, a)
        return await asyncio.gather(*(guarded(r, m) for m in a.model for r in records))


def summarize(rows: list[dict]) -> dict:
    out = {}
    for model in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == model]
        dist = [r["click_dist_px"] for r in rs if r.get("click_dist_px") is not None]
        lp = [r["target_logprob_mean"] for r in rs if r.get("target_logprob_mean") is not None]
        out[model] = {"n": len(rs), "strict_format": sum(r["strict"] for r in rs), "valid": sum(r["valid"] for r in rs),
                      "action_exact": sum(r["action_exact"] for r in rs), "type_match": sum(r["type_match"] for r in rs),
                      "memory_match": sum(r["memory_match"] for r in rs),
                      "memory_target_records": sum(bool(r["memory_target"]) for r in rs),
                      "memory_written_correct": sum(bool(r["memory_target"]) and r["memory_match"] for r in rs),
                      "clicks_compared": len(dist), "click_within_20px": sum(d <= 20 for d in dist),
                      "click_median_px": statistics.median(dist) if dist else None,
                      "target_logprob_mean_per_token": statistics.mean(lp) if lp else None,
                      "target_nll_sum_mean": -statistics.mean(r["target_logprob_sum"] for r in rs
                                                               if r.get("target_logprob_sum") is not None) if lp else None}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True)
    p.add_argument("--model", action="append", required=True, help="served model name (base or LoRA); repeatable")
    p.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    p.add_argument("--system-prompt-file", default=None)
    p.add_argument("--coord-space", default="norm1000")
    p.add_argument("--history-k", type=int, default=8)
    p.add_argument("--image-scale", type=float, default=1.0)
    p.add_argument("--image-max-side", type=int, default=1280)
    p.add_argument("--max-tokens", type=int, default=256)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    a.system_template = Path(a.system_prompt_file).read_text(encoding="utf-8") if a.system_prompt_file else None
    info = verify_dataset_dir(a.dataset)
    records = load_dataset_records(a.dataset, limit=a.limit)
    rows = asyncio.run(run(records, a))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "rows.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {"dataset": info, "models": a.model, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "decoding": "greedy, thinking disabled", "summary": summarize(rows)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
