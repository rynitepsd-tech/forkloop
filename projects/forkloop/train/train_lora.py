"""LoRA supervised fine-tuning of a Qwen3.5-architecture student (Holo-3.1, Qwen3.8, Fara 1.5).

Every current candidate student (``Hcompany/Holo-3.1-9B``, ``Qwen/Qwen3.8-27B``, Fara 1.5) is a
``Qwen3_5ForConditionalGeneration``: ``AutoProcessor`` + ``AutoModelForImageTextToText`` load all
of them. Each training example is one chat rendered by the SAME builder serving uses
(``forkloop.policies.observation.observation_messages``): system prompt, a user turn holding the
instruction, explicit memory, history and previous + current screenshots, then the target reply.

Two input formats:

* ``--dataset DIR`` (repeatable, the correction loop's export, ``forkloop.dataset.v1``):
  ``records.jsonl`` + content-addressed ``images/``. Every file sha256 in ``manifest.json`` and
  every referenced screenshot's sha256 are verified before training, and the dataset ids and
  manifest hashes are written into ``training_args.json`` next to every saved adapter. The
  target is ``forkloop.correction.dataset.render_target`` (reasoning line, ``Memory:`` lines,
  compact action) in the student's coordinate frame. ``--dataset-weights`` mixes several dirs.
* ``--data sft.jsonl`` (legacy ``train.make_sft`` records; unchanged behaviour).

Training/serving parity (checked by ``train/parity.py``): thinking is disabled in the chat
template (``enable_thinking=False``, the same ``chat_template_kwargs`` the serving request sends;
``--thinking auto`` keeps Fara's own template untouched); the processor is loaded with its
defaults, exactly as vLLM loads it (no ``max_pixels`` override: a 1280x720 PNG becomes a 1280x704
grid of 880 visual tokens in both paths); the prompt is tokenized on its own, so its ids equal the
serving prompt ids, and the target is tokenized as a continuation; labels are ``-100`` on every
prompt token and on everything after the first ``<|im_end|>`` (the target and its end-of-turn
token are the only supervised tokens); nothing is ever truncated (``--max-seq-len`` raises).

LoRA targets (``--target-modules auto``): the language model only -- full-attention
``q/k/v/o_proj``, Gated DeltaNet ``in_proj_qkv/z/a/b`` + ``out_proj`` and MLP
``gate/up/down_proj`` of every ``model.language_model.layers.N``; the vision tower and merger are
frozen (vLLM 0.30 serves Qwen3.5 LoRA on the language model; ``train/serve/README.md``).

VRAM: the numbers below are estimates for the old 4B Fara recipe; measured numbers for Holo-3.1-9B
and Qwen3.8-27B on one H100 80 GB are in ``docs/student-qualification.md`` -- always check
``peak_vram_gb`` in ``train_log.jsonl`` on your card:

    weights (bf16)                          ~8 GB (4B) / ~19 GB (9B) / ~55 GB (27B)
    LoRA params + AdamW states              <1 GB
    activations, batch 1 (checkpointed)     a few GB at ~4k tokens (2 screenshots + text)
    logits (fp32, 248k vocab x seq_len)     ~4 GB per 4k tokens

The module imports cleanly without torch/transformers/peft (they are imported lazily inside the
functions that need them), so ``--help`` and the dataset helpers work on a laptop.

Usage::

    python -m train.train_lora --model Hcompany/Holo-3.1-9B --dataset data/ds-abc --output-dir ckpt/holo_ds \\
        --coord-space norm1000 --system-prompt-file forkloop/policies/prompts/agent_memory_v1.md \\
        --epochs 2 --lr 1e-4 --lora-r 16 --lora-alpha 32 --batch-size 1 --grad-accum 8

    # two datasets mixed 3:1 by sampling weight
    python -m train.train_lora ... --dataset data/ds-a --dataset data/ds-b --dataset-weights 3,1

    # legacy sft.jsonl (Fara)
    python -m train.train_lora --model microsoft/Fara1.5-4B --data data/sft_200.jsonl \\
        --output-dir ckpt/fara_sft_200 --prompt-style fara --epochs 2

    # 2 optimisation steps on 4 examples (synthetic if --data is omitted):
    python -m train.train_lora --model microsoft/Fara1.5-4B --output-dir /tmp/smoke --smoke

Serve the result with vLLM either as an adapter (``train/serve/serve.sh holo31-9b --enable-lora
--max-lora-rank 16 --lora-modules sft=ckpt/holo_ds/final``) or merged (``--merge-out``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DATASET_SCHEMA = "forkloop.dataset.v1"
DEFAULT_TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
#: Qwen3.5 language-model projections (full attention, Gated DeltaNet, MLP). Anchored on
#: ``model.language_model.layers`` so the vision tower (``model.visual.*``: ``attn.qkv``,
#: ``attn.proj``, ``mlp.linear_fc1/2``), ``lm_head`` and any MTP head are never adapted.
QWEN35_LM_TARGET_REGEX = (r".*\blanguage_model\.layers\.\d+\.(?:self_attn\.(?:q_proj|k_proj|v_proj|o_proj)"
                          r"|linear_attn\.(?:in_proj_qkv|in_proj_z|in_proj_a|in_proj_b|out_proj)"
                          r"|mlp\.(?:gate_proj|up_proj|down_proj))")
FALLBACK_MODEL_CLASSES = [
    "AutoModelForImageTextToText",
    "Qwen3_5ForConditionalGeneration",
    "Qwen3VLForConditionalGeneration",
    "Qwen2_5_VLForConditionalGeneration",
    "Qwen2VLForConditionalGeneration",
    "AutoModelForVision2Seq",
]
SMOKE_EXAMPLES = 4
SMOKE_STEPS = 2
THINK_OFF_PREFIX = "<think>\n\n</think>\n\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="train.train_lora", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--model", required=True, help="HF id or local path of the base VLM (Holo-3.1-9B, Qwen3.8-27B, Fara1.5-4B, ...)")
    p.add_argument("--revision", default=None, help="model revision (commit sha) to load; recorded in training_args.json")
    p.add_argument("--data", default=None, help="legacy sft.jsonl from train.make_sft (optional with --smoke)")
    p.add_argument("--dataset", action="append", default=[],
                   help="forkloop.dataset.v1 directory (records.jsonl + images/ + manifest.json); repeatable")
    p.add_argument("--dataset-weights", default=None,
                   help="comma-separated sampling weights, one per --dataset, e.g. 3,1 (default: plain concatenation)")
    p.add_argument("--samples-per-epoch", type=int, default=None,
                   help="with --dataset-weights: examples drawn per epoch (default: total records of all datasets)")
    p.add_argument("--output-dir", required=True, help="where checkpoints, logs and the final adapter go")
    p.add_argument("--prompt-style", default="compact", choices=["compact", "json", "fara"],
                   help="prompt style the student will be served with (must match eval)")
    p.add_argument("--history-k", type=int, default=8, help="previous actions shown in the user turn (same as serving)")
    p.add_argument("--max-image-side", type=int, default=1280,
                   help="cap the longest image side after --image-scale (StudentPolicy image_max_side)")
    p.add_argument("--image-scale", type=float, default=1.0,
                   help="upscale every screenshot by this factor before capping at --max-image-side "
                        "(forkloop.policies.observation.resize_for_model; the student config uses 1.5 with "
                        "--max-image-side 1920); must equal StudentPolicy(image_scale=...)")
    p.add_argument("--coord-space", default="auto", choices=["auto", "image", "screen", "norm1000", "norm999"],
                   help="coordinate space of the targets; auto = norm1000 for fara, image otherwise. "
                        "Holo-3.1 / Qwen3.8 are native norm1000")
    p.add_argument("--thinking", default="auto", choices=["auto", "off", "on"],
                   help="chat template thinking: off passes enable_thinking=False (as the serving request does); "
                        "auto = off except for --prompt-style fara (Fara is served with its own template)")
    p.add_argument("--max-seq-len", type=int, default=16384,
                   help="refuse (never truncate) examples longer than this many tokens")
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
    p.add_argument("--target-modules", default="auto",
                   help="'auto' (Qwen3.5 language model only, else the names below) or comma-separated module names; "
                        "default names: " + ",".join(DEFAULT_TARGET_MODULES))
    p.add_argument("--no-gradient-checkpointing", action="store_true")
    p.add_argument("--attn", default="sdpa", choices=["sdpa", "flash_attention_2", "eager"])
    p.add_argument("--dtype", default="bf16", choices=["bf16", "fp16", "fp32"])
    p.add_argument("--full-logits", action="store_true",
                   help="compute the loss from the model's own full-sequence logits instead of the target span only")
    p.add_argument("--allow-slow-kernels", action="store_true",
                   help="train on CUDA even if flash-linear-attention / causal-conv1d are missing (torch fallback)")
    p.add_argument("--save-steps", type=int, default=200, help="save an adapter checkpoint every N optimiser steps")
    p.add_argument("--log-steps", type=int, default=10)
    p.add_argument("--max-steps", type=int, default=None, help="stop after N optimiser steps")
    p.add_argument("--max-minutes", type=float, default=None, help="stop after this wall-clock budget")
    p.add_argument("--limit", type=int, default=None, help="use only the first N records")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--num-workers", type=int, default=0)
    p.add_argument("--merge-out", default=None, help="also write a merged full model here for vLLM")
    p.add_argument("--system-prompt-file", default=None,
                   help="system prompt template the student will be SERVED with (e.g. "
                        "forkloop/policies/prompts/agent_memory_v1.md); {w}/{h}/{w1}/{h1}/{fara_identity}/{fara_tools} "
                        "placeholders as in forkloop.policies.student")
    p.add_argument("--instruction-note", default=None,
                   help="text appended to every instruction, exactly as collect --instruction-note does at eval time")
    p.add_argument("--nav-macro", action="store_true",
                   help="advertise visit_url/history_back in the fara tool schema, as collect --nav-macro does")
    p.add_argument("--smoke", action="store_true",
                   help=f"smoke run: defaults to {SMOKE_STEPS} steps on {SMOKE_EXAMPLES} examples unless --limit / "
                        "--max-steps are given; prints per-example token counts")
    return p


def template_kwargs_for(thinking: str, style: str) -> dict:
    """``chat_template_kwargs`` shared by training and the serving request."""
    if thinking == "off" or (thinking == "auto" and style != "fara"):
        return {"enable_thinking": False}
    if thinking == "on":
        return {"enable_thinking": True}
    return {}


# --------------------------------------------------------------------------- #
# Data (torch-free)
# --------------------------------------------------------------------------- #

def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


def verify_dataset_dir(root: str | Path) -> dict:
    """Check a ``forkloop.dataset.v1`` directory against its manifest; return its identity.

    Every file hash listed in ``manifest.json["files"]`` must match, ``records.jsonl`` must hold only
    action demonstrations, and every referenced screenshot must exist inside the directory with the
    sha256 its record claims. Raises ``ValueError`` on any mismatch (nothing is trained on)."""
    root = Path(root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != DATASET_SCHEMA:
        raise ValueError(f"{root}: manifest schema {manifest.get('schema')!r} != {DATASET_SCHEMA}")
    files = manifest.get("files") or {}
    if "records.jsonl" not in files:
        raise ValueError(f"{root}: manifest lists no records.jsonl hash")
    for name, digest in files.items():
        got = file_sha256(root / name)
        if got != digest:
            raise ValueError(f"{root}/{name}: sha256 {got} != manifest {digest}")
    if manifest.get("dataset_id") != "ds-" + files["records.jsonl"][:12]:
        raise ValueError(f"{root}: dataset_id {manifest.get('dataset_id')!r} does not match records.jsonl")
    seen: dict[str, str] = {}
    n = 0
    resolved = root.resolve()
    with (root / "records.jsonl").open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            n += 1
            if rec.get("schema") != DATASET_SCHEMA or rec.get("kind") != "action_demonstration":
                raise ValueError(f"{root}: record {rec.get('record_id')} is not a {DATASET_SCHEMA} action demonstration")
            inp = rec["input"]
            for key in ("previous_shot", "current_shot"):
                shot = inp.get(key)
                if not shot:
                    if key == "current_shot":
                        raise ValueError(f"{root}: record {rec.get('record_id')} has no current screenshot")
                    continue
                path = (root / shot["path"]).resolve()
                if not path.is_relative_to(resolved):
                    raise ValueError(f"{root}: screenshot {shot['path']} escapes the dataset directory")
                if shot["path"] not in seen:
                    seen[shot["path"]] = file_sha256(path)
                if seen[shot["path"]] != shot["sha256"]:
                    raise ValueError(f"{root}/{shot['path']}: sha256 {seen[shot['path']]} != record {shot['sha256']}")
    if n != (manifest.get("counts") or {}).get("records", n):
        raise ValueError(f"{root}: {n} records != manifest count {manifest['counts']['records']}")
    return {"path": str(root), "dataset_id": manifest["dataset_id"], "name": manifest.get("name"),
            "manifest_sha256": file_sha256(manifest_path), "records_sha256": files["records.jsonl"],
            "records": n, "images_verified": len(seen), "splits": manifest.get("splits"),
            "code_git_sha": manifest.get("code_git_sha")}


def record_task(record: dict) -> tuple[str, str, int] | None:
    """``(family, split, seed)`` of a dataset record's source task (from ``source`` or its ``task_id``)."""
    src = record.get("source") or {}
    if all(k in src for k in ("family", "split", "seed")):
        return str(src["family"]), str(src["split"]), int(src["seed"])
    tid = src.get("task_id") or record.get("task_id")
    if isinstance(tid, str) and tid.count("-") >= 2:
        family, split, seed = tid.rsplit("-", 2)
        if seed.isdigit():
            return family, split, int(seed)
    return None


