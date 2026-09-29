"""Training/serving parity check for one or more rendered training records.

For each record it builds BOTH paths from the same observation and compares them:

* **training path**: ``train.train_lora.SFTExamples`` (the canonical builder
  ``forkloop.policies.observation.observation_messages``, memory included) -> ``make_collate``
  (``AutoProcessor`` chat template with the serving ``chat_template_kwargs``, processor call,
  label masking).
* **serving path**: ``forkloop.policies.student.StudentPolicy.build_request`` for the same
  observation (instruction, full history, memory, previous + current PNGs) -> the exact HTTP body
  the student sends. Its token ids are obtained two ways:
  ``offline``: the HF processor run the way vLLM runs it (chat template over the OpenAI-format
  messages with the request's ``chat_template_kwargs``, then the processor on the prompt text and
  the images decoded from the request's data URLs); ``live`` (``--base-url``): the vLLM server's own
  ``prompt_token_ids`` for that body (``return_token_ids: true``, one generated token).

Checks (all must pass; the report lists every one per record):
``messages_equal`` (text and part order), ``images_equal`` (count, order and decoded pixels of the
request images vs the training images), ``prompt_ids_offline`` / ``prompt_ids_live`` (serving
prompt ids == training prompt ids), ``image_grid_equal`` (processor grids; live: same number of
image tokens), ``thinking_disabled`` (request sends ``enable_thinking=false`` and both prompts end
with the empty think block), ``boundary`` (the prompt ids are a strict prefix of the training ids
and the first supervised position is the first target token), ``labels_mask`` (-100 on every prompt
token and after the end-of-turn token; the supervised tokens decode to exactly the target text +
``<|im_end|>``), ``concat_stable`` (tokenizing prompt text + target text in one go gives the same ids:
no token merges across the boundary), ``no_truncation`` (image tokens == grid, target complete,
length under ``--max-seq-len``).

    python -m train.parity --model Hcompany/Holo-3.1-9B --dataset data/smoke-ds-20260929 --all \\
        --system-prompt-file forkloop/policies/prompts/agent_memory_v1.md --coord-space norm1000 \\
        --base-url http://127.0.0.1:8000/v1 --served-model holo-3.1-9b --out runs/parity.json
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from train.train_lora import (DATASET_SCHEMA, THINK_OFF_PREFIX, SFTExamples, dataset_image_paths,  # noqa: E402
                              load_dataset_records, load_records, template_kwargs_for, verify_dataset_dir)


def canonical_http(messages: list[dict]) -> list[dict]:
    """OpenAI-format request messages -> HF chat-template blocks (image_url -> image), order kept."""
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            c = [{"type": "text", "text": c}]
        else:
            c = [{"type": "image"} if p["type"] == "image_url" else dict(p) for p in c]
        out.append({"role": m["role"], "content": c})
    return out


def request_images(messages: list[dict]) -> list[Any]:
    from PIL import Image
    out = []
    for m in messages:
        if isinstance(m["content"], str):
            continue
        for p in m["content"]:
            if p["type"] == "image_url":
                raw = base64.b64decode(p["image_url"]["url"].split(",", 1)[1])
                out.append(Image.open(io.BytesIO(raw)).convert("RGB"))
    return out


def pixel_digest(im) -> str:
    return hashlib.sha256(im.tobytes() + repr(im.size).encode()).hexdigest()[:16]


def first_mismatch(a: list[int], b: list[int]) -> int | None:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def observation_for(record: dict, max_image_side: int):
    """(Observation, memory or None) for a record, exactly as the serving loop would have it."""
    from forkloop.types import Observation
    if record.get("schema") == DATASET_SCHEMA:
        inp = record["input"]
        paths = dataset_image_paths(record)
        cur = paths[-1].read_bytes()
        prev = paths[0].read_bytes() if len(paths) == 2 else b""
        w, h = inp.get("screen") or (1280, 720)
        return Observation(cur, inp["instruction"], int(inp["step"]), list(inp.get("history") or []), int(w), int(h),
                           prev), inp.get("memory")
    paths = [Path(p) for p in record["images"]]
    w, h = record.get("screen_size") or (1280, 720)
    return Observation(paths[-1].read_bytes(), record.get("instruction", ""), int(record.get("step") or 0),
                       list(record.get("history") or []), int(w), int(h),
                       paths[0].read_bytes() if len(paths) == 2 else b""), record.get("memory")


def check_record(record: dict, *, processor, style: str, coord_space: str, history_k: int, max_image_side: int,
                 system_template: str | None, template_kwargs: dict, max_seq_len: int,
                 base_url: str | None = None, served_model: str | None = None, timeout: float = 300,
                 image_scale: float = 1.0) -> dict:
    import asyncio

    import httpx

    from forkloop.policies.student import StudentPolicy
    from train.train_lora import make_collate

    tok = processor.tokenizer
    checks: dict[str, dict] = {}

    def put(name: str, ok: bool, **detail: Any) -> None:
        checks[name] = {"ok": bool(ok), **detail}

    # training path
    ex = SFTExamples([record], max_image_side=max_image_side, style=style, history_k=history_k,
                     coord_space=coord_space, system_prompt_template=system_template, image_scale=image_scale)[0]
    collate = make_collate(processor, template_kwargs=template_kwargs, max_seq_len=max_seq_len)
    batch = collate([ex])
    stats = collate.stats_rows[-1]
    ids = batch["input_ids"][0].tolist()
    labels = batch["labels"][0].tolist()
    n_prompt = stats["prompt_len"]
    train_prompt_ids = ids[:n_prompt]
    train_prompt_text = processor.apply_chat_template(ex["prompt_messages"], tokenize=False, add_generation_prompt=True,
                                                      **template_kwargs)

    # serving path: the exact request StudentPolicy sends
    obs, memory = observation_for(record, max_image_side)
    pol = StudentPolicy(base_url or "http://127.0.0.1:1/v1", served_model or "parity", prompt_style=style,
                        coord_space=coord_space, image_max_side=max_image_side, history_k=history_k,
                        prev_screenshot=True, memory=memory is not None, system_prompt=system_template,
                        image_scale=image_scale,
                        extra_body={"chat_template_kwargs": dict(template_kwargs)} if template_kwargs else None)
    if memory is not None:
        pol._memory = list(memory)
    body, _ctx = pol.build_request(obs)
    asyncio.run(pol.aclose())
    messages = body["messages"]
    put("messages_equal", canonical_http(messages) == ex["prompt_messages"])
    req_imgs = request_images(messages)
    train_digests = [pixel_digest(im) for im in ex["images"]]
    req_digests = [pixel_digest(im) for im in req_imgs]
    put("images_equal", train_digests == req_digests, train=train_digests, request=req_digests,
        sizes=[list(im.size) for im in req_imgs])

    # serving tokens, offline (HF processor as vLLM applies it)
    kw = body.get("chat_template_kwargs") or {}
    serve_text = processor.apply_chat_template(canonical_http(messages), tokenize=False, add_generation_prompt=True, **kw)
    enc = processor(text=[serve_text], images=req_imgs, return_tensors="pt")
    serve_ids = enc["input_ids"][0].tolist()
    mm = first_mismatch(serve_ids, train_prompt_ids)
    put("prompt_ids_offline", mm is None, n_serving=len(serve_ids), n_training=len(train_prompt_ids), first_mismatch=mm)
    train_grid = batch["image_grid_thw"].tolist() if "image_grid_thw" in batch else None
    serve_grid = enc["image_grid_thw"].tolist() if "image_grid_thw" in enc else None
    put("image_grid_equal", train_grid == serve_grid, training=train_grid, serving=serve_grid,
        image_tokens=stats["image_tokens"])
    if "pixel_values" in batch and "pixel_values" in enc:
        import torch
        put("pixel_values_equal", bool(torch.equal(batch["pixel_values"], enc["pixel_values"])),
            shape=list(enc["pixel_values"].shape),
            image_processor_backend=getattr(processor.image_processor, "backend", type(processor.image_processor).__name__),
            note="vLLM 0.30 loads the same processor with use_fast=True (torchvision backend)")
    think_ok = (kw.get("enable_thinking") is False and serve_text.endswith(THINK_OFF_PREFIX)
                and train_prompt_text.endswith(THINK_OFF_PREFIX)) if template_kwargs.get("enable_thinking") is False \
        else (kw == template_kwargs)
    put("thinking_disabled" if template_kwargs.get("enable_thinking") is False else "thinking_as_configured", think_ok,
        request_kwargs=kw, prompt_tail=serve_text[-40:])

    # boundary + labels
    end_id = tok.convert_tokens_to_ids("<|im_end|>")
    sup = [i for i, x in enumerate(labels) if x != -100]
    target_ids = [ids[i] for i in sup]
    decoded = tok.decode(target_ids)
    put("boundary", bool(sup) and sup[0] == n_prompt and sup == list(range(n_prompt, n_prompt + len(sup))),
        prompt_len=n_prompt, first_supervised=sup[0] if sup else None)
    put("labels_mask", all(x == -100 for x in labels[:n_prompt]) and bool(target_ids) and target_ids[-1] == end_id
        and decoded == ex["target"] + "<|im_end|>" and len(ids) == n_prompt + len(sup),
        supervised_tokens=len(sup), decoded_target=decoded)
    joint = tok(train_prompt_text + ex["target"], add_special_tokens=False)["input_ids"]
    text_prompt_ids = tok(train_prompt_text, add_special_tokens=False)["input_ids"]
    target_only = tok(ex["target"], add_special_tokens=False)["input_ids"]
    put("concat_stable", joint == text_prompt_ids + target_only,
        note="tokenizing prompt+target jointly equals prompt ids followed by target ids (no merged tokens)")
    grid_tokens = None
    merge = getattr(processor.image_processor, "merge_size", 2)
    if train_grid is not None:
        grid_tokens = sum(t * h * w for t, h, w in train_grid) // (merge * merge)
    put("no_truncation", grid_tokens == stats["image_tokens"] and len(ids) <= max_seq_len
        and decoded.startswith(ex["target"]), seq_len=len(ids), max_seq_len=max_seq_len,
        image_tokens=stats["image_tokens"], grid_tokens=grid_tokens, images=len(ex["images"]))

    # serving tokens, live (vLLM's own prompt ids for this exact body)
    if base_url:
        live_body = dict(body, max_tokens=1, temperature=0.0, return_token_ids=True)
        r = httpx.post(base_url.rstrip("/") + "/chat/completions", json=live_body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        live_ids = data.get("prompt_token_ids") or (data.get("choices") or [{}])[0].get("prompt_token_ids")
        mm = first_mismatch(live_ids or [], train_prompt_ids)
        image_id = tok.convert_tokens_to_ids(getattr(processor, "image_token", "<|image_pad|>"))
        put("prompt_ids_live", live_ids is not None and mm is None, n_live=len(live_ids or []),
            n_training=len(train_prompt_ids), first_mismatch=mm,
            live_image_tokens=(live_ids or []).count(image_id), usage=data.get("usage"))
    return {"record_id": record.get("record_id") or record.get("task_id"), "step": (record.get("input") or record).get("step"),
            "memory": memory, "target": ex["target"], "images": len(ex["images"]), "seq_len": len(ids),
            "ok": all(c["ok"] for c in checks.values()), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", required=True, help="HF id/path whose processor the training path uses")
    p.add_argument("--revision", default=None)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--dataset", help="forkloop.dataset.v1 directory (manifest hashes are verified first)")
    src.add_argument("--data", help="legacy sft.jsonl")
    p.add_argument("--index", type=int, action="append", default=None, help="record index (repeatable)")
    p.add_argument("--all", action="store_true", help="check every record")
    p.add_argument("--prompt-style", default="compact", choices=["compact", "json", "fara"])
    p.add_argument("--coord-space", default="norm1000")
    p.add_argument("--history-k", type=int, default=8)
    p.add_argument("--max-image-side", type=int, default=1280)
    p.add_argument("--image-scale", type=float, default=1.0, help="StudentPolicy/train_lora image_scale (student: 1.5 with --max-image-side 1920)")
    p.add_argument("--thinking", default="auto", choices=["auto", "off", "on"])
    p.add_argument("--max-seq-len", type=int, default=16384)
    p.add_argument("--system-prompt-file", default=None)
    p.add_argument("--base-url", default=None, help="vLLM endpoint for the live prompt-id comparison")
    p.add_argument("--served-model", default=None)
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    from transformers import AutoProcessor

    if a.dataset:
        info = verify_dataset_dir(a.dataset)
        records = load_dataset_records(a.dataset)
    else:
        info = {"data": a.data}
        records = load_records(a.data)
    chosen = list(range(len(records))) if a.all else (a.index or [0])
    processor = AutoProcessor.from_pretrained(a.model, revision=a.revision)
    system_template = Path(a.system_prompt_file).read_text(encoding="utf-8") if a.system_prompt_file else None
    tkw = template_kwargs_for(a.thinking, a.prompt_style)
    results = [check_record(records[i], processor=processor, style=a.prompt_style, coord_space=a.coord_space,
                            history_k=a.history_k, max_image_side=a.max_image_side, system_template=system_template,
                            template_kwargs=tkw, max_seq_len=a.max_seq_len, base_url=a.base_url,
                            served_model=a.served_model, image_scale=a.image_scale) for i in chosen]
    report = {"model": a.model, "source": info, "records_checked": len(results),
              "records_ok": sum(r["ok"] for r in results),
              "memory_records_checked": sum(bool(r["memory"]) for r in results),
              "two_image_records_checked": sum(r["images"] == 2 for r in results),
              "chat_template_kwargs": tkw, "live": bool(a.base_url), "image_scale": a.image_scale,
              "max_image_side": a.max_image_side,
              "failed_checks": sorted({k for r in results for k, c in r["checks"].items() if not c["ok"]}),
              "results": results}
    text = json.dumps(report, indent=2, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text)
    print(json.dumps({k: report[k] for k in ("records_checked", "records_ok", "memory_records_checked",
                                             "two_image_records_checked", "failed_checks", "live")}))
    return 0 if report["records_ok"] == report["records_checked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
