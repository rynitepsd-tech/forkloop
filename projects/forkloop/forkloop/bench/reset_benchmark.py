"""Reset benchmark (Chart 2): revert vs from_snapshot vs cold create.

Timer: request sent → world healthy (both apps + DBs) + stable screenshot.
Every trial is a full ``ResetController.reset`` so the numbers are exactly
what an episode pays. Results are JSONL with per-stage timings; ``summarize``
produces p50/p95/p99, failure rate, and cost per 1k resets.

The local docker-compose baseline lives in ``local_baseline/`` and appends to
the same JSONL format so ``plot.py chart2`` can draw all four bars.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any, Optional

from ..backends.base import Backend
from ..pool import WorkerPool
from ..reset import ResetController, ResetError
from ..world import World, load_world
from .cost_model import cost_per_1k_resets, vm_hour_cost


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    ys = sorted(xs)
    k = (len(ys) - 1) * p
    f, c = int(k), min(int(k) + 1, len(ys) - 1)
    return ys[f] + (ys[c] - ys[f]) * (k - f)


async def run_method(backend: Backend, world: World, *, method: str, trials: int, golden: Optional[str],
                     out: Path, family: str, plan: str, vm_size: str, log=print,
                     fallback_to_fork: bool = True) -> list[dict[str, Any]]:
    """method ∈ revert | fork | cold. cold = create from template + full world build (expensive!).

    ``fallback_to_fork=False`` makes a refused ``revert()`` count as a failed trial instead of
    silently switching the pool (and every later row) to fork mode. In revert mode the pool is
    warmed first so trial 0 measures a revert rather than the initial ``create(from_snapshot)``.
    """
    results: list[dict[str, Any]] = []
    if method == "cold":
        for i in range(trials):
            t0 = time.monotonic()
            rec: dict[str, Any] = {"method": "cold", "trial": i, "backend": backend.name}
            try:
                m = await backend.create(template=world.config.template, resolution=world.config.resolution,
                                         metadata={"forkloop": "1", "run_id": "bench-cold"})
                t_create = time.monotonic() - t0
                await world.build(m, log=lambda s: None)
                rec.update(ok=True, total_seconds=time.monotonic() - t0, stages=[{"name": "create", "seconds": t_create}])
                await m.kill()
            except Exception as e:  # noqa: BLE001
                rec.update(ok=False, total_seconds=time.monotonic() - t0, error=str(e))
            results.append(rec)
            _append(out, rec)
            log(f"cold #{i}: {rec['total_seconds']:.2f}s ok={rec['ok']}")
        return results

    pool = WorkerPool(backend, world, size=1, mode=method, golden_snapshot=golden, run_id=f"bench-{method}",
                      fallback_to_fork=fallback_to_fork)
    await pool.start(warm=(method == "revert"))
    ctrl = ResetController(world)
    try:
        worker = await pool.acquire()
        for i in range(trials):
            task = world.generate(family, 900000 + i, "train") if world.config.families else None
            t0 = time.monotonic()
            rec = {"method": method, "trial": i, "backend": backend.name}
            try:
                outcome = await ctrl.reset(worker, task)  # type: ignore[arg-type]
                rec.update(outcome.report.to_dict())
                rec["total_seconds"] = outcome.report.total_seconds
                await pool.release(worker)
                worker = await pool.acquire()
            except ResetError as e:
                rec.update(ok=False, total_seconds=time.monotonic() - t0, error=str(e),
                           stages=e.report.to_dict()["stages"] if e.report else [])
                await pool.release(worker, healthy=False)
                worker = await pool.acquire()
            results.append(rec)
            _append(out, rec)
            restore = next((s["seconds"] for s in rec.get("stages", []) if s["name"] == "restore"), None)
            log(f"{method} #{i}: total {rec['total_seconds']:.2f}s restore {restore if restore is None else f'{restore:.2f}'}s ok={rec['ok']}"
                + ("" if rec.get("ok") else f" error={rec.get('error')}"))
    finally:
        for ev in pool.events:
            log(f"pool event: {json.dumps(ev)}")
        await pool.close()
    return results


def _append(out: Path, rec: dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


_COST_METHOD = {"revert": "revert", "fork": "from_snapshot", "cold": "rebuild", "local": "local"}


def summarize(rows: list[dict[str, Any]], *, plan: str = "starter", vm_size: str = "2x4") -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["method"], []).append(r)
    out: dict[str, Any] = {}
    vcpu, mem = (int(x) for x in vm_size.split("x"))
    for method, rs in by.items():
        ok = [r["total_seconds"] for r in rs if r.get("ok")]
        restore = [s["seconds"] for r in rs if r.get("ok") for s in r.get("stages", []) if s["name"] == "restore"]
        p50 = percentile(ok, 0.5) if ok else None
        out[method] = {
            "n": len(rs), "failures": sum(1 for r in rs if not r.get("ok")),
            "failure_rate": round(sum(1 for r in rs if not r.get("ok")) / len(rs), 4) if rs else None,
            "p50": p50, "p95": percentile(ok, 0.95) if ok else None, "p99": percentile(ok, 0.99) if ok else None,
            "restore_p50": percentile(restore, 0.5) if restore else None,
            "mean": statistics.fmean(ok) if ok else None,
            "state_restored": {"revert": "RAM + disk + windows", "fork": "disk + DBs (new machine id; RAM/process survival unverified)",
                               "local": "disk + DB (no RAM, no window layout)", "cold": "rebuilt from scratch"}.get(method, "?"),
            "cost_per_1k_resets_usd": (round(cost_per_1k_resets(_COST_METHOD.get(method, "revert"), plan, p50, vm_size=(vcpu, mem)), 3)
                                       if p50 else None),
            "backend": rs[0].get("backend") if rs else None,
        }
    return out


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for p in paths:
        if p.exists():
            rows.extend(json.loads(l) for l in p.read_text().splitlines() if l.strip())
    return rows


def format_summary(summary: dict[str, Any]) -> str:
    lines = ["| method | n | fail | p50 s | p95 s | p99 s | restore p50 s | $/1k resets | state restored |", "|---|---|---|---|---|---|---|---|---|"]
    for m, s in summary.items():
        f = lambda v: "n/a" if v is None else f"{v:.2f}"
        lines.append(f"| {m} | {s['n']} | {s['failure_rate']} | {f(s['p50'])} | {f(s['p95'])} | {f(s['p99'])} | {f(s['restore_p50'])} | {s['cost_per_1k_resets_usd']} | {s['state_restored']} |")
    return "\n".join(lines)


async def amain(args: argparse.Namespace) -> int:
    world = load_world(args.world)
    if args.backend == "fake":
        from ..backends.fake import FakeBackend

        backend: Backend = FakeBackend(concurrency_cap=2, gui_factory=world.gui_factory(),
                                       latency={"revert": args.fake_latency, "create": args.fake_latency * 3})
        print("NOTE: fake backend — these numbers measure the local simulator, not Solari.")
    else:
        from ..backends.solari import SolariBackend

        backend = SolariBackend()
    out = Path(args.out)
    for method in args.methods:
        await run_method(backend, world, method=method, trials=args.trials, golden=args.golden, out=out,
                         family=args.family or (world.config.families[0] if world.config.families else ""),
                         plan=args.plan, vm_size=args.vm_size, fallback_to_fork=not args.no_fallback)
    await backend.close()
    summary = summarize(load_rows([out]), plan=args.plan, vm_size=args.vm_size)
    print(format_summary(summary))
    Path(args.summary).write_text(json.dumps(summary, indent=2))
    chart = to_chart2_json(summary, backend=backend.name, title=args.title)
    Path(args.summary).with_name(Path(args.summary).stem + "_chart2.json").write_text(json.dumps(chart, indent=2))
    return 0


_LABELS = {"revert": "revert() to golden snapshot", "fork": "create(from_snapshot) fork", "cold": "fresh VM + full world build",
           "local": "local docker-compose full-state restore"}


def to_chart2_json(summary: dict[str, Any], *, backend: str = "solari", title: Optional[str] = None) -> dict[str, Any]:
    """The input format of ``train/plot.py chart2``."""
    methods = []
    for m, s in summary.items():
        methods.append({"name": _LABELS.get(m, m), "n": s["n"], "p50_s": s["p50"], "p95_s": s["p95"], "p99_s": s["p99"],
                        "failure_rate": s["failure_rate"], "cost_per_1k_usd": s["cost_per_1k_resets_usd"],
                        "state_restored": s["state_restored"]})
    return {"title": title or f"Reset latency by method ({backend})", "synthetic": backend == "fake", "methods": methods,
            "note": "fake backend numbers are simulator numbers" if backend == "fake" else ""}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Forkloop reset benchmark (Chart 2)")
    ap.add_argument("--world", default="claims-ops-v1")
    ap.add_argument("--backend", choices=["solari", "fake"], default="solari")
    ap.add_argument("--methods", nargs="+", default=["revert", "fork"], choices=["revert", "fork", "cold"])
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--golden", default=None, help="golden snapshot id (defaults to the world's env var)")
    ap.add_argument("--family", default=None)
    ap.add_argument("--out", default="bench/reset_results.jsonl")
    ap.add_argument("--summary", default="bench/reset_summary.json")
    ap.add_argument("--plan", default="starter")
    ap.add_argument("--vm-size", default="2x4", help="vcpu x memGB, e.g. 2x4")
    ap.add_argument("--fake-latency", type=float, default=0.0)
    ap.add_argument("--title", default=None)
    ap.add_argument("--no-fallback", action="store_true",
                    help="a refused revert() is a failed trial, not a silent switch of the pool to fork mode")
    args = ap.parse_args(argv)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
