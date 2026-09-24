"""Restore and total reset seconds from every live Solari reset.json under runs/ (local evidence).

    python scripts/reset_times.py [--since 2026-09-15]
Only successful resets write reset.json; failed ones are counted from the comparison cells instead.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(since: dt.datetime) -> None:
    restore, total = defaultdict(list), defaultdict(list)
    for path in ROOT.glob("runs/**/reset.json"):
        if dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).replace(tzinfo=None) < since:
            continue
        run = Path(str(path).split("/episodes/")[0])
        try:
            backend = json.loads((run / "run.json").read_text()).get("backend")
        except (OSError, ValueError):
            continue
        if backend != "solari":
            continue
        report = json.loads(path.read_text())
        stages = {s["name"]: s["seconds"] for s in report["stages"]}
        restore[report["method"]].append(stages["restore"])
        total[report["method"]].append(report["total_seconds"])
    for method, values in sorted(restore.items()):
        q, t = statistics.quantiles(values, n=10), statistics.quantiles(total[method], n=10)
        print(f"{method}: n={len(values)} restore p10 {q[0]:.1f} p50 {statistics.median(values):.1f} p90 {q[-1]:.1f} "
              f"max {max(values):.1f} s; total p50 {statistics.median(total[method]):.1f} p90 {t[-1]:.1f} "
              f"max {max(total[method]):.1f} s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-09-15")
    main(dt.datetime.fromisoformat(parser.parse_args().since))
