"""Annotate exp1 repairs of the 20:18 UTC batch voided only by dropped OpenAI connections (protocol note
2026-09-30 00:25): before `student.HOSTED_TRANSPORT_RETRIES`, a hosted send that failed in transport
(ReadError, RemoteProtocolError, timeouts) ended the whole branch as infrastructure. A void repair that
started at or after 20:18 UTC is annotated ``void_reason=provider_transport`` (not a replacement try)
when every unscored branch either failed on such a transport error or was interrupted/running when the
batch was stopped to deploy the fix. Voids from other causes (``ctrl+-``, failed restores) still count.

    python scripts/exp1/mark_transport_voids.py --store ~/programs/exp1/forkloop.sqlite [--apply]
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import time
from collections import Counter
from pathlib import Path

from forkloop.correction import Store
from forkloop.correction.repair import PROVIDER_TRANSPORT, UNSCORED_BRANCH, repair_is_clean

START = calendar.timegm(time.strptime("2026-09-29 20:18", "%Y-%m-%d %H:%M"))
TRANSPORT = re.compile(r"policy provider: request failed: (ReadError|RemoteProtocolError|ConnectError|WriteError|"
                       r"ReadTimeout|ConnectTimeout|WriteTimeout|PoolTimeout|LocalProtocolError)")


def cause(b: dict) -> str:
    if b["status"] in ("interrupted", "running"):
        return "interrupted"
    f = Path(b["run_dir"]) / "steps.jsonl"
    if b["status"] == "infra_error" and f.exists():
        for line in f.read_text().splitlines():
            if "backend failed:" in line:
                return "transport" if TRANSPORT.search(line) else "other"
    return "other"


def main(a: argparse.Namespace) -> None:
    store = Store(a.store)
    c: Counter = Counter()
    for exp in ("exp1-round1", "exp1-restart"):
        for r in store.repairs(experiment_id=exp):
            if r["started_at"] < START or repair_is_clean(store, r) or r["result"].get("void_reason"):
                continue
            causes = [cause(b) for b in store.branches(repair_id=r["repair_id"]) if b["status"] in UNSCORED_BRANCH]
            if r["status"] not in ("verified", "unrepaired"):
                causes.append("interrupted")
            if causes and all(x in ("transport", "interrupted") for x in causes):
                c[(exp, "provider_transport")] += 1
                if a.apply:
                    store.annotate_repair(r["repair_id"], void_reason=PROVIDER_TRANSPORT,
                                          void_evidence={"unscored_branch_causes": dict(Counter(causes))})
            else:
                c[(exp, "void, other cause (counts as a try)")] += 1
    print(json.dumps({f"{e}: {k}": v for (e, k), v in sorted(c.items())}, indent=2), "\napplied" if a.apply else "\ndry run")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--apply", action="store_true")
    main(ap.parse_args())
