"""Evaluate a policy on held-out seeds through the Env API (contracts.md §11).

For every (family, seed, repeat) the script builds an env via ``forkloop.make``,
runs the policy until ``terminated``/``truncated``, calls ``env.verify()`` and
records the Verdict. Results go to ``<out-dir>/<tag>/episodes.jsonl`` (one line
per episode, appended as they finish) and ``eval_summary.json`` (success rate
with a Wilson 95% CI, per-family / per-repeat breakdowns, invalid-action rate,
tokens per step).

Usage::

    # student behind vLLM, 3 repeats per seed, 8 episodes in flight
    python -m train.eval --world claims-ops-v1 --families resolve_denial,update_insurance_reconcile \\
        --split heldout_seeds --n-episodes 50 --n-seeds 3 --concurrency 8 \\
        --policy student --base-url http://localhost:8000/v1 --model microsoft/Fara1.5-4B \\
        --prompt-style fara --tag fara_base --backend solari

    # same with best-of-4 search
    python -m train.eval ... --best-of 4 --tag fara_base_bo4

    # local smoke with the scripted policy on the fake backend
    python -m train.eval --policy scripted --backend fake --n-episodes 4 --n-seeds 1 --tag scripted_smoke

Seeds: ``--seeds 100000:100050`` (half-open range), ``--seeds 1,2,3``, or
``--n-episodes N`` starting at the split's first seed (train 0, heldout_seeds
100000, heldout_compositions 200000 — contracts.md §9).

Interface assumptions beyond the contract (see README): backends are
``forkloop.backends.fake.FakeBackend()`` / ``forkloop.backends.solari.SolariBackend()``
(or ``forkloop.backends.make_backend(name)`` if present); one shared
``forkloop.pool.WorkerPool(backend, world, size=concurrency)`` and a
``forkloop.trajectories.Recorder(root, run_id)`` are built when possible (both
optional — ``Env`` owns its own pool otherwise); ``env.step(action, meta=...)``
receives the policy meta; ``forkloop.search.best_of_n(env, policy, n, seed, family=)``
returns the winning Verdict. ``--env-factory module:callable`` bypasses all of
that: the callable receives the ``EvalConfig`` and returns an async
``factory(family) -> env``.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from train.wilson import wilson_summary  # noqa: E402

SPLIT_SEED_START = {"train": 0, "heldout_seeds": 100000, "heldout_compositions": 200000}


@dataclass
class EvalConfig:
    world: str = "claims-ops-v1"
    families: list[str] = field(default_factory=lambda: ["resolve_denial"])
    split: str = "heldout_seeds"
    seeds: list[int] = field(default_factory=list)
    n_seeds: int = 3
    best_of: int = 1
    concurrency: int = 4
    max_steps: int | None = None
    out_dir: str = "evals"
    tag: str = "untagged"
    backend: str = "fake"
    run_dir: str | None = None
    policy: dict = field(default_factory=dict)
    checkpoint: str | None = None
    extra: dict = field(default_factory=dict)


def parse_seeds(spec: str | None, split: str, n_episodes: int, seed_start: int | None = None) -> list[int]:
    """``"a:b"`` half-open range, ``"a-b"`` inclusive, ``"1,2,3"`` list, or ``n_episodes`` from the split start."""
    if spec:
        spec = spec.strip()
        if ":" in spec:
            a, b = spec.split(":", 1)
            return list(range(int(a), int(b)))
        if "," in spec:
            return [int(x) for x in spec.split(",") if x.strip()]
        if "-" in spec and not spec.startswith("-"):
            a, b = spec.split("-", 1)
            return list(range(int(a), int(b) + 1))
        return [int(spec)]
    start = seed_start if seed_start is not None else SPLIT_SEED_START.get(split, 0)
    return list(range(start, start + int(n_episodes)))


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _verdict_fields(verdict: Any) -> dict:
    reward = _get(verdict, "reward", 0.0)
    try:
        reward = float(reward)
    except (TypeError, ValueError):
        reward = 0.0
    return {
        "reward": reward,
        "success": reward >= 1.0,
        "milestones": _get(verdict, "milestones", None),
        "reason_code": _get(verdict, "reason_code", None),
        "failed": list(_get(verdict, "failed", None) or []),
    }


# --------------------------------------------------------------------------- #
# Episode loop
# --------------------------------------------------------------------------- #

async def run_episode(env: Any, policy: Any, seed: int, cfg: EvalConfig, *, family: str, repeat: int) -> dict:
    t0 = time.perf_counter()
    result: dict[str, Any] = {
        "task_id": f"{family}-{cfg.split}-{seed:06d}", "family": family, "seed": seed, "split": cfg.split,
        "repeat": repeat, "best_of": cfg.best_of, "steps": 0, "invalid_steps": 0,
        "tokens_in": 0, "tokens_out": 0, "latency_s_total": 0.0, "error": None,
    }
    if cfg.best_of > 1:
        from forkloop.search import best_of_n  # written by the search author (assumed signature)

        verdict = await best_of_n(env, policy, cfg.best_of, seed, family=family)
        result.update(_verdict_fields(verdict))
        steps = _get(verdict, "steps", None)
        if steps is not None:
            result["steps"] = int(steps)
        result["wall_s"] = time.perf_counter() - t0
        return result

    obs, info = await env.reset(seed=seed)
    tid = _get(info, "task_id", None)
    if isinstance(tid, str) and tid:
        result["task_id"] = tid
    while True:
        action, meta = await policy.act(obs)
        meta = meta or {}
        result["tokens_in"] += int((meta.get("tokens") or {}).get("in", 0) or 0)
        result["tokens_out"] += int((meta.get("tokens") or {}).get("out", 0) or 0)
        result["latency_s_total"] += float(meta.get("model_latency_s", 0.0) or 0.0)
        if action is None:
            result["invalid_steps"] += 1
            raw = meta.get("raw_action")
            step_input: Any = raw if isinstance(raw, str) and raw.strip() else ""
        else:
            step_input = action
        step_meta = {k: v for k, v in meta.items() if k in ("raw_action", "model_latency_s", "tokens", "note", "confidence")}
        if step_meta.get("note"):
            step_meta.setdefault("policy_note", step_meta["note"])
        try:
            obs, reward, terminated, truncated, info = await env.step(step_input, meta=step_meta)
        except TypeError:  # an env whose step() has no meta kwarg
            obs, reward, terminated, truncated, info = await env.step(step_input)
        result["steps"] += 1
        if terminated or truncated:
            break
        if cfg.max_steps is not None and result["steps"] >= cfg.max_steps:
            break
    verdict = await env.verify()
    result.update(_verdict_fields(verdict))
    result["wall_s"] = time.perf_counter() - t0
    return result


async def run_eval(
    cfg: EvalConfig,
    env_factory: Callable[[str], Awaitable[Any]],
    policy_factory: Callable[[int], Any],
    *,
    on_result: Callable[[dict], None] | None = None,
) -> dict:
    """Run every (family, seed, repeat) with bounded concurrency; return the summary."""
    sem = asyncio.Semaphore(max(1, cfg.concurrency))
    policies: dict[int, Any] = {}
    results: list[dict] = []
    t_start = time.perf_counter()

    def policy_for(repeat: int) -> Any:
        if repeat not in policies:
            policies[repeat] = policy_factory(repeat)
        return policies[repeat]

    async def one(family: str, seed: int, repeat: int) -> None:
        async with sem:
            env = None
            try:
                env = await env_factory(family)
                res = await run_episode(env, policy_for(repeat), seed, cfg, family=family, repeat=repeat)
            except Exception as e:  # keep the sweep going; the episode counts as a failure
                res = {
                    "task_id": f"{family}-{cfg.split}-{seed:06d}", "family": family, "seed": seed, "split": cfg.split,
                    "repeat": repeat, "best_of": cfg.best_of, "reward": 0.0, "success": False, "milestones": None,
                    "reason_code": "EVAL_ERROR", "failed": [], "steps": 0, "invalid_steps": 0, "tokens_in": 0,
                    "tokens_out": 0, "latency_s_total": 0.0, "wall_s": 0.0,
                    "error": f"{type(e).__name__}: {e}",
                }
            finally:
                if env is not None and hasattr(env, "close"):
                    try:
                        await env.close()
                    except Exception:
                        pass
            results.append(res)
            if on_result is not None:
                on_result(res)

    # Order: repeat-major so a partial run still covers every task once.
    for repeat in range(max(1, cfg.n_seeds)):
        policy_for(repeat)
    tasks = [one(f, s, r) for r in range(max(1, cfg.n_seeds)) for f in cfg.families for s in cfg.seeds]
    await asyncio.gather(*tasks)
    for p in policies.values():
        if hasattr(p, "aclose"):
            try:
                await p.aclose()
            except Exception:
                pass
    summary = summarize(results, cfg)
    summary["wall_s"] = time.perf_counter() - t_start
    return summary


def summarize(results: list[dict], cfg: EvalConfig) -> dict:
    n = len(results)
    k = sum(1 for r in results if r.get("success"))
    steps_all = [int(r.get("steps", 0)) for r in results if r.get("steps")]
    steps_ok = [int(r.get("steps", 0)) for r in results if r.get("success") and r.get("steps")]
    total_steps = sum(int(r.get("steps", 0)) for r in results)
    invalid = sum(int(r.get("invalid_steps", 0)) for r in results)
    tokens_in = sum(int(r.get("tokens_in", 0)) for r in results)
    tokens_out = sum(int(r.get("tokens_out", 0)) for r in results)
    latency = sum(float(r.get("latency_s_total", 0.0)) for r in results)
    per_family: dict[str, dict] = {}
    for fam in sorted({str(r.get("family")) for r in results}):
        rs = [r for r in results if str(r.get("family")) == fam]
        per_family[fam] = wilson_summary(sum(1 for r in rs if r.get("success")), len(rs))
    per_repeat: dict[str, dict] = {}
    for rep in sorted({int(r.get("repeat", 0)) for r in results}):
        rs = [r for r in results if int(r.get("repeat", 0)) == rep]
        per_repeat[str(rep)] = wilson_summary(sum(1 for r in rs if r.get("success")), len(rs))
    milestones = [float(r["milestones"]) for r in results if isinstance(r.get("milestones"), (int, float))]
    summary = {
        "tag": cfg.tag, "world": cfg.world, "families": list(cfg.families), "split": cfg.split,
        "n_tasks": len(cfg.families) * len(cfg.seeds), "n_seeds": cfg.n_seeds, "best_of": cfg.best_of,
        "n_episodes": n, "n_success": k, "n_errors": sum(1 for r in results if r.get("error")),
        "success_rate": (k / n) if n else 0.0,
        "success": wilson_summary(k, n),
        "milestone_score": (sum(milestones) / len(milestones)) if milestones else None,
        "median_steps": statistics.median(steps_all) if steps_all else None,
        "median_steps_success": statistics.median(steps_ok) if steps_ok else None,
        "invalid_action_rate": (invalid / total_steps) if total_steps else 0.0,
        "invalid_action": wilson_summary(invalid, total_steps),
        "action_format_validity_rate": (1.0 - invalid / total_steps) if total_steps else None,
        "tokens_per_step": ((tokens_in + tokens_out) / total_steps) if total_steps else None,
        "tokens_in_per_step": (tokens_in / total_steps) if total_steps else None,
        "tokens_out_per_step": (tokens_out / total_steps) if total_steps else None,
        "model_latency_s_per_step": (latency / total_steps) if total_steps else None,
        "reason_codes": dict(Counter(str(r.get("reason_code")) for r in results)),
        "per_family": per_family, "per_repeat": per_repeat,
        "policy": cfg.policy, "checkpoint": cfg.checkpoint, "backend": cfg.backend, "config": asdict(cfg),
    }
    return summary


# --------------------------------------------------------------------------- #
# Default env / policy factories (assumptions documented in the module docstring)
# --------------------------------------------------------------------------- #

def _load_dotted(spec: str) -> Any:
    if ":" in spec:
        mod, name = spec.split(":", 1)
    else:
        mod, name = spec.rsplit(".", 1)
    return getattr(importlib.import_module(mod), name)


def _make_backend(name: str, cfg: EvalConfig, world: Any = None) -> Any:
    """``FakeBackend`` (with the world's GUI simulator when it has one) or ``SolariBackend``."""
    mod = importlib.import_module("forkloop.backends")
    if hasattr(mod, "make_backend"):
        return mod.make_backend(name)
    cls_name = {"fake": "FakeBackend", "solari": "SolariBackend"}.get(name, name)
    cls = getattr(mod, cls_name, None)
    if cls is None:
        sub = importlib.import_module(f"forkloop.backends.{name}")
        cls = getattr(sub, cls_name)
    kwargs: dict[str, Any] = {"concurrency_cap": max(1, cfg.concurrency)}
    if name == "fake" and world is not None and hasattr(world, "gui_factory"):
        kwargs["gui_factory"] = world.gui_factory()
    for kw in (kwargs, {"concurrency_cap": max(1, cfg.concurrency)}, {}):
        try:
            return cls(**kw)
        except TypeError:
            continue
    return cls()


def _load_world(name: str) -> Any:
    for modname in ("forkloop.world", "forkloop.env"):
        try:
            mod = importlib.import_module(modname)
        except ImportError:
            continue
        fn = getattr(mod, "load_world", None)
        if fn is not None:
            return fn(name)
    raise ImportError("forkloop.world.load_world not found")


def _make_pool(backend: Any, cfg: EvalConfig, world: Any) -> Any:
    """One ``forkloop.pool.WorkerPool(backend, world, size=concurrency)`` shared by every episode env.

    Returns None when the pool cannot be built; ``Env`` then creates and owns its own pool.
    """
    try:
        mod = importlib.import_module("forkloop.pool")
        cls = getattr(mod, "WorkerPool", None) or getattr(mod, "Pool", None)
        if cls is None or world is None:
            return None
    except Exception as e:
        print(f"[eval] shared pool unavailable ({type(e).__name__}: {e}); each env will own a pool")
        return None
    for kwargs in ({"size": cfg.concurrency}, {}):
        try:
            return cls(backend, world, **kwargs)
        except TypeError:
            continue
    return None


def _make_recorder(cfg: EvalConfig) -> Any:
    """``forkloop.trajectories.Recorder(root=runs/, run_id=<run_id>)`` from ``--run-dir runs/<run_id>``."""
    if not cfg.run_dir:
        return None
    try:
        mod = importlib.import_module("forkloop.trajectories")
    except ImportError:
        return None
    cls = getattr(mod, "Recorder", None) or getattr(mod, "TrajectoryRecorder", None)
    if cls is None:
        return None
    run_dir = Path(cfg.run_dir)
    for attempt in (lambda: cls(root=run_dir.parent, run_id=run_dir.name), lambda: cls(run_dir.parent, run_dir.name),
                    lambda: cls(run_dir)):
        try:
            return attempt()
        except TypeError:
            continue
    return None


class DefaultEnvFactory:
    """``await factory(family)`` -> env built with ``forkloop.make`` sharing one backend/pool/recorder."""

    def __init__(self, cfg: EvalConfig) -> None:
        self.cfg = cfg
        self._world: Any = None
        self._backend: Any = None
        self._pool: Any = None
        self._recorder: Any = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> None:
        async with self._lock:
            if self._backend is None:
                try:
                    self._world = _load_world(self.cfg.world)
                except Exception as e:
                    print(f"[eval] load_world failed ({type(e).__name__}: {e}); passing the world name to make()")
                    self._world = None
                self._backend = _make_backend(self.cfg.backend, self.cfg, self._world)
                self._pool = _make_pool(self._backend, self.cfg, self._world)
                if self._pool is not None and hasattr(self._pool, "start"):
                    # Warm the workers serially so the golden snapshot is built once before
                    # concurrent episodes start acquiring machines.
                    try:
                        await self._pool.start(warm=True)
                    except TypeError:
                        await self._pool.start()
                    except Exception as e:
                        print(f"[eval] pool.start failed ({type(e).__name__}: {e}); continuing without warm-up")
                self._recorder = _make_recorder(self.cfg)

    async def __call__(self, family: str) -> Any:
        await self._ensure()
        import forkloop

        kwargs: dict[str, Any] = {"backend": self._backend, "family": family, "split": self.cfg.split}
        if self._pool is not None:
            kwargs["pool"] = self._pool
        if self._recorder is not None:
            kwargs["recorder"] = self._recorder
        kwargs.update(self.cfg.extra.get("env_kwargs") or {})
        env = forkloop.make(self._world if self._world is not None else self.cfg.world, **kwargs)
        if asyncio.iscoroutine(env):
            env = await env
        return env

    async def close(self) -> None:
        for obj in (self._pool, self._backend):
            if obj is not None and hasattr(obj, "close"):
                try:
                    r = obj.close()
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass


def make_policy_factory(args: argparse.Namespace) -> tuple[Callable[[int], Any], dict]:
    """Return ``(factory(repeat) -> policy, description)``."""
    if args.policy == "student":
        from forkloop.policies.student import StudentPolicy

        def factory(repeat: int) -> Any:
            return StudentPolicy(
                args.base_url, args.model, args.api_key, image_max_side=args.image_max_side,
                temperature=args.temperature, max_tokens=args.max_tokens, prompt_style=args.prompt_style,
                history_k=args.history_k, coord_space=args.coord_space, timeout_s=args.timeout_s,
                seed=args.sampling_seed_base + repeat,
            )

        desc = factory(0).describe()
        return factory, desc
    if args.policy == "scripted":
        cls = _load_dotted("forkloop.policies.scripted:ScriptedPolicy")
    else:
        cls = _load_dotted(args.policy)

    def factory(repeat: int) -> Any:
        attempts = [lambda: cls(world=args.world, split=args.split, seed=repeat), lambda: cls(world=args.world),
                    lambda: cls()]
        if args.policy == "scripted":
            # ScriptedPolicy(actions) replays a fixed list; with none it just emits done() — a
            # plumbing smoke test of the env loop, not a real agent.
            attempts.append(lambda: cls([]))
        last: Exception | None = None
        for attempt in attempts:
            try:
                return attempt()
            except TypeError as e:
                last = e
        raise TypeError(f"cannot construct policy {args.policy}: {last}")

    return factory, {"policy": args.policy, "class": f"{cls.__module__}.{cls.__qualname__}"}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="train.eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--world", default="claims-ops-v1")
    p.add_argument("--families", default="resolve_denial", help="comma-separated task families")
    p.add_argument("--split", default="heldout_seeds", choices=["train", "heldout_seeds", "heldout_compositions"])
    p.add_argument("--seeds", default=None, help="'a:b' half-open, 'a-b' inclusive, or 'a,b,c'")
    p.add_argument("--n-episodes", type=int, default=20, help="number of seeds when --seeds is not given")
    p.add_argument("--seed-start", type=int, default=None, help="first seed when --seeds is not given")
    p.add_argument("--n-seeds", type=int, default=3, help="repeats per task with different sampling seeds")
    p.add_argument("--sampling-seed-base", type=int, default=1000)
    p.add_argument("--best-of", type=int, default=1, help="best-of-N search via forkloop.search.best_of_n")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--max-steps", type=int, default=None, help="client-side step cap (the env has its own budget)")
    p.add_argument("--backend", default="fake", help="fake | solari")
    p.add_argument("--env-factory", default=None, help="module:callable(cfg) -> async factory(family) -> env")
    p.add_argument("--run-dir", default=None, help="recorder directory (runs/<run_id>) for trajectories")
    p.add_argument("--out-dir", default="evals")
    p.add_argument("--tag", default=None, help="checkpoint tag; default = policy name + timestamp")
    p.add_argument("--checkpoint", default=None, help="free-text checkpoint identifier recorded in the summary")
    p.add_argument("--settle-s", type=float, default=None, help="override the env's screenshot settle time (Env(settle_s=))")
    # policy
    p.add_argument("--policy", default="student", help="student | scripted | module:Class")
    p.add_argument("--base-url", default="http://localhost:8000/v1")
    p.add_argument("--model", default="microsoft/Fara1.5-4B")
    p.add_argument("--api-key", default=None)
    p.add_argument("--prompt-style", default="compact", choices=["compact", "json", "fara"])
    p.add_argument("--coord-space", default="auto", choices=["auto", "image", "screen", "norm1000", "norm999"])
    p.add_argument("--image-max-side", type=int, default=1280)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--history-k", type=int, default=8)
    p.add_argument("--timeout-s", type=float, default=120.0)
    return p


