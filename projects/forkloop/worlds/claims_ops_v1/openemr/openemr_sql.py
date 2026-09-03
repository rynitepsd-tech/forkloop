"""Portable SQL builders for seeding OpenEMR 8.3.0 (MariaDB) *and* the SQLite shim.

Every function returns a string of one or more complete statements, each
terminated by ``;`` and separated by newlines, that executes unchanged on
MariaDB 10.6+ and on SQLite 3 (see ``docs/contracts.md`` §5, "SQL portability
rule").  The grammar is deliberately tiny:

    INSERT INTO t (c1, c2) VALUES (v1, v2);
    UPDATE t SET c1 = v1 WHERE c2 = v2 AND c3 = v3;
    DELETE FROM t WHERE c1 = v1;

with only string / number / NULL literals.  No ``NOW()``, no backticks, no
``ON DUPLICATE KEY``, no ``LAST_INSERT_ID()``, no reliance on auto-increment:
callers always pass explicit primary keys (>= 100000 by convention).

Column names are the real OpenEMR 8.3.0 names from ``sql/database.sql``
(``DOB``, ``providerID``, ``subscriber_DOB``, ``pc_eventDate`` ... are
case-sensitive on Linux MariaDB, so they are spelled exactly).
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from typing import Any, Iterable, Mapping

__all__ = [
    "FORBIDDEN_TOKENS",
    "assert_portable",
    "quote",
    "insert_row",
    "update_row",
    "delete_rows",
    "join_statements",
    "insert_user",
    "insert_insurance_company",
    "insert_patient",
    "insert_insurance",
    "insert_appointment",
    "insert_document",
    "insert_log",
    "update_insurance_policy",
    "update_appointment",
    "end_time",
    "DEFAULT_FACILITY_ID",
    "ADMIN_USER_ID",
    "CAT_OFFICE_VISIT",
    "CAT_ESTABLISHED_PATIENT",
    "CAT_NEW_PATIENT",
    "DOC_CAT_LAB_REPORT",
    "DOC_CAT_MEDICAL_RECORD",
    "DOC_CAT_PATIENT_INFORMATION",
    "DOC_CAT_ADVANCE_DIRECTIVE",
]

# --------------------------------------------------------------------------
# Constants that mirror what a fresh OpenEMR 8.3.0 install contains.
# --------------------------------------------------------------------------

#: ``facility.id`` of "Your Clinic Name Here" inserted by sql/database.sql.
DEFAULT_FACILITY_ID = 3
#: ``users.id`` of the initial ``admin`` user created by the installer.
ADMIN_USER_ID = 1

# openemr_postcalendar_categories.pc_catid (sql/database.sql, v8_3_0)
CAT_OFFICE_VISIT = 5          # 'Office Visit', 900 s
CAT_ESTABLISHED_PATIENT = 9   # 'Established Patient', 900 s
CAT_NEW_PATIENT = 10          # 'New Patient', 1800 s

# categories.id (document categories, sql/database.sql, v8_3_0)
DOC_CAT_LAB_REPORT = 2
DOC_CAT_MEDICAL_RECORD = 3
DOC_CAT_PATIENT_INFORMATION = 4
DOC_CAT_ADVANCE_DIRECTIVE = 6

#: Substrings that must never appear in seeding SQL (contract §5).
FORBIDDEN_TOKENS: tuple[str, ...] = ("NOW(", "`", "ON DUPLICATE", "LAST_INSERT_ID")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class NonPortableSQL(ValueError):
    """Raised when SQL would not run identically on MariaDB and SQLite."""


def assert_portable(sql: str) -> str:
    """Return ``sql`` unchanged, or raise :class:`NonPortableSQL`.

    The check is a case-insensitive substring scan for
    :data:`FORBIDDEN_TOKENS`; it is a tripwire, not a parser.
    """
    upper = sql.upper()
    for tok in FORBIDDEN_TOKENS:
        if tok.upper() in upper:
            raise NonPortableSQL(f"forbidden construct {tok!r} in SQL: {sql[:120]!r}")
    return sql


# --------------------------------------------------------------------------
# Literal quoting
# --------------------------------------------------------------------------

def quote(v: Any) -> str:
    """Render a Python value as a SQL literal valid on both MariaDB and SQLite.

    * ``None``            -> ``NULL``
    * ``bool``            -> ``1`` / ``0``
    * ``int``             -> decimal
    * ``float``           -> ``repr`` (finite only)
    * ``date``/``datetime``/``time`` -> ISO-8601 in single quotes
      (``'2026-09-07'``, ``'2026-09-07 09:30:00'``, ``'09:30:00'``)
    * ``str``             -> single-quoted, embedded ``'`` doubled

    Backslashes and NUL bytes are rejected: MariaDB's default ``sql_mode``
    treats ``\\`` as an escape character while SQLite does not, so a string
    containing one would seed different bytes on the two engines.
    """
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise NonPortableSQL(f"non-finite float {v!r} has no portable literal")
        return repr(v)
    if isinstance(v, _dt.datetime):
        v = v.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(v, _dt.date):
        v = v.isoformat()
    elif isinstance(v, _dt.time):
        v = v.strftime("%H:%M:%S")
    if isinstance(v, str):
        if "\\" in v:
            raise NonPortableSQL("backslash in string literal is not portable (MariaDB escapes it)")
        if "\x00" in v:
            raise NonPortableSQL("NUL byte in string literal")
        return "'" + v.replace("'", "''") + "'"
    raise TypeError(f"cannot quote value of type {type(v).__name__}")


def _ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise NonPortableSQL(f"identifier {name!r} needs quoting, which is not portable")
    return name


# --------------------------------------------------------------------------
# Generic statement builders
# --------------------------------------------------------------------------

def insert_row(table: str, row: Mapping[str, Any]) -> str:
    """``INSERT INTO table (cols) VALUES (vals);`` for one row (dict order kept)."""
    if not row:
        raise ValueError("insert_row: empty row")
    cols = ", ".join(_ident(c) for c in row)
    vals = ", ".join(quote(v) for v in row.values())
    return assert_portable(f"INSERT INTO {_ident(table)} ({cols}) VALUES ({vals});")


def _where(where: Mapping[str, Any]) -> str:
    if not where:
        raise ValueError("refusing to build a statement without a WHERE clause")
    parts = []
    for c, v in where.items():
        if v is None:
            parts.append(f"{_ident(c)} IS NULL")
        else:
            parts.append(f"{_ident(c)} = {quote(v)}")
    return " AND ".join(parts)


def update_row(table: str, sets: Mapping[str, Any], where: Mapping[str, Any]) -> str:
    """``UPDATE table SET a = 1, b = 'x' WHERE k = v AND ...;``"""
    if not sets:
        raise ValueError("update_row: nothing to set")
    assignments = ", ".join(f"{_ident(c)} = {quote(v)}" for c, v in sets.items())
    return assert_portable(f"UPDATE {_ident(table)} SET {assignments} WHERE {_where(where)};")


def delete_rows(table: str, where: Mapping[str, Any]) -> str:
    """``DELETE FROM table WHERE k = v AND ...;``"""
    return assert_portable(f"DELETE FROM {_ident(table)} WHERE {_where(where)};")


def join_statements(statements: Iterable[str]) -> str:
    """Join statement strings with newlines (each is already ``;``-terminated)."""
    return "\n".join(s.strip() for s in statements if s and s.strip()) + "\n"


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def _parse_hms(t: str | _dt.time) -> _dt.time:
    if isinstance(t, _dt.time):
        return t
    parts = t.split(":")
    if len(parts) == 2:
        parts.append("00")
    if len(parts) != 3:
        raise ValueError(f"bad time {t!r}, want HH:MM or HH:MM:SS")
    h, m, s = (int(p) for p in parts)
    return _dt.time(h, m, s)


def end_time(start_time: str | _dt.time, duration_sec: int) -> str:
    """``'HH:MM:SS'`` of ``start_time + duration_sec`` (same day)."""
    st = _parse_hms(start_time)
    base = _dt.datetime(2000, 1, 1, st.hour, st.minute, st.second)
    return (base + _dt.timedelta(seconds=int(duration_sec))).strftime("%H:%M:%S")


def _hms(t: str | _dt.time) -> str:
    return _parse_hms(t).strftime("%H:%M:%S")


def _ymd(d: str | _dt.date) -> str:
    if isinstance(d, _dt.datetime):
        return d.date().isoformat()
    if isinstance(d, _dt.date):
        return d.isoformat()
    _dt.date.fromisoformat(d)  # validate
    return d


def _ymd_hms(d: str | _dt.datetime) -> str:
    if isinstance(d, _dt.datetime):
        return d.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(d, _dt.date):
        return d.isoformat() + " 00:00:00"
    if len(d) == 10:
        _dt.date.fromisoformat(d)
        return d + " 00:00:00"
    _dt.datetime.fromisoformat(d)
    return d


# --------------------------------------------------------------------------
# Typed helpers (one per table forkloop seeds)
# --------------------------------------------------------------------------

def insert_user(
    *,
    id: int,
    username: str,
    fname: str,
    lname: str,
    npi: str | None = None,
    specialty: str | None = None,
    authorized: int = 1,
    calendar: int = 1,
    active: int = 1,
    facility_id: int = DEFAULT_FACILITY_ID,
    password: str | None = None,
) -> str:
    """A provider row in ``users``.

    OpenEMR 8 keeps login hashes in ``users_secure``; ``users.password`` is a
    legacy column and stays NULL, so seeded providers cannot log in (they do
    not need to -- the agent uses ``admin``).  ``authorized=1`` marks the user
    as a provider, ``calendar=1`` makes them selectable in the calendar.
    """
    return insert_row("users", {
        "id": int(id),
        "username": username,
        "password": password,
        "authorized": int(authorized),
        "fname": fname,
        "lname": lname,
        "facility_id": int(facility_id),
        "calendar": int(calendar),
        "active": int(active),
        "npi": npi,
        "specialty": specialty,
    })


def insert_insurance_company(*, id: int, name: str) -> str:
    return insert_row("insurance_companies", {"id": int(id), "name": name})


def insert_patient(
    *,
    pid: int,
    fname: str,
    lname: str,
    dob: str | _dt.date,
    sex: str,
    street: str = "",
    city: str = "",
    state: str = "",
    postal_code: str = "",
    phone_home: str = "",
    provider_id: int | None = None,
    pubpid: str | None = None,
    id: int | None = None,
) -> str:
    """A ``patient_data`` row.  ``id`` defaults to ``pid``; ``pubpid`` (the
    "External ID" shown in the UI) defaults to ``str(pid)``."""
    return insert_row("patient_data", {
        "id": int(id if id is not None else pid),
        "pid": int(pid),
        "pubpid": pubpid if pubpid is not None else str(pid),
        "fname": fname,
        "lname": lname,
        "DOB": _ymd(dob),
        "sex": sex,
        "street": street,
        "city": city,
        "state": state,
        "postal_code": postal_code,
        "phone_home": phone_home,
        "providerID": int(provider_id) if provider_id is not None else None,
    })


def insert_insurance(
    *,
    id: int,
    pid: int,
    company_id: int,
    plan_name: str,
    policy_number: str,
    group_number: str,
    subscriber_fname: str,
    subscriber_lname: str,
    subscriber_dob: str | _dt.date,
    subscriber_relationship: str = "self",
    type: str = "primary",
    date: str | _dt.date = "2025-01-01",
    subscriber_sex: str | None = None,
    subscriber_street: str | None = None,
    subscriber_city: str | None = None,
    subscriber_state: str | None = None,
    subscriber_postal_code: str | None = None,
) -> str:
    """An ``insurance_data`` row.

    OpenEMR 8.3's insurance editor refuses to save a policy whose subscriber sex,
    street, city, state or ZIP is blank (measured 2026-09-03: family-2 seed 0 could
    not be completed through the GUI), so seed them from the patient row. They are
    optional here so older callers keep emitting byte-identical SQL.

    ``provider`` is OpenEMR's (string) foreign key to ``insurance_companies.id``.
    ``date`` is the policy effective date; OpenEMR treats the row with the
    latest ``date <= today`` per (pid, type) as current, and (pid, type, date)
    is UNIQUE, so per-episode extra policies must use a distinct date.
    """
    if type not in ("primary", "secondary", "tertiary"):
        raise ValueError(f"insurance type {type!r} not in enum")
    return insert_row("insurance_data", {
        "id": int(id),
        "type": type,
        "provider": str(int(company_id)),
        "plan_name": plan_name,
        "policy_number": policy_number,
        "group_number": group_number,
        "subscriber_fname": subscriber_fname,
        "subscriber_lname": subscriber_lname,
        "subscriber_DOB": _ymd(subscriber_dob),
        "subscriber_relationship": subscriber_relationship,
        **{k: v for k, v in {"subscriber_sex": subscriber_sex, "subscriber_street": subscriber_street,
                              "subscriber_city": subscriber_city, "subscriber_state": subscriber_state,
                              "subscriber_postal_code": subscriber_postal_code}.items() if v is not None},
        "pid": int(pid),
        "date": _ymd(date),
    })


def insert_appointment(
    *,
    pc_eid: int,
    pid: int,
    provider_id: int,
    event_date: str | _dt.date,
    start_time: str | _dt.time,
    duration_sec: int = 900,
    pc_catid: int = CAT_OFFICE_VISIT,
    title: str = "Office Visit",
    apptstatus: str = "-",
    facility: int = DEFAULT_FACILITY_ID,
    hometext: str = "",
    end_date: str | _dt.date | None = None,
    informant: int = ADMIN_USER_ID,
) -> str:
    """An ``openemr_postcalendar_events`` row shaped like the calendar's own
    ``InsertEvent()`` (library/encounter_events.inc.php, v8_3_0): single,
    non-recurring, ``pc_eventstatus = 1``, ``pc_sharing = 1``,
    ``pc_multiple = 0`` (NOT NULL on MariaDB, so it must be explicit),
    ``pc_duration`` in seconds, ``pc_endTime = start + duration``.
    ``pc_aid`` / ``pc_pid`` are varchar columns in OpenEMR, hence quoted."""
    return insert_row("openemr_postcalendar_events", {
        "pc_eid": int(pc_eid),
        "pc_catid": int(pc_catid),
        "pc_multiple": 0,
        "pc_aid": str(int(provider_id)),
        "pc_pid": str(int(pid)),
        "pc_title": title,
        "pc_hometext": hometext,
        "pc_informant": str(int(informant)),
        "pc_eventDate": _ymd(event_date),
        "pc_endDate": _ymd(end_date if end_date is not None else event_date),
        "pc_duration": int(duration_sec),
        "pc_recurrtype": 0,
        "pc_startTime": _hms(start_time),
        "pc_endTime": end_time(start_time, duration_sec),
        "pc_alldayevent": 0,
        "pc_apptstatus": apptstatus,
        "pc_eventstatus": 1,
        "pc_sharing": 1,
        "pc_facility": int(facility),
    })


def update_appointment(*, pc_eid: int, **fields: Any) -> str:
    """``UPDATE openemr_postcalendar_events SET ... WHERE pc_eid = ?;``

    Accepts any real column name (``pc_eventDate``, ``pc_startTime``, ...).
    If ``pc_startTime`` and ``pc_duration`` are both given, ``pc_endTime`` is
    recomputed unless supplied explicitly.
    """
    sets = dict(fields)
    if "pc_startTime" in sets and "pc_duration" in sets and "pc_endTime" not in sets:
        sets["pc_endTime"] = end_time(sets["pc_startTime"], sets["pc_duration"])
    if "pc_startTime" in sets:
        sets["pc_startTime"] = _hms(sets["pc_startTime"])
    for k in ("pc_eventDate", "pc_endDate"):
        if k in sets and sets[k] is not None:
            sets[k] = _ymd(sets[k])
    return update_row("openemr_postcalendar_events", sets, {"pc_eid": int(pc_eid)})


def insert_document(
    *,
    doc_id: int,
    pid: int,
    name: str,
    size: int,
    content_hash: str,
    docdate: str | _dt.date,
    category_id: int = DOC_CAT_MEDICAL_RECORD,
    mimetype: str = "application/pdf",
    url: str | None = None,
    date: str | _dt.datetime | None = None,
) -> str:
    """Two statements: the ``documents`` row and its ``categories_to_documents``
    link.  ``url`` defaults to the contract §8 layout
    ``file:///var/www/openemr/sites/default/documents/<pid>/<name>`` (via
    :mod:`docs_paths`).  ``content_hash`` is whatever hex digest the caller
    computed over the file bytes (OpenEMR itself stores sha3-512).

    ``revision`` is ``timestamp NOT NULL`` without a default in 8.3.0's
    schema; MariaDB >= 10.10 (explicit_defaults_for_timestamp=ON) rejects an
    INSERT that omits it under strict mode, so it is always written.
    """
    if url is None:
        try:
            from .docs_paths import document_url  # package import
        except ImportError:  # script / flat import
            from docs_paths import document_url
        url = document_url(pid, name)
    when = _ymd_hms(date if date is not None else docdate)
    doc = insert_row("documents", {
        "id": int(doc_id),
        "type": "file_url",
        "url": url,
        "mimetype": mimetype,
        "docdate": _ymd(docdate),
        "foreign_id": int(pid),
        "name": name,
        "hash": content_hash,
        "size": int(size),
        "date": when,
        "revision": when,
    })
    link = insert_row("categories_to_documents", {
        "category_id": int(category_id),
        "document_id": int(doc_id),
    })
    return join_statements([doc, link]).rstrip("\n")


def insert_log(
    *,
    id: int,
    event: str,
    category: str,
    user: str,
    patient_id: int | None,
    comments: str,
    date: str | _dt.datetime,
    success: int = 1,
) -> str:
    """A ``log`` (audit) row.  Vocabulary used by OpenEMR's EventAuditLogger:
    ``event`` = ``"<category-slug>-<insert|update|delete>"`` e.g.
    ``patient-record-insert``, ``patient-record-update``, ``scheduling-update``;
    ``category`` = ``Patient Demographics`` | ``Patient Insurance`` |
    ``Scheduling`` | ...  ``date`` must be an explicit timestamp (no NOW())."""
    return insert_row("log", {
        "id": int(id),
        "date": _ymd_hms(date),
        "event": event,
        "category": category,
        "user": user,
        "patient_id": int(patient_id) if patient_id is not None else None,
        "comments": comments,
        "success": int(success),
    })


def update_insurance_policy(
    *,
    pid: int,
    policy_number: str,
    plan_name: str | None = None,
    group_number: str | None = None,
    company_id: int | None = None,
    type: str = "primary",
) -> str:
    """``UPDATE insurance_data SET policy_number = ... WHERE pid = ? AND type = ?;``

    Used both by generators (partial starting state) and by the oracle's
    expected-value derivation.  Only the given optional fields are touched.
    """
    sets: dict[str, Any] = {"policy_number": policy_number}
    if plan_name is not None:
        sets["plan_name"] = plan_name
    if group_number is not None:
        sets["group_number"] = group_number
    if company_id is not None:
        sets["provider"] = str(int(company_id))
    return update_row("insurance_data", sets, {"pid": int(pid), "type": type})
