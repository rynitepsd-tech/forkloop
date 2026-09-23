"""No-spend setup diagnostics. Never emit credentials, endpoints or snapshot IDs.

Only remote=True constructs an SDK client, and only GET metadata routes are used.
Readiness is prerequisite readiness, not a successful build or policy evaluation.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import importlib.metadata
import inspect
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .spending import BudgetExceeded, SessionLedger, load_solari_pricing, require_solari_lifetime_bound


async def doctor(*, world: str = "claims-ops-v1", family: str = "resolve_denial",
                 backend: str = "solari", policy: str = "scripted",
                 session_ledger: str | Path | None = None, pricing_file: str | Path | None = None,
                 remote: bool = False, build: bool = False, student_url: str | None = None) -> dict:
    """Return public-safe structured checks; no reservations, creates or model calls.

    build=True checks a fresh build, not the presence of an existing golden image.
    student_url is inspected locally only, never returned or contacted. An omitted
    student endpoint cannot be certified by this doctor. Remote checks use a
    20-second deadline and cannot establish account balance, plan or GUI health.
    """
    checks = []

    def add(name, status, message, remediation="", **details):
        checks.append({"name": name, "status": status, "message": message,
                       "remediation": remediation, "details": details})

    def dependency(module, package, install):
        try:
            importlib.import_module(module)
            version = importlib.metadata.version(package)
        except Exception:
            add("dependency." + module, "fail", f"{package} is unavailable or cannot import.", install)
            return False
        add("dependency." + module, "pass", f"{package} imports.", version=version)
        return True

    add("python", "pass" if sys.version_info >= (3, 11) else "fail", "Python 3.11+ is required.",
        "Install Python 3.11+ and reinstall Forkloop." if sys.version_info < (3, 11) else "")
    for module, package in (("yaml", "PyYAML"), ("PIL", "Pillow"), ("numpy", "numpy"), ("httpx", "httpx")):
        dependency(module, package, "Install the complete Forkloop package: python -m pip install -e .")
    if backend not in {"fake", "solari"}:
        add("backend", "fail", "Unknown backend.", "Choose fake or solari.")
    if policy not in {"scripted", "random", "teacher", "student"}:
        add("policy", "fail", "Unknown policy.", "Choose scripted, random, teacher or student.")

    configured_world = None
    snapshot_id = None
    cpu, mem_mb = 2, 4096
    try:
        from .world import load_world
        configured_world = load_world(world)
        if family not in configured_world.config.families:
            add("world.family", "fail", "Task family does not belong to this world.", "Run forkloop worlds and choose a listed family.")
        else:
            # Pure generator validates the oracle and reads required base-data assets.
            configured_world.generate(family, 0, "train")
            add("world.family", "pass", "World loads and the selected task generator validates.")
        resources = configured_world.config.extra.get("resources", {})
        cpu, mem_mb = int(resources.get("cpu", 2)), int(resources.get("mem_mb", 4096))
        snapshot_id = configured_world.golden_snapshot_id()
        if world == "claims-ops-v1":
            required = ["world.yaml", "build.sh", "browser_setup.sh", "chrome_policy.json", "seed_world.py",
                        "portal/base_data.json", "portal/templates/base.html", "portal/static/style.css",
                        "openemr/base_data.json", "openemr/shim_schema.sql", "openemr/install.sh"]
            missing = [name for name in required if not (configured_world.config.dir / name).is_file()]
            add("world.assets", "fail" if missing else "pass", "Required world assets are missing." if missing else "Required world assets are present.",
                "Install from the complete source checkout, not the report-only publication." if missing else "", missing=missing)
        if backend == "solari":
            if build:
                add("snapshot.config", "pass", "Fresh build selected; existing snapshot configuration is not required or reused.")
            else:
                add("snapshot.config", "pass" if snapshot_id else "fail", "Golden snapshot is configured (value withheld)." if snapshot_id else "No golden snapshot is configured.",
                    "Build your own golden world with forkloop build-world, then export " + configured_world.config.golden_snapshot_env + "." if not snapshot_id else "")
    except Exception:
        add("world.load", "fail", "World configuration, generator or assets could not load.",
            "Run forkloop worlds and forkloop task --family with a listed family in the complete source checkout.")

    if backend == "fake":
        for module, package in (("fastapi", "fastapi"), ("jinja2", "Jinja2"), ("multipart", "python-multipart"), ("itsdangerous", "itsdangerous")):
            dependency(module, package, "Install world dependencies: python -m pip install -e '.[world]'")
        add("evidence", "warn", "Fake execution is a constructed verifier control, not live GUI or policy-performance evidence.")
    if policy == "teacher" and not build:
        dependency("anthropic", "anthropic", "Install python -m pip install -e '.[teacher]'")
        present = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        add("credentials.model", "pass" if present else "fail", "Anthropic credential is present (value withheld)." if present else "Anthropic credential is missing.",
            "Export ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN locally; never commit credentials." if not present else "")
        add("model.authorization", "warn", "This check does not authorize or price Anthropic calls.", "Review model budget authorization before using the teacher policy.")
    hosted_student = False
    if policy == "student" and not build:
        try:
            endpoint = urlsplit(student_url or "")
            valid_endpoint = endpoint.scheme in {"http", "https"} and bool(endpoint.hostname)
            hosted_student = bool(endpoint.hostname and endpoint.hostname.rstrip(".") == "api.openai.com")
            if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                valid_endpoint = False
            from .policies.student import StudentPolicy
            StudentPolicy.validate_options(
                {"base_url": student_url or ""},
                credentialed=bool(os.environ.get("STUDENT_API_KEY") or hosted_student and os.environ.get("OPENAI_API_KEY")),
            )
        except ValueError:
            valid_endpoint = False
        add("model.endpoint", "pass" if valid_endpoint else "fail", "Model endpoint syntax is valid; reachability is not checked." if valid_endpoint else "A valid model endpoint without embedded credentials/query/fragment is required; credentialed remote endpoints require HTTPS.",
            "Use HTTPS for remote credentials or an explicit loopback SSH tunnel; keep keys in environment variables." if not valid_endpoint else "")
        present = bool(os.environ.get("STUDENT_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        add("credentials.model", "fail" if hosted_student and not present else "pass", "Model credential presence checked (values withheld).",
            "Export OPENAI_API_KEY or STUDENT_API_KEY locally." if hosted_student and not present else "", present=present, required=hosted_student)
        add("model.readiness", "warn", "No model call made; model identity, availability, rate bounds and endpoint health remain unverified.",
            "Use the runtime's guarded model preflight and an explicitly budgeted policy attempt.")

    pricing = None
    required_reservation = None
    sdk_ok = False
    kind = os.environ.get("FORKLOOP_SOLARI_KIND", "desktop").lower()
    if backend == "solari":
        key_present = bool(os.environ.get("SOLARI_API_KEY"))
        add("credentials.solari", "pass" if key_present else "fail", "Solari credential is present (value withheld)." if key_present else "SOLARI_API_KEY is missing.",
            "Create a Solari account/API key and export SOLARI_API_KEY locally." if not key_present else "")
        add("solari.kind", "pass" if kind == "desktop" else "fail", "Desktop agent channel selected." if kind == "desktop" else "GUI policy evaluation requires a desktop, not a headless sandbox.",
            "Set FORKLOOP_SOLARI_KIND=desktop." if kind != "desktop" else "")
        plan = os.environ.get("SOLARI_PLAN", "starter").lower()
        add("solari.plan", "pass" if plan == "starter" else "fail", "Starter safety bounds selected; actual account plan is not verified." if plan == "starter" else "Current safety guard supports Starter only.",
            "Verify a funded Starter account in the Solari console; setting SOLARI_PLAN never upgrades your account.")
        try:
            hours = require_solari_lifetime_bound()
            add("solari.lifetime", "pass", f"Each machine is killed after {hours * 60:.0f} minutes; run "
                f"`forkloop reap --older-than-min {hours * 60:.0f}` from a second process as the safety net.")
        except BudgetExceeded as exc:
            add("solari.lifetime", "fail", str(exc),
                "Set the lifetime and balance-bound variables to opt in, or use the no-account workflow.")
        try:
            pricing = load_solari_pricing(pricing_file)
            hourly = pricing.hourly(cpu, mem_mb, desktop=kind == "desktop")
            add("solari.pricing", "pass", "Reviewed hourly rates are current; they do not bound total resource cost.",
                pricing=pricing.public_info(), hourly_usd=hourly)
            billed_storage = dt.date.today() >= pricing.storage_starts_on
            add("solari.storage", "fail" if build and billed_storage else "warn",
                "New snapshots are blocked: retained storage has no provider-enforced expiry." if billed_storage else "Storage billing starts October 1; snapshots retained past then can incur charges.",
                "After storage billing begins, use an existing golden in fork mode without branch snapshots; a pricing acknowledgment does not authorize indefinite storage." if billed_storage else "Review and delete unneeded account snapshots before storage billing starts. Retention is your responsibility.")
        except ValueError as exc:
            add("solari.pricing", "fail", str(exc), "Review https://docs.getsolari.com/pricing and supply a strictly validated pricing JSON via FORKLOOP_SOLARI_PRICING_FILE.")
        try:
            from solari_sandbox import SandboxClient
            from solari_core.desktop import Desktop
            required = {"template", "from_snapshot", "resolution", "cpu", "mem_mb", "disk_gb", "record", "metadata", "timeout_ms", "lifecycle"}
            sdk_ok = (required <= set(inspect.signature(SandboxClient.create_desktop).parameters)
                      and all(callable(getattr(SandboxClient, name, None)) for name in ("get_snapshot", "list_snapshots", "aclose"))
                      and all(callable(getattr(Desktop, name, None)) for name in ("snapshot", "revert", "set_timeout", "kill", "screenshot", "health")))
            version = importlib.metadata.version("solari-sandbox")
            add("solari.sdk", "pass" if sdk_ok else "fail", "Installed SDK supports required snapshot/lifetime/GUI interfaces." if sdk_ok else "Installed SDK lacks required interfaces.",
                "Install solari-sandbox>=0.2.0 and solari-desktop>=0.2.0." if not sdk_ok else "", version=version)
        except Exception:
            add("solari.sdk", "fail", "Solari SDK is unavailable or incompatible.", "Install solari-sandbox>=0.2.0 and solari-desktop>=0.2.0 (not the unrelated solari package).")

    ledger_path = session_ledger or os.environ.get("FORKLOOP_SESSION_LEDGER")
    if backend == "solari" or hosted_student:
        try:
            if not ledger_path:
                raise ValueError("missing ledger")
            ledger = SessionLedger(ledger_path)
            headroom = ledger.headroom()
            writable = os.access(ledger.path, os.W_OK) and os.access(ledger.path.parent, os.W_OK)
            add("ledger.available", "pass" if writable else "fail", "Ledger read-only inspection succeeded; reservation writes require file and directory access.",
                "Fix local ledger/directory permissions without replacing existing accounting." if not writable else "")
            for service in (["solari"] if backend == "solari" else []) + (["openai"] if hosted_student else []):
                amounts = headroom.get(service)
                minimum = required_reservation if service == "solari" else None
                blocked = bool(amounts and amounts.get("blocked"))
                enough = bool(amounts and not blocked and amounts["headroom_usd"] > 0 and (minimum is None or amounts["headroom_usd"] + 1e-9 >= minimum))
                add("ledger." + service, "pass" if enough else "fail",
                    "Observed reservation-bound violation blocks this service." if blocked else
                    "Service has unreserved headroom." if enough else "Service authorization/headroom is missing or insufficient.",
                    "Investigate the invalid resource/cost bound; preserve this ledger and all unknown charges. New authorization alone does not establish a safe bound." if blocked else
                    "Reconcile authoritative charges or explicitly authorize a new session; never clear unknown reservations." if not enough else "",
                    amounts=amounts, next_create_upper_usd=minimum)
        except (OSError, ValueError, sqlite3.Error):
            add("ledger.available", "fail", "Session ledger is missing, invalid or unreadable.", "Initialize a NEW ledger explicitly with forkloop ledger PATH --create and approved service limits; export FORKLOOP_SESSION_LEDGER. Never overwrite an existing ledger.")

    if remote and backend == "solari":
        if not sdk_ok or not os.environ.get("SOLARI_API_KEY"):
            add("remote.solari", "fail", "Read-only remote checks could not start.", "Resolve SDK and Solari credential checks first.")
        else:
            client = None
            try:
                async with asyncio.timeout(20):
                    from solari_sandbox import SandboxClient
                    client = SandboxClient(api_key=os.environ["SOLARI_API_KEY"],
                                           base_url=os.environ.get("SOLARI_BASE_URL", "https://api.getsolari.com"), call_timeout_ms=10_000)
                    # GET only. No account-specific values or provider exception text escape.
                    if snapshot_id and not build:
                        view = await client.get_snapshot(snapshot_id)
                        match = getattr(view, "kind", None) == "desktop"
                        add("remote.snapshot", "pass" if match else "fail", "Configured golden snapshot is accessible and desktop-compatible." if match else "Configured snapshot is not a desktop snapshot.",
                            "Build/select a desktop golden snapshot in your own account." if not match else "")
                    else:
                        await client.list_snapshots(limit=1)
                        add("remote.solari", "pass", "Authenticated snapshot metadata read succeeded; no resource created.")
                    add("remote.limits", "warn", "Read-only metadata cannot establish credits, account plan, capacity, guest health or policy success.", "Verify account plan/credit balance manually; run an explicitly budgeted build/evaluation for live proof.")
            except Exception as exc:
                add("remote.solari", "fail", "Read-only metadata request failed; provider details withheld for privacy.",
                    "Check credential scope, configured API origin, account ownership and connectivity privately.", error_type=type(exc).__name__)
            finally:
                if client is not None:
                    try:
                        async with asyncio.timeout(3):
                            await client.aclose()
                    except Exception:
                        add("remote.close", "warn", "Metadata client did not close cleanly; no compute resource was created.")
    elif backend == "solari":
        add("remote.solari", "skip", "Offline mode: no credentialed network requests made.", "Use --remote explicitly for read-only snapshot accessibility; it still does not run a desktop or model.")
    return {"schema_version": 1, "ready": not any(c["status"] == "fail" for c in checks),
            "mode": "remote-read-only" if remote and backend == "solari" else "offline", "checks": checks,
            "readiness_scope": "Setup prerequisites only; no paid calls, live build, application health or policy performance verified."}


def render_doctor(result: dict) -> str:
    """Human-readable rendering of the same privacy-safe check payload."""
    lines = ["Forkloop doctor: " + ("prerequisites ready" if result["ready"] else "not ready"), result["readiness_scope"]]
    for check in result["checks"]:
        lines.append(f"[{check['status'].upper()}] {check['name']}: {check['message']}")
        if check["remediation"]:
            lines.append("  Next: " + check["remediation"])
        details = check["details"]
        if details.get("amounts") is not None:
            lines.append(f"  Unreserved headroom: ${details['amounts']['headroom_usd']:.6f}")
        upper = details.get("create_upper_usd", details.get("next_create_upper_usd"))
        if upper is not None:
            lines.append(f"  Next machine reservation: ${upper:.6f}")
    return "\n".join(lines) + "\n"
