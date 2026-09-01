"""Headless validation of the claims-ops-v1 controller loop on a real Solari sandbox.

No screen is involved: the "agent" is curl running inside the VM against the
portal's HTTP routes (exactly what Chrome would submit), so this proves the
real reset → seed → health → baseline → UI write → oracle path, and times
fork-mode resets against the golden snapshot.

    FORKLOOP_SOLARI_KIND=sandbox FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1=snap_... \
      python scripts/headless_check.py --trials 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import time

from forkloop.actions import Action
from forkloop.backends.solari import SolariBackend
from forkloop.env import Env
from forkloop.pool import WorkerPool
from forkloop.reset import ResetController
from forkloop.trajectories import Recorder
from forkloop.world import load_world


async def curl(machine, args: str, *, cookies: str = "/tmp/fl-cookies") -> str:
    cmd = f"curl -sS -m 20 -c {cookies} -b {cookies} {args}"
    r = await machine.exec("sh", ["-c", cmd], timeout_ms=30_000)
    if r.exit_code != 0:
        raise RuntimeError(f"curl failed: {r.stderr[-300:]}")
    return r.stdout


async def portal_login(machine) -> None:
    await curl(machine, "-o /dev/null -w '%{http_code}' -d username=agent -d password=agent http://localhost:8080/login")


async def main(args: argparse.Namespace) -> int:
    world = load_world("claims-ops-v1")
    backend = SolariBackend(kind="sandbox")
    pool = WorkerPool(backend, world, size=1, mode="fork", run_id="headless-check")
    rec = Recorder(args.runs, run_id="headless-check", meta={"backend": "solari-sandbox", "policy": "curl-ui-path"})
    env = Env(world, backend, family="resolve_denial", pool=pool, recorder=rec, settle_s=0,
              reset_controller=ResetController(world, skip_screen=True))
    results = []
    try:
        for i in range(args.trials):
            seed = args.seed + i
            t0 = time.monotonic()
            obs, info = await env.reset(seed)
            task = env.ep.task
            m = env.ep.machine
            rep = info["reset"]
            print(f"[{i}] reset ok={rep['ok']} total={rep['total_seconds']:.1f}s stages=" +
                  ", ".join(f"{s['name']}={s['seconds']:.1f}" for s in rep["stages"]), flush=True)
            # UI path from inside the VM
            await portal_login(m)
            listing = await curl(m, "'http://localhost:8080/claims?status=DENIED'")
            assert task.expected["claim_number"] in listing, "seeded claim not visible in the portal"
            mode = args.mode if i == 0 else "correct"
            auth = task.expected["auth_number"] if mode != "wrong" else task.expected["decoy_numbers"][0]
            form = f"--data-urlencode reason_code=PRECERT_OBTAINED --data-urlencode authorization_number={shlex.quote(auth)} " \
                   f"--data-urlencode 'narrative=Prior authorization was obtained before the service date.'"
            n = 2 if mode == "duplicate" else 1
            for _ in range(n):
                await curl(m, f"-o /dev/null -w '%{{http_code}}' {form} http://localhost:8080/claims/{task.expected['claim_number']}/appeal")
            # OpenEMR: prove the mysql path works too (read-only)
            n_pat = await env.ep.dbs["openemr"].scalar("SELECT COUNT(*) AS n FROM patient_data WHERE pid = ?", [task.expected["patient_pid"]])
            obs, reward, term, trunc, info = await env.step(Action.done())
            v = await env.verify()
            print(f"[{i}] mode={mode} openemr_patient_rows={n_pat} reward={v.reward} reason={v.reason_code} failed={v.failed} "
                  f"episode {time.monotonic() - t0:.1f}s", flush=True)
            results.append({"seed": seed, "mode": mode, "reward": v.reward, "reason": v.reason_code, "reset": rep})
    finally:
        await env.close()
        await pool.close()
        await backend.close()
    out = {"results": results, "run_dir": str(rec.dir)}
    print(json.dumps(out, indent=1, default=str)[:3000])
    (rec.dir / "headless_summary.json").write_text(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--mode", choices=["correct", "wrong", "duplicate"], default="wrong",
                    help="what the first trial does (later trials are correct)")
    ap.add_argument("--runs", default="runs")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
