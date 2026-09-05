"""The milestone staircase of one or more runs: the percentage of episodes that reached each
UI rung (docs/student-2026-09-06.md, step 2).

The rungs come from ``verdict.details["ui_milestones"]`` (``ClaimsOpsWorld.ui_milestones``:
OpenEMR login, patient chart, document view, portal claim page, appeal form, appeal submitted),
computed from the audit trails after each episode. Two more rungs are read from the
trajectory itself: ``login_page`` (the agent typed the task's username, i.e. it reached a login
form) and ``auth_typed`` (it typed the task's authorization number anywhere). Runs recorded
before the rungs existed print "needs a re-run".

    PYTHONPATH=. python scripts/milestone_staircase.py runs/<run-id> [runs/<other-run> ...] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forkloop.trajectories import iter_episode_dirs, load_episode

DB_RUNGS: tuple[str, ...] = ("openemr_login", "openemr_chart", "openemr_document",
                             "portal_claim", "portal_appeal_form", "appeal_submitted")
TRAJ_RUNGS: tuple[str, ...] = ("login_page", "auth_typed")
RUNGS: tuple[str, ...] = ("login_page", "openemr_login", "openemr_chart", "openemr_document", "auth_typed",
                          "portal_claim", "portal_appeal_form", "appeal_submitted")


def _typed_texts(steps: list[dict]) -> list[str]:
    out = []
    for s in steps:
        a = s.get("action")
        if isinstance(a, dict) and a.get("type") == "type" and isinstance(a.get("text"), str):
            out.append(a["text"])
    return out


def episode_rungs(ep: dict) -> dict[str, bool | None]:
    """Rung → reached (None when the run has no ``ui_milestones``)."""
    v = ep.get("verdict") or {}
    m = ep.get("manifest") or {}
    steps = ep.get("steps") or []
    ms = ((v.get("details") or {}).get("ui_milestones") or {}).get("rungs")
    out: dict[str, bool | None] = {}
    for r in DB_RUNGS:
        out[r] = (bool(ms.get(r)) if isinstance(ms, dict) else None)
    typed = [t.strip().lower() for t in _typed_texts(steps)]
    expected = m.get("expected") or {}
    auth = str(expected.get("auth_number") or "").strip().lower()
    out["auth_typed"] = bool(auth) and any(auth in t for t in typed)
    # the login form: the task's OpenEMR user typed on its own (the instruction says "admin / pass")
    out["login_page"] = any(t == "admin" for t in typed)
    if v.get("reward", 0) and float(v.get("reward") or 0) >= 1.0:
        out["appeal_submitted"] = True
    return out


def staircase(run_dir: str | Path, *, include_superseded: bool = False) -> dict:
    dirs = iter_episode_dirs(run_dir, include_superseded=include_superseded)
    rows = []
    for d in dirs:
        ep = load_episode(d)
        if ep.get("verdict") is None:
            continue
        r = episode_rungs(ep)
        rows.append({"episode": d.name, "seed": (ep["manifest"] or {}).get("seed"), "rungs": r,
                     "reward": (ep["verdict"] or {}).get("reward"), "reason": (ep["verdict"] or {}).get("reason_code")})
    n = len(rows)
    has_db = any(all(row["rungs"][r] is not None for r in DB_RUNGS) for row in rows)
    counts = {r: sum(1 for row in rows if row["rungs"].get(r)) for r in RUNGS}
    return {"run": str(run_dir), "n": n, "has_ui_milestones": has_db, "counts": counts,
            "percent": {r: (100.0 * counts[r] / n if n else 0.0) for r in RUNGS}, "episodes": rows}


def format_table(results: list[dict]) -> str:
    lines = ["| rung | " + " | ".join(Path(r["run"]).name + f" (n={r['n']})" for r in results) + " |",
             "| --- | " + " | ".join("---" for _ in results) + " |"]
    for rung in RUNGS:
        cells = []
        for r in results:
            if rung in DB_RUNGS and not r["has_ui_milestones"]:
                cells.append("needs a re-run")
            else:
                cells.append(f"{r['counts'][rung]}/{r['n']} = {r['percent'][rung]:.0f} %")
        lines.append(f"| {rung} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="+")
    p.add_argument("--all-attempts", action="store_true")
    p.add_argument("--json", default=None)
    p.add_argument("--episodes", action="store_true", help="also print one line per episode")
    a = p.parse_args()
    results = [staircase(r, include_superseded=a.all_attempts) for r in a.runs]
    print(format_table(results))
    for r in results:
        if not r["has_ui_milestones"]:
            print(f"\n{r['run']}: no ui_milestones in its verdicts (recorded before 2026-09-05): the database rungs "
                  "need a re-run; login_page and auth_typed come from the trajectory and are shown.")
        if a.episodes:
            print(f"\n{r['run']}")
            for row in r["episodes"]:
                reached = [k for k in RUNGS if row["rungs"].get(k)]
                print(f"  seed {row['seed']}: reward={row['reward']} reason={row['reason']} reached={reached}")
    if a.json:
        Path(a.json).write_text(json.dumps([{k: v for k, v in r.items() if k != "episodes"} for r in results], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
