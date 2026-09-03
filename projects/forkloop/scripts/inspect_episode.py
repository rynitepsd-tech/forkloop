"""Failure triage for one or more episodes: verdict details, the last N model turns
(the hosted policy's reasoning arrives in `raw_action` ahead of the action; the teacher's in
`policy_note`), and the last screenshots to open.

    python scripts/inspect_episode.py runs/<run>/episodes/<episode> [...] [--turns 10] [--failed-only RUN]

`--failed-only RUN` walks every selected attempt of a run and prints only those with reward < 1.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forkloop.trajectories import iter_episode_dirs, load_episode


def _turn_text(step: dict) -> str:
    note = (step.get("policy_note") or "").strip()
    raw = (step.get("raw_action") or "").strip()
    text = note or raw
    return " ".join(text.split())


def show(ep_dir: Path, turns: int) -> None:
    e = load_episode(ep_dir)
    m, v, steps = e["manifest"], e["verdict"] or {}, e["steps"]
    print("=" * 100)
    print(f"{ep_dir}  attempt={m.get('attempt', 1)} selected={m.get('selected', '-')}")
    print(f"instruction: {m.get('instruction')}")
    print(f"reward={v.get('reward')} reason={v.get('reason_code')} end={v.get('end_reason')} steps={len(steps)} "
          f"wall={v.get('wall_seconds')}s invalid={v.get('n_invalid')} milestones={v.get('milestones')}")
    for cid in v.get("failed", []):
        print(f"  FAILED {cid}: {json.dumps(v.get('details', {}).get(cid), ensure_ascii=False)[:400]}")
    for cid, d in (v.get("details") or {}).items():
        if isinstance(d, dict) and d.get("passed") is True and cid in ("no_collateral", "ui_path", "no_forbidden"):
            continue
        if cid not in v.get("failed", []):
            print(f"  ok     {cid}: {json.dumps(d, ensure_ascii=False)[:200]}")
    diag = ep_dir / "diagnostics"
    if diag.exists():
        chrome = (diag / "chrome.log").read_text(errors="replace") if (diag / "chrome.log").exists() else ""
        dmesg = (diag / "dmesg.log").read_text(errors="replace") if (diag / "dmesg.log").exists() else ""
        print(f"  diagnostics: netsvc_crashes={chrome.count('Network service crashed')} "
              f"crashpad={chrome.count('scaling_cur_freq')} rcu_stalls={dmesg.count('self-detected stall')}")
    print(f"--- last {turns} turns ---")
    for s in steps[-turns:]:
        act = (s.get("action") or {}).get("type") or "INVALID"
        print(f"[{s['i']:3d}] {act:8s} {_turn_text(s)[:700]}")
    shots = sorted((ep_dir / "shots").glob("*_after.png"))
    print("--- last screenshots ---")
    for p in shots[-3:]:
        print(" ", p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes", nargs="*")
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--failed-only", default=None, metavar="RUN")
    ap.add_argument("--all-attempts", action="store_true")
    a = ap.parse_args()
    dirs = [Path(p) for p in a.episodes]
    if a.failed_only:
        for d in iter_episode_dirs(a.failed_only, include_superseded=a.all_attempts):
            v = load_episode(d)["verdict"] or {}
            if float(v.get("reward", 0) or 0) < 1.0:
                dirs.append(d)
    for d in dirs:
        show(d, a.turns)


if __name__ == "__main__":
    main()
