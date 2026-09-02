"""``forkloop`` command line.

    forkloop worlds                                  list worlds
    forkloop task  --world W --family F --seed N      print a task (instruction + oracle ids; add --full for the manifest)
    forkloop build-world --world W [--backend fake]   build the golden snapshot, print its id
    forkloop run   --world W --family F --seed N --policy scripted|random|teacher|student  run one episode
    forkloop collect --world W --families ... --seeds 0-199 --policy teacher [--best-of 2]  teacher data collection
    forkloop export --run RUN_DIR --format jsonl|sft|osworld --out PATH
    forkloop metrics --run RUN_DIR
    forkloop reset-bench ...                          see forkloop.bench.reset_benchmark
    forkloop reap                                     kill leftover forkloop machines on Solari
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional


def _backend(name: str, world: Any, latency: float = 0.0):
    if name == "fake":
        from .backends.fake import FakeBackend

        return FakeBackend(concurrency_cap=int(os.environ.get("FORKLOOP_CONCURRENCY", 2)), gui_factory=world.gui_factory(),
                           latency={"revert": latency, "create": latency * 3} if latency else None)
    from .backends.solari import SolariBackend

    return SolariBackend()


def _policy(spec: str, args: argparse.Namespace):
    if spec == "random":
        from .policies.scripted import RandomPolicy

        return RandomPolicy(seed=args.policy_seed)
    if spec == "scripted":
        from .policies.scripted import ScriptedPolicy

        return ScriptedPolicy(json.loads(args.script) if args.script else [])
    if spec == "teacher":
        from .policies.teacher import TeacherPolicy

        return TeacherPolicy(model=args.model or "claude-opus-5", effort=args.effort)
    if spec == "student":
        from .policies.student import StudentPolicy

        return StudentPolicy(base_url=args.student_url, model=args.model or "student", prompt_style=args.prompt_style)
    raise SystemExit(f"unknown policy {spec!r}")


def cmd_worlds(args: argparse.Namespace) -> int:
    from .world import list_worlds, load_world

    for name in list_worlds():
        w = load_world(name)
        print(f"{name:20s} v{w.config.version}  {w.config.resolution}  families={w.config.families}")
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    from .world import load_world

    w = load_world(args.world)
    t = w.generate(args.family, args.seed, args.split)
    if args.full:
        print(t.to_json(indent=2))
        return 0
    print(t.task_id)
    print(t.instruction)
    print("effects   :", [c.id for c in t.oracle.effects])
    print("invariants:", [c.id for c in t.oracle.invariants])
    print("difficulty:", t.difficulty)
    return 0


async def _build(args: argparse.Namespace) -> int:
    from .world import load_world

    w = load_world(args.world)
    backend = _backend(args.backend, w)
    res = w.config.extra.get("resources", {})
    if args.attach:
        m = await backend.attach(args.attach, resolution=w.config.resolution)
        print("attached to", m.id[:24])
    else:
      m = await backend.create(template=w.config.template, resolution=w.config.resolution, cpu=args.cpu or int(res.get("cpu", 2)),
                             mem_mb=args.mem_mb or int(res.get("mem_mb", 4096)), disk_gb=args.disk_gb or res.get("disk_gb"),
                             metadata={"forkloop": "1", "run_id": "build"},
                             timeout_ms=180 * 60_000)  # a golden build outlives the 30-minute default (onTimeout=kill)
    try:
        sid = await w.build(m, log=print)
        print(f"\nGOLDEN_SNAPSHOT={sid}\nexport {w.config.golden_snapshot_env}={sid}")
    finally:
        if not args.keep:
            await m.kill()
        await backend.close()
    return 0


async def _run(args: argparse.Namespace) -> int:
    from .env import Env, run_episode
    from .search import best_of_n
    from .trajectories import Recorder
    from .world import load_world

    w = load_world(args.world)
    backend = _backend(args.backend, w)
    rec = Recorder(args.runs, run_id=args.run_id, meta={"policy": args.policy, "world": w.name, "backend": backend.name})
    env = Env(w, backend, family=args.family, split=args.split, recorder=rec)
    try:
        pol = _policy(args.policy, args)
        if args.best_of > 1:
            v = await best_of_n(env, pol, args.best_of, args.seed, family=args.family, mode=args.search_mode)
        else:
            v = await run_episode(env, pol, args.seed, family=args.family)
        print(json.dumps(v.to_dict(), indent=2))
        print("run dir:", rec.dir)
        return 0 if v.reward >= 1.0 else 1
    finally:
        await env.close()
        await backend.close()


def _seed_list(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


async def _collect(args: argparse.Namespace) -> int:
    from .env import Env, run_episode
    from .pool import WorkerPool
    from .search import best_of_n
    from .trajectories import Recorder
    from .world import load_world

    w = load_world(args.world)
    backend = _backend(args.backend, w)
    rec = Recorder(args.runs, run_id=args.run_id, meta={"policy": args.policy, "world": w.name, "backend": backend.name,
                                                       "best_of": args.best_of})
    pool = WorkerPool(backend, w, size=args.concurrency, mode=args.pool_mode)
    await pool.start()
    seeds = _seed_list(args.seeds)
    families = args.families or w.config.families
    jobs = [(f, s) for f in families for s in seeds]
    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict[str, Any]] = []

    async def one(fam: str, seed: int) -> None:
        async with sem:
            env = Env(w, backend, family=fam, split=args.split, pool=pool, recorder=rec)
            pol = _policy(args.policy, args)
            try:
                if args.best_of > 1:
                    v = await best_of_n(env, pol, args.best_of, seed, family=fam, mode=args.search_mode)
                else:
                    v = await run_episode(env, pol, seed, family=fam)
                results.append({"family": fam, "seed": seed, "reward": v.reward, "reason": v.reason_code})
                print(f"{fam} seed={seed} reward={v.reward} reason={v.reason_code}", flush=True)
            except Exception as e:  # noqa: BLE001
                results.append({"family": fam, "seed": seed, "error": f"{type(e).__name__}: {e}"})
                print(f"{fam} seed={seed} ERROR {type(e).__name__}: {e}", flush=True)
            finally:
                await env.close()

    try:
        await asyncio.gather(*(one(f, s) for f, s in jobs))
    finally:
        await pool.close()
        await backend.close()
    ok = sum(1 for r in results if r.get("reward") == 1.0)
    print(f"\n{ok}/{len(results)} verified. run dir: {rec.dir}")
    (rec.dir / "collect_summary.json").write_text(json.dumps(results, indent=2))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .exporters import export_jsonl, export_osworld, export_sft_pairs

    if args.format == "jsonl":
        print("episodes:", export_jsonl(args.run, args.out))
    elif args.format == "sft":
        print(export_sft_pairs(args.run, args.out, history_k=args.history_k, limit_episodes=args.limit))
    else:
        print("tasks:", export_osworld(args.run, args.out))
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    from .metrics import format_table, summarize_run

    s = summarize_run(args.run)
    print(format_table(s))
    if args.json:
        Path(args.json).write_text(json.dumps(s, indent=2))
    return 0


async def _reap(args: argparse.Namespace) -> int:
    from .backends.solari import SolariBackend

    b = SolariBackend()
    try:
        infos = await b.list_machines(metadata={"forkloop": "1"})
        for i in infos:
            if i.state in ("running", "starting", "paused"):
                print("killing", i.id, i.metadata)
                if not args.dry_run:
                    await b.kill_machine(i.id)
        print(f"{len(infos)} forkloop machines listed")
    finally:
        await b.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="forkloop", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser, *, policy: bool = False) -> None:
        p.add_argument("--world", default="claims-ops-v1")
        p.add_argument("--backend", choices=["solari", "fake"], default=os.environ.get("FORKLOOP_BACKEND", "solari"))
        if policy:
            p.add_argument("--policy", default="teacher", help="scripted|random|teacher|student")
            p.add_argument("--model", default=None)
            p.add_argument("--effort", default="high")
            p.add_argument("--student-url", default=os.environ.get("STUDENT_URL", "http://localhost:8000/v1"))
            p.add_argument("--prompt-style", default="compact")
            p.add_argument("--script", default=None, help="JSON list of compact actions for --policy scripted")
            p.add_argument("--policy-seed", type=int, default=0)
            p.add_argument("--split", default="train")
            p.add_argument("--runs", default="runs")
            p.add_argument("--run-id", default=None)
            p.add_argument("--best-of", type=int, default=1)
            p.add_argument("--search-mode", choices=["revert", "fork"], default="revert")

    sub.add_parser("worlds", help="list worlds").set_defaults(fn=cmd_worlds)
    p = sub.add_parser("task", help="print a generated task"); common(p)
    p.add_argument("--family", required=True); p.add_argument("--seed", type=int, required=True)
    p.add_argument("--split", default="train"); p.add_argument("--full", action="store_true"); p.set_defaults(fn=cmd_task)
    p = sub.add_parser("build-world", help="build the golden snapshot"); common(p)
    p.add_argument("--cpu", type=int, default=None); p.add_argument("--mem-mb", type=int, default=None)
    p.add_argument("--disk-gb", type=int, default=None)
    p.add_argument("--attach", default=None, help="resume the build on an existing machine id instead of creating one")
    p.add_argument("--keep", action="store_true", help="leave the build machine running")
    p.set_defaults(fn=lambda a: asyncio.run(_build(a)))
    p = sub.add_parser("run", help="run one episode"); common(p, policy=True)
    p.add_argument("--family", default=None); p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=lambda a: asyncio.run(_run(a)))
    p = sub.add_parser("collect", help="run many episodes (teacher data)"); common(p, policy=True)
    p.add_argument("--families", nargs="*", default=None); p.add_argument("--seeds", default="0-49")
    p.add_argument("--concurrency", type=int, default=int(os.environ.get("FORKLOOP_CONCURRENCY", 2)))
    p.add_argument("--pool-mode", choices=["revert", "fork"], default="revert")
    p.set_defaults(fn=lambda a: asyncio.run(_collect(a)))
    p = sub.add_parser("export", help="export a run"); p.add_argument("--run", required=True)
    p.add_argument("--format", choices=["jsonl", "sft", "osworld"], default="jsonl"); p.add_argument("--out", required=True)
    p.add_argument("--history-k", type=int, default=8); p.add_argument("--limit", type=int, default=None); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("metrics", help="summarise a run"); p.add_argument("--run", required=True); p.add_argument("--json", default=None)
    p.set_defaults(fn=cmd_metrics)
    p = sub.add_parser("reset-bench", help="reset benchmark (Chart 2)", add_help=False)
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda a: __import__("forkloop.bench.reset_benchmark", fromlist=["main"]).main(a.rest))
    p = sub.add_parser("reap", help="kill leftover forkloop machines"); p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=lambda a: asyncio.run(_reap(a)))

    args = ap.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
