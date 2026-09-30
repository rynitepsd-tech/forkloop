"""Select a frozen set of recorded states for the offline student probes (no world needed).

Reads verified teacher episodes (``verdict.json`` reward 1.0, contracts.md §10) and writes
``<out>/states.jsonl`` plus copies of the screenshots it needs under ``<out>/shots/``:

* ``read`` states: the first step on which the teacher's reply transcribes the true letter's
  authorization number (exactly or within 2 edits: the teacher's own first reading is sometimes a
  misread) and the action is not the typing step. The teacher read it from that step's
  screenshot, so the letter is on screen (one per episode, in episode order, up to ``--n-read``).
  The later "first exact mention" is often already on the portal screen, from memory.
* ``click`` states: teacher ``click`` steps that carry a reasoning line (the target
  description), two per episode at 1/3 and 2/3 of that episode's click list, up to ``--n-click``.
  Clicks above ``--min-click-y`` (the browser tab strip and omnibox, y < 70 on 1280x720) and
  steps already used as read states are skipped, so the set exercises in-page UI elements.

Each record holds only what the student would see (instruction, compact history in screen
coordinates, previous/current screenshots) plus the scoring fields (``expected_auth``,
``teacher_action``, ``teacher_reasoning``) kept apart under ``score``. Selection is
deterministic: episodes are sorted by (run dir, episode id).

    python -m train.probe_select \\
        --run-dir runs/model-upgrade-live-20260924/compare-a/runs/A \\
        --run-dir runs/model-upgrade-live-20260924/compare-b/runs/A \\
        --out data/probe_states_20260928 --n-read 16 --n-click 28
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from train.make_sft import iter_episodes, reasoning_from_raw, _target_for  # noqa: E402


AUTH_TOKEN = re.compile(r"AUTH-[A-Z0-9]+")


def _near(token: str, expected: str, max_edits: int = 2) -> bool:
    """Levenshtein distance <= max_edits: the teacher's first (possibly misread) transcription of the
    true letter's number. Decoy letters carry unrelated numbers, which are farther away."""
    prev = list(range(len(expected) + 1))
    for i, ca in enumerate(token, 1):
        cur = [i]
        for j, cb in enumerate(expected, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] <= max_edits


def _state(ep, index: int, kind: str, out: Path, history_k: int) -> dict:
    steps = ep.steps
    step = steps[index]
    shots = []
    for s in ([steps[index - 1]] if index else []) + [step]:
        src = (ep.episode_dir / s["shot_before"]).resolve()
        name = f"{ep.run_dir.parent.parent.name}-{ep.run_dir.name}-{ep.episode_dir.name}-{int(s['i']):03d}.png"
        dst = out / "shots" / name
        if not dst.exists():
            shutil.copyfile(src, dst)
        shots.append("shots/" + name)
    history = [t for t in (_target_for(s) for s in steps[:index]) if t][-history_k:] if history_k else []
    m = ep.manifest
    sid = f"{kind}-{ep.run_dir.parent.parent.name}-{ep.run_dir.name}-{ep.episode_dir.name}-{int(step['i']):03d}"
    return {
        "id": sid, "kind": kind, "step": int(step["i"]), "instruction": m.get("instruction", ""),
        "history": history, "images": shots, "image_roles": ["previous", "current"] if index else ["current"],
        "screen_size": [1280, 720], "task_id": m.get("task_id"), "seed": m.get("seed"), "split": m.get("split"),
        "source": str(ep.episode_dir),
        "score": {"expected_auth": (m.get("expected") or {}).get("auth_number"),
                  "teacher_action": step.get("action"), "teacher_reasoning": reasoning_from_raw(step.get("raw_action"))},
    }


def select_states(run_dirs: list[Path], out: Path, *, n_read: int, n_click: int, history_k: int = 8,
                  min_click_y: int = 70) -> list[dict]:
    (out / "shots").mkdir(parents=True, exist_ok=True)
    episodes = []
    for rd in run_dirs:
        for ep in iter_episodes(rd):
            if ep.reward >= 1.0 and not ep.bad_lines:
                episodes.append(ep)
    episodes.sort(key=lambda e: (str(e.run_dir), e.episode_dir.name))
    reads, clicks = [], []
    for ep in episodes:
        auth = (ep.manifest.get("expected") or {}).get("auth_number")
        if auth and len(reads) < n_read:
            for i, s in enumerate(ep.steps):
                a = s.get("action") or {}
                if a.get("type") != "type" and any(_near(tok, auth) for tok in AUTH_TOKEN.findall(s.get("raw_action") or "")):
                    reads.append(_state(ep, i, "read", out, history_k))
                    break
        if len(clicks) < n_click:
            taken = {r["step"] for r in reads if r["source"] == str(ep.episode_dir)}
            idx = [i for i, s in enumerate(ep.steps) if (s.get("action") or {}).get("type") == "click"
                   and int((s.get("action") or {}).get("y", 0)) >= min_click_y  # skip the browser tab strip
                   and reasoning_from_raw(s.get("raw_action")) and i > 0 and int(s["i"]) not in taken]
            for frac in (1 / 3, 2 / 3):
                if idx and len(clicks) < n_click:
                    clicks.append(_state(ep, idx[int(frac * len(idx))], "click", out, history_k))
    states = reads + clicks
    with (out / "states.jsonl").open("w", encoding="utf-8") as f:
        for s in states:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    digest = hashlib.sha256((out / "states.jsonl").read_bytes()).hexdigest()
    (out / "states.meta.json").write_text(json.dumps({
        "runs": [str(r) for r in run_dirs], "episodes_used": len(episodes), "n_read": len(reads), "n_click": len(clicks),
        "states_sha256": digest, "history_k": history_k, "min_click_y": min_click_y}, indent=2), encoding="utf-8")
    return states


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-dir", action="append", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--n-read", type=int, default=16)
    p.add_argument("--n-click", type=int, default=28)
    p.add_argument("--history-k", type=int, default=8)
    p.add_argument("--min-click-y", type=int, default=70)
    a = p.parse_args(argv)
    states = select_states([Path(r) for r in a.run_dir], Path(a.out), n_read=a.n_read, n_click=a.n_click,
                           history_k=a.history_k, min_click_y=a.min_click_y)
    print(json.dumps({"out": a.out, "states": len(states),
                      "read": sum(s["kind"] == "read" for s in states),
                      "click": sum(s["kind"] == "click" for s in states)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