def assert_no_final_test(records: list[dict]) -> dict:
    """Refuse any record whose source task is final-test (split name, sealed seed block or held-out
    structure: ``forkloop.splits.final_reasons``) or whose task cannot be identified."""
    from forkloop import splits

    tasks: dict[tuple[str, str, int], list[str]] = {}
    unknown = [r.get("record_id") for r in records if record_task(r) is None]
    if unknown:
        raise SystemExit(f"{len(unknown)} records name no source task (family/split/seed or task_id): {unknown[:3]}")
    for r in records:
        triple = record_task(r)
        if triple not in tasks:
            tasks[triple] = splits.final_reasons(triple)
    bad = {f"{f}-{sp}-{sd:06d}": why for (f, sp, sd), why in tasks.items() if why}
    if bad:
        raise SystemExit(f"refusing to train on final-test tasks: {bad}")
    return {"tasks_checked": len(tasks), "policy": f"forkloop.splits v{splits.POLICY_VERSION} ({splits.POLICY_DATE})"}


def load_dataset_records(root: str | Path, limit: int | None = None) -> list[dict]:
    """Action demonstrations of one (already verified) dataset dir; each carries ``_root``."""
    root = Path(root)
    out = []
    with (root / "records.jsonl").open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                rec["_root"] = str(root)
                out.append(rec)
                if limit is not None and len(out) >= limit:
                    break
    return out


