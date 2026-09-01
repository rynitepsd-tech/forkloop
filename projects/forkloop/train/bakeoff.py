"""Student model bake-off (plan §6): pick the base model before spending GPU hours.

For each candidate endpoint the script (1) runs ``train.eval`` for N episodes to
get base success rate, action-format validity and tokens/step, (2) runs a
``train.train_lora --smoke`` (2 optimiser steps on 4 examples, bounded by
``--smoke-max-minutes``) to prove the model loads, tokenises and trains on this
card, and (3) reads VRAM from ``nvidia-smi`` (``"n/a"`` when absent). It writes
``bakeoff.md`` (a markdown table) and ``bakeoff.json``.

Config (``--config bakeoff.json``; ``--example-config`` prints one)::

    {
      "candidates": [
        {"name": "fara-1.5-4b", "base_url": "http://localhost:8001/v1", "model": "microsoft/Fara1.5-4B",
         "hf_id": "microsoft/Fara1.5-4B", "prompt_style": "fara", "coord_space": "norm1000"},
        {"name": "qwen3.5-vl-4b", "base_url": "http://localhost:8002/v1", "model": "Qwen/Qwen3.5-VL-4B-Instruct",
         "hf_id": "Qwen/Qwen3.5-VL-4B-Instruct", "prompt_style": "compact", "coord_space": "image"}
      ],
      "eval": {"world": "claims-ops-v1", "families": "resolve_denial", "split": "heldout_seeds",
               "n_episodes": 20, "n_seeds": 1, "backend": "solari", "concurrency": 4},
      "smoke": {"data": "data/sft_all.jsonl", "max_minutes": 60, "max_image_side": 1280}
    }

Usage::

    python -m train.bakeoff --config bakeoff.json --out-dir bakeoff
    python -m train.bakeoff --config bakeoff.json --dry-run     # print the commands only
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

EXAMPLE_CONFIG: dict = {
    "candidates": [
        {"name": "fara-1.5-4b", "base_url": "http://localhost:8001/v1", "model": "microsoft/Fara1.5-4B",
         "hf_id": "microsoft/Fara1.5-4B", "prompt_style": "fara", "coord_space": "norm1000"},
        {"name": "qwen3.5-vl-4b", "base_url": "http://localhost:8002/v1", "model": "Qwen/Qwen3.5-VL-4B-Instruct",
         "hf_id": "Qwen/Qwen3.5-VL-4B-Instruct", "prompt_style": "compact", "coord_space": "image"},
        {"name": "ui-tars-1.5-7b", "base_url": "http://localhost:8003/v1", "model": "ByteDance-Seed/UI-TARS-1.5-7B",
         "hf_id": "ByteDance-Seed/UI-TARS-1.5-7B", "prompt_style": "json", "coord_space": "image"},
    ],
    "eval": {"world": "claims-ops-v1", "families": "resolve_denial", "split": "heldout_seeds",
             "n_episodes": 20, "n_seeds": 1, "backend": "solari", "concurrency": 4, "max_tokens": 512},
    "smoke": {"data": None, "max_minutes": 60, "max_image_side": 1280, "lora_r": 16},
}

COLUMNS = [
    ("name", "Candidate"), ("model", "Served model"), ("prompt_style", "Style"),
    ("base_success", "Base success (95% CI)"), ("validity", "Action-format validity"),
    ("tokens_per_step", "Tokens/step"), ("latency_s", "Latency/step (s)"),
    ("smoke", "LoRA smoke"), ("s_per_step", "Train s/step"), ("vram", "VRAM"), ("notes", "Notes"),
]


def read_nvidia_smi() -> str:
    """``"<gpu>: <used>/<total> MiB"`` per GPU, or ``"n/a"`` when nvidia-smi is unavailable."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return "n/a"
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "n/a"
    if out.returncode != 0 or not out.stdout.strip():
        return "n/a"
    rows = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            rows.append(f"{parts[0]}: {parts[1]}/{parts[2]} MiB")
    return "; ".join(rows) if rows else "n/a"


