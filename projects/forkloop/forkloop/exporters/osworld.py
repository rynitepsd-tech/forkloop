"""OSWorld-style task JSON for interoperability (one file per task).

OSWorld tasks carry ``instruction``, a ``config`` list of setup steps and an
``evaluator``. We map: instruction → instruction; seeding → a single
``forkloop_seed`` config step (opaque, so the expected state stays out of the
file unless ``include_expected`` is set); evaluator → ``{"func": "forkloop_oracle"}``
with the check ids only. This is enough for a runner to schedule the task on a
Solari snapshot and call back into forkloop for reward.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..trajectories import iter_episode_dirs, load_episode


def export_osworld(run_dir: str | Path, out_dir: str | Path, *, include_expected: bool = False) -> int:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    seen: set[str] = set()
    for ep_dir in iter_episode_dirs(run_dir):
        m = load_episode(ep_dir)["manifest"]
        if m["task_id"] in seen:
            continue
        seen.add(m["task_id"])
        task = {
            "id": m["task_id"],
            "snapshot": m.get("world"),
            "instruction": m["instruction"],
            "source": "forkloop",
            "config": [
                {"type": "forkloop_seed", "parameters": {"world": m["world"], "family": m["family"], "seed": m["seed"], "split": m["split"]}},
                {"type": "launch", "parameters": {"command": ["forkloop", "open-screen", json.dumps(m.get("initial_screen", {}))]}},
            ],
            "related_apps": ["chrome", "openemr", "payer-portal"],
            "evaluator": {"func": "forkloop_oracle", "expected": m.get("expected") if include_expected else None,
                          "checks": [c["id"] for c in m["oracle"]["effects"]] + [c["id"] for c in m["oracle"]["invariants"]]},
            "budget": m.get("budget"),
        }
        (out_dir / f"{m['task_id']}.json").write_text(json.dumps(task, indent=2, ensure_ascii=False))
        n += 1
    return n


__all__ = ["export_osworld"]