def mix_records(groups: list[list[dict]], weights: list[float] | None, *, seed: int,
                samples: int | None = None) -> tuple[list[dict], list[dict]]:
    """Combine datasets. Without weights: plain concatenation. With weights: ``samples`` examples
    (default: all records) split by weight; each dataset contributes whole shuffled passes first
    and a seeded partial pass for the remainder, so a weight never silently drops a dataset's
    records unless it asks for fewer than it has. Returns ``(records, per-dataset counts)``."""
    if not weights:
        flat = [r for g in groups for r in g]
        return flat, [{"records": len(g), "drawn": len(g), "weight": None} for g in groups]
    if len(weights) != len(groups) or any(w < 0 for w in weights) or not sum(weights):
        raise ValueError("--dataset-weights needs one nonnegative weight per --dataset, not all zero")
    total = samples if samples is not None else sum(len(g) for g in groups)
    rng = random.Random(seed)
    raw = [w / sum(weights) * total for w in weights]
    counts = [int(math.floor(x)) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - counts[i], reverse=True)[: total - sum(counts)]:
        counts[i] += 1
    out, info = [], []
    for g, n, w in zip(groups, counts, weights):
        if n and not g:
            raise ValueError("a weighted dataset is empty")
        drawn: list[dict] = []
        while len(drawn) < n:
            idx = list(range(len(g)))
            rng.shuffle(idx)
            drawn.extend(g[i] for i in idx[: n - len(drawn)])
        out.extend(drawn)
        info.append({"records": len(g), "drawn": n, "weight": w})
    return out, info


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


def load_image(path: str | Path, max_side: int, scale: float = 1.0):
    """Open a screenshot and apply THE serving transform,
    ``forkloop.policies.observation.resize_for_model`` (upscale by ``scale`` with LANCZOS, then cap the
    longest side at ``max_side``), exactly as ``StudentPolicy.prepare_image`` does. Returns RGB PIL."""
    from PIL import Image

    from forkloop.policies.observation import resize_for_model

    im = Image.open(path)
    im.load()
    return resize_for_model(im, image_max_side=max_side, image_scale=scale)


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
    # recipe v2-reasoning (make_sft --with-reasoning): the teacher's reasoning line precedes the action,
    # exactly the shape base Fara replies in (prose, then the tool call); "" falls back to the action alone.
    reasoning = str(record.get("reasoning") or "").strip()
    prefix = reasoning + "\n" if reasoning else ""
    if style == "compact":
        return prefix + target
    action, err = parse_compact(target)
    if action is None:
        return prefix + target
    if style == "json":
        return prefix + "```json\n" + json.dumps(action, ensure_ascii=False) + "\n```"
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
    return prefix + "<tool_call>\n" + body + "\n</tool_call>"


