"""Failure triage for one or more episodes. Since 2026-09-07 this is ``forkloop report``; the
script keeps its historical argv so older notes still work.

    python scripts/inspect_episode.py runs/<run>/episodes/<episode> [...] [--turns 10] [--failed-only RUN]

`--failed-only RUN` walks every selected attempt of a run and prints only those with reward < 1.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from forkloop.report import episode_report, report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes", nargs="*")
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--failed-only", default=None, metavar="RUN")
    ap.add_argument("--all-attempts", action="store_true")
    a = ap.parse_args()
    for p in a.episodes:
        print(episode_report(Path(p), turns=a.turns))
        print()
    if a.failed_only:
        print(report(a.failed_only, turns=a.turns, failed=True, all_attempts=a.all_attempts))


if __name__ == "__main__":
    main()