def eval_command(cand: dict, ev: dict, out_dir: Path, python: str) -> list[str]:
    cmd = [python, "-m", "train.eval",
           "--world", str(ev.get("world", "claims-ops-v1")),
           "--families", str(ev.get("families", "resolve_denial")),
           "--split", str(ev.get("split", "heldout_seeds")),
           "--n-episodes", str(ev.get("n_episodes", 20)),
           "--n-seeds", str(ev.get("n_seeds", 1)),
           "--concurrency", str(ev.get("concurrency", 4)),
           "--backend", str(ev.get("backend", "fake")),
           "--policy", "student",
           "--base-url", str(cand["base_url"]),
           "--model", str(cand["model"]),
           "--prompt-style", str(cand.get("prompt_style", "compact")),
           "--coord-space", str(cand.get("coord_space", "auto")),
           "--max-tokens", str(ev.get("max_tokens", 512)),
           "--image-max-side", str(cand.get("image_max_side", ev.get("image_max_side", 1280))),
           "--out-dir", str(out_dir), "--tag", "base", "--checkpoint", f"{cand['name']}:base"]
    if cand.get("api_key"):
        cmd += ["--api-key", str(cand["api_key"])]
    if ev.get("env_factory"):
        cmd += ["--env-factory", str(ev["env_factory"])]
    if ev.get("seeds"):
        cmd += ["--seeds", str(ev["seeds"])]
    return cmd


def smoke_command(cand: dict, sm: dict, out_dir: Path, python: str) -> list[str]:
    cmd = [python, "-m", "train.train_lora", "--model", str(cand.get("hf_id") or cand["model"]),
           "--output-dir", str(out_dir / "smoke"), "--smoke",
           "--prompt-style", str(cand.get("prompt_style", "compact")),
           "--coord-space", str(cand.get("coord_space", "auto")) if cand.get("coord_space", "auto") != "screen" else "auto",
           "--max-image-side", str(sm.get("max_image_side", 1280)),
           "--lora-r", str(sm.get("lora_r", 16)), "--lora-alpha", str(sm.get("lora_alpha", 32))]
    if sm.get("data"):
        cmd += ["--data", str(sm["data"])]
    if sm.get("max_minutes") is not None:
        cmd += ["--max-minutes", str(sm["max_minutes"])]
    return cmd


def _run(cmd: list[str], cwd: Path, log_path: Path, timeout_s: float | None) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s, check=False)
        log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-5:])
        return proc.returncode, tail
    except subprocess.TimeoutExpired as e:
        log_path.write_text(f"TIMEOUT after {timeout_s}s\n{e.stdout or ''}\n{e.stderr or ''}", encoding="utf-8")
        return -1, f"timeout after {timeout_s}s"
    except OSError as e:
        return -2, f"{type(e).__name__}: {e}"


def _fmt_pct(v: float | None) -> str:
    return "n/a" if v is None else f"{100 * v:.1f}%"


def run_candidate(cand: dict, cfg: dict, out_root: Path, *, python: str, skip_eval: bool, skip_smoke: bool,
                  dry_run: bool) -> dict:
    out_dir = out_root / cand["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    row: dict[str, Any] = {
        "name": cand["name"], "model": cand["model"], "prompt_style": cand.get("prompt_style", "compact"),
        "base_success": "n/a", "validity": "n/a", "tokens_per_step": "n/a", "latency_s": "n/a",
        "smoke": "skipped", "s_per_step": "n/a", "vram": "n/a", "notes": "",
        "eval_summary": None, "smoke_summary": None, "commands": {},
    }
    ev = cfg.get("eval", {})
    sm = cfg.get("smoke", {})
    notes: list[str] = []

    if not skip_eval:
        cmd = eval_command(cand, ev, out_dir, python)
        row["commands"]["eval"] = " ".join(cmd)
        if dry_run:
            print("[bakeoff] would run:", " ".join(cmd))
        else:
            rc, tail = _run(cmd, _ROOT, out_dir / "eval.log", timeout_s=ev.get("timeout_s"))
            summary_path = out_dir / "base" / "eval_summary.json"
            if rc == 0 and summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                row["eval_summary"] = summary
                s = summary.get("success", {})
                row["base_success"] = f"{_fmt_pct(s.get('rate'))} [{_fmt_pct(s.get('low'))}, {_fmt_pct(s.get('high'))}] n={s.get('n')}"
                row["validity"] = _fmt_pct(summary.get("action_format_validity_rate"))
                tps = summary.get("tokens_per_step")
                row["tokens_per_step"] = "n/a" if tps is None else f"{tps:.0f}"
                lat = summary.get("model_latency_s_per_step")
                row["latency_s"] = "n/a" if lat is None else f"{lat:.2f}"
                if summary.get("n_errors"):
                    notes.append(f"{summary['n_errors']} eval errors")
            else:
                row["base_success"] = "error"
                notes.append(f"eval failed (rc={rc}): {tail[:160]}")

    if not skip_smoke:
        cmd = smoke_command(cand, sm, out_dir, python)
        row["commands"]["smoke"] = " ".join(cmd)
        if dry_run:
            print("[bakeoff] would run:", " ".join(cmd))
        else:
            budget = sm.get("max_minutes")
            t0 = time.time()
            rc, tail = _run(cmd, _ROOT, out_dir / "smoke.log", timeout_s=(budget * 60 + 900) if budget else None)
            summary_path = out_dir / "smoke" / "train_summary.json"
            if rc == 0 and summary_path.exists():
                ts = json.loads(summary_path.read_text(encoding="utf-8"))
                row["smoke_summary"] = ts
                row["smoke"] = f"ok ({ts.get('steps')} steps, loss {ts.get('first_loss'):.3f}->{ts.get('final_loss'):.3f})" \
                    if ts.get("first_loss") is not None else f"ok ({ts.get('steps')} steps)"
                sps = ts.get("s_per_step")
                row["s_per_step"] = "n/a" if sps is None else f"{sps:.1f}"
                if ts.get("peak_vram_gb") is not None:
                    row["vram"] = f"{ts['peak_vram_gb']:.1f} GB peak (torch)"
            else:
                row["smoke"] = f"FAILED (rc={rc}, {time.time() - t0:.0f}s)"
                notes.append(f"smoke: {tail[:160]}")
            if row["vram"] == "n/a":
                row["vram"] = read_nvidia_smi()

    row["notes"] = "; ".join(notes)
    return row