def build_messages(record: dict, style: str, history_k: int, image_size: tuple[int, int],
                   coord_space: str = "auto", *, system_prompt_template: str | None = None,
                   instruction_note: str | None = None, nav_macro: bool = False) -> tuple[list[dict], list[dict], str]:
    """``(prompt_messages, full_messages, target_text)`` in HF chat-template form.

    ``system_prompt_template`` / ``instruction_note`` / ``nav_macro`` reproduce the serving-time
    prompt of ``StudentPolicy(system_prompt=..., instruction_note=..., nav_macro=...)`` so the
    training chat is byte-for-byte the chat the model sees at evaluation. A ``forkloop.dataset.v1``
    record is rendered by :func:`build_dataset_messages`.
    """
    if record.get("schema") == DATASET_SCHEMA:
        return build_dataset_messages(record, style=style, history_k=history_k, image_size=image_size,
                                      coord_space=coord_space, system_prompt_template=system_prompt_template,
                                      instruction_note=instruction_note, nav_macro=nav_macro)
    from forkloop.policies.observation import coordinate_size, observation_messages
    from forkloop.policies.action_parse import parse_compact, scale_coords, to_compact

    rec = dict(record)
    screen = tuple(rec.get("screen_size") or (1280, 720))
    coords = coordinate_size(coord_space, style, image_size, screen)
    act, _ = parse_compact(str(rec["target"]))
    if act is not None:
        rec["target"] = to_compact(scale_coords(act, screen, coords))
    prompt = observation_messages(
        instruction=str(rec.get("instruction", "")), history=list(rec.get("history") or []), step=rec.get("step"),
        screen=screen, coords=coords, style=style, history_k=history_k, image_count=len(rec["images"]),
        system_template=system_prompt_template, instruction_note=instruction_note, nav_macro=nav_macro,
        notes=rec.get("notes"),  # recipe v4-notes; None keeps the notes-free v3 prompt byte-identical
        memory=rec.get("memory"))  # explicit memory (None = channel off, [] = on and empty)
    target = target_text(rec, style)
    full = prompt + [{"role": "assistant", "content": [{"type": "text", "text": target}]}]
    return prompt, full, target


def dataset_image_paths(record: dict) -> list[Path]:
    """Previous (when shown) then current screenshot of a dataset.v1 record, as serving orders them."""
    inp = record["input"]
    root = Path(record.get("_root") or ".")
    paths = []
    if inp.get("previous_shot") and int(inp.get("step") or 0) > 0:
        paths.append(root / inp["previous_shot"]["path"])
    paths.append(root / inp["current_shot"]["path"])
    return paths


def build_dataset_messages(record: dict, *, style: str, history_k: int, image_size: tuple[int, int],
                           coord_space: str, system_prompt_template: str | None,
                           instruction_note: str | None = None, nav_macro: bool = False) -> tuple[list[dict], list[dict], str]:
    """Render one ``forkloop.dataset.v1`` action demonstration with the serving builder.

    Input: ``observation_messages(..., memory=input.memory)`` exactly as ``StudentPolicy(memory=True)``
    renders the same observation; target: ``render_target`` in the same coordinate frame."""
    from forkloop.correction.dataset import render_target
    from forkloop.policies.observation import coordinate_size, observation_messages

    inp = record["input"]
    screen = tuple(inp.get("screen") or (1280, 720))
    coords = coordinate_size(coord_space, style, image_size, screen)
    prompt = observation_messages(
        instruction=str(inp["instruction"]), history=list(inp.get("history") or []), step=int(inp["step"]),
        screen=screen, coords=coords, style=style, history_k=history_k,
        image_count=len(dataset_image_paths(record)), system_template=system_prompt_template,
        instruction_note=instruction_note, nav_macro=nav_macro, memory=inp.get("memory"))
    target = render_target(record["target"], screen=screen, coords=coords)
    full = prompt + [{"role": "assistant", "content": [{"type": "text", "text": target}]}]
    return prompt, full, target


class SFTExamples:
    """Map-style dataset (works with ``torch.utils.data.DataLoader`` without importing torch here)."""

    def __init__(self, records: list[dict], *, max_image_side: int, style: str, history_k: int,
                 coord_space: str = "auto", system_prompt_template: str | None = None,
                 instruction_note: str | None = None, nav_macro: bool = False, image_scale: float = 1.0) -> None:
        self.records = records
        self.image_scale = image_scale
        self.max_image_side = max_image_side
        self.style = style
        self.history_k = history_k
        self.coord_space = coord_space
        self.system_prompt_template = system_prompt_template
        self.instruction_note = instruction_note
        self.nav_macro = nav_macro

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> dict:
        rec = self.records[idx]
        if rec.get("schema") == DATASET_SCHEMA:
            paths = dataset_image_paths(rec)
        else:
            from forkloop.policies.observation import OBSERVATION_SCHEMA
            if rec.get("schema_version") == OBSERVATION_SCHEMA:
                expected = ["current"] if rec["step"] == 0 else ["previous", "current"]
                if rec.get("image_roles") != expected or len(rec["images"]) != len(expected):
                    raise ValueError("v3 observation is missing or reorders required screenshots")
                if rec.get("history_coordinate_space") != "screen":
                    raise ValueError("v3 history must store desktop coordinates")
            paths = rec["images"]
        images = [load_image(path, self.max_image_side, self.image_scale) for path in paths]
        image = images[-1]
        # "image" coordinate space = the size of the image the model is sent (StudentPolicy: model_size)
        prompt, full, target = build_messages(rec, self.style, self.history_k, image.size, self.coord_space,
                                              system_prompt_template=self.system_prompt_template,
                                              instruction_note=self.instruction_note, nav_macro=self.nav_macro)
        return {"image": image, "images": images, "prompt_messages": prompt, "full_messages": full, "target": target,
                "record_id": rec.get("record_id")}


def supervise(prompt_ids: list[int], continuation_ids: list[int], eos_id: int | None) -> tuple[list[int], list[int]]:
    """``(input_ids, labels)`` for one example (torch-free).

    The prompt is never supervised; the continuation is supervised up to and including its first
    end-of-turn token and dropped after it (a chat template's trailing ``"\\n"`` after ``<|im_end|>``
    is never generated at serving time, so it is not trained either)."""
    if not continuation_ids:
        raise ValueError("example has no supervised target tokens")
    cont = list(continuation_ids)
    if eos_id is not None:
        if eos_id not in cont:
            raise ValueError("target continuation has no end-of-turn token")
        cont = cont[: cont.index(eos_id) + 1]
        if len(cont) < 2:
            raise ValueError("target is empty before the end-of-turn token")
    ids = list(prompt_ids) + cont
    labels = [-100] * len(prompt_ids) + cont
    return ids, labels


# --------------------------------------------------------------------------- #
# Torch-dependent pieces (lazy imports)
# --------------------------------------------------------------------------- #

