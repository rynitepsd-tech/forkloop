"""``forkloop`` command line.

Start here (no account needed):
    forkloop doctor --backend fake                    check the installation
    forkloop demo --out runs/demo                     five verifier controls with HTML reports
    forkloop compare --config CONFIG --out DIR        matched A/B policy comparison on the same seeds
    forkloop compare-report DIR [--format html]       read or share a comparison
    forkloop report RUN_DIR|EPISODE_DIR               explain a recorded run or episode

Inspect and operate:
    forkloop worlds | task                            list worlds; print a generated task
    forkloop run | metrics | export                   one episode; run summary; data export
    forkloop ledger | reap                            spend ledger; clean up leftover Solari machines

Research tools: build-world, collect, reset-bench (see system.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

EXIT_INCOMPLETE = 3  # compare: planned pairs missing, interrupted or not comparable
EXIT_ERROR = 4       # configuration or runtime error (argparse usage errors stay 2)


def _backend(name: str, world: Any, latency: float = 0.0):
    if name == "fake":
        from .backends.fake import FakeBackend

        return FakeBackend(concurrency_cap=int(os.environ.get("FORKLOOP_CONCURRENCY", 2)), gui_factory=world.gui_factory(),
                           latency={"revert": latency, "create": latency * 3} if latency else None)
    from .backends.base import BackendError
    from .backends.solari import SolariBackend

    try:
        return SolariBackend()
    except BackendError as e:
        raise SystemExit(f"{e} (the default backend is the paid Solari desktop; pass --backend fake for the offline "
                         "simulation, or export SOLARI_API_KEY and FORKLOOP_SESSION_LEDGER for a live run)") from None



def _env_history_k(args: Any) -> int:
    """The env keeps at least as many past actions as the policy wants to see. Until 2026-09-04
    the env used its default of 8 whatever ``--history-k`` said, so ``--history-k 16`` showed 8."""
    return max(int(getattr(args, "history_k", 8) or 8), 8)


def _budget_override(args: argparse.Namespace) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if getattr(args, "max_steps", None) is not None:
        if args.max_steps <= 0:
            raise ValueError("--max-steps must be positive")
        out["max_steps"] = int(args.max_steps)
    if getattr(args, "max_seconds", None) is not None:
        if not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
            raise ValueError("--max-seconds must be positive and finite")
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
    if spec == "student" and urlsplit(getattr(args, "student_url", "")).hostname == "api.openai.com" and not (
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

        hosted = urlsplit(args.student_url).hostname == "api.openai.com"
        system_prompt = None
        if getattr(args, "system_prompt_file", None):
            system_prompt = Path(args.system_prompt_file).read_text()
        return StudentPolicy(base_url=args.student_url, model=args.model or "student", prompt_style=args.prompt_style,
                             system_prompt=system_prompt, history_k=args.history_k, prev_screenshot=args.prev_shot,
                             history_notes=bool(getattr(args, "history_notes", False)),
                             nav_macro=bool(getattr(args, "nav_macro", False)),
                             instruction_note=getattr(args, "instruction_note", None),
                             image_detail=(args.image_detail or ("high" if hosted else None)),
                             api_key=os.environ.get("STUDENT_API_KEY") or (os.environ.get("OPENAI_API_KEY") if hosted else None),
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
                             # The spend guard (backends/solari.py::create) refuses lifetimes above 30 minutes; the
                             # last measured golden build took ~10 min (docs/HANDOFF.md). A build that outlives
                             # this window is killed by Solari and must be restarted.
                             timeout_ms=30 * 60_000)
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

    _preflight(args.policy, args)
    budget = _budget_override(args)
    w = load_world(args.world)
    backend = _backend(args.backend, w)
    rec = Recorder(args.runs, run_id=args.run_id, meta={"policy": args.policy, "world": w.name, "backend": backend.name,
                                                       "model": _policy_model(args.policy, args),
                                                       "budget_override": budget, "policy_options": _policy_options(args.policy, args)})
    env = Env(w, backend, family=args.family, split=args.split, recorder=rec, history_k=_env_history_k(args),
              budget_override=budget)
    pol = None
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
        try:
            await env.close()
        finally:
            try:
                if callable(getattr(pol, "aclose", None)):
                    await pol.aclose()
            finally:
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
                                                       "session_ledger": os.environ.get("FORKLOOP_SESSION_LEDGER"),
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
                    if env.ep and env.ep.recorder:
                        ap = env.ep.recorder.dir / "accounting.json"
                        accounting = json.loads(ap.read_text()) if ap.exists() else {}
                        accounting["experiment_tokens"] = dict(getattr(pol, "usage", {}) or {})
                        ap.write_text(json.dumps(accounting, indent=2))
                    try:
                        await env.close()
                    finally:
                        if callable(getattr(pol, "aclose", None)):
                            await pol.aclose()
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


def cmd_report(args: argparse.Namespace) -> int:
    from .report import report

    options = dict(turns=args.turns, failed=args.failed, all_episodes=args.all, all_attempts=args.all_attempts)
    if args.format == "html":
        from .report_html import html_report

        target = Path(args.out)
        if target.suffix.lower() != ".html" or target.is_symlink():
            raise SystemExit("HTML output must be a non-symlink .html file")
        rendered = html_report(args.path, crop_top=args.crop_top, **options)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
        print(f"HTML evidence report: {target}")
    else:
        print(report(args.path, **options))
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    """Create or show the session spend ledger every paid Solari/OpenAI call reserves against."""
    from .spending import SessionLedger

    path = Path(args.path)
    if args.create:
        if path.exists():
            raise SystemExit(f"{path} already exists; a ledger is never recreated (it would clear recorded spend)")
        limits = {"solari": {"ceiling": args.solari_usd, "stop": args.solari_usd * 0.8},
                  "openai": {"ceiling": args.openai_usd, "stop": args.openai_usd * 0.9},
                  "gpu": {"ceiling": 0.0, "stop": 0.0}}
        SessionLedger.create(path, limits=limits)
        print(f"created {path}")
        print(f"export FORKLOOP_SESSION_LEDGER={path.resolve()}")
    s = SessionLedger(path).summary()
    for name, row in s["services"].items():
        print(f"{name:<8} " + "  ".join(f"{k}={v}" for k, v in row.items()))
    print(f"operations: {len(s['operations'])}")
    return 0


async def _reap(args: argparse.Namespace) -> int:
    from .backends.solari import SolariBackend
    from .spending import SessionLedger

    ledger_path = args.ledger or os.environ.get("FORKLOOP_SESSION_LEDGER")
    if not ledger_path and not args.all_sessions:
        raise ValueError("reap requires --ledger PATH or FORKLOOP_SESSION_LEDGER; use --all-sessions only for intentional account-wide cleanup")
    ledger = SessionLedger(ledger_path) if ledger_path else None
    operations = {row["id"]: row for row in ledger.summary()["operations"] if row["service"] == "solari"} if ledger else {}
    for row in operations.values():
        row["evidence"] = json.loads(row["evidence"])
    machine_operations = {row["evidence"]["machine_id"]: op for op, row in operations.items()
                          if isinstance(row.get("evidence"), dict) and row["evidence"].get("machine_id")}
    b = SolariBackend(session_ledger=ledger_path)
    active = ("running", "starting", "paused")
    def owned(info: Any) -> bool:
        return args.all_sessions or info.id in machine_operations or info.metadata.get("spend_operation") in operations

    try:
        infos = [info for info in await b.list_machines(metadata={"forkloop": "1"}) if owned(info)]
        for info in infos:
            if info.state not in active:
                continue
            operation = machine_operations.get(info.id) or info.metadata.get("spend_operation")
            print("would kill" if args.dry_run else "killing", "session machine", operation or "(account-wide selection)")
            if args.dry_run:
                continue
            record = operations.get(operation, {})
            evidence = record.get("evidence", {})
            if record.get("actual") is None and evidence.get("hourly_usd") and evidence.get("started_at"):
                b.resources[info.id] = {**evidence, "operation": operation, "closed": False}
            await b.kill_machine(info.id)
            if ledger and operation in operations and record.get("actual") is None and info.id not in b.resources:
                ledger.reconcile(operation, None, status="resource_closed_usage_pending",
                                 evidence={**evidence, "machine_id": info.id, "closed": True})
        if not args.dry_run:
            remaining = [info for info in await b.list_machines(metadata={"forkloop": "1"})
                         if owned(info) and info.state in active]
            print(f"{len(remaining)} selected machines remain active")
            return 1 if remaining else 0
        print(f"{len(infos)} selected machines listed; no resources changed")
    finally:
        await b.close()
    return 0


def _live_execution_note(backend: str) -> str:
    if backend == "fake":
        return "fake backend: offline, no provider spending"
    from .spending import solari_allocation_status

    return solari_allocation_status()


async def _compare(args: argparse.Namespace) -> int:
    from .comparison import PolicyVariant, format_comparison, run_comparison
    from .policy_config import load_config
    from .world import load_world

    config, policies = load_config(args.config, require_env=not args.check)
    world = load_world(config["world"])
    if config["family"] not in world.config.families:
        raise ValueError(f"Family {config['family']!r} is not supported by {world.name}")
    if args.check:
        print(json.dumps({"world": world.name, "backend": config["backend"], "family": config["family"],
                          "seeds": config["seeds"], "budget": config["budget"],
                          "variants": [{"name": p.name, "identity": p.identity} for p in policies],
                          "missing_environment": sorted({v for p in policies for v in p.missing_env}),
                          "live_execution": _live_execution_note(config["backend"]),
                          "note": "Configuration only; no machine allocation or model request."}, indent=2))
        return 0
    if Path(args.out).exists():
        raise ValueError("Comparison output already exists; choose a new --out to preserve prior evidence")
    backend = _backend(config["backend"], world)
    try:
        result = await run_comparison(
            world, backend, [PolicyVariant(p.name, p.identity, p.factory) for p in policies],
            config["seeds"], output=args.out, family=config["family"], split=config["split"],
            budget_override=config["budget"],
            history_k=max(8, *(int(p.identity["options"].get("history_k", 8)) for p in policies)),
        )
    finally:
        await backend.close()
    print(format_comparison(result))
    print(f"\nOpen {Path(args.out) / 'comparison.html'} in a browser.")
    if result["execution"]["status"] != "finished" or result["matched_pairs"] != result["planned_pairs"]:
        return EXIT_INCOMPLETE
    return 1 if args.fail_on_regression and result["regression_seeds"] else 0


def _comparison_bundle(source: str, destination: str, summary: dict[str, Any], crop_top: int) -> Path:
    """Export HTML views only; never copy raw controller artifacts or source images."""
    from copy import deepcopy
    import shutil

    from .comparison import _inside, html_comparison
    from .report_html import html_report

    root = Path(source).resolve()
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=False)
    result = deepcopy(summary)
    try:
        for cell in result["cells"]:
            cell.pop("report", None)
            relative = cell.get("episode")
            if not relative or not cell.get("evidence"):
                continue
            episode = _inside(root, relative)
            # Regenerate from primary evidence, not a possibly stale/uncropped HTML.
            report = html_report(episode, crop_top=crop_top)
            report_path = Path("runs") / cell["arm"] / "episodes" / cell["id"] / "report.html"
            output = _inside(target.resolve(), report_path.as_posix())
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(report, encoding="utf-8")
            cell["report"] = report_path.as_posix()
        result["limits"] = [*result["limits"],
                            f"HTML-only sharing bundle. Screenshot crop: {crop_top} top pixels. "
                            "Raw controller files are excluded. Review image pixels and free text before sharing; "
                            "cropping and text redaction are not automatic secret detection."]
        (target / "comparison.html").write_text(html_comparison(result), encoding="utf-8")
    except BaseException:
        shutil.rmtree(target)
        raise
    return target / "comparison.html"


def cmd_compare_report(args: argparse.Namespace) -> int:
    from .comparison import format_comparison, html_comparison, summarize_comparison

    result = summarize_comparison(args.path)
    if args.bundle:
        destination = _comparison_bundle(args.path, args.bundle, result, args.crop_top)
        print(f"HTML-only comparison bundle: {destination}")
        print("Review screenshots and free text before sharing. No raw controller artifacts were copied.")
        return 0
    if args.format == "html":
        destination = Path(args.out) if args.out else Path(args.path) / "comparison.html"
        if destination.resolve().parent != Path(args.path).resolve() or destination.suffix.lower() != ".html":
            raise ValueError("HTML must be written inside the comparison directory so episode links remain valid; share that directory together")
        destination.write_text(html_comparison(result), encoding="utf-8")
        print(f"Comparison report: {destination}")
    else:
        text = json.dumps(result, indent=2) if args.format == "json" else format_comparison(result)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import doctor, render_doctor

    result = asyncio.run(doctor(world=args.world, family=args.family, backend=args.backend,
                               policy=args.policy, remote=args.remote, build=args.build,
                               session_ledger=args.ledger, pricing_file=args.pricing_file,
                               student_url=args.student_url))
    print(json.dumps(result, indent=2) if args.json else render_doctor(result), end="\n")
    return 0 if result["ready"] else 1


def cmd_demo(args: argparse.Namespace) -> int:
    try:
        from .controls import main as controls_main
    except ImportError as exc:
        raise SystemExit("The offline demo needs the world extra: pip install '.[world]'") from exc
    return controls_main(["--out", args.out])


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
            p.add_argument("--prev-shot", action=argparse.BooleanOptionalAction, default=True,
                           help="student: previous/current observation (default); --no-prev-shot is a diagnostic ablation")
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
    p = sub.add_parser("demo", help="run five offline verifier controls and write inspectable HTML reports")
    p.add_argument("--out", default="runs/offline-controls", help="new output directory; never overwritten")
    p.set_defaults(fn=cmd_demo)
    p = sub.add_parser("doctor", help="check setup without allocating a VM or calling a model"); common(p)
    p.add_argument("--family", default="resolve_denial")
    p.add_argument("--policy", choices=["scripted", "random", "student", "teacher"], default="scripted")
    p.add_argument("--student-url", default=os.environ.get("STUDENT_URL"))
    p.add_argument("--ledger", default=None)
    p.add_argument("--pricing-file", default=None)
    p.add_argument("--remote", action="store_true", help="explicitly allow read-only Solari metadata requests")
    p.add_argument("--build", action="store_true", help="check fresh-build prerequisites, not an existing golden")
    p.add_argument("--json", action="store_true", help="machine-readable, credential-redacted diagnostics")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("compare", help="run a matched best-of-one policy comparison from a YAML configuration")
    p.add_argument("--config", required=True, help="versioned YAML; custom factories are trusted local Python code")
    p.add_argument("--out", required=True, help="new comparison directory; existing evidence is never overwritten")
    p.add_argument("--check", action="store_true", help="validate configuration without allocating machines or calling models")
    p.add_argument("--fail-on-regression", action="store_true",
                   help="exit 1 if B fails a comparable seed that A passes (exit codes: 0 ok, 1 regression, "
                        "2 usage, 3 incomplete evidence, 4 configuration or runtime error)")
    p.set_defaults(fn=lambda a: asyncio.run(_compare(a)))
    p = sub.add_parser("compare-report", help="inspect a recorded comparison without any account or model")
    p.add_argument("path", help="comparison directory containing protocol.json")
    p.add_argument("--format", choices=["text", "json", "html"], default="text")
    p.add_argument("--out", default=None, help="output file; HTML stays inside the comparison directory to preserve evidence links")
    p.add_argument("--bundle", default=None, metavar="DIRECTORY",
                   help="new HTML-only sharing directory with regenerated linked episode reports; excludes raw controller files")
    p.add_argument("--crop-top", type=int, default=0, metavar="PIXELS",
                   help="with --bundle: omit top screenshot pixels from exported copies; originals stay unchanged")
    p.set_defaults(fn=cmd_compare_report)
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
    p = sub.add_parser("report", help="explain a recorded episode or run from its artifacts (no machine needed)")
    p.add_argument("path", help="runs/<run> or runs/<run>/episodes/<episode>")
    p.add_argument("--turns", type=int, default=6, help="model turns to print at the end of an episode report")
    p.add_argument("--failed", action="store_true", help="run: append the episode report of every failed episode")
    p.add_argument("--all", action="store_true", help="run: append the episode report of every episode")
    p.add_argument("--all-attempts", action="store_true", help="run: include superseded retry attempts")
    p.add_argument("--format", choices=["text", "html"], default="text", help="default text; html is a self-contained evidence export")
    p.add_argument("--out", default=None, help="HTML destination (.html); required with --format html")
    p.add_argument("--crop-top", type=int, default=0, metavar="PIXELS",
                   help="HTML only: omit this many top pixels from exported screenshots; originals stay unchanged")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("ledger", help="create or show the session spend ledger required for paid Solari/OpenAI calls")
    p.add_argument("path", help="SQLite file, e.g. runs/<session>/session-ledger.sqlite")
    p.add_argument("--create", action="store_true")
    p.add_argument("--solari-usd", type=float, default=10.0, help="Solari ceiling in USD for this session (create)")
    p.add_argument("--openai-usd", type=float, default=0.0, help="OpenAI ceiling in USD for this session (create)")
    p.set_defaults(fn=cmd_ledger)
    p = sub.add_parser("reset-bench", help="reset benchmark (Chart 2)", add_help=False)
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda a: __import__("forkloop.bench.reset_benchmark", fromlist=["main"]).main(a.rest))
    p = sub.add_parser("reap", help="kill leftover machines owned by a session ledger")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--ledger", default=None, help="session ledger; defaults to FORKLOOP_SESSION_LEDGER")
    p.add_argument("--all-sessions", action="store_true", help="explicitly select all Forkloop-tagged machines on the account")
    p.set_defaults(fn=lambda a: asyncio.run(_reap(a)))

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "reset-bench":
        # argparse.REMAINDER swallows leading options into the parent parser ("unrecognized arguments: --world"),
        # so hand the benchmark its own argv untouched.
        return int(__import__("forkloop.bench.reset_benchmark", fromlist=["main"]).main(argv[1:]) or 0)
    args = ap.parse_args(argv)
    if args.cmd == "compare-report":
        if args.bundle and (args.format != "html" or args.out):
            ap.error("compare-report --bundle requires --format html and cannot be combined with --out")
        if args.crop_top < 0 or (args.crop_top and not args.bundle):
            ap.error("compare-report --crop-top must be nonnegative and requires --bundle")
    if args.cmd == "report":
        if args.format == "html" and not args.out:
            ap.error("report --format html requires --out FILE.html")
        if args.format == "text" and args.out:
            ap.error("report --out is used with --format html; redirect text output in your shell")
        if args.turns < 0:
            ap.error("report --turns must be nonnegative")
        if args.crop_top < 0:
            ap.error("report --crop-top must be nonnegative")
        if args.format != "html" and args.crop_top:
            ap.error("report --crop-top requires --format html")
    try:
        return int(args.fn(args) or 0)
    except (ValueError, TypeError, OSError) as exc:
        # Distinct from argparse's usage errors (2) and incomplete comparison evidence (3).
        print(f"forkloop {args.cmd}: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
