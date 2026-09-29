"""Qualify claims-ops-v1 on the Docker backend and measure it (docs/docker-world.md).

Every subcommand drives the real library (WorkerPool, ResetController, Env, Oracle) against
DockerBackend and writes raw JSON/JSONL + PNGs under --out. Nothing here is a model or a policy under
evaluation: the scripted GUI solve is a test fixture that reads the controller-side expected values.

  python worlds/claims_ops_v1/docker/qualify.py resets  --out DIR [--families F ...] [--seeds 1,2,3]
  python worlds/claims_ops_v1/docker/qualify.py gui     --out DIR [--seed 1]
  python worlds/claims_ops_v1/docker/qualify.py latency --out DIR [--n 100]
  python worlds/claims_ops_v1/docker/qualify.py restore --out DIR [--n 12]
  python worlds/claims_ops_v1/docker/qualify.py load    --out DIR --worlds 4 8 16 [--seconds 60]
  python worlds/claims_ops_v1/docker/qualify.py checkpoint --out DIR

Needs Docker and the golden image (FORKLOOP_DOCKER_IMAGE, default forkloop/claims-ops-v1:1).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import importlib.util
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]  # projects/forkloop
sys.path.insert(0, str(ROOT))

from forkloop.backends.docker import DockerBackend  # noqa: E402
from forkloop.oracle import Oracle, OracleSpec  # noqa: E402
from forkloop.pool import WorkerPool  # noqa: E402
from forkloop.reset import ResetController, ResetError  # noqa: E402
from forkloop.world import load_world  # noqa: E402

FAMILIES = ["resolve_denial", "update_insurance_reconcile", "reschedule_constrained"]


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    k = (len(ys) - 1) * p
    f, c = int(k), min(int(k) + 1, len(ys) - 1)
    return round(ys[f] + (ys[c] - ys[f]) * (k - f), 4)


def summary(xs: list[float]) -> dict[str, Any]:
    return {"n": len(xs), "p50": pct(xs, 0.5), "p90": pct(xs, 0.9), "p99": pct(xs, 0.99),
            "mean": round(statistics.fmean(xs), 4) if xs else None, "max": round(max(xs), 4) if xs else None}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


async def title(machine: Any) -> str:
    r = await machine.exec("runuser", ["-u", "desktop", "--", "env", "DISPLAY=:0", "xdotool", "getactivewindow",
                                       "getwindowname"])
    return r.stdout.strip()


def host_info() -> dict[str, Any]:
    out: dict[str, Any] = {"loadavg": Path("/proc/loadavg").read_text().split()[:3] if Path("/proc/loadavg").exists() else None,
                           "nproc": os.cpu_count()}
    try:
        mem = {ln.split(":")[0]: int(ln.split()[1]) for ln in Path("/proc/meminfo").read_text().splitlines()[:3]}
        out["mem_available_gb"] = round(mem.get("MemAvailable", 0) / 1024 / 1024, 1)
    except Exception:  # noqa: BLE001
        pass
    return out


# --------------------------------------------------------------------------- resets
async def cmd_resets(args: argparse.Namespace) -> int:
    world = load_world("claims-ops-v1")
    backend = DockerBackend.for_world(world)
    out = Path(args.out)
    (out / "shots").mkdir(parents=True, exist_ok=True)
    pool = WorkerPool(backend, world, size=1, mode=args.mode, run_id=f"qualify-resets-{os.getpid()}")
    ctl = ResetController(world)
    seeds = [int(s) for s in args.seeds.split(",")]
    rows = []
    try:
        for fam in args.families:
            for seed in seeds:
                task = world.generate(fam, seed, args.split)
                worker = await pool.acquire()
                row: dict[str, Any] = {"family": fam, "seed": seed, "task_id": task.task_id, "at": now()}
                try:
                    outcome = await ctl.reset(worker, task)
                    row["report"] = outcome.report.to_dict()
                    row["boot"] = getattr(outcome.machine, "last_boot", {})
                    row["window_title"] = await title(outcome.machine)
                    shot = out / "shots" / f"{fam}-seed{seed}-step0.png"
                    shot.write_bytes(outcome.screenshot)
                    row["screenshot"] = str(shot)
                    row["ok"] = outcome.report.ok
                    await pool.release(worker)
                except ResetError as e:
                    row.update(ok=False, error=str(e), report=e.report.to_dict() if e.report else None)
                    await pool.release(worker, healthy=False)
                rows.append(row)
                st = {s["name"]: s["seconds"] for s in (row.get("report") or {}).get("stages", [])}
                print(f"{fam} seed={seed} ok={row['ok']} total={row.get('report', {}).get('total_seconds')} "
                      f"restore={st.get('restore')} title={row.get('window_title')!r} {row.get('error', '')}", flush=True)
                with (out / "resets.jsonl").open("a") as f:
                    f.write(json.dumps(row) + "\n")
    finally:
        await pool.close()
        await backend.close()
    stages: dict[str, list[float]] = {}
    for r in rows:
        for s in (r.get("report") or {}).get("stages", []):
            stages.setdefault(s["name"], []).append(s["seconds"])
    agg = {"at": now(), "mode": args.mode, "n": len(rows), "ok": sum(1 for r in rows if r["ok"]),
           "total": summary([r["report"]["total_seconds"] for r in rows if r.get("report") and r["ok"]]),
           "stages": {k: summary(v) for k, v in stages.items()}, "host": host_info(),
           "boot_failures": backend.boot_failures, "pool_events": pool.events}
    (out / "resets_summary.json").write_text(json.dumps(agg, indent=2))
    print(json.dumps(agg, indent=2))
    return 0 if agg["ok"] == agg["n"] else 1


# --------------------------------------------------------------------------- scripted GUI episode + DB-write controls
def _gui_script(task: Any) -> list[str]:
    spec = importlib.util.spec_from_file_location("gui_episode", ROOT / "scripts" / "gui_episode.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_script(task)


async def cmd_gui(args: argparse.Namespace) -> int:
    from forkloop.actions import Action
    from forkloop.env import Env
    from forkloop.trajectories import Recorder

    world = load_world("claims-ops-v1")
    backend = DockerBackend.for_world(world)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"at": now()}
    pool = WorkerPool(backend, world, size=1, mode="revert", run_id=f"qualify-gui-{os.getpid()}")
    rec = Recorder(str(out / "runs"), run_id="gui-episodes", meta={"backend": "docker", "policy": "scripted-gui-fixture",
                                                                   "world": world.name})
    try:
        # 1. UI solve of resolve_denial through Env.step (recorded): the verdict must be reward 1 with ui_path passing.
        env = Env(world, backend, family="resolve_denial", pool=pool, recorder=rec, settle_s=0.8)
        obs, info = await env.reset(args.seed)
        steps = []
        for raw in _gui_script(env.ep.task):
            a = Action.parse(raw, width=obs.width, height=obs.height)
            obs, reward, term, trunc, info2 = await env.step(a, meta={"raw_action": raw})
            steps.append({"action": raw, "error": info2.get("error")})
            if term or trunc:
                break
        v = await env.verify()
        results["ui_solve"] = {"seed": args.seed, "reward": v.reward, "reason": v.reason_code, "failed": v.failed,
                               "ui_path": v.details.get("ui_path"), "no_collateral": v.details.get("no_collateral"),
                               "steps": steps, "episode_dir": str(env.ep.recorder.dir) if env.ep.recorder else None,
                               "reset": info["reset"]}
        print("ui solve:", v.reward, v.reason_code, v.failed, flush=True)
        await env.close()

        # 2. The same end state written straight into the portal DB: every effect passes, ui_path must not.
        env = Env(world, backend, family="resolve_denial", pool=pool, settle_s=0.8)
        await env.reset(args.seed)
        ex = env.ep.task.expected
        sql = (f"INSERT INTO appeals (id, claim_id, reason_code, authorization_number, narrative, created_at) VALUES "
               f"(990001, {int(ex['claim_id'])}, 'PRECERT_OBTAINED', '{ex['auth_number']}', "
               f"'Prior authorization was obtained before the service date.', '2026-09-28T00:00:00Z');\n"
               f"UPDATE claims SET status = 'APPEAL_SUBMITTED' WHERE id = {int(ex['claim_id'])};")
        try:
            await env.ep.dbs["portal"].execute_script(sql)
            v = await env.verify()
            results["portal_direct_write"] = {"reward": v.reward, "reason": v.reason_code, "failed": v.failed,
                                              "ui_path": v.details.get("ui_path")}
        except Exception as e:  # noqa: BLE001 - the appeals schema may need more columns; record it
            results["portal_direct_write"] = {"error": f"{type(e).__name__}: {e}"}
        print("portal direct write:", results["portal_direct_write"], flush=True)
        await env.close()

        # 3. OpenEMR: log in and open the patient's chart through the GUI (reschedule starts on the
        #    login page), then (a) the UI activity is in OpenEMR's audit log (ui_milestones), and
        #    (b) a direct SQL edit of an OpenEMR row is caught by ui_path_only as DIRECT_DB_WRITE.
        env = Env(world, backend, family="reschedule_constrained", pool=pool, recorder=rec, settle_s=0.8)
        obs, info = await env.reset(args.seed)
        task = env.ep.task
        pid = task.expected["patient_pid"]
        script = ["click(706, 422)", 'type("admin")', "click(684, 476)", 'type("pass")', "click(640, 585)", "wait(5)",
                  "click(640, 90)", 'key("ctrl+a")',
                  f'type("http://localhost/openemr/interface/patient_file/summary/demographics.php?set_pid={pid}")',
                  'key("Return")', "wait(1.5)",
                  'key("Return")', "wait(5)"]   # leaving OpenEMR's main page asks "Leave site?"; Leave has the focus
        for raw in script:
            obs, *_ = await env.step(Action.parse(raw, width=obs.width, height=obs.height), meta={"raw_action": raw})
        (out / "openemr_chart_after_gui_login.png").write_bytes(obs.screenshot)
        ms = await world.ui_milestones(env.ep.dbs, env.ep.baseline, task)
        clean = await Oracle(world.oracle_context(env.ep.dbs, env.ep.baseline)).evaluate(
            OracleSpec(effects=[], invariants=[c for c in task.oracle.invariants if c.kind in ("ui_path_only", "baseline_checksum")]))
        ui_only = OracleSpec(effects=[], invariants=[c for c in task.oracle.invariants if c.kind == "ui_path_only"])
        # (a) a direct write to the patient whose chart the GUI opened: OpenEMR's audit match is "loose"
        #     (world.yaml: any log row for that patient after the watermark), so ui_path cannot tell it apart
        await env.ep.dbs["openemr"].execute_script(
            f"UPDATE patient_data SET phone_home = '555-0100' WHERE pid = {int(pid)};")
        same = await Oracle(world.oracle_context(env.ep.dbs, env.ep.baseline)).evaluate(ui_only)
        # (b) a direct write to a patient the GUI never touched: no audit row can match it
        other = await env.ep.dbs["openemr"].scalar(
            "SELECT pid FROM patient_data WHERE pid BETWEEN 100001 AND 100040 AND pid <> ? ORDER BY pid LIMIT 1", [int(pid)])
        await env.ep.dbs["openemr"].execute_script(
            f"UPDATE patient_data SET phone_home = '555-0199' WHERE pid = {int(other)};")
        dirty = await Oracle(world.oracle_context(env.ep.dbs, env.ep.baseline)).evaluate(ui_only)
        results["openemr"] = {"milestones": ms, "after_gui_only": {"reward": clean.reward, "reason": clean.reason_code,
                                                                   "details": clean.details},
                              "direct_sql_on_opened_patient": {"pid": pid, "reward": same.reward, "reason": same.reason_code,
                                                               "details": same.details},
                              "direct_sql_on_untouched_patient": {"pid": other, "reward": dirty.reward,
                                                                  "reason": dirty.reason_code, "details": dirty.details},
                              "window_title": await title(env.ep.machine)}
        print("openemr milestones:", ms and ms["rungs"], "| gui-only:", clean.reason_code, "| direct sql, opened patient:",
              same.reason_code, "| direct sql, untouched patient:", dirty.reason_code, flush=True)
        await env.close()
    finally:
        await pool.close()
        await backend.close()
    (out / "gui_results.json").write_text(json.dumps(results, indent=2, default=str))
    ok = (results.get("ui_solve", {}).get("reward") == 1.0
          and results.get("portal_direct_write", {}).get("reason") == "DIRECT_DB_WRITE"
          and results.get("openemr", {}).get("after_gui_only", {}).get("reason") == "OK"
          and results.get("openemr", {}).get("direct_sql_on_untouched_patient", {}).get("reason") == "DIRECT_DB_WRITE")
    print("GUI QUALIFICATION", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# --------------------------------------------------------------------------- latency
async def _time(fn, n: int) -> list[float]:
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        await fn()
        xs.append(time.perf_counter() - t0)
    return xs


async def machine_latency(m: Any, n: int) -> dict[str, Any]:
    res: dict[str, list[float]] = {}
    res["screenshot"] = await _time(m.screenshot, n)
    res["move"] = await _time(lambda: m.move(700, 12), n)                # empty stretch of the top panel
    res["click"] = await _time(lambda: m.click(700, 12), n)
    res["press"] = await _time(lambda: m.press(["shift"]), n)
    res["exec_true"] = await _time(lambda: m.exec("true"), n)
    res["read_file"] = await _time(lambda: m.read_file("/etc/hostname"), n)
    await m.click(640, 90)
    res["type_10_chars"] = await _time(lambda: m.type_text("abcdefghij"), max(5, n // 10))
    await m.press(["Escape"])
    await m.press(["Escape"])
    png = await m.screenshot()
    return {"ms": {k: {kk: (round(vv * 1000, 2) if isinstance(vv, float) else vv) for kk, vv in summary(v).items()}
                   for k, v in res.items()}, "png_bytes": len(png)}


async def cmd_latency(args: argparse.Namespace) -> int:
    world = load_world("claims-ops-v1")
    backend = DockerBackend.for_world(world)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"at": now(), "n": args.n, "host": host_info()}
    m = await backend.create(metadata={"run_id": f"qualify-latency-{os.getpid()}"})
    try:
        result["boot"] = m.last_boot
        for level in (1, 6):
            backend.png_level = level
            t = await _time(m.screenshot, 30)
            result[f"screenshot_png_level_{level}_ms"] = {k: (round(v * 1000, 2) if isinstance(v, float) else v)
                                                          for k, v in summary(t).items()}
            result[f"screenshot_png_level_{level}_bytes"] = len(await m.screenshot())
        backend.png_level = 1
        result["single_world"] = await machine_latency(m, args.n)
        # the baseline comparison the design rests on: one `docker exec` per call
        t = []
        for _ in range(20):
            t0 = time.perf_counter()
            await backend.cli.run("exec", m.id, "true")
            t.append(time.perf_counter() - t0)
        result["docker_exec_true_ms"] = {k: (round(v * 1000, 2) if isinstance(v, float) else v) for k, v in summary(t).items()}
        (out / "latency_screen.png").write_bytes(await m.screenshot())
    finally:
        await m.kill()
        await backend.close()
    (out / "latency.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0


# --------------------------------------------------------------------------- container restore (revert/fork)
async def cmd_restore(args: argparse.Namespace) -> int:
    world = load_world("claims-ops-v1")
    backend = DockerBackend.for_world(world)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    m = await backend.create(metadata={"run_id": f"qualify-restore-{os.getpid()}"})
    rows.append({"op": "create", "seconds": time.perf_counter() - t0, "boot": m.last_boot})
    try:
        for i in range(args.n):
            t0 = time.perf_counter()
            await m.revert(backend.image)
            rows.append({"op": "revert", "i": i, "seconds": time.perf_counter() - t0, "boot": m.last_boot,
                         "title": await title(m)})
            print(f"revert {i}: {rows[-1]['seconds']:.2f}s {rows[-1]['title']!r}", flush=True)
        for i in range(args.n):
            t0 = time.perf_counter()
            m2 = await backend.create(metadata={"run_id": f"qualify-restore-{os.getpid()}"})
            t_create = time.perf_counter() - t0
            t1 = time.perf_counter()
            await m2.kill()
            rows.append({"op": "fork", "i": i, "seconds": t_create, "kill_seconds": time.perf_counter() - t1,
                         "boot": m2.last_boot})
            print(f"fork {i}: {t_create:.2f}s", flush=True)
    finally:
        await m.kill()
        await backend.close()
    with (out / "restore.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    agg = {"at": now(), "host": host_info(), "boot_failures": backend.boot_failures}
    for op in ("revert", "fork"):
        xs = [r["seconds"] for r in rows if r["op"] == op]
        agg[op] = summary(xs)
        boots = [r["boot"].get("boot", {}).get("stages_ms", {}) for r in rows if r["op"] == op]
        agg[op + "_in_container_ms"] = {k: summary([b[k] for b in boots if b.get(k) is not None])
                                        for k in (boots[0] if boots else {})}
    (out / "restore_summary.json").write_text(json.dumps(agg, indent=2))
    print(json.dumps(agg, indent=2))
    return 0


# --------------------------------------------------------------------------- concurrency / load
async def cmd_load(args: argparse.Namespace) -> int:
    """For each N: N worker pools (one world each, revert mode) on one host.
    round 1  every world does a full ResetController.reset at once (container creation + seed + health +
             feasibility + baseline + initial screen), tasks cycling through the three families;
    steady   every world runs screenshot / click / key / exec rounds for --seconds (agent-like pacing);
    round 2  every world resets again at once (revert: docker rm + run of the golden image under load)."""
    world = load_world("claims-ops-v1")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for n in args.worlds:
        backend = DockerBackend.for_world(world, concurrency_cap=n + 2)
        pools = [WorkerPool(backend, world, size=1, mode="revert", run_id=f"qualify-load{n}-{os.getpid()}-{i}",
                            reap_orphans_enabled=False) for i in range(n)]
        ctl = ResetController(world)
        res: dict[str, Any] = {"worlds": n, "at": now(), "host_before": host_info()}
        workers: list[Any] = [None] * n

        async def reset_round(tag: str, seed0: int) -> dict[str, Any]:
            async def one(i: int) -> dict[str, Any]:
                fam = FAMILIES[i % len(FAMILIES)]
                task = world.generate(fam, seed0 + i, "train")
                w = workers[i] or await pools[i].acquire()
                workers[i] = w
                t = time.perf_counter()
                try:
                    o = await ctl.reset(w, task)
                    return {"ok": True, "family": fam, "wall": time.perf_counter() - t, "report": o.report.to_dict(),
                            "title": await title(o.machine), "boot": dict(getattr(o.machine, "last_boot", {}))}
                except ResetError as e:
                    return {"ok": False, "family": fam, "wall": time.perf_counter() - t, "error": str(e)[:300],
                            "report": e.report.to_dict() if e.report else None}
            t0 = time.perf_counter()
            rows = await asyncio.gather(*(one(i) for i in range(n)))
            stages: dict[str, list[float]] = {}
            for r in rows:
                for st in (r.get("report") or {}).get("stages", []):
                    stages.setdefault(st["name"], []).append(st["seconds"])
            bad_titles = [r.get("title") for r in rows if r["ok"] and not str(r.get("title", "")).startswith(
                ("Claims - Meridian", "OpenEMR Login"))]
            boot_parts: dict[str, list[float]] = {}
            for r in rows:
                b = r.get("boot") or {}
                for k in ("docker_rm_seconds", "docker_run_seconds", "ready_seconds"):
                    if b.get(k) is not None:
                        boot_parts.setdefault(k, []).append(b[k])
                for k, v in ((b.get("boot") or {}).get("stages_ms") or {}).items():
                    if v is not None:
                        boot_parts.setdefault("in_container_" + k, []).append(v / 1000)
            return {"round": tag, "all_seconds": round(time.perf_counter() - t0, 2), "ok": sum(r["ok"] for r in rows),
                    "restore_breakdown": {k: summary(v) for k, v in boot_parts.items()},
                    "n": n, "reset_total": summary([r["wall"] for r in rows if r["ok"]]),
                    "stages": {k: summary(v) for k, v in stages.items()}, "unexpected_titles": bad_titles,
                    "errors": [r["error"] for r in rows if not r["ok"]][:5]}

        try:
            res["round1_create"] = await reset_round("create", 1000)
            print(f"N={n} round1 {json.dumps({k: res['round1_create'][k] for k in ('all_seconds', 'ok', 'reset_total')})}",
                  flush=True)
            machines = [w.machine for w in workers if w is not None and w.machine is not None]
            lat: dict[str, list[float]] = {"screenshot": [], "click": [], "press": [], "exec": []}
            stop_at = time.perf_counter() + args.seconds
            stats_task = asyncio.create_task(asyncio.sleep(args.seconds / 2))

            async def loop(mm: Any) -> None:
                while time.perf_counter() < stop_at:
                    for name, fn in (("screenshot", mm.screenshot), ("click", lambda: mm.click(700, 12)),
                                     ("press", lambda: mm.press(["shift"])), ("exec", lambda: mm.exec("true"))):
                        t = time.perf_counter()
                        await fn()
                        lat[name].append(time.perf_counter() - t)
                    await asyncio.sleep(args.think_s)

            async def sample_stats() -> None:
                await stats_task
                res["host_during"] = host_info()
                _, stats, _ = await backend.cli.run("stats", "--no-stream", "--format", "{{json .}}",
                                                    *[mm.id for mm in machines], timeout=300)
                res["docker_stats"] = [json.loads(ln) for ln in stats.splitlines() if ln.strip()]

            await asyncio.gather(sample_stats(), *(loop(mm) for mm in machines))
            res["steady_latency_ms"] = {k: {kk: (round(vv * 1000, 1) if isinstance(vv, float) else vv)
                                            for kk, vv in summary(v).items()} for k, v in lat.items()}
            print(f"N={n} steady {json.dumps(res['steady_latency_ms'])}", flush=True)
            res["round2_revert"] = await reset_round("revert", 2000)
            print(f"N={n} round2 {json.dumps({k: res['round2_revert'][k] for k in ('all_seconds', 'ok', 'reset_total')})}",
                  flush=True)
        finally:
            for i, w in enumerate(workers):
                if w is not None:
                    await pools[i].release(w)
            await asyncio.gather(*(p.close() for p in pools), return_exceptions=True)
            await backend.close()
        res["boot_failures"] = backend.boot_failures
        res["pool_events"] = [e for p in pools for e in p.events]
        results.append(res)
        (out / "load.json").write_text(json.dumps(results, indent=2))
    return 0


# --------------------------------------------------------------------------- checkpoint semantics
async def cmd_checkpoint(args: argparse.Namespace) -> int:
    """snapshot() mid-episode, keep editing, revert to the checkpoint: DB rows written before the
    checkpoint are back, rows after it are gone, and Chrome restarted (filesystem semantics)."""
    world = load_world("claims-ops-v1")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    res: dict[str, Any] = {"at": now()}
    for mode in ("flush", "stop"):
        backend = DockerBackend.for_world(world, snapshot_db=mode)
        m = await backend.create(metadata={"run_id": f"qualify-checkpoint-{os.getpid()}"})
        dbs = world.databases(m)
        try:
            await dbs["portal"].execute_script("INSERT INTO messages (id, subject, body, received_at, is_read) VALUES "
                                               "(990101, 'before', 'x', '2026-09-28T00:00:00Z', 0);")
            await dbs["openemr"].execute_script("UPDATE patient_data SET phone_home = '555-0101' WHERE pid = 100001;")
            await m.click(640, 90)
            await m.type_text("http://localhost:8080/messages")   # typed, not submitted: in-memory browser state
            t0 = time.perf_counter()
            sid = await m.snapshot(f"qualify-{mode}")
            t_snap = time.perf_counter() - t0
            await dbs["portal"].execute_script("INSERT INTO messages (id, subject, body, received_at, is_read) VALUES "
                                               "(990102, 'after', 'x', '2026-09-28T00:00:00Z', 0);")
            await dbs["openemr"].execute_script("UPDATE patient_data SET phone_home = '555-0102' WHERE pid = 100001;")
            t0 = time.perf_counter()
            await m.revert(sid)
            t_rev = time.perf_counter() - t0
            dbs = world.databases(m)
            rows = await dbs["portal"].query("SELECT id FROM messages WHERE id >= 990101 ORDER BY id")
            phone = await dbs["openemr"].scalar("SELECT phone_home FROM patient_data WHERE pid = 100001")
            health = await world.health(m, dbs)
            (out / f"after_revert_{mode}.png").write_bytes(await m.screenshot())
            _, size, _ = await backend.cli.run("image", "inspect", "-f", "{{.Size}}", sid)
            _, hist, _ = await backend.cli.run("history", "--format", "{{.Size}}", sid)
            res[mode] = {"snapshot_seconds": round(t_snap, 2), "revert_seconds": round(t_rev, 2),
                         "portal_rows_after_revert": [r["id"] for r in rows], "openemr_phone_after_revert": phone,
                         "health_ok": health.ok, "window_title_after_revert": await title(m),
                         "image_size_bytes": int(size.strip() or 0), "top_layer_size": hist.splitlines()[0] if hist else None,
                         "boot": m.last_boot}
            print(mode, json.dumps(res[mode]), flush=True)
            await m.kill()
            await backend.cli.run("rmi", sid, check=False)
        finally:
            await m.kill()
            await backend.close()
    (out / "checkpoint.json").write_text(json.dumps(res, indent=2))
    return 0


# --------------------------------------------------------------------------- replay determinism (correction engine)
def _replay_script(task: Any) -> list[str]:
    """~30 GUI actions: file the appeal in the portal (scripts/gui_episode.py), then log into OpenEMR in a
    new tab and open the patient's chart in another (a new tab avoids OpenEMR's "Leave site?" dialog)."""
    pid = task.expected["patient_pid"]
    portal = [a for a in _gui_script(task) if not a.startswith("done")]
    openemr = ['key("ctrl+t")', 'type("http://localhost/openemr/interface/login/login.php?site=default")', 'key("Return")',
               "wait(3)", "click(706, 422)", 'type("admin")', "click(684, 476)", 'type("pass")', "click(640, 585)", "wait(5)",
               'key("ctrl+t")',
               f'type("http://localhost/openemr/interface/patient_file/summary/demographics.php?set_pid={pid}")',
               'key("Return")', "wait(4)"]
    return portal + openemr


async def cmd_replay(args: argparse.Namespace) -> int:
    """The correction engine's replay restore (forkloop/correction/restore.py) on Docker: reset on a fresh
    container, re-execute the action prefix through the agent channel with wait_stable after each action,
    compare world_digest with the digest of the original execution at several checkpoints."""
    from forkloop.actions import Action
    from forkloop.backends.base import apply_action
    from forkloop.correction.digest import compare, world_digest
    from forkloop.correction.restore import _settle
    from forkloop.env import Env

    world = load_world("claims-ops-v1")
    backend = DockerBackend.for_world(world)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    task = world.generate("resolve_denial", args.seed, "train")
    script = _replay_script(task)
    marks = sorted({0, *[int(x) for x in args.checkpoints.split(",")], len(script)})
    runs: list[dict[str, Any]] = []
    try:
        for r in range(args.runs):
            pool = WorkerPool(backend, world, size=1, mode="fork", run_id=f"qualify-replay-{os.getpid()}-{r}")
            env = Env(world, backend, family=task.family, split=task.split, pool=pool)
            t0 = time.perf_counter()
            try:
                await env.reset(task.seed, task=task)
                digests: dict[int, Any] = {}
                for i, raw in enumerate(script + [None]):  # one extra turn to take the final digest
                    if i in marks:
                        digests[i] = await world_digest(world, env.ep.dbs, env.ep.last_shot)
                        (out / f"run{r}-step{i:02d}.png").write_bytes(env.ep.last_shot)
                    if raw is None:
                        break
                    a = Action.parse(raw, width=1280, height=720)
                    if not a.is_terminal:  # exactly restore.py's replay loop
                        await apply_action(env.ep.machine, a)
                        env.ep.last_shot = await _settle(env.ep.machine, a, "stable", 0.6)
                runs.append({"run": r, "machine": env.ep.machine.id, "seconds": round(time.perf_counter() - t0, 1),
                             "digests": {i: d.to_dict() for i, d in digests.items()}})
            finally:
                await env.close()
                await pool.close()
            print(f"run {r}: {runs[-1]['seconds']}s on {runs[-1]['machine']}", flush=True)
    finally:
        await backend.close()
    from forkloop.correction.digest import WorldDigest

    ref = runs[0]["digests"]
    comparisons = []
    for run in runs[1:]:
        for i in marks:
            fid = compare(WorldDigest.from_dict(ref[i]), WorldDigest.from_dict(run["digests"][i]), max_screen_distance=0.10)
            comparisons.append({"replay_run": run["run"], "step": i, **fid.to_dict()})
            print(f"replay {run['run']} step {i:2d}: tables_equal={fid.tables_equal} "
                  f"screen_distance={fid.screen_distance:.4f} differing={fid.differing_tables}", flush=True)
    res = {"at": now(), "task": task.task_id, "actions": script, "checkpoints": marks, "runs": runs,
           "comparisons": comparisons,
           "all_tables_equal": all(c["tables_equal"] for c in comparisons),
           "max_screen_distance": max((c["screen_distance"] for c in comparisons), default=None)}
    (out / "replay.json").write_text(json.dumps(res, indent=2))
    print(json.dumps({k: res[k] for k in ("all_tables_equal", "max_screen_distance")}))
    return 0 if res["all_tables_equal"] and (res["max_screen_distance"] or 0) <= 0.10 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("resets")
    p.add_argument("--out", required=True)
    p.add_argument("--families", nargs="+", default=FAMILIES)
    p.add_argument("--seeds", default="1,2,3")
    p.add_argument("--mode", choices=["revert", "fork"], default="revert")
    p.add_argument("--split", default="train")
    p.set_defaults(fn=cmd_resets)
    p = sub.add_parser("gui")
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=2, help="resolve_denial seed without the attachment requirement "
                   "(scripts/gui_episode.py does not attach files; seed 1 requires one)")
    p.set_defaults(fn=cmd_gui)
    p = sub.add_parser("latency")
    p.add_argument("--out", required=True)
    p.add_argument("--n", type=int, default=100)
    p.set_defaults(fn=cmd_latency)
    p = sub.add_parser("restore")
    p.add_argument("--out", required=True)
    p.add_argument("--n", type=int, default=12)
    p.set_defaults(fn=cmd_restore)
    p = sub.add_parser("load")
    p.add_argument("--out", required=True)
    p.add_argument("--worlds", type=int, nargs="+", default=[4, 8, 16])
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--think-s", type=float, default=0.5, help="pause between action rounds (a model call stands here)")
    p.set_defaults(fn=cmd_load)
    p = sub.add_parser("replay")
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=2)
    p.add_argument("--runs", type=int, default=3, help="run 0 records; runs 1.. replay on fresh containers")
    p.add_argument("--checkpoints", default="5,16,24", help="action indices at which digests are compared")
    p.set_defaults(fn=cmd_replay)
    p = sub.add_parser("checkpoint")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_checkpoint)
    args = ap.parse_args()
    return asyncio.run(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