def make_collate(processor, *, template_kwargs: dict | None = None, max_seq_len: int | None = None):
    """Collate that tokenises with the processor and masks everything but the target in ``labels``.

    The prompt goes through ``processor(text=..., images=...)`` on its own -- exactly what the
    serving engine does with the same chat-template text -- and the target is appended as a
    separately tokenized continuation, so the prompt ids are the serving ids and no token merges
    across the prompt/target boundary."""
    import torch

    tokenizer = getattr(processor, "tokenizer", processor)
    template_kwargs = dict(template_kwargs or {})
    pad_id = tokenizer.pad_token_id
    if pad_id is None:
        pad_id = tokenizer.eos_token_id if tokenizer.eos_token_id is not None else 0
    image_token = getattr(processor, "image_token", None) or "<|image_pad|>"
    try:
        image_token_id = tokenizer.convert_tokens_to_ids(image_token)
    except Exception:
        image_token_id = None
    try:
        end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
        end_id = end_id if isinstance(end_id, int) and end_id != getattr(tokenizer, "unk_token_id", None) else None
    except Exception:
        end_id = None
    if end_id is None:
        end_id = tokenizer.eos_token_id
    merge = getattr(getattr(processor, "image_processor", None), "merge_size", None)

    stats_rows: list[dict] = []  # one entry per example, read by train() for the smoke report / summary

    def collate(batch: list[dict]) -> dict:
        encs = []
        for ex in batch:
            prompt_text = processor.apply_chat_template(ex["prompt_messages"], tokenize=False, add_generation_prompt=True,
                                                        **template_kwargs)
            full_text = processor.apply_chat_template(ex["full_messages"], tokenize=False, add_generation_prompt=False,
                                                      **template_kwargs)
            if not full_text.startswith(prompt_text):
                raise ValueError("assistant template does not extend the inference generation prefix")
            if template_kwargs.get("enable_thinking") is False and not prompt_text.endswith(THINK_OFF_PREFIX):
                raise ValueError("thinking is off but the generation prefix does not end with an empty think block")
            enc_prompt = processor(text=[prompt_text], images=ex["images"], return_tensors="pt")
            n_prompt = int(enc_prompt["input_ids"].shape[1])
            # Tokenize the exact inference prefix, then its continuation. Fara's
            # template ends generation at '<think>\n'; tokenizing the full turn
            # merges that newline with the target's first newline. Masking by
            # separately measured length silently changes the last prompt token.
            suffix = full_text[len(prompt_text):]
            continuation = tokenizer(suffix, add_special_tokens=False)["input_ids"]
            ids_l, labels_l = supervise(enc_prompt["input_ids"][0].tolist(), list(continuation), end_id)
            if max_seq_len is not None and len(ids_l) > max_seq_len:
                raise ValueError(f"example of {len(ids_l)} tokens exceeds --max-seq-len {max_seq_len}; refusing to truncate")
            ids = torch.tensor(ids_l, dtype=enc_prompt["input_ids"].dtype)
            labels = torch.tensor(labels_l, dtype=enc_prompt["input_ids"].dtype)
            n_cont = len(ids_l) - n_prompt
            am = torch.cat([enc_prompt["attention_mask"][0], torch.ones(n_cont, dtype=enc_prompt["attention_mask"].dtype)])
            extras = {k: v for k, v in enc_prompt.items() if k not in ("input_ids", "attention_mask")}
            for key in ("mm_token_type_ids", "token_type_ids"):
                if key in extras:
                    value = extras[key]
                    extras[key] = torch.cat([value, torch.zeros((1, n_cont), dtype=value.dtype)], dim=1)
            n_img = int((ids == image_token_id).sum()) if image_token_id is not None else -1
            grid = extras.get("image_grid_thw")
            if grid is not None and merge and n_img >= 0:
                expected = int(grid.prod(dim=1).sum()) // (merge * merge)
                if expected != n_img:
                    raise ValueError(f"{n_img} image tokens in the prompt != {expected} from image_grid_thw (truncated image?)")
            encs.append((ids, am, labels, extras))
            stats_rows.append({"seq_len": int(ids.shape[0]), "prompt_len": n_prompt,
                               "target_len": n_cont, "image_tokens": n_img,
                               "images": int(grid.shape[0]) if grid is not None else len(ex["images"])})
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
            if not vals or not all(hasattr(v, "shape") for v in vals):
                continue
            # Per-token extras (e.g. transformers 5's ``mm_token_type_ids``, shape (1, seq_len)) are padded
            # like input_ids; per-image extras (``pixel_values`` (n_patches, dim), ``image_grid_thw``) concatenate.
            per_token = all(v.dim() >= 2 and int(v.shape[0]) == 1 and int(v.shape[1]) == int(e[0].shape[0])
                            for v, e in zip(vals, encs))
            if per_token and bsz > 1:
                padded = torch.zeros((bsz, maxlen) + tuple(vals[0].shape[2:]), dtype=vals[0].dtype)
                for i, v in enumerate(vals):
                    padded[i, : int(v.shape[1])] = v[0]
                out[key] = padded
            else:
                out[key] = torch.cat(vals, dim=0)
        return out

    collate.stats_rows = stats_rows  # type: ignore[attr-defined]
    collate.template_kwargs = template_kwargs  # type: ignore[attr-defined]
    return collate


def fast_kernel_status() -> dict:
    """Whether transformers will find the fast Gated DeltaNet / causal-conv kernels (Qwen3.5)."""
    import importlib

    out = {}
    try:
        from transformers.integrations import hub_kernels as hk
        mapping = getattr(hk, "_KERNELS_INTERNAL_PATH_MAPPINGS", {})
        resolve = getattr(hk, "resolve_internal_import", None)
    except Exception:
        mapping, resolve = {}, None
    for pkg, name in (("fla", "chunk_gated_delta_rule"), ("fla", "fused_recurrent_gated_delta_rule"),
                      ("causal_conv1d", "causal_conv1d_fn"), ("causal_conv1d", "causal_conv1d_update")):
        impl = None
        try:
            mod = importlib.import_module(pkg)
            internal = mapping.get(name)
            full = name if internal is None else f"{internal}.{name}"
            impl = resolve(mod, full) if resolve else getattr(mod, name, None)
            if impl is None and internal is not None:
                impl = getattr(importlib.import_module(f"{pkg}.{internal}"), name, None)
        except Exception:
            impl = None
        out[f"{pkg}.{name}"] = impl is not None
    return out


