"""Per-step SFT examples from verified (reward == 1) episodes.

Each record: ``{"images": [abs png], "instruction", "history": [...], "target": raw_action,
"task_id", "family", "seed", "split", "step"}``. The history is the last ``k``
compact actions before this step, matching what the student sees at inference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..actions import Action, InvalidAction
from ..trajectories import iter_episode_dirs, load_episode


def _compact(step: dict) -> Optional[str]:
    a = step.get("action")
    if not a:
        return None
    try:
        return Action.from_dict(a).to_compact()
    except InvalidAction:
        return None


def export_sft_pairs(run_dir: str | Path, out: str | Path, *, history_k: int = 8, limit_episodes: Optional[int] = None,
                     families: Optional[list[str]] = None, exclude_splits: tuple[str, ...] = ("heldout_seeds", "heldout_compositions"),
                     drop_invalid: bool = True) -> dict:
    n_eps = n_steps = 0
    eps = []
    for ep_dir in iter_episode_dirs(run_dir):
        ep = load_episode(ep_dir)
        v = ep["verdict"]
        if not v or v.get("reward", 0) < 1.0:
            continue
        m = ep["manifest"]
        if families and m["family"] not in families:
            continue
        if m["split"] in exclude_splits:
            continue
        eps.append(ep)
    eps.sort(key=lambda e: e["manifest"]["task_id"])
    if limit_episodes is not None:
        eps = eps[:limit_episodes]
    with open(out, "w", encoding="utf-8") as fh:
        for ep in eps:
            m = ep["manifest"]
            hist: list[str] = []
            n_eps += 1
            for s in ep["steps"]:
                target = _compact(s)
                if target is None or (drop_invalid and not s.get("valid", True)):
                    if target:
                        hist.append(target)
                    continue
                img = ep["dir"] / s["shot_before"] if s.get("shot_before") else None
                if img is None or not img.exists():
                    hist.append(target)
                    continue
                fh.write(json.dumps({
                    "images": [str(img.resolve())], "instruction": m["instruction"], "history": hist[-history_k:],
                    "target": target, "task_id": m["task_id"], "family": m["family"], "seed": m["seed"],
                    "split": m["split"], "step": s["i"],
                }, ensure_ascii=False) + "\n")
                n_steps += 1
                hist.append(target)
    return {"episodes": n_eps, "examples": n_steps, "out": str(out)}


__all__ = ["export_sft_pairs"]
