"""Matched best-of-one policy evaluation; episode evidence uses the normal Recorder.

Factories receive no arguments and must return a fresh screenshot-only policy.
Identity metadata is caller-attested, not remote model attestation. This runner owns
its worker pool, but the caller owns the backend and its credentials. Existing
output directories are never resumed, merged or overwritten by execution.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence
from urllib.parse import urlsplit, urlunsplit

from .backends.base import Backend
from .env import BACKEND_FAILURE_PREFIX, Env, act_with_deadline
from .metrics import episode_tokens, failure_codes
from .policies.base import Policy
from .pool import WorkerPool
from .trajectories import Recorder, load_episode
from .world import World, load_world

SCHEMA = "forkloop.comparison.v1"
ARMS = ("A", "B")
TASK_KEYS = ("world", "family", "split", "seed", "instruction", "initial_screen", "seeding", "oracle", "budget", "expected")
LIMITS = [
    "Best-of-one, sequential matched seeds; no policy retries or selection of successful attempts.",
    "Identity is caller-attested. Factories and remote endpoints must actually serve the declared policy/version/options.",
    "Reset equivalence covers configured baseline tables, watermarks and preserved rows, not all application or VM state.",
    "Pixel equality is diagnostic, not required for task-state equivalence. Screenshots may differ in clocks or rendering.",
    "Usage is recorded policy token accounting, not an invoice. Setup, cleanup, failed calls and idle resource costs may be unpriced.",
    "Observed differences on these seeds do not establish statistical reliability or justify deployment. Synthetic data only.",
]


@dataclass(frozen=True)
class PolicyVariant:
    label: str
    identity: dict[str, Any]
    factory: Callable[[], Policy | Awaitable[Policy]]


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic(path: Path, value: Any, *, text: bool = False) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(value if text else json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


def _safe_metadata(value: Any, key: str = "identity") -> None:
    """Reject credentials rather than saving a misleading redacted identity."""
    if isinstance(value, dict):
        for name, item in value.items():
            if not isinstance(name, str):
                raise ValueError("identity keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", name.lower())
            if any(word in normalized for word in ("apikey", "password", "secret", "authorization", "credential", "accesstoken", "authtoken")) or normalized in ("token", "headers", "cookies"):
                raise ValueError(f"credentials must not be saved in {key}")
            _safe_metadata(item, name)
    elif isinstance(value, list):
        for item in value:
            _safe_metadata(item, key)
    elif isinstance(value, str):
        if re.search(r"\b(?:sk-|slr_live_|Bearer\s+)\S+", value, re.I):
            raise ValueError(f"credentials must not be saved in {key}")
        for raw in re.findall(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+", value):
            url = urlsplit(raw)
            if url.username is not None or url.password is not None or url.query or url.fragment:
                raise ValueError(f"saved endpoint in {key} must have no userinfo, query or fragment")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise ValueError("identity must contain only JSON values")


def _identity(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k].strip() for k in ("policy", "version")):
        raise ValueError("identity requires nonempty policy and version strings")
    if not isinstance(value.get("options"), dict):
        raise ValueError("identity requires an options object")
    _safe_metadata(value)
    return json.loads(_json(value))


def _error(exc: BaseException) -> str:
    # Exception messages can include request URLs; never retain URL credentials.
    def clean(match: re.Match[str]) -> str:
        try:
            url = urlsplit(match.group())
            return urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
        except ValueError:
            return "[invalid URL omitted]"
    message = re.sub(r"https?://[^\s\"'<>]+", clean, str(exc))
    message = re.sub(r"(?i)\b(?:(?:sk-|slr_live_)[\w-]+|Bearer\s+\S+)", "[credential omitted]", message)
    return f"{type(exc).__name__}: {message[:2000]}"


def _task_digest(manifest: dict[str, Any]) -> str:
    return _digest({key: manifest[key] for key in TASK_KEYS})


def _baseline(env: Env) -> dict[str, Any] | None:
    if env.ep is None or env.ep.baseline is None:
        return None
    raw = env.ep.baseline.to_dict()
    return {"tables": {name: _digest(table) for name, table in raw["tables"].items()},
            "watermarks": raw["watermarks"], "ignore_columns": raw["ignore_columns"],
            "preserved_rows_sha256": _digest(raw["preserved_rows"])}


async def run_comparison(world: World | str, backend: Backend, variants: Sequence[PolicyVariant],
                         seeds: Sequence[int], *, output: str | Path, family: str | None = None,
                         split: str = "test", budget_override: dict[str, Any] | None = None,
                         reset_mode: str = "revert", history_k: int = 8,
                         settle_s: float = 0.6, max_invalid: int = 10) -> dict[str, Any]:
    """Run every planned cell once; persist plan before constructing/allocating a pool.

    Factory/policy errors retain an unscored cell and continue. Cancellation
    retains the interrupted attempt, leaves remaining cells missing and propagates.
    Reset or cleanup errors stop: a failed release may leave the pool unusable.
    """
    world = load_world(world) if isinstance(world, str) else world
    variants, seeds = tuple(variants), tuple(seeds)
    if len(variants) != 2 or len({v.label for v in variants}) != 2:
        raise ValueError("comparison requires exactly two uniquely labeled variants")
    if any(not isinstance(v.label, str) or not v.label.strip() or not callable(v.factory) for v in variants):
        raise ValueError("each variant requires a label and callable factory")
    if not seeds or any(type(seed) is not int for seed in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be a nonempty sequence of unique integers")
    family = family or (world.config.families[0] if world.config.families else None)
    if family not in world.config.families or not isinstance(split, str) or not split:
        raise ValueError("a supported family and nonempty split are required")
    budget = dict(budget_override or {})
    if set(budget) - {"max_steps", "max_seconds"}:
        raise ValueError("budget supports only max_steps and max_seconds")
    for key, value in budget.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 or (key == "max_steps" and type(value) is not int):
            raise ValueError(f"invalid budget {key}")
    if reset_mode not in ("revert", "fork") or type(history_k) is not int or history_k < 0 or type(max_invalid) is not int or max_invalid < 1:
        raise ValueError("invalid reset mode, history or invalid-action limit")
    if isinstance(settle_s, bool) or not isinstance(settle_s, (int, float)) or not math.isfinite(settle_s) or settle_s < 0:
        raise ValueError("settle_s must be finite and nonnegative")
    identities = {arm: _identity(v.identity) for arm, v in zip(ARMS, variants)}
    labels = {arm: v.label for arm, v in zip(ARMS, variants)}
    _safe_metadata(labels)
    protocol = {"schema": SCHEMA, "created_at": _now(), "world": world.name,
                "world_version": world.config.version, "backend": backend.name,
                "evidence_kind": "constructed_control" if backend.name == "fake" else "live_policy_evaluation",
                "family": family, "split": split, "seeds": list(seeds), "variants": identities,
                "labels": labels, "budget_override": budget, "reset_mode": reset_mode,
                "history_k": history_k, "settle_s": settle_s, "max_invalid": max_invalid,
                "best_of": 1, "concurrency": 1, "attempts_per_cell": 1,
                "identity_basis": "caller-attested", "limits": LIMITS,
                "plan": [{"arm": arm, "seed": seed, "id": f"{arm}-{index:06d}"}
                         for index, seed in enumerate(seeds)
                         for arm in (ARMS if index % 2 == 0 else tuple(reversed(ARMS)))]}
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    _atomic(root / "protocol.json", protocol)
    (root / "cells").mkdir()
    protocol_hash = _digest(protocol)
    state: dict[str, Any] = {"status": "running", "started_at": _now(), "protocol_sha256": protocol_hash, "events": []}
    _atomic(root / "execution.json", state)
    pool = None
    try:
        recorders = {arm: Recorder(root / "runs", run_id=arm, meta={
            **{k: protocol[k] for k in ("world", "backend", "family", "split", "seeds", "budget_override", "best_of", "concurrency", "reset_mode", "evidence_kind")},
            "policy": identities[arm]["policy"], "policy_identity": identities[arm], "protocol_sha256": protocol_hash}) for arm in ARMS}
        pool = WorkerPool(backend, world, size=1, mode=reset_mode, max_retries=1,
                          fallback_to_fork=False, reap_orphans_enabled=False)
        for planned in protocol["plan"]:
            arm, seed = planned["arm"], planned["seed"]
            cell = {**planned, "status": "started", "started_at": _now(), "protocol_sha256": protocol_hash,
                    "identity_sha256": _digest(identities[arm]), "episode": None}
            cell_path = root / "cells" / f"{planned['id']}.json"
            _atomic(cell_path, cell)
            t0, env, policy, phase = time.monotonic(), None, None, "setup"
            stop = False
            try:
                task = world.generate(family, seed, split)
                cell["task_sha256"] = _task_digest(task.to_dict())
                cell["effective_budget"] = {**task.budget, **budget}
                policy = variants[ARMS.index(arm)].factory()
                if inspect.isawaitable(policy):
                    policy = await policy
                if not callable(getattr(policy, "act", None)):
                    raise TypeError("policy factory must return an object with async act(observation)")
                reset = getattr(policy, "reset", None)
                if callable(reset):
                    result = reset()
                    if inspect.isawaitable(result):
                        await result
                env = Env(world, backend, family=family, split=split, pool=pool, recorder=recorders[arm],
                          budget_override=budget, history_k=history_k, settle_s=settle_s, max_invalid=max_invalid,
                          record_extra={"comparison_cell": planned["id"], "protocol_sha256": protocol_hash,
                                        "policy_identity": identities[arm], "evidence_kind": protocol["evidence_kind"]})
                obs, _ = await env.reset(seed, task=task, episode_id=planned["id"])
                cell.update(episode=f"runs/{arm}/episodes/{planned['id']}", baseline_digest=_baseline(env),
                            initial_observation_sha256=hashlib.sha256(obs.screenshot).hexdigest() if obs.screenshot else None,
                            reset_report=env.last_reset_report, golden_snapshot=pool.golden,
                            machine=env.ep.machine.id if env.ep else None, status="running")
                _atomic(cell_path, cell)
                _atomic(env.ep.recorder.dir / "baseline-digest.json", cell["baseline_digest"])
                if not obs.screenshot:
                    stop = True
                    raise RuntimeError("screenshot-only comparison requires a nonempty initial screenshot")
                phase = "policy"
                while True:
                    call_start = time.monotonic()
                    action, meta = await act_with_deadline(env, policy, obs)
                    meta = dict(meta or {})
                    if meta.get("error"):
                        raise RuntimeError(str(meta.get("note") or meta["error"]))
                    meta.setdefault("model_latency_s", time.monotonic() - call_start)
                    obs, _, terminated, truncated, step_info = await env.step(action, meta=meta)
                    if str(step_info.get("error") or "").startswith(BACKEND_FAILURE_PREFIX):
                        raise RuntimeError(step_info["error"])
                    if terminated or truncated:
                        verdict = await env.verify()
                        cell["status"] = "oracle_error" if verdict.reason_code == "ORACLE_ERROR" else "completed"
                        break
            except BaseException as exc:
                cell.update(status=("setup_error" if phase == "setup" else "execution_error") if isinstance(exc, Exception) else "interrupted", error=_error(exc))
                if env is not None and env.ep is None:
                    stop = True
                if not isinstance(exc, Exception):
                    raise
            finally:
                if env is not None:
                    cell["reset_report"] = env.last_reset_report
                    if env.ep is not None and env.ep.recorder is not None:
                        cell["episode"] = env.ep.recorder.dir.relative_to(root).as_posix()
                    try:
                        await env.close()
                        if cell.get("episode"):
                            from .report_html import html_report
                            episode_dir = _inside(root, cell["episode"])
                            try:
                                _atomic(episode_dir / "report.html", html_report(episode_dir), text=True)
                            except Exception as exc:
                                cell["report_error"] = _error(exc)
                    except Exception as exc:
                        cell["cleanup_error"] = _error(exc)
                        stop = True
                close = getattr(policy, "aclose", None) or getattr(policy, "close", None)
                if callable(close):
                    try:
                        result = close()
                        if inspect.isawaitable(result):
                            await result
                    except Exception as exc:
                        cell["policy_cleanup_error"] = _error(exc)
                        stop = True
                if cell.get("cleanup_error") or cell.get("policy_cleanup_error"):
                    cell["status_before_cleanup"] = cell["status"]
                    cell["status"] = "cleanup_error"
                usage = getattr(policy, "usage", None)
                if isinstance(usage, dict):
                    cell["recorded_policy_usage"] = {
                        key: value for key, value in usage.items()
                        if key in ("in", "out", "cache_read", "cache_write")
                        and type(value) in (int, float) and math.isfinite(value) and value >= 0
                    }
                cell.update(finished_at=_now(), setup_and_episode_seconds=round(time.monotonic() - t0, 3))
                _atomic(cell_path, cell)
                state["events"] = list(pool.events)
                _atomic(root / "execution.json", state)
            if stop:
                state["status"] = "cleanup_error" if cell.get("cleanup_error") or cell.get("policy_cleanup_error") else "setup_error"
                state["stop_cell"] = cell["id"]
                break
        else:
            state["status"] = "finished"
    except BaseException as exc:
        state.update(status="interrupted" if not isinstance(exc, Exception) else "execution_error", error=_error(exc))
        raise
    finally:
        if pool is not None:
            try:
                await pool.close()
            except Exception as exc:
                state.update(status="cleanup_error", cleanup_error=_error(exc))
            state["events"] = list(pool.events)
        state["finished_at"] = _now()
        _atomic(root / "execution.json", state)
        write_comparison_report(root)
    return summarize_comparison(root)


def _inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("evidence path must be relative")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("evidence path escapes comparison directory")
    return resolved


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    # The writer rejects non-finite numbers; readers must not accept them either.
    _json(value)
    return value


def load_comparison(output: str | Path) -> dict[str, Any]:
    """Read offline artifacts, retaining corrupt/missing cells as explicit errors."""
    root = Path(output)
    protocol = _read_object(root / "protocol.json")
    if protocol.get("schema") != SCHEMA:
        raise ValueError("unsupported comparison protocol")
    errors = []
    try:
        execution = _read_object(root / "execution.json")
        if not isinstance(execution.get("status"), str):
            raise ValueError("execution status is missing or invalid")
    except (OSError, ValueError) as exc:
        execution = {"status": "unknown"}
        errors.append(f"execution metadata unavailable: {_error(exc)}")
    cells = []
    for path in sorted((root / "cells").glob("*.json")):
        try:
            cell = _read_object(path)
            if cell.get("id") != path.stem:
                errors.append(f"cell filename {path.name} differs from its recorded id")
            cells.append(cell)
        except (OSError, ValueError) as exc:
            problem = f"unreadable cell {path.name}: {_error(exc)}"
            errors.append(problem)
            cells.append({"id": path.stem, "status": "unreadable", "error": problem})
    return {"root": root, "protocol": protocol, "execution": execution, "cells": cells, "errors": errors}


def reset_equivalence(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    left, right = a.get("baseline_digest"), b.get("baseline_digest")
    baselines = [value if isinstance(value, dict) else {} for value in (left, right)]
    tables_by_arm = [value.get("tables") if isinstance(value.get("tables"), dict) else {} for value in baselines]
    available = all(
        tables and all(isinstance(digest, str) and digest for digest in tables.values())
        and isinstance(value.get("watermarks"), dict)
        and isinstance(value.get("ignore_columns"), dict)
        and isinstance(value.get("preserved_rows_sha256"), str) and value["preserved_rows_sha256"]
        for value, tables in zip(baselines, tables_by_arm)
    )
    tables = sorted(set(tables_by_arm[0]) | set(tables_by_arm[1]))
    differing = [name for name in tables if tables_by_arm[0].get(name) != tables_by_arm[1].get(name)]
    resets = [value if isinstance(value, dict) else {} for value in (a.get("reset_report"), b.get("reset_report"))]
    reasons = []
    if not available:
        reasons.append("baseline fingerprints unavailable, malformed or no configured tables")
    if left != right:
        reasons.append("baseline fingerprints differ")
    if any(reset.get("ok") is not True for reset in resets):
        reasons.append("reset did not record success for both arms")
    if any(reset.get("method") not in ("revert", "fork") for reset in resets):
        reasons.append("actual reset method missing or unsupported")
    elif resets[0]["method"] != resets[1]["method"]:
        reasons.append("actual reset methods differ")
    if not isinstance(a.get("golden_snapshot"), str) or not a["golden_snapshot"] or a["golden_snapshot"] != b.get("golden_snapshot"):
        reasons.append("golden snapshot missing or different")
    return {"equivalent": not reasons, "reasons": reasons, "baseline_tables_compared": len(tables),
            "baseline_tables_differing": differing, "reset_methods": [r.get("method") for r in resets],
            "initial_observation_equal": bool(a.get("initial_observation_sha256")) and a.get("initial_observation_sha256") == b.get("initial_observation_sha256"),
            "scope": LIMITS[2]}


def _configuration_changes(a: dict[str, Any], b: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    changes = []
    for key in sorted(a.keys() | b.keys()):
        if not prefix and key == "configuration_sha256":
            continue
        path = f"{prefix}.{key}" if prefix else key
        left, right = a.get(key), b.get(key)
        if key in a and key in b and isinstance(left, dict) and isinstance(right, dict):
            changes.extend(_configuration_changes(left, right, path))
        elif key not in a or key not in b or _json(left) != _json(right):
            changes.append({"path": path, "A": left if key in a else "[not supplied]",
                            "B": right if key in b else "[not supplied]"})
    return changes


def _nonnegative_number(value: Any) -> bool:
    return type(value) is int and value >= 0 or type(value) is float and math.isfinite(value) and value >= 0


def _validate_episode(episode: dict[str, Any]) -> None:
    """Reject malformed evidence before metrics or renderers consume nested values."""
    manifest, verdict, reset = episode["manifest"], episode["verdict"], episode["reset"]
    if not isinstance(manifest, dict) or not isinstance(manifest.get("budget"), dict):
        raise ValueError("manifest or task budget is not an object")
    oracle = manifest.get("oracle")
    if not isinstance(oracle, dict):
        raise ValueError("oracle specification is not an object")
    for group in ("effects", "invariants"):
        checks = oracle.get(group, [])
        if not isinstance(checks, list) or any(
            not isinstance(check, dict) or not isinstance(check.get("id"), str)
            or not isinstance(check.get("reason_code", "CHECK_FAILED"), str) for check in checks
        ):
            raise ValueError("oracle check specification is malformed")
    if reset is not None and not isinstance(reset, dict):
        raise ValueError("reset evidence is not an object")
    if verdict is not None:
        if not isinstance(verdict, dict) or not isinstance(verdict.get("details"), dict):
            raise ValueError("verdict or check details are not objects")
        if not isinstance(verdict.get("reason_code"), str) or not isinstance(verdict.get("failed"), list):
            raise ValueError("verdict reason or failed checks are malformed")
        if any(not isinstance(key, str) for key in verdict["failed"]):
            raise ValueError("failed check identifiers must be strings")
        if any(not isinstance(detail, dict) for detail in verdict["details"].values()):
            raise ValueError("verdict check detail is not an object")
    for value in (manifest, verdict, reset):
        _json(value)
    for step in episode["steps"]:
        if not isinstance(step, dict):
            raise ValueError("step record is not an object")
        _json(step)
        tokens = step.get("tokens", {})
        if not isinstance(tokens, dict) or any(not _nonnegative_number(value) for value in tokens.values()):
            raise ValueError("step token usage is malformed")


def summarize_comparison(output: str | Path) -> dict[str, Any]:
    data = load_comparison(output)
    root, protocol = data["root"], data["protocol"]
    issues = list(data["errors"])
    protocol_hash = _digest(protocol)
    if data["execution"].get("protocol_sha256") != protocol_hash:
        issues.append("protocol differs from execution identity/settings")
    try:
        identities = {arm: _identity(protocol["variants"][arm]) for arm in ARMS}
    except (KeyError, TypeError, ValueError) as exc:
        identities = {}
        issues.append(f"non-comparable policy identities: {_error(exc)}")
    if any(protocol.get(k) != 1 for k in ("best_of", "concurrency", "attempts_per_cell")):
        issues.append("protocol is not sequential matched best-of-one")
    execution = dict(data["execution"])
    if execution["status"] in ("running", "started"):
        execution.update(recorded_status=execution["status"], status="incomplete",
                         explanation="No terminal execution record was saved. The run may have been interrupted "
                         "or may still be active; offline artifacts cannot establish process or resource liveness. "
                         "Unfinished and unstarted cells are unscored. Historical artifacts are unchanged.")
    seeds = protocol.get("seeds", [])
    if not isinstance(seeds, list) or any(type(seed) is not int for seed in seeds) or len(set(seeds)) != len(seeds):
        issues.append("protocol seeds are not unique integers; recovering only seeds identifiable in the saved plan")
        saved_plan = protocol.get("plan")
        seeds = list(dict.fromkeys(item["seed"] for item in saved_plan
                                 if isinstance(item, dict) and type(item.get("seed")) is int)) if isinstance(saved_plan, list) else []
    expected = [{"arm": arm, "seed": seed, "id": f"{arm}-{i:06d}"} for i, seed in enumerate(seeds)
                for arm in (ARMS if i % 2 == 0 else tuple(reversed(ARMS)))]
    if not seeds or len(set(seeds)) != len(seeds) or protocol.get("plan") != expected:
        issues.append("planned cells do not match unique alternating seeds")
    indexed: dict[str, list[dict[str, Any]]] = {}
    for cell in data["cells"]:
        indexed.setdefault(str(cell.get("id")), []).append(cell)
    expected_ids = {p["id"] for p in expected}
    extras = [cell for cell in data["cells"] if str(cell.get("id")) not in expected_ids]
    if extras:
        issues.append("unplanned attempted cells present")
    cells = []
    for plan in expected:
        matches = indexed.get(plan["id"], [])
        row = {**plan, "status": "missing", "scored": False, "success": None, "issues": [], "evidence": None}
        row["issues"].append("no attempt record found for planned cell; not started or evidence missing")
        if not matches:
            cells.append(row)
            continue
        row.update(matches[0])
        row.update(scored=False, success=None, issues=[], evidence=None, report=None)
        if not isinstance(row.get("status"), str):
            row.update(recorded_status=row.get("status"), status="unreadable")
            row["issues"].append("cell status is missing or malformed")
        if row["status"] in ("started", "running"):
            row.update(recorded_status=row["status"], status="unfinished")
            row["issues"].append("no terminal attempt record; interrupted or still active, never scored from partial evidence")
        if row["status"] not in ("completed", "unfinished", "interrupted", "setup_error", "execution_error", "oracle_error", "cleanup_error", "unreadable"):
            row["issues"].append("unsupported attempt status")
        for key in ("error", "cleanup_error", "policy_cleanup_error"):
            if row.get(key):
                row["issues"].append(f"{key}: attempt has an infrastructure or execution error; unscored")
        if "setup_and_episode_seconds" in row and not _nonnegative_number(row["setup_and_episode_seconds"]):
            row["issues"].append("invalid recorded duration; omitted from totals")
        usage = row.get("recorded_policy_usage")
        if usage is not None and (not isinstance(usage, dict) or any(not _nonnegative_number(value) for value in usage.values())):
            row["issues"].append("invalid recorded token usage; omitted from totals")
        if len(matches) != 1:
            row["issues"].append("duplicate attempted cell; no attempt selected")
        if any(row.get(k) != plan[k] for k in ("arm", "seed", "id")):
            row["issues"].append("cell differs from planned arm/seed")
        if row.get("protocol_sha256") != protocol_hash or not identities or row.get("identity_sha256") != _digest(identities.get(plan["arm"])):
            row["issues"].append("cell identity/settings do not match protocol")
        if row.get("episode"):
            try:
                episode_path = _inside(root, row["episode"])
                for filename in ("manifest.json", "steps.jsonl", "verdict.json", "reset.json", "baseline-digest.json", "accounting.json"):
                    _inside(episode_path, filename)
                episode = load_episode(episode_path)
                _validate_episode(episode)
                manifest, verdict = episode["manifest"], episode["verdict"]
                if manifest.get("protocol_sha256") != protocol_hash or manifest.get("policy_identity") != identities.get(plan["arm"]):
                    row["issues"].append("episode identity/settings do not match protocol")
                if manifest.get("comparison_cell") != plan["id"]:
                    row["issues"].append("episode belongs to a different comparison cell")
                if any(manifest.get(k) != protocol.get(k) for k in ("world", "family", "split")) or manifest.get("seed") != plan["seed"]:
                    row["issues"].append("episode task does not match plan")
                task_hash = _task_digest(manifest)
                if row.get("task_sha256") != task_hash:
                    row["issues"].append("episode task fingerprint changed")
                effective = {**manifest["budget"], **protocol["budget_override"]}
                if row.get("effective_budget") != effective:
                    row["issues"].append("effective budget differs from protocol")
                row["evidence"] = {"verdict": verdict, "failure_codes": sorted(failure_codes(episode)),
                                   "tokens": episode_tokens(episode) if episode["steps"] else None,
                                   "steps": len(episode["steps"]), "reset": episode["reset"],
                                   "wall_seconds": (verdict or {}).get("wall_seconds")}
                if any(str(step.get("error") or "").startswith(BACKEND_FAILURE_PREFIX) for step in episode["steps"]):
                    row["issues"].append("backend action failure recorded in episode")
                if not (episode_path / "steps.jsonl").is_file():
                    row["issues"].append("recorded steps are missing")
                if verdict is None:
                    row["issues"].append("no verifier result was saved; episode is unscored")
                elif verdict.get("reason_code") == "ORACLE_ERROR":
                    row["issues"].append("oracle execution failed; episode is unscored")
                elif type(verdict.get("n_steps")) is not int or verdict["n_steps"] != len(episode["steps"]):
                    row["issues"].append("recorded step count differs from verifier result")
                if any(type(step.get("i")) is not int or step["i"] != index for index, step in enumerate(episode["steps"])):
                    row["issues"].append("recorded steps are incomplete, duplicated or out of order")
                for step in episode["steps"]:
                    if any(not step.get(key) or not _inside(episode_path, step[key]).is_file()
                           for key in ("shot_before", "shot_after")):
                        row["issues"].append("referenced screenshot evidence is missing")
                        break
                baseline_path = episode_path / "baseline-digest.json"
                if not baseline_path.is_file() or _read_object(baseline_path) != row.get("baseline_digest"):
                    row["issues"].append("recorded baseline missing or differs from cell baseline")
                report_path = episode_path / "report.html"
                row["report"] = report_path.relative_to(root.resolve()).as_posix() if report_path.is_file() and report_path.resolve().is_relative_to(root.resolve()) else None
                if row["status"] == "completed" and verdict and verdict.get("reason_code") != "ORACLE_ERROR":
                    reward = verdict.get("reward")
                    if type(reward) not in (int, float) or not math.isfinite(reward) or reward not in (0, 1):
                        row["issues"].append("invalid oracle reward")
                    else:
                        specs = manifest.get("oracle") or {}
                        checks = [check["id"] for group in ("effects", "invariants") for check in specs.get(group, [])]
                        details = verdict.get("details") or {}
                        if len(set(checks)) != len(checks):
                            row["issues"].append("duplicate oracle check identifiers")
                        if not checks or any(not isinstance(details.get(key), dict)
                                             or type(details[key].get("passed")) is not bool
                                             or details[key].get("error") is not None for key in checks):
                            row["issues"].append("oracle check evidence missing or errored; verdict not scorable")
                        elif (reward == 1) != all(details[key]["passed"] for key in checks):
                            row["issues"].append("oracle reward disagrees with declared check outcomes")
                        elif set(verdict["failed"]) != {key for key in checks if not details[key]["passed"]}:
                            row["issues"].append("failed-check list disagrees with declared check outcomes")
                        else:
                            row.update(scored=True, success=reward == 1)
                if episode["reset"] != row.get("reset_report"):
                    row["issues"].append("recorded reset differs from cell reset")
                reset = episode["reset"] or {}
                if reset.get("ok") is not True or reset.get("method") != protocol.get("reset_mode"):
                    row["issues"].append("successful reset using the planned method was not recorded")
            except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
                row["issues"].append(f"evidence unavailable: {_error(exc)}")
                row.update(scored=False, success=None)
        elif row["status"] == "completed":
            row["issues"].append("completed cell has no episode evidence")
        if row["issues"]:
            row.update(scored=False, success=None)
        row.update(plan)
        cells.append(row)
    arms = {}
    for arm in ARMS:
        selected = [c for c in cells if c["arm"] == arm]
        scored = [c for c in selected if c["scored"]]
        successes = sum(c["success"] is True for c in scored)
        usage = [c.get("recorded_policy_usage") or (c.get("evidence") or {}).get("tokens") for c in selected]
        usage = [u for u in usage if isinstance(u, dict) and u and all(_nonnegative_number(value) for value in u.values())]
        durations = [c["setup_and_episode_seconds"] for c in selected if _nonnegative_number(c.get("setup_and_episode_seconds"))]
        labels = protocol.get("labels")
        tokens = {key: sum(u.get(key, 0) for u in usage) for key in ("in", "out", "cache_read", "cache_write")}
        arms[arm] = {"label": str(labels.get(arm, arm)) if isinstance(labels, dict) else arm, "planned": len(seeds),
                     "successes": successes, "scored": len(scored), "failures": len(scored) - successes,
                     "unscored": len(selected) - len(scored), "success_rate": successes / len(scored) if scored else None,
                     "statuses": dict(Counter(c["status"] for c in selected)),
                     "recorded_setup_and_episode_seconds": sum(durations),
                     "duration_cells": len(durations),
                     "recorded_tokens": tokens, "usage_cells": len(usage),
                     "recorded_steps": sum((c.get("evidence") or {}).get("steps", 0) for c in selected)}
    pairs, outcomes = [], {"both_pass": 0, "A_only": 0, "B_only": 0, "neither": 0}
    for seed in seeds:
        pair_cells = {arm: next(c for c in cells if c["seed"] == seed and c["arm"] == arm) for arm in ARMS}
        a, b = pair_cells["A"], pair_cells["B"]
        equivalence = reset_equivalence(a, b)
        reasons = list(issues)
        if not all(c["scored"] for c in pair_cells.values()):
            reasons.append("incomplete or unscored pair")
        reasons.extend(f"{arm}: {reason}" for arm, cell in pair_cells.items() for reason in cell["issues"])
        if not a.get("task_sha256") or a.get("task_sha256") != b.get("task_sha256") or a.get("effective_budget") != b.get("effective_budget"):
            reasons.append("task or effective budget differs between arms")
        reasons.extend(equivalence["reasons"])
        outcome = None
        if not reasons:
            outcome = "both_pass" if a["success"] and b["success"] else "A_only" if a["success"] else "B_only" if b["success"] else "neither"
            outcomes[outcome] += 1
        pairs.append({"seed": seed, "comparable": not reasons, "outcome": outcome, "reasons": reasons,
                      "reset_equivalence": equivalence, "cells": {arm: c["id"] for arm, c in pair_cells.items()}})
    eligible = bool(pairs) and all(p["comparable"] for p in pairs) and execution.get("status") == "finished"
    controls = protocol.get("evidence_kind") != "live_policy_evaluation" or protocol.get("backend") == "fake"
    leader = None
    if eligible and not controls and outcomes["A_only"] != outcomes["B_only"]:
        leader = "A" if outcomes["A_only"] > outcomes["B_only"] else "B"
    return {"schema": SCHEMA, "protocol": protocol, "execution": execution, "issues": issues,
            "planned_pairs": len(seeds), "matched_pairs": sum(p["comparable"] for p in pairs),
            "arms": arms, "paired_outcomes": outcomes, "pairs": pairs, "cells": cells, "unplanned_attempts": extras,
            "configuration_changes": _configuration_changes(identities.get("A", {}), identities.get("B", {})),
            "attempts": data["cells"],
            "discordant_seeds": [p["seed"] for p in pairs if p["outcome"] in ("A_only", "B_only")],
            "regression_seeds": [p["seed"] for p in pairs if p["outcome"] == "A_only"],
            "missing_cells": [c["id"] for c in cells if c["status"] == "missing"],
            "recommendation": {"eligible": eligible and not controls, "observed_leader": leader,
                               "message": "Constructed controls; not live policy performance." if controls else
                               "Recommendation withheld: incomplete or non-comparable evidence." if not eligible else
                               "Complete comparable sample; leader is descriptive only, not a deployment recommendation."},
            "limits": LIMITS}


def format_comparison(summary: dict[str, Any]) -> str:
    p = summary["protocol"]
    lines = ["Forkloop / Matched policy comparison", f"{p.get('world', 'unknown')} · {p.get('family', 'unknown')} · {p.get('split', 'unknown')}",
             f"Evidence: {p.get('evidence_kind', 'unknown')} | execution: {summary['execution']['status']}",
             f"Matched pairs: {summary['matched_pairs']}/{summary['planned_pairs']}", summary["recommendation"]["message"], ""]
    for key in ("recorded_status", "explanation", "error", "cleanup_error", "stop_cell"):
        if summary["execution"].get(key):
            lines.append(f"Execution {key}: {summary['execution'][key]}")
    for arm, values in summary["arms"].items():
        lines.append(f"{arm} / {values['label']}: {values['successes']}/{values['scored']} scored successes; "
                     f"{values['failures']} failures; {values['unscored']} unscored; {values['planned']} planned")
    lines += ["", "Paired outcomes: " + ", ".join(f"{k}={v}" for k, v in summary["paired_outcomes"].items()),
              "Discordant seeds: " + str(summary["discordant_seeds"]), ""]
    for change in summary.get("configuration_changes", []):
        lines.append(f"Changed {change['path']}: A={_json(change['A'])}; B={_json(change['B'])}")
    for pair in summary["pairs"]:
        lines.append(f"Seed {pair['seed']}: {pair['outcome'] or 'NOT COMPARABLE'}" + (" — " + "; ".join(pair["reasons"]) if pair["reasons"] else ""))
        lines.append("Reset equivalence: " + json.dumps(pair["reset_equivalence"], ensure_ascii=False))
    for cell in summary["cells"]:
        lines += ["", f"{cell['id']} | {cell['arm']} seed {cell['seed']} | {cell['status']}",
                  f"Evidence: {cell.get('episode') or 'no episode allocated'}"]
        lines.append("Comparison scoring: " + ("passed" if cell["success"] is True else "failed" if cell["scored"] else "unscored"))
        for key in ("error", "cleanup_error", "policy_cleanup_error", "report_error"):
            if cell.get(key):
                lines.append(f"{key}: {cell[key]}")
        lines.append(f"Recorded setup + episode seconds: {cell.get('setup_and_episode_seconds', 'unknown')}")
        if cell.get("recorded_policy_usage") is not None:
            lines.append("Recorded policy usage (including failed calls where reported): " + json.dumps(cell["recorded_policy_usage"]))
        if cell.get("evidence"):
            evidence = cell["evidence"]
            verdict = evidence["verdict"] or {}
            lines.append(f"Retained verifier result: {verdict.get('reason_code', 'unavailable')} | failed checks: {verdict.get('failed', [])}")
            for check in verdict.get("failed", []):
                detail = (verdict.get("details") or {}).get(check, {})
                lines.append(f"  {check}: " + _json(detail))
            lines.append(f"Steps: {evidence['steps']} | wall seconds: {evidence['wall_seconds']} | tokens: {evidence['tokens']}")
        lines.extend(cell["issues"])
    lines += ["", "Integrity issues: " + ("; ".join(summary["issues"]) or "none detected"), "", "Limits:", *[f"- {limit}" for limit in summary["limits"]]]
    return "\n".join(lines)


def html_comparison(summary: dict[str, Any]) -> str:
    from .comparison_html import html_comparison as render
    return render(summary)


def write_comparison_report(output: str | Path) -> dict[str, Any]:
    """Regenerate derived views only; protocol, attempts and episodes stay untouched."""
    root = Path(output)
    summary = summarize_comparison(root)
    _atomic(root / "summary.json", summary)
    _atomic(root / "comparison.txt", format_comparison(summary), text=True)
    _atomic(root / "comparison.html", html_comparison(summary), text=True)
    return summary


__all__ = ["PolicyVariant", "run_comparison", "load_comparison", "summarize_comparison", "reset_equivalence",
           "format_comparison", "html_comparison", "write_comparison_report"]