def load_model_and_processor(model_id: str, *, max_image_side: int, attn: str, dtype: str, device_map: Any = None,
                             revision: str | None = None):
    """Base model + processor. The processor keeps its defaults (vLLM loads it the same way);
    ``max_image_side`` is applied by :func:`load_image` / ``StudentPolicy`` before the processor."""
    import torch
    import transformers
    from transformers import AutoProcessor

    torch_dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[dtype]
    processor = AutoProcessor.from_pretrained(model_id, revision=revision)
    last_err: Exception | None = None
    for cls_name in FALLBACK_MODEL_CLASSES:
        cls = getattr(transformers, cls_name, None)
        if cls is None:
            continue
        try:
            try:
                model = cls.from_pretrained(model_id, dtype=torch_dtype, attn_implementation=attn, device_map=device_map,
                                            revision=revision)
            except TypeError:
                model = cls.from_pretrained(model_id, torch_dtype=torch_dtype, attn_implementation=attn,
                                            device_map=device_map, revision=revision)
            print(f"[train_lora] loaded {model_id} with {cls_name} ({type(model).__name__})")
            return model, processor
        except Exception as e:  # try the next class
            last_err = e
            print(f"[train_lora] {cls_name} failed: {type(e).__name__}: {e}")
    raise RuntimeError(f"could not load {model_id} with any of {FALLBACK_MODEL_CLASSES}: {last_err}")


def resolve_target_modules(spec: str, module_names: list[str]) -> Any:
    """``auto``: the Qwen3.5 language-model regex when it matches the model, else the default names."""
    if spec.strip() != "auto":
        return [m.strip() for m in spec.split(",") if m.strip()]
    rx = re.compile(QWEN35_LM_TARGET_REGEX)
    if any(rx.fullmatch(n) for n in module_names):
        return QWEN35_LM_TARGET_REGEX
    return list(DEFAULT_TARGET_MODULES)


def apply_lora(model, *, r: int, alpha: int, dropout: float, target_modules: Any):
    from peft import LoraConfig, get_peft_model

    cfg = LoraConfig(r=r, lora_alpha=alpha, lora_dropout=dropout, target_modules=target_modules,
                     bias="none", task_type="CAUSAL_LM")
    model = get_peft_model(model, cfg)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    visual = [n for n, p in model.named_parameters() if p.requires_grad and ".visual." in n]
    if visual:
        raise RuntimeError(f"LoRA reached the vision tower ({visual[:3]}...); vLLM would not serve it")
    print(f"[train_lora] trainable params: {trainable/1e6:.1f}M / {total/1e6:.0f}M ({100*trainable/total:.2f}%)")
    return model


def _forward_params(model) -> set:
    import inspect

    for m in (model, getattr(model, "base_model", None), getattr(getattr(model, "base_model", None), "model", None)):
        if m is None:
            continue
        try:
            params = set(inspect.signature(m.forward).parameters)
        except (TypeError, ValueError):
            continue
        if "logits_to_keep" in params:
            return params
    return set()


