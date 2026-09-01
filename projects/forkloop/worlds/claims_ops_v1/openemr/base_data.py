"""Deterministic base OpenEMR dataset for the claims-ops-v1 golden snapshot.

``generate()`` is a pure function of the module constants (seed 20260901,
anchor Monday 2026-09-07).  It produces:

* 6 provider ``users``            ids 100001-100006  (see :data:`PROVIDERS`)
* 4 ``insurance_companies``        ids 100001-100004
* 40 ``patient_data`` rows         pids 100001-100040
* 40 ``insurance_data`` rows       ids 100001-100040 (one primary per patient)
* ~80 ``openemr_postcalendar_events`` over the 6 weeks from the anchor
* 40 ``log`` rows (one ``patient-record-insert`` per patient)

Run as a module to (re)write ``base_data.json`` and ``providers.json`` next to
this file, or ``--sql`` to print the portable seeding SQL::

    python -m worlds.claims_ops_v1.openemr.base_data            # write json
    python -m worlds.claims_ops_v1.openemr.base_data --sql      # print SQL
    python3 base_data.py --sql > base_data.sql                  # also works flat

PROVIDER CONTRACT: the payer portal keeps its own ``providers`` table.  It
MUST be populated from :data:`PROVIDERS` (same names, NPIs and specialties,
same order) so that a provider named in a task instruction resolves to the
same person in both apps.  ``providers.json`` is the machine-readable copy;
``portal/base_data.py`` should import :data:`PROVIDERS` or read that file
rather than re-declaring the list.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import random
import sys
from typing import Any

try:  # package import (worlds.claims_ops_v1.openemr.base_data)
    from . import openemr_sql as _sql
except ImportError:  # flat import (python3 base_data.py inside the VM)
    import openemr_sql as _sql  # type: ignore[no-redef]

__all__ = [
    "SEED",
    "ANCHOR_DATE",
    "WEEKS",
    "PROVIDERS",
    "PROVIDER_ID_BASE",
    "INSURANCE_COMPANIES",
    "PATIENT_PID_BASE",
    "PATIENT_COUNT",
    "TABLE_ORDER",
    "provider_records",
    "generate",
    "render_base_sql",
    "load_base_data",
    "npi_is_valid",
]

# --------------------------------------------------------------------------
# Fixed constants
# --------------------------------------------------------------------------

SEED = 20260901
ANCHOR_DATE = _dt.date(2026, 9, 7)  # Monday
WEEKS = 6
PATIENT_COUNT = 40
PATIENT_PID_BASE = 100001
PROVIDER_ID_BASE = 100001
INSURANCE_COMPANY_ID_BASE = 100001
INSURANCE_ID_BASE = 100001
EVENT_ID_BASE = 100001
LOG_ID_BASE = 100001

#: (name, npi, specialty) -- the six providers shared with the portal.
#: NPIs are Luhn-valid (prefix 80840) and synthetic; 1234567893 is the
#: canonical example NPI from the NPI check-digit specification.
PROVIDERS: list[tuple[str, str, str]] = [
    ("Aiko Nakamura", "1234567893", "Family Medicine"),
    ("Marcus Oyelaran", "1745638209", "Internal Medicine"),
    ("Priya Venkataraman", "2859401137", "Pediatrics"),
    ("Daniel Whitfield", "3962715041", "Orthopedic Surgery"),
    ("Sofia Castellanos", "4173859669", "Cardiology"),
    ("Elena Marchetti", "5281930771", "Dermatology"),
]

#: (insurance_companies.id, name, [plan names], policy-number prefix, policy digits)
INSURANCE_COMPANIES: list[dict[str, Any]] = [
    {"id": 100001, "name": "Aetna", "plans": ["Aetna Choice POS II", "Aetna Open Access HMO"], "prefix": "W", "digits": 9},
    {"id": 100002, "name": "Blue Cross Blue Shield", "plans": ["BCBS PPO Blue Advantage", "BCBS HMO Blue Select"], "prefix": "ZGP", "digits": 9},
    {"id": 100003, "name": "UnitedHealthcare", "plans": ["UHC Choice Plus", "UHC Navigate HMO"], "prefix": "9", "digits": 8},
    {"id": 100004, "name": "Cigna", "plans": ["Cigna Open Access Plus", "Cigna LocalPlus"], "prefix": "U", "digits": 8},
]

_FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
    "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph",
    "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Christopher", "Lisa",
    "Daniel", "Nancy", "Matthew", "Betty", "Anthony", "Margaret", "Mark", "Sandra",
    "Donald", "Ashley", "Steven", "Kimberly", "Paul", "Emily", "Andrew", "Donna",
    "Joshua", "Michelle", "Kenneth", "Carol", "Kevin", "Amanda", "Brian", "Dorothy",
    "George", "Melissa", "Timothy", "Deborah",
]
_LAST_NAMES = [
    "Abernathy", "Bellweather", "Calloway", "Dunmore", "Ellison", "Fairbanks",
    "Galloway", "Hartigan", "Ingersoll", "Jamison", "Kowalczyk", "Lindqvist",
    "Mendoza", "Northcutt", "Okafor", "Pemberton", "Quintero", "Rasmussen",
    "Sorensen", "Thackeray", "Underhill", "Vasquez", "Wetherby", "Yamaguchi",
    "Zielinski", "Ashworth", "Brightman", "Castellano", "Delacroix", "Espinoza",
]
_STREETS = [
    "Oak St", "Maple Ave", "Cedar Ln", "Elm St", "Pine Rd", "Birch Ct",
    "Willow Way", "Juniper Dr", "Sycamore Blvd", "Magnolia Pl", "Hickory Ln",
    "Aspen Ct", "Redwood Dr", "Poplar St", "Chestnut Ave",
]
# (city, state, zip3) -- all in one metro so the clinic looks local
_CITIES = [
    ("Austin", "TX", "787"),
    ("Round Rock", "TX", "786"),
    ("Cedar Park", "TX", "786"),
    ("Pflugerville", "TX", "786"),
    ("Georgetown", "TX", "786"),
]

# Appointment categories: (pc_catid, title, duration_sec, weight)
_APPT_CATEGORIES = [
    (_sql.CAT_OFFICE_VISIT, "Office Visit", 900, 6),
    (_sql.CAT_ESTABLISHED_PATIENT, "Established Patient", 900, 3),
    (_sql.CAT_NEW_PATIENT, "New Patient", 1800, 1),
]
_SLOT_MINUTES = 15
_DAY_START = 8 * 60          # 08:00
_DAY_END = 17 * 60           # 17:00 (appointments must end by then)
_LUNCH = (12 * 60, 13 * 60)  # no appointments starting 12:00-12:59

TABLE_ORDER = [
    "users",
    "insurance_companies",
    "patient_data",
    "insurance_data",
    "openemr_postcalendar_events",
    "log",
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def npi_is_valid(npi: str) -> bool:
    """Luhn check with the ``80840`` prefix used for NPIs."""
    if not (isinstance(npi, str) and len(npi) == 10 and npi.isdigit()):
        return False
    s = "80840" + npi
    total = 0
    for i, ch in enumerate(reversed(s)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _split_name(name: str) -> tuple[str, str]:
    first, _, last = name.partition(" ")
    return first, last


def provider_records() -> list[dict[str, Any]]:
    """PROVIDERS with ids/usernames attached (this is what providers.json holds)."""
    out = []
    for i, (name, npi, specialty) in enumerate(PROVIDERS):
        fname, lname = _split_name(name)
        out.append({
            "openemr_user_id": PROVIDER_ID_BASE + i,
            "name": name,
            "fname": fname,
            "lname": lname,
            "npi": npi,
            "specialty": specialty,
            "username": (fname[0] + lname).lower(),
        })
    return out


def _random_dob(rng: random.Random) -> _dt.date:
    lo = _dt.date(1940, 1, 1)
    hi = _dt.date(2006, 12, 31)
    return lo + _dt.timedelta(days=rng.randrange((hi - lo).days + 1))


def _policy_number(rng: random.Random, company: dict[str, Any]) -> str:
    return company["prefix"] + "".join(str(rng.randrange(10)) for _ in range(company["digits"]))


def _fmt_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


# --------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------

def generate(seed: int = SEED) -> dict[str, Any]:
    """Return the base dataset as JSON-ready dicts keyed by real table names."""
    rng = random.Random(seed)
    providers = provider_records()
    provider_ids = [p["openemr_user_id"] for p in providers]

    users = [
        {
            "id": p["openemr_user_id"],
            "username": p["username"],
            "password": None,
            "authorized": 1,
            "fname": p["fname"],
            "lname": p["lname"],
            "facility_id": _sql.DEFAULT_FACILITY_ID,
            "calendar": 1,
            "active": 1,
            "npi": p["npi"],
            "specialty": p["specialty"],
        }
        for p in providers
    ]

    companies = [{"id": c["id"], "name": c["name"]} for c in INSURANCE_COMPANIES]

    # ---- patients -------------------------------------------------------
    patients: list[dict[str, Any]] = []
    used_names: set[tuple[str, str]] = set()
    while len(patients) < PATIENT_COUNT:
        fname = rng.choice(_FIRST_NAMES)
        lname = rng.choice(_LAST_NAMES)
        if (fname, lname) in used_names:
            continue
        used_names.add((fname, lname))
        pid = PATIENT_PID_BASE + len(patients)
        city, state, zip3 = rng.choice(_CITIES)
        patients.append({
            "id": pid,
            "pid": pid,
            "pubpid": str(pid),
            "fname": fname,
            "lname": lname,
            "DOB": _random_dob(rng).isoformat(),
            "sex": rng.choice(["Female", "Male"]),
            "street": f"{rng.randrange(100, 9900)} {rng.choice(_STREETS)}",
            "city": city,
            "state": state,
            "postal_code": f"{zip3}{rng.randrange(10, 99):02d}",
            "phone_home": f"512-555-{rng.randrange(100, 200):04d}",
            "providerID": rng.choice(provider_ids),
        })

    # ---- insurance ------------------------------------------------------
    insurance: list[dict[str, Any]] = []
    for i, p in enumerate(patients):
        company = rng.choice(INSURANCE_COMPANIES)
        eff = _dt.date(rng.choice([2024, 2025]), rng.randrange(1, 13), 1)
        if eff > _dt.date(2025, 6, 1):
            eff = _dt.date(2025, 6, 1)
        insurance.append({
            "id": INSURANCE_ID_BASE + i,
            "type": "primary",
            "provider": str(company["id"]),
            "plan_name": rng.choice(company["plans"]),
            "policy_number": _policy_number(rng, company),
            "group_number": str(rng.randrange(10000, 9999999)),
            "subscriber_fname": p["fname"],
            "subscriber_lname": p["lname"],
            "subscriber_DOB": p["DOB"],
            "subscriber_relationship": "self",
            "pid": p["pid"],
            "date": eff.isoformat(),
        })

    # ---- appointments ---------------------------------------------------
    cat_weights = [c[3] for c in _APPT_CATEGORIES]
    booked_provider: set[tuple[int, str, int]] = set()   # (provider, date, start_min)
    booked_patient: set[tuple[int, str]] = set()         # (pid, date) one visit per day
    raw_events: list[dict[str, Any]] = []
    for p in patients:
        n_appts = rng.choice([1, 2, 2, 3])
        for _ in range(n_appts):
            for _attempt in range(50):
                provider = p["providerID"] if rng.random() < 0.8 else rng.choice(provider_ids)
                week = rng.randrange(WEEKS)
                weekday = rng.randrange(5)  # Mon..Fri
                day = ANCHOR_DATE + _dt.timedelta(days=week * 7 + weekday)
                catid, title, duration, _w = rng.choices(_APPT_CATEGORIES, weights=cat_weights, k=1)[0]
                start = _DAY_START + _SLOT_MINUTES * rng.randrange((_DAY_END - _DAY_START) // _SLOT_MINUTES)
                if _LUNCH[0] <= start < _LUNCH[1]:
                    continue
                if start + duration // 60 > _DAY_END:
                    continue
                # provider must be free for every slot the visit occupies
                slots = range(start, start + duration // 60, _SLOT_MINUTES)
                key_day = day.isoformat()
                if any((provider, key_day, s) in booked_provider for s in slots):
                    continue
                if (p["pid"], key_day) in booked_patient:
                    continue
                for s in slots:
                    booked_provider.add((provider, key_day, s))
                booked_patient.add((p["pid"], key_day))
                raw_events.append({
                    "pc_catid": catid,
                    "pc_aid": str(provider),
                    "pc_pid": str(p["pid"]),
                    "pc_title": title,
                    "pc_hometext": "",
                    "pc_eventDate": key_day,
                    "pc_duration": duration,
                    "pc_startTime": _fmt_time(start),
                    "pc_apptstatus": "-",
                    "pc_facility": _sql.DEFAULT_FACILITY_ID,
                })
                break

    raw_events.sort(key=lambda e: (e["pc_eventDate"], e["pc_startTime"], int(e["pc_aid"]), int(e["pc_pid"])))
    events: list[dict[str, Any]] = []
    for i, e in enumerate(raw_events):
        events.append({
            "pc_eid": EVENT_ID_BASE + i,
            "pc_catid": e["pc_catid"],
            "pc_multiple": 0,
            "pc_aid": e["pc_aid"],
            "pc_pid": e["pc_pid"],
            "pc_title": e["pc_title"],
            "pc_hometext": e["pc_hometext"],
            "pc_informant": str(_sql.ADMIN_USER_ID),
            "pc_eventDate": e["pc_eventDate"],
            "pc_endDate": e["pc_eventDate"],
            "pc_duration": e["pc_duration"],
            "pc_recurrtype": 0,
            "pc_startTime": e["pc_startTime"],
            "pc_endTime": _sql.end_time(e["pc_startTime"], e["pc_duration"]),
            "pc_alldayevent": 0,
            "pc_apptstatus": e["pc_apptstatus"],
            "pc_eventstatus": 1,
            "pc_sharing": 1,
            "pc_facility": e["pc_facility"],
        })

    # ---- audit log ------------------------------------------------------
    log_base = _dt.datetime(2026, 8, 24, 9, 0, 0)
    log_rows = [
        {
            "id": LOG_ID_BASE + i,
            "date": (log_base + _dt.timedelta(minutes=3 * i)).strftime("%Y-%m-%d %H:%M:%S"),
            "event": "patient-record-insert",
            "category": "Patient Demographics",
            "user": "admin",
            "patient_id": p["pid"],
            "comments": f"forkloop base seed: patient_data pid={p['pid']}",
            "success": 1,
        }
        for i, p in enumerate(patients)
    ]

    return {
        "seed": seed,
        "anchor_date": ANCHOR_DATE.isoformat(),
        "weeks": WEEKS,
        "providers": providers,
        "insurance_companies": [
            {"id": c["id"], "name": c["name"], "plans": list(c["plans"])} for c in INSURANCE_COMPANIES
        ],
        "tables": {
            "users": users,
            "insurance_companies": companies,
            "patient_data": patients,
            "insurance_data": insurance,
            "openemr_postcalendar_events": events,
            "log": log_rows,
        },
    }


def render_base_sql(data: dict[str, Any]) -> str:
    """Portable SQL that inserts every row of ``data["tables"]`` (contract §5).

    Rows are keyed by real column names, so a plain ``INSERT`` per row is
    exact; tables are emitted in :data:`TABLE_ORDER`.
    """
    stmts = [f"-- forkloop claims-ops-v1 OpenEMR base data (seed {data['seed']}, anchor {data['anchor_date']})"]
    for table in TABLE_ORDER:
        rows = data["tables"].get(table, [])
        stmts.append(f"-- {table}: {len(rows)} rows")
        for row in rows:
            stmts.append(_sql.insert_row(table, row))
    return _sql.join_statements(stmts)


def _here() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def load_base_data(path: str | None = None) -> dict[str, Any]:
    """Read ``base_data.json`` (default: the copy next to this module)."""
    with open(path or os.path.join(_here(), "base_data.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=_here(), help="directory for base_data.json / providers.json")
    ap.add_argument("--sql", action="store_true", help="print portable SQL to stdout instead of writing json")
    ap.add_argument("--check", action="store_true", help="exit 1 if the on-disk json differs from a fresh generate()")
    ns = ap.parse_args(argv)

    data = generate()
    if ns.sql:
        sys.stdout.write(render_base_sql(data))
        return 0
    if ns.check:
        on_disk = load_base_data(os.path.join(ns.out, "base_data.json"))
        if on_disk != data:
            print("base_data.json is stale; rerun without --check", file=sys.stderr)
            return 1
        print("base_data.json is up to date")
        return 0
    os.makedirs(ns.out, exist_ok=True)
    with open(os.path.join(ns.out, "base_data.json"), "w", encoding="utf-8") as fh:
        fh.write(_dump_json(data))
    with open(os.path.join(ns.out, "providers.json"), "w", encoding="utf-8") as fh:
        fh.write(_dump_json(data["providers"]))
    t = data["tables"]
    print(
        f"wrote {ns.out}/base_data.json: {len(t['users'])} users, "
        f"{len(t['insurance_companies'])} insurance companies, {len(t['patient_data'])} patients, "
        f"{len(t['insurance_data'])} insurance rows, {len(t['openemr_postcalendar_events'])} appointments, "
        f"{len(t['log'])} log rows; providers.json: {len(data['providers'])} providers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
