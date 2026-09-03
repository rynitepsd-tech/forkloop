"""Per-seed comparison of two teacher configurations on one family.

    python scripts/compare_teachers.py --a "Opus" runs/teacher-f3-s0-9 runs/teacher-f3-s1-9 runs/teacher-f3-s10-14-8gb \
                                       --b "Luna v4" runs/luna-high-v4-s0-2 runs/luna-high-v4-s3-19 [--md]

Prints one row per seed (reward, reason, steps, model cost) for both sides, Wilson 95 % success
intervals, total and per-verified cost, and the head-to-head on shared seeds. Model prices come
from run.json's `model` via forkloop.metrics.MODEL_PRICES_PER_M.
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from forkloop.metrics import MODEL_PRICES_PER_M, episode_tokens, token_cost_usd, wilson
from forkloop.trajectories import load_episode


def load(runs: list[str]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for run in runs:
        meta = json.loads((Path(run) / "run.json").read_text()) if (Path(run) / "run.json").exists() else {}
        price = MODEL_PRICES_PER_M.get(meta.get("model") or "", (0.0, 0.0))
        for ep in sorted(glob.glob(f"{run}/episodes/*")):
            e = load_episode(Path(ep))
            if not e["verdict"]:
                continue
            out[int(e["manifest"]["seed"])] = {"reward": e["verdict"]["reward"], "reason": e["verdict"]["reason_code"],
                                              "steps": len(e["steps"]), "usd": token_cost_usd(episode_tokens(e), price)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", nargs="+", required=True, metavar=("LABEL", "RUN"))
    ap.add_argument("--b", nargs="+", required=True, metavar=("LABEL", "RUN"))
    ap.add_argument("--md", action="store_true")
    args = ap.parse_args()
    la, a = args.a[0], load(args.a[1:])
    lb, b = args.b[0], load(args.b[1:])
    sep = " | " if args.md else "  "
    hdr = f"seed{sep}{la}{sep}{lb}"
    print(("| " + hdr + " |") if args.md else hdr)
    if args.md:
        print("|---|---|---|")
    for s in sorted(set(a) | set(b)):
        cells = []
        for d in (a, b):
            r = d.get(s)
            cells.append(f"{r['reward']:.0f} {r['reason']} {r['steps']} st ${r['usd']:.3f}" if r else "-")
        line = f"{s}{sep}{cells[0]}{sep}{cells[1]}"
        print(("| " + line + " |") if args.md else line)
    print()
    for label, d in ((la, a), (lb, b)):
        k = sum(1 for r in d.values() if r["reward"] == 1.0)
        n = len(d)
        p, lo, hi = wilson(k, n)
        cost = sum(r["usd"] for r in d.values())
        print(f"{label}: {k}/{n} = {p * 100:.0f}% [{lo * 100:.0f}, {hi * 100:.0f}]; model spend ${cost:.2f}; "
              f"per verified ${cost / max(1, k):.3f}")
    shared = sorted(set(a) & set(b))
    if shared:
        ka = sum(1 for s in shared if a[s]["reward"] == 1.0)
        kb = sum(1 for s in shared if b[s]["reward"] == 1.0)
        print(f"shared seeds ({len(shared)}): {la} {ka}, {lb} {kb}")


if __name__ == "__main__":
    main()