def _set_seed(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except ImportError:
        pass
    import torch
    torch.manual_seed(seed)


def _model_revision(model_id: str, revision: str | None) -> str | None:
    if revision:
        return revision
    p = Path(model_id)
    if p.exists():
        return None
    try:
        from huggingface_hub import snapshot_download
        return Path(snapshot_download(model_id, local_files_only=True)).name
    except Exception:
        return None


def _versions() -> dict:
    """Installed distribution versions (metadata only: importing vllm here would be slow)."""
    from importlib import metadata

    out = {}
    for dist in ("torch", "transformers", "peft", "accelerate", "vllm", "flash-linear-attention", "causal-conv1d"):
        try:
            out[dist] = metadata.version(dist)
        except Exception:
            out[dist] = None
    return out


def _git_sha() -> str | None:
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:
        return None


def train(args: argparse.Namespace) -> dict:
    """Run SFT; returns a summary dict (also written to ``<output-dir>/train_summary.json``)."""
    import torch
    from torch.utils.data import DataLoader
    from transformers import get_cosine_schedule_with_warmup

    _set_seed(args.seed)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "train_log.jsonl"

    datasets_info: list[dict] = []
    mixture: list[dict] = []
    limit = args.limit
    max_steps = args.max_steps
    if args.smoke:  # explicit --limit / --max-steps win; the tiny defaults are for a laptop check
        limit = SMOKE_EXAMPLES if limit is None else limit
        max_steps = SMOKE_STEPS if max_steps is None else max_steps
    dataset_dirs = list(getattr(args, "dataset", None) or [])
    if dataset_dirs:
        if args.data:
            raise SystemExit("give either --data or --dataset, not both")
        groups = []
        for d in dataset_dirs:
            info = verify_dataset_dir(d)  # raises on any hash mismatch: nothing is trained on
            datasets_info.append(info)
            groups.append(load_dataset_records(d))
            info["split_policy"] = assert_no_final_test(groups[-1])  # re-checked per record, not trusted from the manifest
            print(f"[train_lora] dataset {info['dataset_id']} ({info['records']} records, "
                  f"{info['images_verified']} screenshots verified) from {d}")
        weights = [float(w) for w in args.dataset_weights.split(",")] if getattr(args, "dataset_weights", None) else None
        records, mixture = mix_records(groups, weights, seed=args.seed, samples=getattr(args, "samples_per_epoch", None))
        if limit is not None:
            records = records[:limit]
        data_path = ",".join(dataset_dirs)
    else:
        data_path = args.data
        if data_path is None:
            if not args.smoke:
                raise SystemExit("--data or --dataset is required unless --smoke")
            data_path = make_synthetic_records(out_dir / "synthetic")
        records = load_records(data_path, limit=limit)
    if not records:
        raise SystemExit(f"no records in {data_path}")
    print(f"[train_lora] {len(records)} records from {data_path}")

    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")
    kernels = fast_kernel_status()
    model, processor = load_model_and_processor(
        args.model, max_image_side=args.max_image_side, attn=args.attn, dtype=args.dtype,
        device_map={"": 0} if use_cuda else None, revision=getattr(args, "revision", None),
    )
    arch = type(model).__name__
    if use_cuda and "Qwen3_5" in arch and not all(kernels.values()) and not getattr(args, "allow_slow_kernels", False):
        raise SystemExit(f"fast Gated DeltaNet kernels missing {kernels}; install flash-linear-attention and "
                         "causal-conv1d (train/serve/install.sh) or pass --allow-slow-kernels")
    if not use_cuda:
        model.to(device)
    target_modules = resolve_target_modules(getattr(args, "target_modules", "auto") or "auto",
                                            [n for n, _ in getattr(model, "named_modules", lambda: [])()])
    model = apply_lora(model, r=args.lora_r, alpha=args.lora_alpha, dropout=args.lora_dropout, target_modules=target_modules)
    lora_tensors = [n for n, p in getattr(model, "named_parameters", lambda: [])() if p.requires_grad]
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

    template_kwargs = template_kwargs_for(getattr(args, "thinking", "auto"), args.prompt_style)
    system_prompt_template = Path(args.system_prompt_file).read_text(encoding="utf-8") if args.system_prompt_file else None
    dataset = SFTExamples(records, max_image_side=args.max_image_side, style=args.prompt_style,
                          history_k=args.history_k, coord_space=args.coord_space,
                          system_prompt_template=system_prompt_template, instruction_note=args.instruction_note,
                          nav_macro=args.nav_macro, image_scale=getattr(args, "image_scale", 1.0))
    collate = make_collate(processor, template_kwargs=template_kwargs, max_seq_len=getattr(args, "max_seq_len", None))
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True, collate_fn=collate,
                        num_workers=args.num_workers, drop_last=False)
    training_args = {
        "model": args.model, "model_class": arch, "model_revision": _model_revision(args.model, getattr(args, "revision", None)),
        "datasets": datasets_info, "mixture": mixture, "data": None if dataset_dirs else str(data_path),
        "records": len(records), "prompt_style": args.prompt_style, "coord_space": args.coord_space,
        "history_k": args.history_k, "max_image_side": args.max_image_side,
        "image_scale": getattr(args, "image_scale", 1.0), "chat_template_kwargs": template_kwargs,
        "system_prompt_file": args.system_prompt_file,
        "system_prompt_sha256": hashlib.sha256(system_prompt_template.encode()).hexdigest() if system_prompt_template else None,
        "instruction_note": args.instruction_note, "nav_macro": args.nav_macro,
        "lora": {"r": args.lora_r, "alpha": args.lora_alpha, "dropout": args.lora_dropout,
                 "target_modules": target_modules, "trainable_tensors": len(lora_tensors)},
        "optim": {"lr": args.lr, "epochs": args.epochs, "batch_size": args.batch_size, "grad_accum": args.grad_accum,
                  "warmup_ratio": args.warmup_ratio, "weight_decay": args.weight_decay, "max_grad_norm": args.max_grad_norm,
                  "max_steps": max_steps, "seed": args.seed},
        "dtype": args.dtype, "attn": args.attn, "gradient_checkpointing": not args.no_gradient_checkpointing,
        "fast_kernels": kernels, "versions": _versions(), "code_git_sha": _git_sha(),
        "argv": list(sys.argv),
    }
    (out_dir / "training_args.json").write_text(json.dumps(training_args, indent=2, default=str), encoding="utf-8")
    if args.smoke:  # show one rendered chat so template problems are visible before the first step
        ex0 = dataset[0]
        print("[train_lora] smoke: first example prompt (tail) ...\n" + processor.apply_chat_template(
            ex0["prompt_messages"], tokenize=False, add_generation_prompt=True, **template_kwargs)[-600:])
        print("[train_lora] smoke: first example target: " + ex0["target"])
        b0 = collate([ex0])
        kept = b0["labels"][0][b0["labels"][0] != -100]
        tok = getattr(processor, "tokenizer", processor)
        print(f"[train_lora] smoke: {int(kept.numel())} label tokens in the loss; decoded: "
              + repr(tok.decode(kept.tolist()))[:400])
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
    span_loss = not getattr(args, "full_logits", False) and "logits_to_keep" in _forward_params(model)

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
    examples_seen = 0
    tokens_seen = 0
    completed_epochs = 0
    initial_adapter = {n: p.detach().cpu().clone() for n, p in model.named_parameters() if p.requires_grad} if args.smoke else {}
    gradient_norms = []
    step_times: list[float] = []
    t_step = time.time()

    def _save(tag: str) -> Path:
        p = out_dir / tag
        model.save_pretrained(p)
        try:
            processor.save_pretrained(p)
        except Exception:
            pass
        try:
            p.mkdir(parents=True, exist_ok=True)
            (p / "training_args.json").write_text(json.dumps(dict(training_args, saved_at_step=global_step), indent=2,
                                                             default=str), encoding="utf-8")
        except Exception:
            pass
        return p

    with log_path.open("a", encoding="utf-8") as log:
        while not stop:
            for epoch_micro, batch in enumerate(loader, start=1):
                current_batch_size = int(batch["input_ids"].shape[0])
                group_start = ((epoch_micro - 1) // args.grad_accum) * args.grad_accum
                group_size = min(args.grad_accum, micro_per_epoch - group_start)
                if "attention_mask" in batch:
                    tokens_seen += int(batch["attention_mask"].sum())
                batch = {k: (v.to(device) if hasattr(v, "to") else v) for k, v in batch.items()}
                if use_cuda and autocast_dtype is not None:
                    ctx = torch.autocast("cuda", dtype=autocast_dtype)
                else:
                    ctx = torch.autocast("cpu", dtype=torch.bfloat16) if autocast_dtype == torch.bfloat16 and not use_cuda else _NullCtx()
                with ctx:
                    if span_loss:
                        step_loss = target_span_loss(model, batch)
                        if args.smoke and micro == 0:  # the span loss must equal the model's own labels loss
                            with torch.no_grad():
                                ref = float(model(**batch).loss)
                            print(f"[train_lora] smoke: target-span loss {float(step_loss):.6f} vs full-logits loss {ref:.6f}")
                            if abs(ref - float(step_loss)) > 1e-3 * max(1.0, abs(ref)):
                                raise RuntimeError("target-span loss differs from the full-logits loss")
                    else:
                        step_loss = model(**batch).loss
                    if not bool(torch.isfinite(step_loss)):
                        raise FloatingPointError("nonfinite training loss")
                    loss = step_loss / group_size
                loss.backward()
                micro += 1
                examples_seen += current_batch_size
                running += float(step_loss.item())
                running_n += 1
                if args.smoke and micro <= 8:
                    rows = getattr(collate, "stats_rows", [])[-args.batch_size:]
                    print(f"[train_lora] smoke micro {micro}: loss={float(step_loss.item()):.4f} "
                          f"tokens={rows} peak_vram_gb={torch.cuda.max_memory_allocated() / 1e9 if use_cuda else 0:.2f}")
                if epoch_micro % args.grad_accum == 0 or epoch_micro == micro_per_epoch:
                    grad_norm = float(torch.nn.utils.clip_grad_norm_(params, args.max_grad_norm, error_if_nonfinite=True))
                    gradient_norms.append(grad_norm)
                    if args.smoke and grad_norm <= 0:
                        raise RuntimeError("smoke has zero adapter gradients")
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                    global_step += 1
                    now = time.time()
                    step_times.append(now - t_step)
                    t_step = now
                    mean_loss = running / max(1, running_n)
                    losses.append(mean_loss)
                    running, running_n = 0.0, 0
                    elapsed = time.time() - t_start
                    if global_step % args.log_steps == 0 or global_step == 1 or global_step == total_steps:
                        rec = {"step": global_step, "loss": mean_loss, "lr": scheduler.get_last_lr()[0],
                               "elapsed_s": elapsed, "s_per_step": elapsed / global_step,
                               "last_step_s": step_times[-1], "tokens_seen": tokens_seen,
                               "epoch": examples_seen / len(records), "examples_seen": examples_seen, "grad_norm": grad_norm}
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
            if epoch_micro == micro_per_epoch:
                completed_epochs += 1
            epoch += 1
            if not stop and epoch >= math.ceil(args.epochs):
                stop = True

    final = _save("final")
    elapsed = time.time() - t_start
    summary = {
        "model": args.model, "data": str(data_path), "records": len(records), "prompt_style": args.prompt_style,
        "datasets": datasets_info, "mixture": mixture,
        "steps": global_step, "planned_steps": total_steps, "epochs_completed": completed_epochs,
        "epoch_fraction": examples_seen / len(records), "examples_seen": examples_seen,
        "gradient_norms": gradient_norms, "tokens_seen": tokens_seen,
        "final_loss": losses[-1] if losses else None, "first_loss": losses[0] if losses else None,
        "elapsed_s": elapsed, "s_per_step": (elapsed / global_step) if global_step else None,
        "s_per_step_after_first": (sum(step_times[1:]) / len(step_times[1:])) if len(step_times) > 1 else None,
        "tokens_per_s": tokens_seen / elapsed if elapsed else None,
        "peak_vram_gb": (torch.cuda.max_memory_allocated() / 1e9) if use_cuda else None,
        "device": str(device), "adapter": str(final), "smoke": bool(args.smoke),
        "lora": {"r": args.lora_r, "alpha": args.lora_alpha, "dropout": args.lora_dropout, "target_modules": target_modules},
        "batch_size": args.batch_size, "grad_accum": args.grad_accum, "lr": args.lr, "max_image_side": args.max_image_side,
        "epochs": args.epochs, "warmup_ratio": args.warmup_ratio, "weight_decay": args.weight_decay,
        "max_grad_norm": args.max_grad_norm, "seed": args.seed, "history_k": args.history_k,
        "coord_space": args.coord_space, "dtype": args.dtype, "attn": args.attn,
        "gradient_checkpointing": not args.no_gradient_checkpointing, "chat_template_kwargs": template_kwargs,
        "fast_kernels": kernels, "loss": "target_span_logits" if span_loss else "full_logits",
        "system_prompt_file": args.system_prompt_file, "instruction_note": args.instruction_note, "nav_macro": args.nav_macro,
        "losses": losses,
    }
    if args.smoke:
        changed = {n: float((p.detach().cpu() - initial_adapter[n]).abs().max()) for n, p in model.named_parameters() if n in initial_adapter}
        summary["adapter_weight_change"] = {"tensors": len(changed), "changed_tensors": sum(v > 0 for v in changed.values()), "max_abs_delta": max(changed.values())}
        if not any(v > 0 for v in changed.values()):
            raise RuntimeError("smoke adapter weights did not change")
    rows = getattr(collate, "stats_rows", [])
    if rows:
        def _agg(key: str) -> dict:
            vals = [r[key] for r in rows]
            return {"min": min(vals), "max": max(vals), "mean": sum(vals) / len(vals)}
        summary["tokens"] = {"examples_tokenised": len(rows), "seq_len": _agg("seq_len"),
                             "image_tokens": _agg("image_tokens"), "target_len": _agg("target_len")}
    if args.merge_out:
        merged = model.merge_and_unload()
        merged.save_pretrained(args.merge_out)
        processor.save_pretrained(args.merge_out)
        summary["merged"] = str(args.merge_out)
    (out_dir / "train_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print("[train_lora] done: " + json.dumps({k: summary[k] for k in ("steps", "final_loss", "elapsed_s", "adapter")}))
    return summary


def target_span_loss(model, batch: dict):
    """Mean cross-entropy over supervised tokens, computing logits only for the target span.

    Targets sit at the end of every (right-padded) sequence, so the lm_head runs on the last
    ``L - first_supervised + 1`` positions (``logits_to_keep``) instead of all ~3-9k prompt
    positions: the 248k-vocab fp32 logits of the prompt (4-9 GB at batch 1) are never built.
    Same value as the model's own ``labels`` loss (shifted CE, mean over non-ignored tokens)."""
    import torch
    import torch.nn.functional as F

    labels = batch["labels"]
    sup = labels != -100
    if not bool(sup.any()):
        raise ValueError("batch has no supervised tokens")
    first = int(torch.where(sup.any(dim=0))[0].min())
    keep = labels.shape[1] - first + 1
    inputs = {k: v for k, v in batch.items() if k != "labels"}
    out = model(**inputs, logits_to_keep=keep)
    logits = out.logits[:, :-1, :].float()
    target = labels[:, labels.shape[1] - keep + 1:]
    return F.cross_entropy(logits.reshape(-1, logits.shape[-1]), target.reshape(-1), ignore_index=-100)


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
