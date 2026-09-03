"""Replay one recorded episode on a fresh fork and dump the OpenEMR ``log`` rows written after
the seeding watermark, to see what the calendar (or any) save actually audited.

    python scripts/audit_probe.py runs/<run>/episodes/<episode> [--like postcalendar]

Prints every new log row (id, event, patient_id, user, comments) that matches ``--like``
(all rows with ``--like ''``), then the oracle verdict of the replay. One fork, no model calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forkloop.actions import Action  # noqa: E402
from forkloop.backends.solari import SolariBackend  # noqa: E402
from forkloop.env import Env  # noqa: E402
from forkloop.pool import WorkerPool  # noqa: E402
from forkloop.trajectories import Recorder  # noqa: E402
from forkloop.world import load_world  # noqa: E402


async def main(a: argparse.Namespace) -> int:
    world = load_world("claims-ops-v1")
    backend = SolariBackend(kind="desktop")
    ep_dir = Path(a.episode)
    manifest = json.loads((ep_dir / "manifest.json").read_text())
    seed, family = int(manifest["seed"]), manifest["family"]
    # Hosted policies put their reasoning in raw_action ahead of the action; replay the parsed dicts.
    raws = [s["action"] for s in (json.loads(l) for l in (ep_dir / "steps.jsonl").read_text().splitlines() if l.strip())
            if s.get("valid", True) and s.get("action") and s["action"].get("type") != "done"]
    pool = WorkerPool(backend, world, size=1, mode="fork", run_id="audit-probe")
    rec = Recorder("runs/audit_probe", run_id=f"{ep_dir.name}-{int(time.time())}",
                   meta={"policy": "replay", "replay": str(ep_dir)})
    env = Env(world, backend, family=family, pool=pool, recorder=rec, settle_s=0.8)
    try:
        obs, info = await env.reset(seed)
        env.ep.task.budget = {**dict(env.ep.task.budget), "max_steps": len(raws) + 5, "max_seconds": 3600}
        wm = env.ep.baseline.watermarks.get("openemr.log", 0)
        print(f"reset {info['reset']['total_seconds']:.1f}s; openemr.log watermark {wm}; replaying {len(raws)} actions", flush=True)
        for i, raw in enumerate([] if a.no_replay else raws):
            act = Action.parse(raw, width=obs.width, height=obs.height)
            obs, _, term, trunc, info2 = await env.step(act, meta={"raw_action": act.to_compact()})
            if info2.get("error"):
                print(f"  step {i}: {info2['error']}", flush=True)
            if term or trunc:
                break
        db = env.ep.dbs["openemr"]
        rows = await db.query("SELECT id, event, patient_id, user, comments FROM log WHERE id > ? ORDER BY id", [wm])
        hits = [r for r in rows if not a.like or a.like.lower() in str(r.get("comments", "")).lower()]
        print(f"\n{len(rows)} log rows after watermark; {len(hits)} matching {a.like!r}:")
        for r in hits[-a.max_rows:]:
            print(json.dumps({k: (str(v)[:500] if k == "comments" else v) for k, v in r.items()}, ensure_ascii=False))
        ev = await db.query("SELECT event, COUNT(*) AS n FROM log WHERE id > ? GROUP BY event ORDER BY n DESC", [wm])
        print("events:", ", ".join(f"{r['event']}={r['n']}" for r in ev))
        cal = await db.query("SELECT pc_eid, pc_pid, pc_eventDate, pc_startTime, pc_aid FROM openemr_postcalendar_events WHERE pc_eid >= 500000")
        print("calendar rows:", json.dumps(cal, default=str))
        for q in a.extra_sql or []:
            try:
                res = await db.query(q)
                print(f"\n{q}\n  -> {json.dumps(res[:a.max_rows], default=str)[:3000]}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"\n{q}\n  -> ERROR {type(e).__name__}: {e}", flush=True)
        v = await env.verify()
        print("\nverdict:", v.reward, v.reason_code, json.dumps(v.details.get("ui_path")), flush=True)
        (rec.dir / "audit_probe.json").write_text(json.dumps({"rows": rows, "verdict": v.to_dict()}, indent=2, default=str))
    finally:
        await env.close()
        await pool.close()
        await backend.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--like", default="postcalendar")
    ap.add_argument("--max-rows", type=int, default=30)
    ap.add_argument("--extra-sql", action="append", help="extra read-only OpenEMR query to run after the replay (repeatable)")
    ap.add_argument("--no-replay", action="store_true", help="skip the action replay; just reset and run the queries")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