def config_from_args(args: argparse.Namespace) -> EvalConfig:
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    seeds = parse_seeds(args.seeds, args.split, args.n_episodes, args.seed_start)
    tag = args.tag or f"{args.policy}_{time.strftime('%Y%m%d_%H%M%S')}"
    extra: dict[str, Any] = {}
    if getattr(args, "settle_s", None) is not None:
        extra["env_kwargs"] = {"settle_s": args.settle_s}
    return EvalConfig(
        world=args.world, families=families, split=args.split, seeds=seeds, n_seeds=args.n_seeds,
        best_of=args.best_of, concurrency=args.concurrency, max_steps=args.max_steps, out_dir=args.out_dir,
        tag=tag, backend=args.backend, run_dir=args.run_dir, checkpoint=args.checkpoint, extra=extra,
    )


async def _amain(args: argparse.Namespace) -> dict:
    cfg = config_from_args(args)
    policy_factory, desc = make_policy_factory(args)
    cfg.policy = desc
    out = Path(cfg.out_dir) / cfg.tag
    out.mkdir(parents=True, exist_ok=True)
    episodes_path = out / "episodes.jsonl"
    episodes_path.write_text("", encoding="utf-8")

    def on_result(res: dict) -> None:
        with episodes_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
        status = "OK " if res.get("success") else "FAIL"
        print(f"[eval] {status} {res.get('task_id')} r{res.get('repeat')} steps={res.get('steps')} "
              f"reason={res.get('reason_code')}" + (f" error={res['error']}" if res.get("error") else ""))

    if args.env_factory:
        env_factory = _load_dotted(args.env_factory)(cfg)
        closer = None
    else:
        env_factory = DefaultEnvFactory(cfg)
        closer = env_factory
    try:
        summary = await run_eval(cfg, env_factory, policy_factory, on_result=on_result)
    finally:
        if closer is not None:
            await closer.close()
    if cfg.run_dir:
        try:
            from forkloop.metrics import summarize_run

            summary["run_metrics"] = summarize_run(Path(cfg.run_dir))
        except Exception as e:  # metrics module optional at this stage
            summary["run_metrics_error"] = f"{type(e).__name__}: {e}"
    (out / "eval_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    s = summary["success"]
    print(f"[eval] {cfg.tag}: success {100*s['rate']:.1f}% [{100*s['low']:.1f}, {100*s['high']:.1f}] "
          f"(k={s['k']}/{s['n']}), invalid-action rate {100*summary['invalid_action_rate']:.1f}%, "
          f"wrote {out / 'eval_summary.json'}")
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    asyncio.run(_amain(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
