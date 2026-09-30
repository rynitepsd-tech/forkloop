"""Annotate exp1 repairs voided by the OpenAI credit outage (protocol notes 2026-09-29 20:10 / 20:20).

The account's credit ran out at ≈ 14:05 UTC and was restored at ≈ 20:10 UTC; in between the provider
answered every teacher request with HTTP 429 ``insufficient_quota``. A repair is annotated
``void_reason=provider_outage`` (so it is not a replacement try under ``repair.counted_repair``) when it
is not clean AND (it started inside the outage window OR one of its branches recorded a 429 refusal).
Clean repairs are never touched. Statuses are unchanged; each annotation is an event in the store.

    python scripts/exp1/mark_provider_outage.py --store ~/programs/exp1/forkloop.sqlite [--apply]
"""
from __future__ import annotations

import argparse
import calendar
import json
import time
from collections import Counter
from pathlib import Path

from forkloop.correction import Store
from forkloop.correction.repair import PROVIDER_OUTAGE, repair_is_clean

START = calendar.timegm(time.strptime("2026-09-29 14:00", "%Y-%m-%d %H:%M"))
END = calendar.timegm(time.strptime("2026-09-29 20:10", "%Y-%m-%d %H:%M"))
REFUSAL = "429 Too Many Requests"


def refused_branches(store: Store, repair_id: str) -> int:
    n = 0
    for b in store.branches(repair_id=repair_id):
        f = Path(b["run_dir"]) / "steps.jsonl"
        if f.exists() and REFUSAL in f.read_text():
            n += 1
    return n


def main(a: argparse.Namespace) -> None:
    store = Store(a.store)
    c: Counter = Counter()
    for exp in ("exp1-round1", "exp1-restart"):
        for r in store.repairs(experiment_id=exp):
            if repair_is_clean(store, r):
                c[(exp, "clean")] += 1
                continue
            in_window = START <= r["started_at"] <= END
            refused = refused_branches(store, r["repair_id"])
            if not (in_window or refused):
                c[(exp, "void, other cause (counts as a try)")] += 1
                continue
            c[(exp, "provider_outage")] += 1
            if a.apply:
                store.annotate_repair(r["repair_id"], void_reason=PROVIDER_OUTAGE,
                                      void_evidence={"started_in_outage_window": in_window, "branches_refused_429": refused,
                                                     "window_utc": ["2026-09-29T14:00Z", "2026-09-29T20:10Z"],
                                                     "diagnostic": "HTTP 429 insufficient_quota / credit_balance_exhausted"})
    print(json.dumps({f"{e}: {k}": v for (e, k), v in sorted(c.items())}, indent=2), "\napplied" if a.apply else "\ndry run")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--apply", action="store_true")
    main(ap.parse_args())
