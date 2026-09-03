"""One row per episode: reward, steps, waits, Chrome crashes, kernel stalls, tokens, cost.

    python scripts/episode_table.py runs/teacher-pilot4 runs/teacher-f3-s0-9 runs/teacher-f3-s1-9 [--md]

"crashes" counts model turns whose note mentions a crashed page/tab (the teacher's own
words); "netsvc" counts Chrome "Network service crashed" lines; "crashpad" counts crash reports Chrome's crashpad handler wrote (one per crashed process); "stall" is the number of
RCU stall reports in the guest kernel log captured at episode end (the golden image
carries one from its own snapshot, so subtract the baseline when comparing).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from forkloop.metrics import MODEL_PRICES_PER_M, episode_tokens, token_cost_usd
from forkloop.trajectories import iter_episode_dirs, load_episode

CRASH = re.compile(r"crash", re.I)


def row(ep_dir: Path, model: str) -> dict:
    e = load_episode(ep_dir)
    steps, v = e["steps"], e["verdict"] or {}
    notes = []
    for s in steps:
        n = s.get("policy_note", "")
        if n and (not notes or notes[-1] != n):
            notes.append(n)
    diag = ep_dir / "diagnostics"
    chrome = (diag / "chrome.log").read_text() if (diag / "chrome.log").exists() else ""
    dmesg = (diag / "dmesg.log").read_text() if (diag / "dmesg.log").exists() else ""
    toks = episode_tokens(e)
    return {
        "episode": ep_dir.name.rsplit("-", 1)[0],
        "reward": v.get("reward"),
        "reason": v.get("reason_code", "NO_VERDICT"),
        "steps": len(steps),
        "waits": sum(1 for s in steps if (s.get("action") or {}).get("type") == "wait"),
        "wall_s": round(float(v.get("wall_seconds", 0.0))),
        "crashes": sum(1 for n in notes if CRASH.search(n)),
        "netsvc": chrome.count("Network service crashed"),
        "crashpad": chrome.count("scaling_cur_freq"),
        "stall": len(re.findall(r"self-detected stall", dmesg)),
        "tok_in": toks["in"],
        "tok_out": toks["out"],
        "usd": round(token_cost_usd(toks, MODEL_PRICES_PER_M.get(model, (0.0, 0.0))), 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--model", default=None)
    ap.add_argument("--md", action="store_true")
    a = ap.parse_args()
    rows = []
    for r in a.runs:
        meta = json.loads((Path(r) / "run.json").read_text()) if (Path(r) / "run.json").exists() else {}
        model = a.model or meta.get("model") or "claude-opus-5"
        for d in iter_episode_dirs(r):
            rows.append({"run": Path(r).name, **row(d, model)})
    cols = ["run", "episode", "reward", "reason", "steps", "waits", "wall_s", "crashes", "netsvc", "crashpad", "stall", "tok_in", "tok_out", "usd"]
    if a.md:
        print("| " + " | ".join(cols) + " |")
        print("|" + "---|" * len(cols))
        for r in rows:
            print("| " + " | ".join(str(r[c]) for c in cols) + " |")
    else:
        w = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
        print("  ".join(c.ljust(w[c]) for c in cols))
        for r in rows:
            print("  ".join(str(r[c]).ljust(w[c]) for c in cols))
    ok = [r for r in rows if r["reward"] == 1.0]
    print(f"\n{len(ok)}/{len(rows)} verified; crashes per episode: verified "
          f"{sum(r['crashes'] for r in ok) / max(1, len(ok)):.1f}, failed "
          f"{sum(r['crashes'] for r in rows if r not in ok) / max(1, len(rows) - len(ok)):.1f}; "
          f"total model spend ${sum(r['usd'] for r in rows):.2f}")


if __name__ == "__main__":
    main()
