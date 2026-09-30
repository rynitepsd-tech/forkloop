"""Annotate main's A0/S_W final-test attempts voided by the operator (protocol note 2026-09-30 12:30).

Two early runs of main's A0/S_W shard were stopped by the operator, outcomes unread: 2026-09-29
09:00–09:08 UTC (server load) and 10:24–10:31 UTC (Docker world cap). In those windows an attempt that
ended ``interrupted`` is annotated ``void_reason=operator_stop``; one whose reset failed because the
operator had oversubscribed the host's world cap (``ConcurrencyError``) is ``operator_overload``. Neither
is a replacement try (``runner.NOT_A_TRY``), as operator stops already were not for repairs. Scored
attempts and any other unscored attempt are left alone.

    python scripts/exp1/mark_operator_stops.py --store ~/programs/exp1/forkloop.sqlite [--apply]
"""
from __future__ import annotations

import argparse
import calendar
import json
import time
from collections import Counter

from forkloop.correction import Store

WINDOWS = [("2026-09-29 09:00:00", "2026-09-29 09:08:30"), ("2026-09-29 10:24:00", "2026-09-29 10:31:30")]


def _t(s: str) -> float:
    return calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def main(a: argparse.Namespace) -> None:
    store = Store(a.store)
    wins = [(_t(x), _t(y)) for x, y in WINDOWS]
    c: Counter = Counter()
    for att in store.attempts(experiment_id="exp1-eval"):
        role = att["info"].get("role", "")
        if role not in ("eval:A0", "eval:sw") or not any(x <= att["started_at"] <= y for x, y in wins):
            continue
        err = str(att["info"].get("error") or "")
        if att["status"] in ("interrupted", "running"):
            reason = "operator_stop"
        elif att["status"] == "infra_error" and "ConcurrencyError" in err:
            reason = "operator_overload"
        else:
            c[(role, att["status"], "unchanged")] += 1
            continue
        c[(role, att["status"], reason)] += 1
        if a.apply:
            store.annotate_attempt(att["attempt_id"], void_reason=reason,
                                   void_evidence={"window_utc": WINDOWS, "error": err[:200]})
    print(json.dumps({" / ".join(k): v for k, v in sorted(c.items())}, indent=2), "\napplied" if a.apply else "\ndry run")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--apply", action="store_true")
    main(ap.parse_args())