def render_markdown(rows: list[dict], cfg: dict | None = None) -> str:
    cfg = cfg or {}
    ev = cfg.get("eval", {})
    lines = ["# Student bake-off", ""]
    if ev:
        lines.append(f"World `{ev.get('world', '?')}`, families `{ev.get('families', '?')}`, split `{ev.get('split', '?')}`, "
                     f"{ev.get('n_episodes', '?')} episodes x {ev.get('n_seeds', 1)} seed(s), backend `{ev.get('backend', '?')}`.")
        lines.append("")
    lines.append(f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')}. VRAM is read from `nvidia-smi` "
                 f"(or torch peak allocation during the smoke); `n/a` means no GPU was visible.")
    lines.append("")
    header = "| " + " | ".join(label for _, label in COLUMNS) + " |"
    sep = "|" + "|".join(" --- " for _ in COLUMNS) + "|"
    lines += [header, sep]
    for r in rows:
        cells = [str(r.get(key, "")).replace("|", "\\|").replace("\n", " ") for key, _ in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Pick the candidate with the highest base success whose action-format validity is >= 95% "
                 "and whose smoke trained on the card; ties go to fewer tokens/step.")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="train.bakeoff", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="bakeoff config JSON (see --example-config)")
    p.add_argument("--out-dir", default="bakeoff")
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--skip-eval", action="store_true")
    p.add_argument("--skip-smoke", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="print commands, run nothing")
    p.add_argument("--example-config", action="store_true", help="print an example config and exit")
    p.add_argument("--only", default=None, help="comma-separated candidate names to run")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.example_config:
        print(json.dumps(EXAMPLE_CONFIG, indent=2))
        return 0
    if not args.config:
        print("error: --config is required (see --example-config)", file=sys.stderr)
        return 2
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    cands = cfg.get("candidates") or []
    if args.only:
        keep = {n.strip() for n in args.only.split(",")}
        cands = [c for c in cands if c.get("name") in keep]
    if not cands:
        print("error: no candidates", file=sys.stderr)
        return 2
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for cand in cands:
        print(f"[bakeoff] === {cand['name']} ===")
        rows.append(run_candidate(cand, cfg, out_root, python=args.python, skip_eval=args.skip_eval,
                                  skip_smoke=args.skip_smoke, dry_run=args.dry_run))
    md = render_markdown(rows, cfg)
    (out_root / "bakeoff.md").write_text(md, encoding="utf-8")
    (out_root / "bakeoff.json").write_text(json.dumps({"config": cfg, "rows": rows}, indent=2, default=str), encoding="utf-8")
    print(md)
    print(f"[bakeoff] wrote {out_root / 'bakeoff.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
