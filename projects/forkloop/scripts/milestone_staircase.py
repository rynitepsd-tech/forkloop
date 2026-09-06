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
from worlds.claims_ops_v1.world import document_view_path

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
    ui = (v.get("details") or {}).get("ui_milestones") or {}
    ms = ui.get("rungs")
    out: dict[str, bool | None] = {}
    for r in DB_RUNGS:
        out[r] = (bool(ms.get(r)) if isinstance(ms, dict) else None)
    if isinstance(ms, dict) and out["openemr_document"]:
        # verdicts written before 2026-09-05 23:30 matched any path containing "document" (the
        # /Documentation/ help pages and the patient-picture fetch included); re-derive from the
        # recorded path samples with the strict rule
        samples = (ui.get("evidence") or {}).get("openemr_document_paths") or []
        out["openemr_document"] = any(document_view_path(p) for p in samples)
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


def render_png(results: list[dict], path: str | Path, labels: list[str] | None = None, title: str | None = None) -> Path:
    """Side-by-side grouped bars: one group per rung, one bar per run (percent of episodes that reached it)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = labels or [Path(r["run"]).name for r in results]
    n_runs = len(results)
    width = 0.8 / max(1, n_runs)
    xs = list(range(len(RUNGS)))
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for i, (r, lab) in enumerate(zip(results, labels)):
        vals = [r["percent"][k] for k in RUNGS]
        pos = [x - 0.4 + width * (i + 0.5) for x in xs]
        bars = ax.bar(pos, vals, width, label=f"{lab} (n={r['n']})")
        for b, k in zip(bars, RUNGS):
            c = r["counts"][k]
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1, str(c), ha="center", va="bottom", fontsize=7)
    ax.set_xticks(xs)
    ax.set_xticklabels([k.replace("_", "\n") for k in RUNGS], fontsize=8)
    ax.set_ylabel("% of episodes reaching the rung")
    ax.set_ylim(0, 108)
    ax.set_title(title or "Milestone staircase, family 3 (resolve_denial), held-out seeds 200-229")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="+")
    p.add_argument("--all-attempts", action="store_true")
    p.add_argument("--json", default=None)
    p.add_argument("--png", default=None, help="write a side-by-side bar chart of the runs (needs matplotlib)")
    p.add_argument("--labels", default=None, help="comma-separated legend labels for --png (default: run ids)")
    p.add_argument("--title", default=None, help="chart title for --png")
    p.add_argument("--episodes", action="store_true", help="also print one line per episode")
    a = p.parse_args()
    results = [staircase(r, include_superseded=a.all_attempts) for r in a.runs]
    print(format_table(results))
    if a.png:
        labels = [x.strip() for x in a.labels.split(",")] if a.labels else None
        print("wrote", render_png(results, a.png, labels=labels, title=a.title))
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
