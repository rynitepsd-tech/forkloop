"""``forkloop`` command line.

    forkloop worlds                                  list worlds
    forkloop task  --world W --family F --seed N      print a task (instruction + oracle ids; add --full for the manifest)
    forkloop build-world --world W [--backend fake]   build the golden snapshot, print its id
    forkloop run   --world W --family F --seed N --policy scripted|random|teacher|student  run one episode
    forkloop collect --world W --families ... --seeds 0-199 --policy teacher [--retry-failed 2]  teacher data collection
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



def _env_history_k(args: Any) -> int:
    """The env keeps at least as many past actions as the policy wants to see. Until 2026-09-04
    the env used its default of 8 whatever ``--history-k`` said, so ``--history-k 16`` showed 8."""
    return max(int(getattr(args, "history_k", 8) or 8), 8)


def _budget_override(args: argparse.Namespace) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if getattr(args, "max_steps", None):
        out["max_steps"] = int(args.max_steps)
    if getattr(args, "max_seconds", None):
        out["max_seconds"] = float(args.max_seconds)
    return out


def _policy_options(spec: str, args: argparse.Namespace) -> dict[str, Any]:
    """The policy-side knobs of a run, for run.json (the world and the task manifests never
    change with them, so this is the only place they are recorded)."""
    if spec != "student":
        return {}
    return {"prompt_style": getattr(args, "prompt_style", None), "history_k": getattr(args, "history_k", None),
            "history_notes": bool(getattr(args, "history_notes", False)), "prev_shot": bool(getattr(args, "prev_shot", False)),
            "nav_macro": bool(getattr(args, "nav_macro", False)),
            "system_prompt_file": getattr(args, "system_prompt_file", None),
            "instruction_note": getattr(args, "instruction_note", None), "image_detail": getattr(args, "image_detail", None)}


def _policy_model(spec: str, args: argparse.Namespace) -> Optional[str]:  # noqa: D401
    if spec == "teacher":
        return args.model or "claude-opus-5"
    if spec == "student":
        return args.model or "student"
    return None


def _preflight(spec: str, args: Optional[argparse.Namespace] = None) -> None:
    """Fail before any machine is created when the policy cannot possibly run."""
    args = args or argparse.Namespace()
    if spec == "student" and "openai.com" in getattr(args, "student_url", "") and not (
            os.environ.get("STUDENT_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        raise SystemExit("hosted student needs OPENAI_API_KEY (or STUDENT_API_KEY) in the environment")
    if spec == "teacher":
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise SystemExit("teacher policy needs the anthropic package: pip install -e '.[teacher]'") from e
        if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            raise SystemExit("teacher policy needs ANTHROPIC_API_KEY (source ~/.config/forkloop/env)")


def _policy(spec: str, args: argparse.Namespace, **context: Any):
    """Build the policy for one episode. ``context`` (family, seed, attempt) is ignored here; it
    exists so tests can monkeypatch an attempt-aware factory under ``collect --retry-failed``."""
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

        hosted = "openai.com" in args.student_url
        system_prompt = None
        if getattr(args, "system_prompt_file", None):
            system_prompt = Path(args.system_prompt_file).read_text()
        return StudentPolicy(base_url=args.student_url, model=args.model or "student", prompt_style=args.prompt_style,
                             system_prompt=system_prompt, history_k=args.history_k, prev_screenshot=args.prev_shot,
                             history_notes=bool(getattr(args, "history_notes", False)),
                             nav_macro=bool(getattr(args, "nav_macro", False)),
                             instruction_note=getattr(args, "instruction_note", None),
                             image_detail=(args.image_detail or ("high" if hosted else None)),
                             api_key=os.environ.get("STUDENT_API_KEY") or os.environ.get("OPENAI_API_KEY"),
                             hosted_reasoning=hosted, max_tokens=4096 if hosted else 512, timeout_s=300.0 if hosted else 120.0,
                             extra_body={"reasoning_effort": args.effort} if hosted else None)
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
    _preflight(args.policy, args)
    rec = Recorder(args.runs, run_id=args.run_id, meta={"policy": args.policy, "world": w.name, "backend": backend.name,
                                                       "model": _policy_model(args.policy, args)})
    env = Env(w, backend, family=args.family, split=args.split, recorder=rec, history_k=_env_history_k(args))
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
    from .reset import ResetError
    from .search import best_of_n
    from .trajectories import Recorder, select_attempts
    from .world import load_world

    _preflight(args.policy, args)
    w = load_world(args.world)
    backend = _backend(args.backend, w)
    retry_failed = max(0, int(getattr(args, "retry_failed", 0) or 0))
    rec = Recorder(args.runs, run_id=args.run_id, meta={"policy": args.policy, "world": w.name, "backend": backend.name,
                                                       "best_of": args.best_of, "model": _policy_model(args.policy, args),
                                                       "effort": args.effort if args.policy == "teacher" else None,
                                                       "pool_mode": args.pool_mode, "concurrency": args.concurrency,
                                                       "cpu": args.cpu, "mem_mb": args.mem_mb,
                                                       "budget_override": _budget_override(args),
                                                       "retry_failed": retry_failed,
                                                       "policy_options": _policy_options(args.policy, args)})
    pool = WorkerPool(backend, w, size=args.concurrency, mode=args.pool_mode, cpu=args.cpu, mem_mb=args.mem_mb)
    await pool.start()
    seeds = _seed_list(args.seeds)
    families = args.families or w.config.families
    jobs = [(f, s) for f in families for s in seeds]
    sem = asyncio.Semaphore(args.concurrency)
    attempts: list[dict[str, Any]] = []  # every attempt, in the order it landed

    def _episode_info(env: Env) -> dict[str, Any]:
        ep = env.ep
        if ep is None or ep.recorder is None:
            return {}
        return {"episode_id": ep.recorder.episode_id, "steps": len(ep.recorder.steps)}

    async def one(fam: str, seed: int, attempt: int) -> None:
        tag = f"{fam} seed={seed}" + (f" attempt={attempt}" if retry_failed else "")
        for reset_try in range(1, args.reset_retries + 2):
            async with sem:
                # Every attempt is a fresh reset: in fork mode that is a new fork of the golden.
                env = Env(w, backend, family=fam, split=args.split, pool=pool, recorder=rec, history_k=_env_history_k(args),
                          budget_override=_budget_override(args), record_extra={"attempt": attempt})
                pol = _policy(args.policy, args, family=fam, seed=seed, attempt=attempt)
                row: dict[str, Any] = {"family": fam, "seed": seed, "attempt": attempt}
                try:
                    if args.best_of > 1:
                        v = await best_of_n(env, pol, args.best_of, seed, family=fam, mode=args.search_mode)
                    else:
                        v = await run_episode(env, pol, seed, family=fam)
                    row.update({"reward": v.reward, "reason": v.reason_code, **_episode_info(env)})
                    attempts.append(row)
                    print(f"{tag} reward={v.reward} reason={v.reason_code} steps={row.get('steps')}", flush=True)
                    return
                except ResetError as e:
                    # The machine never came up (capacity, concurrency cap, health): nothing was
                    # spent on the policy, so the seed is retried after a pause rather than lost.
                    if reset_try <= args.reset_retries:
                        print(f"{tag} reset failed (try {reset_try}): {e}; retrying in "
                              f"{args.reset_retry_wait_s:.0f}s", flush=True)
                    else:
                        row["error"] = f"ResetError: {e}"
                        attempts.append(row)
                        print(f"{tag} ERROR ResetError: {e}", flush=True)
                        return
                except Exception as e:  # noqa: BLE001
                    row.update({"error": f"{type(e).__name__}: {e}", **_episode_info(env)})
                    attempts.append(row)
                    print(f"{tag} ERROR {type(e).__name__}: {e}", flush=True)
                    return
                finally:
                    await env.close()
            await asyncio.sleep(args.reset_retry_wait_s)

    def unverified() -> list[tuple[str, int]]:
        best: dict[tuple[str, int], float] = {}
        for r in attempts:
            key = (r["family"], r["seed"])
            best[key] = max(best.get(key, 0.0), float(r.get("reward") or 0.0))
        return [(f, s) for f, s in jobs if best.get((f, s), 0.0) < 1.0]

    def record_selection() -> dict[str, dict[str, Any]]:
        selection = select_attempts(rec.dir)
        rec.update_meta(attempts=selection, n_attempts=len(attempts))
        return selection

    selection: dict[str, dict[str, Any]] = {}
    try:
        await asyncio.gather(*(one(f, s, 1) for f, s in jobs))
        for k in range(1, retry_failed + 1):
            record_selection()
            todo = unverified()
            if not todo:
                break
            print(f"\nretry pass {k}/{retry_failed}: {len(todo)} seed(s) below 1.0: "
                  + ", ".join(f"{f}:{s}" for f, s in todo), flush=True)
            await asyncio.gather(*(one(f, s, k + 1) for f, s in todo))
    finally:
        try:
            await pool.close()
        finally:
            await backend.close()
        try:
            selection = record_selection()
        except Exception as e:  # noqa: BLE001
            print(f"could not record attempt selection: {type(e).__name__}: {e}", flush=True)
    summary: list[dict[str, Any]] = []
    for fam, seed in jobs:
        sel = selection.get(f"{fam}:{seed}")
        mine = [r for r in attempts if r["family"] == fam and r["seed"] == seed]
        chosen = next((a for a in (sel or {}).get("attempts", []) if a["selected"]), None)
        row = {"family": fam, "seed": seed,
               "reward": chosen["reward"] if chosen else max((float(r.get("reward") or 0.0) for r in mine), default=None),
               "reason": chosen["reason"] if chosen else (mine[-1].get("reason") or mine[-1].get("error") if mine else "NOT_RUN"),
               "episode_id": chosen["episode_id"] if chosen else None,
               "n_attempts": len(mine), "attempts": mine}
        summary.append(row)
    ok = sum(1 for r in summary if (r.get("reward") or 0) >= 1.0)
    extra = f" ({len(attempts)} attempts, retry_failed={retry_failed})" if retry_failed else ""
    print(f"\n{ok}/{len(summary)} verified{extra}. run dir: {rec.dir}")
    (rec.dir / "collect_summary.json").write_text(json.dumps(summary, indent=2))
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

    s = summarize_run(args.run, model=args.model, vm_hour_usd=args.vm_hour_usd)
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
            p.add_argument("--system-prompt-file", default=None,
                           help="student: replace the system prompt with this file ({w},{h},{w1},{h1} are filled in)")
            p.add_argument("--history-k", type=int, default=8, help="student: previous actions shown as text")
            p.add_argument("--prev-shot", action="store_true", help="student: also send the screenshot from before the last action")
            p.add_argument("--history-notes", action="store_true",
                           help="student: show the model's own reasoning line next to each previous action (its memory)")
            p.add_argument("--image-detail", default=None, help="student: OpenAI image detail hint (hosted default: high)")
            p.add_argument("--instruction-note", default=None,
                           help="student: text appended to every instruction the model sees (policy-side; recorded in run.json)")
            p.add_argument("--nav-macro", action="store_true",
                           help="student (fara): expand visit_url into omnibox click + ctrl+a + type + Return and "
                                "history_back into alt+Left instead of rejecting them")
            p.add_argument("--max-steps", type=int, default=None, help="override the task's action budget for this run")
            p.add_argument("--max-seconds", type=float, default=None, help="override the task's wall budget for this run")
            p.add_argument("--script", default=None, help="JSON list of compact actions for --policy scripted")
            p.add_argument("--policy-seed", type=int, default=0)
            p.add_argument("--split", default="train")
            p.add_argument("--runs", default="runs")
            p.add_argument("--run-id", default=None)
            p.add_argument("--best-of", type=int, default=1)
            p.add_argument("--search-mode", choices=["revert", "fork"], default="revert")
            p.add_argument("--cpu", type=int, default=None, help="vCPUs per machine (default: world.yaml resources)")
            p.add_argument("--mem-mb", type=int, default=None, help="RAM per machine in MB (default: world.yaml resources)")

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
    p.add_argument("--reset-retries", type=int, default=2, help="re-queue a seed whose reset failed this many times")
    p.add_argument("--reset-retry-wait-s", type=float, default=60.0)
    p.add_argument("--retry-failed", type=int, default=0,
                   help="after the pass, re-run every seed with reward < 1 up to N more times on a fresh fork; "
                        "exports/metrics keep the shortest verified attempt per seed")
    p.set_defaults(fn=lambda a: asyncio.run(_collect(a)))
    p = sub.add_parser("export", help="export a run"); p.add_argument("--run", required=True)
    p.add_argument("--format", choices=["jsonl", "sft", "osworld"], default="jsonl"); p.add_argument("--out", required=True)
    p.add_argument("--history-k", type=int, default=8); p.add_argument("--limit", type=int, default=None); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("metrics", help="summarise a run"); p.add_argument("--run", required=True); p.add_argument("--json", default=None)
    p.add_argument("--model", default=None, help="price tokens as this model (default: run.json model)")
    p.add_argument("--vm-hour-usd", type=float, default=0.134, help="VM $/h incl. screen (Starter 2 vCPU/4 GB: 0.134)")
    p.set_defaults(fn=cmd_metrics)
    p = sub.add_parser("reset-bench", help="reset benchmark (Chart 2)", add_help=False)
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda a: __import__("forkloop.bench.reset_benchmark", fromlist=["main"]).main(a.rest))
    p = sub.add_parser("reap", help="kill leftover forkloop machines"); p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=lambda a: asyncio.run(_reap(a)))

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "reset-bench":
        # argparse.REMAINDER swallows leading options into the parent parser ("unrecognized arguments: --world"),
        # so hand the benchmark its own argv untouched.
        return int(__import__("forkloop.bench.reset_benchmark", fromlist=["main"]).main(argv[1:]) or 0)
    args = ap.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
