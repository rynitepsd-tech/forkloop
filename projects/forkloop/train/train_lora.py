"""LoRA supervised fine-tuning of a Qwen3.5-VL-style / Fara 1.5 student on ``sft.jsonl``.

Both ``microsoft/Fara1.5-4B`` and the Qwen3.5-VL family are Qwen3.5-based
image-text-to-text models, so one script covers both: ``AutoProcessor`` +
``AutoModelForImageTextToText`` with a fallback chain of explicit Qwen-VL
classes. Each SFT record becomes one chat: system prompt (the same one
``forkloop.policies.student`` uses for the chosen ``--prompt-style``), a user
turn holding the screenshot + instruction + history, and an assistant turn
holding the target action. Labels are masked (``-100``) on every prompt token
so the loss is only on the action text.

VRAM expectations for a 4B model, LoRA r=16 on all attention+MLP projections,
bf16 weights, gradient checkpointing ON, one 1280x720 screenshot per example
(about 1.2k visual tokens + ~500 text tokens). **These are estimates, not
measurements** — check ``peak_vram_gb`` in ``train_log.jsonl`` on your card:

    weights (bf16)                          ~8 GB
    LoRA params + AdamW states (~35M)       <0.5 GB
    activations, batch 1 (checkpointed)     ~3-6 GB
    activations, batch 2 (checkpointed)     ~6-10 GB
    ---------------------------------------------------
    total, batch 1                          ~12-15 GB  -> fits a 24 GB card (4090 / L4 / A10G)
    total, batch 2                          ~16-20 GB  -> 24 GB card is tight; 48 GB (A6000 / L40S) comfortable
    without gradient checkpointing          roughly 2x the activation term

Throughput estimate: 1.5-3 s per micro-step at batch 1 on an RTX 4090; 200
verified episodes x ~15 steps = ~3k examples, so one epoch is ~1.5-2.5 h.

The module imports cleanly without torch/transformers/peft (they are imported
lazily inside the functions that need them), so ``--help`` and the dataset
helpers work on a laptop.

Usage::

    python -m train.train_lora --model microsoft/Fara1.5-4B --data data/sft_200.jsonl \\
        --output-dir ckpt/fara_sft_200 --prompt-style fara --epochs 2 --lr 1e-4 \\
        --lora-r 16 --lora-alpha 32 --batch-size 1 --grad-accum 8 --max-image-side 1280

    # 2 optimisation steps on 4 examples (synthetic if --data is omitted):
    python -m train.train_lora --model microsoft/Fara1.5-4B --output-dir /tmp/smoke --smoke

Serve the result with vLLM either as an adapter (``--enable-lora --lora-modules
sft=ckpt/fara_sft_200/final``) or merged (``--merge-out ckpt/fara_sft_200/merged``).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DEFAULT_TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
FALLBACK_MODEL_CLASSES = [
    "AutoModelForImageTextToText",
    "Qwen3VLForConditionalGeneration",
    "Qwen2_5_VLForConditionalGeneration",
    "Qwen2VLForConditionalGeneration",
    "AutoModelForVision2Seq",
]
SMOKE_EXAMPLES = 4
SMOKE_STEPS = 2


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="train.train_lora", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--model", required=True, help="HF id or local path of the base VLM (Fara1.5-4B, Qwen3.5-VL-4B, ...)")
    p.add_argument("--data", default=None, help="sft.jsonl from train.make_sft (optional with --smoke)")
    p.add_argument("--output-dir", required=True, help="where checkpoints, logs and the final adapter go")
    p.add_argument("--prompt-style", default="compact", choices=["compact", "json", "fara"],
                   help="prompt style the student will be served with (must match eval)")
    p.add_argument("--history-k", type=int, default=8, help="previous actions shown in the user turn")
    p.add_argument("--max-image-side", type=int, default=1280, help="resize screenshots so max(w, h) <= this")
    p.add_argument("--coord-space", default="auto", choices=["auto", "image", "norm1000", "norm999"],
                   help="coordinate space of the targets; auto = norm1000 for fara, image otherwise")
    p.add_argument("--epochs", type=float, default=1.0)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--grad-accum", type=int, default=8)
    p.add_argument("--warmup-ratio", type=float, default=0.03)
    p.add_argument("--weight-decay", type=float, default=0.0)
    p.add_argument("--max-grad-norm", type=float, default=1.0)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--lora-dropout", type=float, default=0.05)
    p.add_argument("--target-modules", default=",".join(DEFAULT_TARGET_MODULES),
                   help="comma-separated module names to adapt")
    p.add_argument("--no-gradient-checkpointing", action="store_true")
    p.add_argument("--attn", default="sdpa", choices=["sdpa", "flash_attention_2", "eager"])
    p.add_argument("--dtype", default="bf16", choices=["bf16", "fp16", "fp32"])
    p.add_argument("--save-steps", type=int, default=200, help="save an adapter checkpoint every N optimiser steps")
    p.add_argument("--log-steps", type=int, default=10)
    p.add_argument("--max-steps", type=int, default=None, help="stop after N optimiser steps")
    p.add_argument("--max-minutes", type=float, default=None, help="stop after this wall-clock budget")
    p.add_argument("--limit", type=int, default=None, help="use only the first N records")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--num-workers", type=int, default=0)
    p.add_argument("--merge-out", default=None, help="also write a merged full model here for vLLM")
    p.add_argument("--smoke", action="store_true", help=f"run {SMOKE_STEPS} steps on {SMOKE_EXAMPLES} examples")
    return p


# --------------------------------------------------------------------------- #
# Data (torch-free)
# --------------------------------------------------------------------------- #

def load_records(path: str | Path, limit: int | None = None) -> list[dict]:
    """Read sft.jsonl (see train.make_sft)."""
    out: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if not isinstance(rec, dict) or not rec.get("images") or "target" not in rec:
                continue
            out.append(rec)
            if limit is not None and len(out) >= limit:
                break
    return out


def make_synthetic_records(out_dir: str | Path, n: int = SMOKE_EXAMPLES, size: tuple[int, int] = (1280, 720)) -> Path:
    """Write ``n`` throw-away records with blank screenshots for ``--smoke`` without data."""
    from PIL import Image, ImageDraw

    out_dir = Path(out_dir)
    (out_dir / "shots").mkdir(parents=True, exist_ok=True)
    targets = ['click(640, 360)', 'type("C-1042")', 'key("Return")', "done()"]
    path = out_dir / "sft_synthetic.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for i in range(n):
            img_path = out_dir / "shots" / f"{i:03d}_before.png"
            im = Image.new("RGB", size, (245, 245, 245))
            d = ImageDraw.Draw(im)
            d.rectangle([560, 330, 720, 390], outline=(30, 30, 30), width=2)
            d.text((580, 350), f"SYNTHETIC {i}", fill=(30, 30, 30))
            im.save(img_path)
            rec = {
                "images": [str(img_path.resolve())],
                "instruction": "SYNTHETIC smoke example: click the button, type the claim number, press Return, finish.",
                "history": targets[:i],
                "target": targets[i % len(targets)],
                "task_id": f"synthetic-train-{i:06d}", "family": "synthetic", "seed": i, "split": "train", "step": i,
            }
            f.write(json.dumps(rec) + "\n")
    return path


def load_image(path: str | Path, max_side: int):
    """Open + resize (aspect preserved) with Pillow. Returns a PIL image."""
    from PIL import Image

    im = Image.open(path)
    im.load()
    if im.mode != "RGB":
        im = im.convert("RGB")
    if max_side and max(im.size) > max_side:
        s = max_side / float(max(im.size))
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    return im


def _coord_size(coord_space: str, style: str, image_size: tuple[int, int]) -> tuple[int, int]:
    if coord_space == "auto":
        coord_space = "norm1000" if style == "fara" else "image"
    if coord_space == "norm1000":
        return (1000, 1000)
    if coord_space == "norm999":
        return (999, 999)
    return image_size


def target_text(record: dict, style: str) -> str:
    """Render the record's compact target in the requested output style.

    Records store the compact form. For ``json`` the assistant turn is a JSON
    fence; for ``fara`` it is a ``<tool_call>`` block in Fara's own vocabulary.
    """
    from forkloop.policies.action_parse import parse_compact

    target = str(record["target"])
    if style == "compact":
        return target
    action, err = parse_compact(target)
    if action is None:
        return target
    if style == "json":
        return "```json\n" + json.dumps(action, ensure_ascii=False) + "\n```"
    # fara
    t = action["type"]
    args: dict[str, Any]
    if t == "click":
        args = {"action": "left_click", "coordinate": [action["x"], action["y"]]}
    elif t == "double_click":
        args = {"action": "double_click", "coordinate": [action["x"], action["y"]]}
    elif t == "right_click":
        args = {"action": "right_click", "coordinate": [action["x"], action["y"]]}
    elif t == "move":
        args = {"action": "mouse_move", "coordinate": [action["x"], action["y"]]}
    elif t == "drag":
        args = {"action": "left_click_drag", "start_coordinate": [action["x"], action["y"]],
                "coordinate": [action["x2"], action["y2"]]}
    elif t == "scroll":
        sign = 1 if action["direction"] in ("up", "left") else -1
        name = "hscroll" if action["direction"] in ("left", "right") else "scroll"
        args = {"action": name, "coordinate": [action["x"], action["y"]], "pixels": sign * 100 * int(action["amount"])}
    elif t == "type":
        args = {"action": "type", "text": action["text"]}
    elif t == "key":
        fara_names = {"Return": "Enter", "ctrl": "Control", "alt": "Alt", "shift": "Shift", "super": "Meta",
                      "BackSpace": "Backspace", "Page_Down": "PageDown", "Page_Up": "PageUp",
                      "Up": "ArrowUp", "Down": "ArrowDown", "Left": "ArrowLeft", "Right": "ArrowRight"}
        args = {"action": "key", "keys": [fara_names.get(k, k) for k in action["keys"]]}
    elif t == "wait":
        args = {"action": "wait", "time": action["seconds"]}
    else:  # done
        args = {"action": "terminate", "status": "success" if action.get("success", True) else "failure",
                "answer": action.get("note") or ("Task completed." if action.get("success", True) else "Task failed.")}
    body = json.dumps({"name": "computer_use", "arguments": args}, ensure_ascii=False)
    return "<tool_call>\n" + body + "\n</tool_call>"


def build_messages(record: dict, style: str, history_k: int, image_size: tuple[int, int],
                   coord_space: str = "auto") -> tuple[list[dict], list[dict], str]:
    """``(prompt_messages, full_messages, target_text)`` in HF chat-template form."""
    from forkloop.policies.action_parse import parse_compact, scale_coords, to_compact
    from forkloop.policies.student import build_system_prompt, build_user_text

    cw, ch = _coord_size(coord_space, style, image_size)
    rec = dict(record)
    # Targets are stored in screen pixels; if the model's coordinate space differs, rescale them.
    screen = tuple(rec.get("screen_size") or (1280, 720))
    if (cw, ch) != tuple(screen):
        act, _ = parse_compact(str(rec["target"]))
        if act is not None:
            rec["target"] = to_compact(scale_coords(act, screen, (cw, ch)))
        hist = []
        for h in rec.get("history") or []:
            a, _ = parse_compact(str(h))
            hist.append(to_compact(scale_coords(a, screen, (cw, ch))) if a is not None else h)
        rec["history"] = hist
    system = build_system_prompt(style, cw, ch)
    history = list(rec.get("history") or [])
    history = history[-history_k:] if history_k > 0 else []
    user_text = build_user_text(str(rec.get("instruction", "")), history, style, step=rec.get("step"))
    target = target_text(rec, style)
    prompt = [
        {"role": "system", "content": [{"type": "text", "text": system}]},
        {"role": "user", "content": [{"type": "image"}, {"type": "text", "text": user_text}]},
    ]
    full = prompt + [{"role": "assistant", "content": [{"type": "text", "text": target}]}]
    return prompt, full, target


class SFTExamples:
    """Map-style dataset (works with ``torch.utils.data.DataLoader`` without importing torch here)."""

    def __init__(self, records: list[dict], *, max_image_side: int, style: str, history_k: int,
                 coord_space: str = "auto") -> None:
        self.records = records
        self.max_image_side = max_image_side
        self.style = style
        self.history_k = history_k
        self.coord_space = coord_space

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> dict:
        rec = self.records[idx]
        image = load_image(rec["images"][0], self.max_image_side)
        prompt, full, target = build_messages(rec, self.style, self.history_k, image.size, self.coord_space)
        return {"image": image, "prompt_messages": prompt, "full_messages": full, "target": target}


# --------------------------------------------------------------------------- #
# Torch-dependent pieces (lazy imports)
# --------------------------------------------------------------------------- #

def make_collate(processor):
    """Collate that tokenises with the processor and masks prompt tokens in ``labels``."""
    import torch

    tokenizer = getattr(processor, "tokenizer", processor)
    pad_id = tokenizer.pad_token_id
    if pad_id is None:
        pad_id = tokenizer.eos_token_id if tokenizer.eos_token_id is not None else 0
    eos = tokenizer.eos_token or ""

    def collate(batch: list[dict]) -> dict:
        encs = []
        for ex in batch:
            prompt_text = processor.apply_chat_template(ex["prompt_messages"], tokenize=False, add_generation_prompt=True)
            full_text = processor.apply_chat_template(ex["full_messages"], tokenize=False, add_generation_prompt=False)
            if not full_text.startswith(prompt_text):
                full_text = prompt_text + ex["target"] + eos
            enc_full = processor(text=[full_text], images=[ex["image"]], return_tensors="pt")
            enc_prompt = processor(text=[prompt_text], images=[ex["image"]], return_tensors="pt")
            n_prompt = int(enc_prompt["input_ids"].shape[1])
            ids = enc_full["input_ids"][0]
            am = enc_full["attention_mask"][0]
            labels = ids.clone()
            labels[:n_prompt] = -100
            extras = {k: v for k, v in enc_full.items() if k not in ("input_ids", "attention_mask")}
            encs.append((ids, am, labels, extras))
        maxlen = max(int(e[0].shape[0]) for e in encs)
        bsz = len(encs)
        input_ids = torch.full((bsz, maxlen), pad_id, dtype=encs[0][0].dtype)
        attention = torch.zeros((bsz, maxlen), dtype=encs[0][1].dtype)
        labels = torch.full((bsz, maxlen), -100, dtype=encs[0][2].dtype)
        for i, (ids, am, lab, _) in enumerate(encs):
            n = int(ids.shape[0])
            input_ids[i, :n] = ids
            attention[i, :n] = am
            labels[i, :n] = lab
        out: dict[str, Any] = {"input_ids": input_ids, "attention_mask": attention, "labels": labels}
        for key in encs[0][3]:
            vals = [e[3][key] for e in encs if key in e[3]]
            if vals and all(hasattr(v, "shape") for v in vals):
                out[key] = torch.cat(vals, dim=0)
        return out

    return collate


def load_model_and_processor(model_id: str, *, max_image_side: int, attn: str, dtype: str, device_map: Any = None):
    import torch
    import transformers
    from transformers import AutoProcessor

    torch_dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[dtype]
    max_pixels = int(max_image_side * max_image_side)
    try:
        processor = AutoProcessor.from_pretrained(model_id, max_pixels=max_pixels)
    except TypeError:
        processor = AutoProcessor.from_pretrained(model_id)
    last_err: Exception | None = None
    for cls_name in FALLBACK_MODEL_CLASSES:
        cls = getattr(transformers, cls_name, None)
        if cls is None:
            continue
        try:
            model = cls.from_pretrained(model_id, torch_dtype=torch_dtype, attn_implementation=attn, device_map=device_map)
            print(f"[train_lora] loaded {model_id} with {cls_name}")
            return model, processor
        except Exception as e:  # try the next class
            last_err = e
            print(f"[train_lora] {cls_name} failed: {type(e).__name__}: {e}")
    raise RuntimeError(f"could not load {model_id} with any of {FALLBACK_MODEL_CLASSES}: {last_err}")


def apply_lora(model, *, r: int, alpha: int, dropout: float, target_modules: list[str]):
    from peft import LoraConfig, get_peft_model

    cfg = LoraConfig(r=r, lora_alpha=alpha, lora_dropout=dropout, target_modules=target_modules,
                     bias="none", task_type="CAUSAL_LM")
    model = get_peft_model(model, cfg)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"[train_lora] trainable params: {trainable/1e6:.1f}M / {total/1e6:.0f}M ({100*trainable/total:.2f}%)")
    return model


def _set_seed(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except ImportError:
        pass
    import torch
    torch.manual_seed(seed)


def train(args: argparse.Namespace) -> dict:
    """Run SFT; returns a summary dict (also written to ``<output-dir>/train_summary.json``)."""
    import torch
    from torch.utils.data import DataLoader
    from transformers import get_cosine_schedule_with_warmup

    _set_seed(args.seed)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "train_log.jsonl"

    data_path = args.data
    if data_path is None:
        if not args.smoke:
            raise SystemExit("--data is required unless --smoke")
        data_path = make_synthetic_records(out_dir / "synthetic")
    limit = args.limit
    max_steps = args.max_steps
    if args.smoke:
        limit = SMOKE_EXAMPLES if limit is None else min(limit, SMOKE_EXAMPLES)
        max_steps = SMOKE_STEPS if max_steps is None else min(max_steps, SMOKE_STEPS)
    records = load_records(data_path, limit=limit)
    if not records:
        raise SystemExit(f"no records in {data_path}")
    print(f"[train_lora] {len(records)} records from {data_path}")

    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")
    model, processor = load_model_and_processor(
        args.model, max_image_side=args.max_image_side, attn=args.attn, dtype=args.dtype,
        device_map={"": 0} if use_cuda else None,
    )
    if not use_cuda:
        model.to(device)
    target_modules = [m.strip() for m in args.target_modules.split(",") if m.strip()]
    model = apply_lora(model, r=args.lora_r, alpha=args.lora_alpha, dropout=args.lora_dropout, target_modules=target_modules)
    if hasattr(model, "config"):
        try:
            model.config.use_cache = False
        except Exception:
            pass
    if not args.no_gradient_checkpointing:
        try:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        except TypeError:
            model.gradient_checkpointing_enable()
        if hasattr(model, "enable_input_require_grads"):
            model.enable_input_require_grads()
    model.train()

    dataset = SFTExamples(records, max_image_side=args.max_image_side, style=args.prompt_style,
                          history_k=args.history_k, coord_space=args.coord_space)
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True, collate_fn=make_collate(processor),
                        num_workers=args.num_workers, drop_last=False)
    micro_per_epoch = len(loader)
    steps_per_epoch = max(1, math.ceil(micro_per_epoch / args.grad_accum))
    total_steps = max(1, int(math.ceil(args.epochs * steps_per_epoch)))
    if max_steps is not None:
        total_steps = min(total_steps, max_steps)
    warmup = int(round(args.warmup_ratio * total_steps))
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=args.lr, weight_decay=args.weight_decay)
    scheduler = get_cosine_schedule_with_warmup(optimizer, warmup, total_steps)
    autocast_dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": None}[args.dtype]

    print(f"[train_lora] total optimiser steps: {total_steps} (micro-batches/epoch={micro_per_epoch}, "
          f"accum={args.grad_accum}, warmup={warmup})")
    t_start = time.time()
    global_step = 0
    micro = 0
    running = 0.0
    running_n = 0
    losses: list[float] = []
    stop = False
    epoch = 0

    def _save(tag: str) -> Path:
        p = out_dir / tag
        model.save_pretrained(p)
        try:
            processor.save_pretrained(p)
        except Exception:
            pass
        return p

    with log_path.open("a", encoding="utf-8") as log:
        while not stop:
            for batch in loader:
                batch = {k: (v.to(device) if hasattr(v, "to") else v) for k, v in batch.items()}
                if use_cuda and autocast_dtype is not None:
                    ctx = torch.autocast("cuda", dtype=autocast_dtype)
                else:
                    ctx = torch.autocast("cpu", dtype=torch.bfloat16) if autocast_dtype == torch.bfloat16 and not use_cuda else _NullCtx()
                with ctx:
                    out = model(**batch)
                    loss = out.loss / args.grad_accum
                loss.backward()
                micro += 1
                running += float(loss.item()) * args.grad_accum
                running_n += 1
                if micro % args.grad_accum == 0 or micro == micro_per_epoch * (epoch + 1):
                    torch.nn.utils.clip_grad_norm_(params, args.max_grad_norm)
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                    global_step += 1
                    mean_loss = running / max(1, running_n)
                    losses.append(mean_loss)
                    running, running_n = 0.0, 0
                    elapsed = time.time() - t_start
                    if global_step % args.log_steps == 0 or global_step == 1 or global_step == total_steps:
                        rec = {"step": global_step, "loss": mean_loss, "lr": scheduler.get_last_lr()[0],
                               "elapsed_s": elapsed, "s_per_step": elapsed / global_step, "epoch": epoch}
                        if use_cuda:
                            rec["peak_vram_gb"] = torch.cuda.max_memory_allocated() / 1e9
                        log.write(json.dumps(rec) + "\n")
                        log.flush()
                        print("[train_lora] " + json.dumps(rec))
                    if args.save_steps and global_step % args.save_steps == 0 and global_step < total_steps:
                        _save(f"checkpoint-{global_step}")
                    if global_step >= total_steps:
                        stop = True
                        break
                    if args.max_minutes is not None and elapsed > args.max_minutes * 60:
                        print(f"[train_lora] wall-clock budget of {args.max_minutes} min reached")
                        stop = True
                        break
            epoch += 1
            if not stop and epoch >= math.ceil(args.epochs):
                stop = True

    final = _save("final")
    elapsed = time.time() - t_start
    summary = {
        "model": args.model, "data": str(data_path), "records": len(records), "prompt_style": args.prompt_style,
        "steps": global_step, "planned_steps": total_steps, "epochs_completed": epoch,
        "final_loss": losses[-1] if losses else None, "first_loss": losses[0] if losses else None,
        "elapsed_s": elapsed, "s_per_step": (elapsed / global_step) if global_step else None,
        "peak_vram_gb": (torch.cuda.max_memory_allocated() / 1e9) if use_cuda else None,
        "device": str(device), "adapter": str(final), "smoke": bool(args.smoke),
        "lora": {"r": args.lora_r, "alpha": args.lora_alpha, "dropout": args.lora_dropout, "target_modules": target_modules},
        "batch_size": args.batch_size, "grad_accum": args.grad_accum, "lr": args.lr, "max_image_side": args.max_image_side,
    }
    if args.merge_out:
        merged = model.merge_and_unload()
        merged.save_pretrained(args.merge_out)
        processor.save_pretrained(args.merge_out)
        summary["merged"] = str(args.merge_out)
    (out_dir / "train_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("[train_lora] done: " + json.dumps({k: summary[k] for k in ("steps", "final_loss", "elapsed_s", "adapter")}))
    return summary


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    train(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
