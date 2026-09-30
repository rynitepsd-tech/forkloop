"""Checkpoint restore and branch-independence check (any backend).

For one recorded checkpoint, open two branches at once from it, then:

1. **restore fidelity** — each branch's world digest (per-table row hashes, screen thumbnail)
   against the digest recorded at the checkpoint;
2. **policy state** — the checkpoint's policy state blob decodes and its declared memory matches
   the step records;
3. **independence under mutation** — mutate branch A through the controller channel (a marker
   row in the portal messages table and a marker file), re-digest both branches: A must change,
   B must not; then show B still has no marker file;
4. **files** — a seeded document file is byte-identical on both branches.

Writes a JSON report. Usage:
  python scripts/checkpoint_restore_check.py --config configs/loop-solari-flagship.yaml --ckpt ck-… --out report.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from forkloop.correction.checkpoint import Blobs
from forkloop.correction.digest import WorldDigest, compare, world_digest
from forkloop.correction.project import load_project
from forkloop.correction.repair import task_for
from forkloop.correction.restore import open_branch


async def main(a: argparse.Namespace) -> dict:
    proj = load_project(a.config, require_env=False)
    store, world = proj.store(), proj.world()
    backend = proj.backend(world)
    ckpt = store.checkpoint(a.ckpt)
    attempt = store.attempt(ckpt["attempt_id"])
    task = task_for(store, attempt["task_id"], world)
    report: dict = {"ckpt_id": a.ckpt, "strategy": ckpt["strategy"], "world_ref": ckpt["world_ref"], "step": ckpt["step"],
                    "attempt_id": attempt["attempt_id"], "backend": proj.backend_name, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    blobs = Blobs(store.root / "blobs")
    state = blobs.get_json(ckpt["policy_state_ref"])
    report["policy_state"] = {"fields": sorted(state), "memory": state.get("_memory"),
                              "agent_state": ckpt["agent_state"],
                              "memory_matches_agent_state": state.get("_memory") == ckpt["agent_state"].get("memory")}
    envs = []
    try:
        opened = await asyncio.gather(*(open_branch(world=world, backend=backend, task=task, ckpt=ckpt, attempt=attempt,
                                                     recorder=None, run_id=f"restorecheck-{a.ckpt}-{i}",
                                                     max_screen_distance=proj.repair.max_screen_distance,
                                                     history_k=proj.history_k) for i in range(2)),
                                      return_exceptions=True)
        for i, o in enumerate(opened):
            if isinstance(o, BaseException):
                report[f"branch_{i}"] = {"error": f"{type(o).__name__}: {o}"}
            else:
                env, rep = o
                envs.append(env)
                report[f"branch_{i}"] = rep.to_dict() | {"machine": env.ep.machine.id if env.ep else None}
        if len(envs) == 2 and all(e.ep is not None for e in envs):
            A, B = envs
            ref = WorldDigest.from_dict(ckpt["digest"])
            # files: a seeded document is byte-identical on both
            files = [f for f in (task.seeding.files or [])][:1]
            if files:
                path = files[0].path if hasattr(files[0], "path") else files[0]["path"]
                ha = hashlib.sha256(await A.ep.machine.read_file(path)).hexdigest()
                hb = hashlib.sha256(await B.ep.machine.read_file(path)).hexdigest()
                report["seeded_file"] = {"path": path, "equal": ha == hb, "sha256_a": ha}
            # mutate A only
            marker = f"forkloop-independence-{int(time.time())}"
            portal = A.ep.dbs["portal"]
            await portal.execute_script(f"INSERT INTO messages (id, subject, body, received_at, is_read) "
                                        f"VALUES (899999, '{marker}', 'x', '2026-09-29 00:00:00', 0);")
            await A.ep.machine.write_file("/tmp/forkloop-marker.txt", marker)
            da = await world_digest(world, A.ep.dbs, await A.ep.machine.screenshot())
            db = await world_digest(world, B.ep.dbs, await B.ep.machine.screenshot())
            ca, cb = compare(ref, da), compare(ref, db)
            b_marker = await B.ep.machine.exec("test", ["-e", "/tmp/forkloop-marker.txt"])
            report["independence"] = {"a_changed_tables": ca.differing_tables, "b_changed_tables": cb.differing_tables,
                                      "b_has_marker_file": b_marker.exit_code == 0,
                                      "ok": "portal.messages" in ca.differing_tables and not cb.differing_tables
                                            and b_marker.exit_code != 0}
    finally:
        for e in envs:
            try:
                await e.close()
            except Exception as ex:  # noqa: BLE001
                report.setdefault("cleanup_errors", []).append(str(ex))
        await backend.close()
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rep = asyncio.run(main(args))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))
