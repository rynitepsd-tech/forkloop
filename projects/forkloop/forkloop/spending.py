"""Process-safe session reservations. Accounting is independent of trajectories.

An unresolved/failed operation retains its full reservation. Only authoritative
usage or confirmed non-billing can release it; unknown charges are never zero.
The caller must bound input/output, retries and resource lifetime BEFORE reserve.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


SOLARI_LIFETIME_ENV = "FORKLOOP_SOLARI_MAX_LIFETIME_MIN"
SOLARI_BALANCE_ENV = "FORKLOOP_SOLARI_ACCEPT_BALANCE_BOUND"
SOLARI_SETUP_MARGIN_H = 10 / 60


def require_solari_lifetime_bound() -> float:
    """Return the enforced maximum machine lifetime in hours, or refuse allocation.

    Solari VMs have only a rolling idle timeout, and two desktops were once observed
    running ~10 h. Live allocation therefore needs an explicit operator opt-in to a bound
    Forkloop can defend, in three layers:

    1. ``FORKLOOP_SOLARI_MAX_LIFETIME_MIN`` (5–300): a hard lifetime the backend enforces
       in-process — every machine is killed at its deadline;
    2. ``forkloop reap --older-than-min N`` run from a separate process (e.g. a loop or
       cron) kills ledger-owned machines past the same age if the controller died;
    3. ``FORKLOOP_SOLARI_ACCEPT_BALANCE_BOUND=1`` acknowledges that if both controller
       layers fail, the provider-side limit is the prepaid balance ("we don't bill past your
       balance"; keep auto top-up off).

    Reservations are sized from (1). Idle timeouts remain requested as defence in depth.
    """
    raw = os.environ.get(SOLARI_LIFETIME_ENV)
    if not raw or os.environ.get(SOLARI_BALANCE_ENV) != "1":
        raise BudgetExceeded(
            "Solari allocations need an explicit lifetime bound: set "
            f"{SOLARI_LIFETIME_ENV}=<5-300 minutes> (enforced by the controller, plus "
            "`forkloop reap --older-than-min` from a second process) and "
            f"{SOLARI_BALANCE_ENV}=1 (the prepaid balance is the provider-side cap; keep auto "
            "top-up off). VM timeouts are idle-based; observed desktops once ran ~10 h."
        )
    try:
        minutes = float(raw)
    except ValueError:
        raise BudgetExceeded(f"{SOLARI_LIFETIME_ENV} must be a number of minutes") from None
    if not (math.isfinite(minutes) and 5 <= minutes <= 300):
        raise BudgetExceeded(f"{SOLARI_LIFETIME_ENV} must be between 5 and 300 minutes")
    return minutes / 60


def solari_allocation_status() -> str:
    """One line for diagnostics: whether guarded Solari allocations can run."""
    try:
        hours = require_solari_lifetime_bound()
    except BudgetExceeded as exc:
        return f"solari: blocked — {exc}"
    return f"solari: allocations permitted; each machine is killed after {hours * 60:.0f} minutes"


@dataclass(frozen=True)
class SolariPricing:
    """Reviewed rates and historical assumptions, never a lifetime guarantee."""

    reviewed_on: dt.date
    valid_until: dt.date
    cpu_hour_usd: float
    memory_gb_hour_usd: float
    screen_hour_usd: float
    max_session_hours: float
    storage_starts_on: dt.date
    storage_gb_month_usd: float
    storage_free_gb: float

    def hourly(self, cpu: int, mem_mb: int, *, desktop: bool) -> float:
        if type(cpu) is not int or type(mem_mb) is not int or (cpu, mem_mb) not in {
            (1, 2048), (2, 4096), (4, 8192)
        }:
            raise ValueError("guarded Solari shapes are 1 CPU/2048 MB, 2/4096, or 4/8192")
        hourly = cpu * self.cpu_hour_usd + mem_mb / 1024 * self.memory_gb_hour_usd + (
            self.screen_hour_usd if desktop else 0)
        if not math.isfinite(hourly) or hourly <= 0:
            raise ValueError("pricing produces an invalid hourly bound")
        return hourly

    def reservation(self, hourly_usd: float) -> float:
        """The enforced lifetime plus a setup margin, at the machine's hourly rate."""
        return hourly_usd * (require_solari_lifetime_bound() + SOLARI_SETUP_MARGIN_H)

    def public_info(self) -> dict:
        return {
            "source": "https://docs.getsolari.com/pricing", "plan": "starter",
            "reviewed_on": self.reviewed_on.isoformat(), "valid_until": self.valid_until.isoformat(),
            "cpu_hour_usd": self.cpu_hour_usd, "memory_gb_hour_usd": self.memory_gb_hour_usd,
            "screen_hour_usd": self.screen_hour_usd, "max_session_hours": self.max_session_hours,
            "storage_starts_on": self.storage_starts_on.isoformat(),
            "storage_gb_month_usd": self.storage_gb_month_usd, "storage_free_gb": self.storage_free_gb,
        }


def load_solari_pricing(path: str | Path | None = None, *, today: dt.date | None = None) -> SolariPricing:
    """Load an explicit local review; renewal never extends resource lifetimes.

    JSON uses public_info's fields plus acknowledged=true. The official source,
    Starter plan, positive finite rates, and a <=31-day review window are required.
    An expired review fails closed, including when a supplied file is unreadable.
    Storage is separate: SDK 0.2.0 has no snapshot expiry, so a pricing review
    cannot authorize new indefinitely billable snapshots after storage starts.
    """
    today = today or dt.date.today()
    path = path or os.environ.get("FORKLOOP_SOLARI_PRICING_FILE")
    if path is None:
        pricing = SolariPricing(dt.date(2026, 9, 15), dt.date(2026, 10, 1),
                                0.035, 0.011, 0.02, 5.0, dt.date(2026, 10, 1), 0.05, 10.0)
    else:
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("cannot read pricing JSON; check FORKLOOP_SOLARI_PRICING_FILE") from exc
        fields = {"source", "plan", "acknowledged", "reviewed_on", "valid_until", "cpu_hour_usd",
                  "memory_gb_hour_usd", "screen_hour_usd", "max_session_hours", "storage_starts_on",
                  "storage_gb_month_usd", "storage_free_gb"}
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("pricing JSON must contain exactly the documented pricing review fields")
        if (data["acknowledged"] is not True or data["plan"] != "starter"
                or data["source"] != "https://docs.getsolari.com/pricing"):
            raise ValueError("pricing review requires acknowledged=true, plan=starter and the official pricing source")
        dates = {}
        for key in ("reviewed_on", "valid_until", "storage_starts_on"):
            try:
                value = data[key]
                dates[key] = dt.date.fromisoformat(value)
                if dates[key].isoformat() != value:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValueError(f"pricing {key} must be an ISO YYYY-MM-DD date") from exc
        rates = {}
        for key in ("cpu_hour_usd", "memory_gb_hour_usd", "screen_hour_usd",
                    "max_session_hours", "storage_gb_month_usd", "storage_free_gb"):
            value = data[key]
            try:
                valid = type(value) in (int, float) and math.isfinite(value) and value > 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"pricing {key} must be a positive finite number")
            rates[key] = float(value)
        if dates["storage_starts_on"] > dt.date(2026, 10, 1):
            raise ValueError("pricing review cannot postpone the published October 1 storage start")
        pricing = SolariPricing(**dates, **rates)
    if not pricing.reviewed_on <= today < pricing.valid_until:
        raise ValueError("Solari pricing review is not current; review official rates and set FORKLOOP_SOLARI_PRICING_FILE")
    if not 0 < (pricing.valid_until - pricing.reviewed_on).days <= 31:
        raise ValueError("pricing validity must be at most 31 days after review")
    return pricing



class SessionLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError("initialize the session ledger explicitly before paid operations")

    @classmethod
    def create(cls, path: str | Path, *, limits: dict | None = None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents an accidental new session from clearing spend.
        with path.open("xb"):
            pass
        if limits is None:
            limits = {"openai": {"ceiling": 20.0, "stop": 18.0},
                      "solari": {"ceiling": 10.0, "stop": 8.0},
                      "gpu": {"ceiling": 0.0, "stop": 0.0}}
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE services(name TEXT PRIMARY KEY, ceiling REAL NOT NULL, stop REAL NOT NULL);
                CREATE TABLE operations(id TEXT PRIMARY KEY, service TEXT NOT NULL, label TEXT NOT NULL,
                    reserved REAL NOT NULL, actual REAL, status TEXT NOT NULL, created REAL NOT NULL,
                    updated REAL NOT NULL, evidence TEXT NOT NULL DEFAULT '{}');
            """)
            for name, spec in limits.items():
                ceiling, stop = float(spec["ceiling"]), float(spec["stop"])
                if not (math.isfinite(ceiling) and 0 <= stop <= ceiling):
                    raise ValueError("invalid service limit")
                db.execute("INSERT INTO services VALUES (?, ?, ?)", (name, ceiling, stop))
        return cls(path)

    def reserve(self, service: str, upper_usd: float, *, label: str, cleanup: bool = False,
                evidence: dict | None = None) -> str:
        if not math.isfinite(upper_usd) or upper_usd <= 0:
            raise ValueError("reservation must be a positive finite upper bound")
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            spec = db.execute("SELECT ceiling, stop FROM services WHERE name=?", (service,)).fetchone()
            if spec is None:
                raise BudgetExceeded(f"service {service} is not authorized in this ledger")
            if any(json.loads(row[0]).get("reservation_bound_violated")
                   for row in db.execute("SELECT evidence FROM operations WHERE service=?", (service,))):
                raise BudgetExceeded(f"{service}: observed exposure exceeded its reservation bound; service is blocked in this ledger")
            used = db.execute("SELECT COALESCE(SUM(COALESCE(actual, reserved)),0) FROM operations WHERE service=?",
                              (service,)).fetchone()[0]
            limit = spec[0] if cleanup else spec[1]
            if used + upper_usd > limit + 1e-9:
                raise BudgetExceeded(f"{service}: accounted/pending ${used:.6f} + reservation ${upper_usd:.6f} exceeds ${limit:.2f}")
            op = uuid.uuid4().hex
            db.execute("INSERT INTO operations VALUES (?, ?, ?, ?, NULL, 'reserved', ?, ?, ?)",
                       (op, service, label, upper_usd, time.time(), time.time(), json.dumps(evidence or {})))
            return op

    def retain_exposure(self, operation: str, estimated_usd: float, *, evidence: dict | None = None) -> None:
        """Raise unresolved accounting to observed exposure without granting budget.

        This is not an invoice or a renewed lifetime guarantee. Exposure is
        retained even above the service ceiling. An observed bound violation
        blocks further service reservations. Authoritative settlements stay final.
        """
        if not math.isfinite(estimated_usd) or estimated_usd < 0:
            raise ValueError("estimated exposure must be finite and nonnegative")
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT reserved, actual, evidence FROM operations WHERE id=?", (operation,)).fetchone()
            if row is None:
                raise KeyError(operation)
            if row[1] is not None:
                raise ValueError("operation already reconciled")
            retained = max(row[0], estimated_usd)
            details = json.loads(row[2])
            original_reserved = details.get("original_reserved_usd", row[0])
            violated = bool(details.get("reservation_bound_violated") or estimated_usd > row[0] + 1e-9)
            previous_exposure = details.get("observed_exposure_usd", 0)
            details.update(evidence or {})
            details["original_reserved_usd"] = original_reserved
            details["observed_exposure_usd"] = max(previous_exposure, estimated_usd)
            details["reservation_bound_violated"] = violated
            db.execute("UPDATE operations SET reserved=?, status=?, updated=?, evidence=? WHERE id=?",
                       (retained, "exposure_retained_usage_pending", time.time(), json.dumps(details), operation))

    def reconcile(self, operation: str, actual_usd: float | None, *, status: str, evidence: dict | None = None):
        if actual_usd is not None and (not math.isfinite(actual_usd) or actual_usd < 0):
            raise ValueError("invalid actual cost")
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT reserved, actual, evidence FROM operations WHERE id=?", (operation,)).fetchone()
            if row is None:
                raise KeyError(operation)
            if row[1] is not None:
                if row[1] == actual_usd:
                    return  # idempotent retry; never revise a settled charge down
                raise ValueError("operation already reconciled")
            details = json.loads(row[2])
            violated = bool(details.get("reservation_bound_violated")
                            or actual_usd is not None and actual_usd > row[0] + 1e-9)
            details.update(evidence or {})
            details["reservation_bound_violated"] = violated
            db.execute("UPDATE operations SET actual=?, status=?, updated=?, evidence=? WHERE id=?",
                       (actual_usd, status, time.time(), json.dumps(details), operation))
        if actual_usd is not None and actual_usd > row[0] + 1e-9:
            # Persist the overrun first so no subsequent caller can ignore it.
            raise BudgetExceeded("actual cost exceeded reservation; stop experiments and investigate the bound")

    def summary(self) -> dict:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            operations = [dict(r) for r in db.execute("SELECT * FROM operations ORDER BY created")]
            out = {}
            for spec in db.execute("SELECT * FROM services"):
                ops = [o for o in operations if o["service"] == spec["name"]]
                actual = sum(o["actual"] or 0 for o in ops)
                pending = sum(o["reserved"] for o in ops if o["actual"] is None)
                out[spec["name"]] = {"actual_usd": actual, "pending_upper_usd": pending,
                                     "accounted_upper_usd": actual + pending, "attempts": len(ops),
                                     "ceiling_usd": spec["ceiling"], "stop_usd": spec["stop"],
                                     "blocked": any(json.loads(o["evidence"]).get("reservation_bound_violated") for o in ops)}
            return {"services": out, "operations": operations}

    def headroom(self) -> dict:
        """Read-only aggregate for public preflight; never includes operation evidence."""
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            rows = db.execute("""
                SELECT s.name, s.ceiling, s.stop,
                       COALESCE(SUM(COALESCE(o.actual, o.reserved)), 0)
                FROM services s LEFT JOIN operations o ON o.service=s.name
                GROUP BY s.name, s.ceiling, s.stop
            """).fetchall()
            blocked = {service for service, evidence in db.execute("SELECT service, evidence FROM operations")
                       if json.loads(evidence).get("reservation_bound_violated")}
        out = {}
        for name, ceiling, stop, used in rows:
            if not all(type(v) in (int, float) and math.isfinite(v) for v in (ceiling, stop, used)):
                raise ValueError("ledger contains non-finite amounts")
            if not 0 <= stop <= ceiling or used < 0:
                raise ValueError("ledger contains invalid limits or usage")
            out[name] = {"ceiling_usd": ceiling, "stop_usd": stop, "accounted_upper_usd": used,
                         "headroom_usd": 0 if name in blocked else max(0, stop - used),
                         "blocked": name in blocked}
        return out
