"""Build a small SYNTHETIC-BUT-REALISTIC ``forkloop.dataset.v1`` directory for smoke training.

Not an export of the correction loop (``forkloop dataset`` needs a correction store): it converts
verified recorded teacher episodes (``runs/<run>/episodes/*``, contracts.md §10) into the same
record layout, so ``train/train_lora.py --dataset`` and ``train/parity.py`` can be exercised on
real 1280x720 screenshots and real teacher replies before the loop has produced data.

The teacher of those runs did not use the explicit-memory prompt, so memory is synthesized: on
the first step whose reply contains the authorization number the episode later types, the target
gains ``Memory: Authorization number <AUTH>`` and every later input carries that fact. Everything
else (instruction, compact history in screen pixels, previous/current screenshots, reasoning
line, action) is the recorded episode. The manifest says ``"synthetic": true``.

    python -m train.smoke_dataset --run-dir runs/luna-v5-f3-s20-99 --episodes 2 --per-episode 16 \\
        --out data/smoke-ds-20260929
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from train.make_sft import _target_for, iter_episodes  # noqa: E402

SCHEMA = "forkloop.dataset.v1"
AUTH_RE = re.compile(r"AUTH-[A-Z0-9]{6,}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def episode_records(ep, images_dir: Path, *, per_episode: int) -> list[dict]:
    from forkloop.correction.dataset import reasoning_text

    steps = ep.steps
    typed = [s["action"]["text"] for s in steps if (s.get("action") or {}).get("type") == "type"
             and AUTH_RE.search(s["action"].get("text", ""))]
    auth = AUTH_RE.search(typed[0]).group(0) if typed else None
    read_i = next((i for i, s in enumerate(steps) if auth and auth in (s.get("raw_action") or "")
                   and (s.get("action") or {}).get("type") != "type"), None)
    type_i = next((i for i, s in enumerate(steps) if auth and (s.get("action") or {}).get("type") == "type"
                   and auth in s["action"].get("text", "")), None)
    wanted: list[int] = []
    for center in (read_i, type_i):
        if center is not None:
            wanted += [i for i in range(center - 3, center + 3) if 0 <= i < len(steps)]
    wanted += [i for i in range(len(steps)) if i not in wanted][: max(0, per_episode - len(set(wanted)))]
    wanted = sorted(set(wanted))[:per_episode]

    def shot(i: int) -> dict:
        src = ep.episode_dir / steps[i]["shot_before"]
        sha = _sha(src)
        dst = images_dir / f"{sha}.png"
        if not dst.exists():
            shutil.copyfile(src, dst)
        return {"path": f"images/{sha}.png", "sha256": sha}

    out, history, memory = [], [], []
    for i, s in enumerate(steps):
        raw = s.get("raw_action") or ""
        written = [f"Authorization number {auth}"] if (i == read_i and auth) else []
        if i in wanted and s.get("action") and s.get("valid", True):
            reasoning = reasoning_text(raw)
            reply_lines = [reasoning] if reasoning else []
            reply_lines += [f"Memory: {m}" for m in written]
            reply_lines.append(_target_for(s))
            out.append({
                "schema": SCHEMA, "kind": "action_demonstration", "origin": "synthetic_smoke_from_recorded_episode",
                "record_id": hashlib.sha256(f"{ep.run_dir.name}/{ep.episode_dir.name}:{i}".encode()).hexdigest()[:16],
                "input": {"instruction": ep.manifest["instruction"], "history": list(history), "memory": list(memory),
                          "step": i, "screen": [1280, 720], "current_shot": shot(i),
                          "previous_shot": shot(i - 1) if i > 0 else None},
                "target": {"reasoning": reasoning, "memory_written": written, "action": s["action"],
                           "raw_reply": "\n".join(reply_lines)},
                "source": {"run": ep.run_dir.name, "episode": ep.episode_dir.name, "task_id": ep.task_id,
                           "split": ep.manifest.get("split"), "step": i},
                "audit": {"synthetic_memory": bool(written) or bool(memory)},
            })
        t = _target_for(s)
        if t:
            history.append(t)
        memory = memory + [m for m in written if m not in memory]
    return out


def build(run_dirs: list[Path], out: Path, *, episodes: int, per_episode: int) -> dict:
    if out.exists():
        raise FileExistsError(f"{out} exists; datasets are immutable")
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "images").mkdir(parents=True)
    eps = []
    for rd in run_dirs:
        eps += [e for e in iter_episodes(rd) if e.reward >= 1.0 and not e.bad_lines
                and str(e.manifest.get("split", "")).startswith("train")]
    eps.sort(key=lambda e: e.task_id)
    records = []
    for ep in eps[:episodes]:
        records += episode_records(ep, tmp / "images", per_episode=per_episode)
    with (tmp / "records.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
    for name in ("preferences.jsonl", "diagnostics.jsonl"):
        (tmp / name).write_text("", encoding="utf-8")
    files = {n: _sha(tmp / n) for n in ("records.jsonl", "preferences.jsonl", "diagnostics.jsonl")}
    manifest = {"schema": SCHEMA, "dataset_id": "ds-" + files["records.jsonl"][:12], "name": out.name,
                "synthetic": True, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "counts": {"records": len(records), "preferences": 0, "diagnostics": 0,
                           "images": len(list((tmp / "images").glob("*.png")))},
                "files": files, "splits": sorted({r["source"]["split"] for r in records}),
                "sources": [{"run": str(e.run_dir), "episode": e.episode_dir.name} for e in eps[:episodes]],
                "notes": ["SYNTHETIC smoke dataset built by train/smoke_dataset.py from recorded teacher episodes; "
                          "Memory lines are synthesized (the teacher did not use the memory prompt)"]}
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.rename(out)
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-dir", action="append", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--episodes", type=int, default=2)
    p.add_argument("--per-episode", type=int, default=16)
    a = p.parse_args(argv)
    m = build([Path(r) for r in a.run_dir], Path(a.out), episodes=a.episodes, per_episode=a.per_episode)
    print(json.dumps({k: m[k] for k in ("dataset_id", "counts", "splits")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
