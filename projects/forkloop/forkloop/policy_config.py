"""Explicit, reproducible policy configurations for the comparison CLI.

Custom factories are trusted local Python code, not a sandbox boundary. Keys are
read from named environment variables at construction and never put in identity.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import yaml


@dataclass(frozen=True)
class ConfiguredPolicy:
    name: str
    identity: dict[str, Any]
    factory: Callable[[], Any]
    missing_env: tuple[str, ...] = ()


def _mapping(value: Any, label: str, allowed: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError(f"{label} must be a mapping with string keys")
    if allowed is not None and (unknown := value.keys() - allowed):
        raise ValueError(f"Unknown {label} fields: {', '.join(sorted(unknown))}")
    return value


def _public_options(value: Any) -> None:
    from .comparison import _safe_metadata

    _safe_metadata(value)
    json.dumps(value, allow_nan=False)


def configure_policy(raw: dict, base: Path, *, require_env: bool = True) -> ConfiguredPolicy:
    spec = _mapping(raw, "variant", {"name", "policy", "factory", "options", "revision", "api_key_env", "system_prompt_file"})
    name = spec.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError("Each variant needs a nonempty name of at most 80 characters")
    if bool(spec.get("policy")) == bool(spec.get("factory")):
        raise ValueError(f"{name}: choose exactly one of policy or factory")
    options = dict(_mapping(spec.get("options", {}), f"{name} options"))
    if spec.get("system_prompt_file"):
        if "system_prompt" in options:
            raise ValueError("Use system_prompt_file or options.system_prompt, not both")
        options["system_prompt"] = (base / spec["system_prompt_file"]).read_text(encoding="utf-8")
    _public_options(options)
    _public_options({"name": name, "revision": spec.get("revision")})
    kind = spec.get("policy")
    key_env = spec.get("api_key_env")
    if key_env is not None and (not isinstance(key_env, str) or not key_env.isidentifier()):
        raise ValueError("api_key_env must name an environment variable")
    if kind == "student":
        from .policies.student import StudentPolicy
        constructor = StudentPolicy
        url = options.get("base_url")
        model = options.get("model")
        if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
            raise ValueError(f"{name}: student requires an absolute http(s) base_url")
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"{name}: student requires an explicit model")
        if urlsplit(url).hostname.rstrip(".") == "api.openai.com":
            key_env = key_env or "OPENAI_API_KEY"
            if model != "gpt-5.6-luna":
                raise ValueError("The guarded OpenAI path currently supports gpt-5.6-luna only")
            options.setdefault("hosted_reasoning", True)
            options.setdefault("max_tokens", 4096)
            options.setdefault("image_detail", "high")
            options.setdefault("timeout_s", 120.0)
        forbidden = {"transport", "session_ledger", "api_key"} & options.keys()
        if forbidden:
            raise ValueError(f"{name}: runtime-only options are not configurable: {sorted(forbidden)}")
        StudentPolicy.validate_options(options, credentialed=bool(key_env))
    elif kind == "scripted":
        from .policies.scripted import ScriptedPolicy
        constructor = ScriptedPolicy
        options.setdefault("actions", [])
    elif kind == "random":
        from .policies.scripted import RandomPolicy
        constructor = RandomPolicy
    elif kind == "teacher":
        from .policies.teacher import TeacherPolicy
        constructor = TeacherPolicy
        if key_env:
            raise ValueError("Teacher reads ANTHROPIC_API_KEY directly; api_key_env is student/custom only")
        if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            raise ValueError("Teacher requires ANTHROPIC_API_KEY; its provider costs are not guarded by the OpenAI ledger")
        if "client" in options:
            raise ValueError("Teacher client is runtime-only")
    elif spec.get("factory"):
        reference = spec["factory"]
        if not isinstance(reference, str) or reference.count(":") != 1:
            raise ValueError("factory must be an importable module:callable")
        module, symbol = reference.split(":")
        if not module or not symbol.isidentifier():
            raise ValueError("factory must be an importable module:callable")
        constructor = getattr(importlib.import_module(module), symbol)
        if not callable(constructor):
            raise ValueError("factory is not callable")
        if not isinstance(spec.get("revision"), str) or not spec["revision"].strip():
            raise ValueError("Custom factories require an explicit revision identifying the evaluated code")
    else:
        raise ValueError(f"Unknown policy {kind!r}; choose student, teacher, scripted, random, or factory")
    missing_env = (key_env,) if key_env and not os.environ.get(key_env) else ()
    if missing_env and require_env:
        raise ValueError(f"{name}: required environment variable {key_env} is not set")
    # Validate keyword names before any machine allocation. Do not instantiate a
    # plugin here: constructors may connect to paid services or own async clients.
    inspect.signature(constructor).bind(**options, **({"api_key": "environment"} if key_env else {}))
    from . import __version__

    identity = {"policy": kind or spec["factory"], "options": options,
                "version": spec.get("revision") or f"forkloop/{__version__}"}
    source = inspect.getsourcefile(constructor)
    if source and Path(source).is_file():
        identity["policy_source_sha256"] = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    identity["configuration_sha256"] = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    encoded_options = json.dumps(options, allow_nan=False)

    def checked_policy(policy):
        if not callable(getattr(policy, "act", None)):
            raise TypeError(f"{name}: factory must return a policy with async act(observation)")
        return policy

    def factory():
        # JSON round-trip makes nested options private to each fresh policy.
        kwargs = json.loads(encoded_options)
        if key_env:
            kwargs["api_key"] = os.environ[key_env]
        policy = constructor(**kwargs)
        if inspect.isawaitable(policy):
            async def resolve():
                return checked_policy(await policy)
            return resolve()
        return checked_policy(policy)

    return ConfiguredPolicy(name, identity, factory, missing_env)


def load_config(path: str | Path, *, require_env: bool = True) -> tuple[dict, list[ConfiguredPolicy]]:
    """Validate a comparison file. ``require_env=False`` (``compare --check``) reports
    missing credential variables instead of refusing, so a config can be reviewed anywhere."""
    path = Path(path)
    config = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "comparison",
                      {"version", "world", "backend", "family", "split", "seeds", "budget", "variants"})
    if type(config.get("version")) is not int or config["version"] != 1:
        raise ValueError("Comparison configuration requires version: 1")
    for key, default in (("world", "claims-ops-v1"), ("backend", "solari"), ("family", "resolve_denial"), ("split", "train")):
        config.setdefault(key, default)
        if not isinstance(config[key], str) or not config[key]:
            raise ValueError(f"{key} must be a nonempty string")
    if config["backend"] not in ("solari", "fake"):
        raise ValueError("backend must be solari or fake")
    seeds = config.get("seeds")
    if not isinstance(seeds, list) or not seeds or any(type(s) is not int or s < 0 for s in seeds):
        raise ValueError("seeds must be a nonempty list of nonnegative integers")
    if len(seeds) != len(set(seeds)):
        raise ValueError("Duplicate seeds would overcount evidence; each seed must be unique")
    budget = _mapping(config.get("budget", {}), "budget", {"max_steps", "max_seconds"})
    for key, value in budget.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0 or (key == "max_steps" and type(value) is not int):
            raise ValueError(f"budget.{key} must be positive and finite (max_steps must be an integer)")
    config["budget"] = budget
    variants = config.get("variants")
    if not isinstance(variants, list) or len(variants) != 2:
        raise ValueError("Comparison requires exactly two variants")
    policies = [configure_policy(variant, path.parent, require_env=require_env) for variant in variants]
    if policies[0].name == policies[1].name:
        raise ValueError("Variant names must be distinct")
    return config, policies
