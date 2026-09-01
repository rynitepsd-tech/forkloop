"""Episode-level JSONL: one line per episode with verdict, steps and reset timings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..trajectories import iter_episode_dirs, load_episode


def export_jsonl(run_dir: str | Path, out: str | Path, *, include_steps: bool = True,
                 only_success: bool = False) -> int:
    n = 0
    with open(out, "w", encoding="utf-8") as fh:
        for ep_dir in iter_episode_dirs(run_dir):
            ep = load_episode(ep_dir)
            v = ep["verdict"]
            if only_success and not (v and v.get("reward", 0) >= 1.0):
                continue
            m = ep["manifest"]
            rec: dict[str, Any] = {
                "episode_id": m.get("episode_id", ep_dir.name), "task_id": m["task_id"], "family": m["family"],
                "seed": m["seed"], "split": m["split"], "world": m["world"], "instruction": m["instruction"],
                "verdict": v, "reset": ep["reset"], "n_steps": len(ep["steps"]),
                "dir": str(ep_dir),
            }
            if include_steps:
                rec["steps"] = ep["steps"]
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n


__all__ = ["export_jsonl"]
