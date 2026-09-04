"""Deterministic verifier: effects + invariants → reward, milestones, reason code.

No LLM anywhere in this file. Expected values never leave the controller.
See docs/contracts.md §6.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .dbaccess import DbAccess

REASON_CODES = (
    "OK", "WRONG_RECORD", "NOT_DONE", "WRONG_VALUE", "DUPLICATE_SIDE_EFFECT", "COLLATERAL_EDIT",
    "DIRECT_DB_WRITE", "FORBIDDEN_SCREEN", "MISSING_ATTACHMENT", "WRONG_ATTACHMENT",
    "PROVIDER_CHANGED", "WRONG_SLOT", "BUDGET_EXCEEDED", "INVALID_ACTION_LIMIT", "CHECK_FAILED",
    "ORACLE_ERROR",
)

CHECK_KINDS = ("query", "count", "baseline_checksum", "ui_path_only", "forbidden_screens")


@dataclass
class Check:
    id: str
    kind: str = "query"
    db: Optional[str] = None
    sql: Optional[str] = None
    params: list[Any] = field(default_factory=list)
    equals: Any = None
    reason_code: str = "CHECK_FAILED"
    allow: Optional[dict[str, list[Any]]] = None
    exempt_tables: Optional[list[str]] = None
    #: query only: compare with one of "eq" (default), "in", "ne", "ge", "le", "contains"
    op: str = "eq"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "kind": self.kind, "reason_code": self.reason_code}
        if self.db is not None:
            d["db"] = self.db
        if self.sql is not None:
            d["sql"] = self.sql
        if self.params:
            d["params"] = list(self.params)
        if self.equals is not None:
            d["equals"] = self.equals
        if self.allow is not None:
            d["allow"] = self.allow
        if self.exempt_tables is not None:
            d["exempt_tables"] = list(self.exempt_tables)
        if self.op != "eq":
            d["op"] = self.op
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Check":
        return Check(
            id=d["id"], kind=d.get("kind", "query"), db=d.get("db"), sql=d.get("sql"),
            params=list(d.get("params", [])), equals=d.get("equals"),
            reason_code=d.get("reason_code", "CHECK_FAILED"), allow=d.get("allow"),
            exempt_tables=d.get("exempt_tables"), op=d.get("op", "eq"),
        )


@dataclass
class OracleSpec:
    effects: list[Check] = field(default_factory=list)
    invariants: list[Check] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"effects": [c.to_dict() for c in self.effects],
                "invariants": [c.to_dict() for c in self.invariants]}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "OracleSpec":
        return OracleSpec(effects=[Check.from_dict(c) for c in d.get("effects", [])],
                          invariants=[Check.from_dict(c) for c in d.get("invariants", [])])

    def validate(self) -> None:
        seen: set[str] = set()
        for c in [*self.effects, *self.invariants]:
            if c.id in seen:
                raise ValueError(f"duplicate check id {c.id!r}")
            seen.add(c.id)
            if c.kind not in CHECK_KINDS:
                raise ValueError(f"check {c.id}: unknown kind {c.kind!r}")
            if c.kind in ("query", "count") and not (c.db and c.sql):
                raise ValueError(f"check {c.id}: query needs db and sql")
            if c.reason_code not in REASON_CODES:
                raise ValueError(f"check {c.id}: unknown reason code {c.reason_code!r}")


@dataclass
class Verdict:
    reward: float
    milestones: float
    reason_code: str
    failed: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    n_effects: int = 0
    n_invariants: int = 0

    @property
    def success(self) -> bool:
        return self.reward >= 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"reward": self.reward, "milestones": round(self.milestones, 4), "reason_code": self.reason_code,
                "failed": list(self.failed), "details": self.details, "n_effects": self.n_effects,
                "n_invariants": self.n_invariants, "success": self.success}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Verdict":
        return Verdict(reward=float(d["reward"]), milestones=float(d.get("milestones", 0.0)),
                       reason_code=d.get("reason_code", "CHECK_FAILED"), failed=list(d.get("failed", [])),
                       details=dict(d.get("details", {})), n_effects=int(d.get("n_effects", 0)),
                       n_invariants=int(d.get("n_invariants", 0)))

    @staticmethod
    def error(msg: str) -> "Verdict":
        return Verdict(reward=0.0, milestones=0.0, reason_code="ORACLE_ERROR", failed=["oracle"],
                       details={"oracle": {"error": msg, "passed": False}})


# --------------------------------------------------------------------------- baseline


def row_hash(values: list[Any]) -> str:
    return hashlib.md5(json.dumps(values, sort_keys=False, default=str, ensure_ascii=False).encode()).hexdigest()


@dataclass
class TableSnapshot:
    pk: str
    rows: dict[str, str]  # pk value (as str) → hash


@dataclass
class Baseline:
    """Row hashes of every checksummed table plus append-only watermarks."""

    tables: dict[str, TableSnapshot] = field(default_factory=dict)    # "db.table" → snapshot
    watermarks: dict[str, int] = field(default_factory=dict)           # "db.table" → max pk at seed time
    ignore_columns: dict[str, list[str]] = field(default_factory=dict)  # db → columns left out of every row hash

    @staticmethod
    async def capture(dbs: dict[str, "DbAccess"], checksum_tables: dict[str, list[str]],
                      primary_keys: dict[str, str], watermark_tables: dict[str, list[str]],
                      ignore_columns: Optional[dict[str, list[str]]] = None) -> "Baseline":
        b = Baseline(ignore_columns={k: list(v) for k, v in (ignore_columns or {}).items()})
        for db_name, tables in checksum_tables.items():
            db = dbs[db_name]
            hashes = await db.row_hashes(tables, primary_keys, b.ignore_columns.get(db_name))
            for t, rows in hashes.items():
                b.tables[f"{db_name}.{t}"] = TableSnapshot(pk=primary_keys.get(f"{db_name}.{t}", "id"), rows=rows)
        for db_name, tables in watermark_tables.items():
            db = dbs[db_name]
            for t in tables:
                pk = primary_keys.get(f"{db_name}.{t}", "id")
                b.watermarks[f"{db_name}.{t}"] = await db.max_pk(t, pk)
        return b

    def to_dict(self) -> dict[str, Any]:
        return {"tables": {k: {"pk": v.pk, "rows": v.rows} for k, v in self.tables.items()},
                "watermarks": dict(self.watermarks), "ignore_columns": dict(self.ignore_columns)}


@dataclass
class RowChange:
    table: str  # "db.table"
    pk: str
    kind: str   # changed | added | deleted


async def diff_baseline(baseline: Baseline, dbs: dict[str, "DbAccess"], primary_keys: dict[str, str]) -> list[RowChange]:
    changes: list[RowChange] = []
    by_db: dict[str, list[str]] = {}
    for key in baseline.tables:
        db_name, t = key.split(".", 1)
        by_db.setdefault(db_name, []).append(t)
    for db_name, tables in by_db.items():
        now = await dbs[db_name].row_hashes(tables, primary_keys, baseline.ignore_columns.get(db_name))
        for t in tables:
            key = f"{db_name}.{t}"
            before = baseline.tables[key].rows
            after = now.get(t, {})
            for pk, h in after.items():
                if pk not in before:
                    changes.append(RowChange(key, pk, "added"))
                elif before[pk] != h:
                    changes.append(RowChange(key, pk, "changed"))
            for pk in before:
                if pk not in after:
                    changes.append(RowChange(key, pk, "deleted"))
    changes.sort(key=lambda c: (c.table, c.kind, c.pk))
    return changes


# --------------------------------------------------------------------------- context


@dataclass
class OracleContext:
    """Everything the oracle needs besides the spec. Built by the env at reset."""

    dbs: dict[str, "DbAccess"]
    baseline: Optional[Baseline]
    primary_keys: dict[str, str]                  # "db.table" → pk column (default id)
    exempt_tables: list[str]                      # "db.table" append-only tables ignored by checksum
    audit: dict[str, dict[str, str]]              # db → {table, entity_col, id_col, pk}
    page_views: Optional[dict[str, str]]          # {db, table, path_col, pk}
    forbidden_paths: list[str]
    #: maps "db.table" → audit entity name written by the UI for that table (portal: claims → "claim")
    audit_entity_names: dict[str, str] = field(default_factory=dict)
    #: "db.table" → column whose value identifies the row in that db's audit table
    #: (e.g. OpenEMR's log keys by patient_id, so insurance_data changes are looked up via "pid")
    audit_id_lookup: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- engine


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "eq":
        return _norm(actual) == _norm(expected)
    if op == "ne":
        return _norm(actual) != _norm(expected)
    if op == "in":
        return _norm(actual) in [_norm(e) for e in (expected or [])]
    if op == "ge":
        return actual is not None and float(actual) >= float(expected)
    if op == "le":
        return actual is not None and float(actual) <= float(expected)
    if op == "contains":
        return actual is not None and str(expected) in str(actual)
    raise ValueError(f"unknown op {op!r}")


def _norm(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        try:
            f = float(s)
            if f.is_integer() and s.lstrip("-").isdigit():
                return int(f)
        except ValueError:
            pass
        return s
    return v


class Oracle:
    """Evaluates an :class:`OracleSpec` against live DB state."""

    def __init__(self, ctx: OracleContext) -> None:
        self.ctx = ctx

    async def evaluate(self, spec: OracleSpec) -> Verdict:
        spec.validate()
        details: dict[str, Any] = {}
        failed: list[str] = []
        reason = "OK"
        passed_effects = 0
        for c in spec.effects:
            ok = await self._run_check(c, details)
            if ok:
                passed_effects += 1
            else:
                failed.append(c.id)
                if reason == "OK":
                    reason = c.reason_code
        for c in spec.invariants:
            ok = await self._run_check(c, details)
            if not ok:
                failed.append(c.id)
                if reason == "OK":
                    reason = c.reason_code
        n_eff = len(spec.effects)
        milestones = (passed_effects / n_eff) if n_eff else (0.0 if failed else 1.0)
        reward = 1.0 if not failed else 0.0
        return Verdict(reward=reward, milestones=milestones, reason_code=reason, failed=failed,
                       details=details, n_effects=n_eff, n_invariants=len(spec.invariants))

    async def _run_check(self, c: Check, details: dict[str, Any]) -> bool:
        try:
            if c.kind in ("query", "count"):
                rows = await self.ctx.dbs[c.db].query(c.sql, c.params)  # type: ignore[index,arg-type]
                actual = None
                if rows:
                    first = rows[0]
                    actual = next(iter(first.values())) if first else None
                if c.kind == "count" and actual is None:
                    actual = 0
                ok = _compare(c.op, actual, c.equals)
                details[c.id] = {"expected": c.equals, "actual": actual, "passed": ok, "op": c.op}
                return ok
            if c.kind == "baseline_checksum":
                return await self._check_baseline(c, details)
            if c.kind == "ui_path_only":
                return await self._check_ui_path(c, details)
            if c.kind == "forbidden_screens":
                return await self._check_forbidden(c, details)
            raise ValueError(f"unknown check kind {c.kind!r}")
        except Exception as e:  # noqa: BLE001 - every check must produce a verdict
            details[c.id] = {"passed": False, "error": f"{type(e).__name__}: {e}"}
            return False

    async def _collateral_changes(self, c: Check) -> list[RowChange]:
        if self.ctx.baseline is None:
            raise RuntimeError("baseline_checksum requires a captured baseline")
        changes = await diff_baseline(self.ctx.baseline, self.ctx.dbs, self.ctx.primary_keys)
        exempt = set(self.ctx.exempt_tables) | set(c.exempt_tables or [])
        allow = {k: {str(v) for v in vs} for k, vs in (c.allow or {}).items()}
        bad: list[RowChange] = []
        for ch in changes:
            if ch.table in exempt:
                continue
            if ch.pk in allow.get(ch.table, set()):
                continue
            bad.append(ch)
        return bad

    async def _check_baseline(self, c: Check, details: dict[str, Any]) -> bool:
        bad = await self._collateral_changes(c)
        details[c.id] = {"passed": not bad,
                         "unexpected_changes": [{"table": b.table, "pk": b.pk, "kind": b.kind} for b in bad[:50]],
                         "n_unexpected": len(bad)}
        return not bad

    async def _check_ui_path(self, c: Check, details: dict[str, Any]) -> bool:
        """Every allowed row change must be matched by an audit row written after seeding."""
        if self.ctx.baseline is None:
            raise RuntimeError("ui_path_only requires a captured baseline")
        changes = await diff_baseline(self.ctx.baseline, self.ctx.dbs, self.ctx.primary_keys)
        exempt = set(self.ctx.exempt_tables) | set(c.exempt_tables or [])
        missing: list[dict[str, Any]] = []
        audit_rows: dict[str, list[dict[str, Any]]] = {}
        checked = 0
        for ch in changes:
            if ch.table in exempt:
                continue
            db_name, table = ch.table.split(".", 1)
            audit = self.ctx.audit.get(db_name)
            if not audit:
                continue
            checked += 1
            wm = self.ctx.baseline.watermarks.get(f"{db_name}.{audit['table']}", 0)
            entity = self.ctx.audit_entity_names.get(ch.table, table)
            pk_col = audit.get("pk", "id")
            audit_id: Any = ch.pk
            lookup_col = self.ctx.audit_id_lookup.get(ch.table)
            if lookup_col and ch.kind != "deleted":
                row_pk = self.ctx.primary_keys.get(ch.table, "id")
                got = await self.ctx.dbs[db_name].query(f"SELECT {lookup_col} AS v FROM {table} WHERE {row_pk} = ?", [ch.pk])
                if got:
                    audit_id = got[0]["v"]
            sql = (f"SELECT COUNT(*) AS n FROM {audit['table']} WHERE {pk_col} > ? "
                   f"AND {audit['entity_col']} = ? AND {audit['id_col']} = ?")
            rows = await self.ctx.dbs[db_name].query(sql, [wm, entity, audit_id])
            n = int(next(iter(rows[0].values()))) if rows else 0
            if n == 0:
                # OpenEMR's log table keys by patient_id — the *session's* active chart, not the
                # patient the changed row belongs to (EventAuditLogger::auditSQLEvent; a calendar
                # save made from the Finder with no chart open logs under patient_id 0). Its
                # comments hold the SQL with the bound values appended, so fall back to "any audit
                # row after the watermark for this patient id, or whose SQL names this row's
                # primary key" for that dialect.
                if audit.get("loose"):
                    ccol = audit.get("comments_col", "comments")
                    sql2 = (f"SELECT COUNT(*) AS n FROM {audit['table']} WHERE {pk_col} > ? "
                            f"AND ({audit['id_col']} = ? OR {ccol} LIKE ? OR ({ccol} LIKE ? AND {ccol} LIKE ?))")
                    rows2 = await self.ctx.dbs[db_name].query(
                        sql2, [wm, audit_id, f"%{audit_id}%", f"%{table}%", f"%'{ch.pk}'%"])
                    n = int(next(iter(rows2[0].values()))) if rows2 else 0
            if n == 0:
                missing.append({"table": ch.table, "pk": ch.pk, "kind": ch.kind})
                # Keep the evidence: what the audit table actually holds after the watermark, so a
                # false DIRECT_DB_WRITE can be diagnosed from the verdict alone (the VM is gone by then).
                if db_name not in audit_rows:
                    ccol = audit.get("comments_col")
                    cols = f"{pk_col} AS pk, {audit['entity_col']} AS entity, {audit['id_col']} AS entity_id"
                    if ccol:
                        cols += f", SUBSTR({ccol}, 1, 300) AS comments"
                    try:
                        got = await self.ctx.dbs[db_name].query(
                            f"SELECT {cols} FROM {audit['table']} WHERE {pk_col} > ? ORDER BY {pk_col} DESC", [wm])
                        audit_rows[db_name] = [dict(r) for r in got[:20]]
                    except Exception as e:  # noqa: BLE001 - diagnostics only
                        audit_rows[db_name] = [{"error": f"{type(e).__name__}: {str(e)[:200]}"}]
        details[c.id] = {"passed": not missing, "checked": checked, "unaudited_changes": missing[:50]}
        if audit_rows:
            details[c.id]["audit_rows_after_watermark"] = audit_rows
        return not missing

    async def _check_forbidden(self, c: Check, details: dict[str, Any]) -> bool:
        pv = self.ctx.page_views
        if not pv or self.ctx.baseline is None:
            details[c.id] = {"passed": True, "note": "no page_views table configured"}
            return True
        wm = self.ctx.baseline.watermarks.get(f"{pv['db']}.{pv['table']}", 0)
        rows = await self.ctx.dbs[pv["db"]].query(
            f"SELECT {pv['path_col']} AS p FROM {pv['table']} WHERE {pv.get('pk', 'id')} > ? ORDER BY {pv.get('pk', 'id')}", [wm])
        hits = [r["p"] for r in rows if any(str(r["p"]).startswith(fp) for fp in self.ctx.forbidden_paths)]
        details[c.id] = {"passed": not hits, "visited": hits[:20], "n_pages": len(rows)}
        return not hits


__all__ = ["Check", "OracleSpec", "Verdict", "Oracle", "OracleContext", "Baseline", "TableSnapshot",
           "RowChange", "diff_baseline", "row_hash", "REASON_CODES", "CHECK_KINDS"]
