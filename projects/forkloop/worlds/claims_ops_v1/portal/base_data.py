"""Deterministic generator for the portal's fixed base data.

``build_base_data()`` is a pure function of the fixed seed ``20260901``: calling it
twice yields identical dicts. Running the module writes ``base_data.json`` next to
it; ``db.seed_base`` reads that JSON (never this module) so the JSON file is the
single source of truth for the golden snapshot and for task generators
(``BaseData`` in docs/contracts.md §9).

Produces exactly: 1 user (agent/agent), 6 providers, 40 patients, 120 claims
spread across all six statuses, 8 inbox messages. Claims already in
APPEAL_SUBMITTED / RESUBMITTED carry one matching appeals / resubmissions row so
the detail pages are internally consistent.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import date, timedelta
from pathlib import Path

from .db import hash_password

SEED = 20260901
OUTPUT_PATH = Path(__file__).with_name("base_data.json")

FIRST_NAMES = [
    "Aaron", "Beatriz", "Caleb", "Dana", "Elias", "Farah", "Gavin", "Helena", "Ivan", "Jolene",
    "Kofi", "Lorena", "Marcus", "Noor", "Otis", "Priya", "Quentin", "Rosa", "Silas", "Tamsin",
    "Umar", "Vera", "Wendell", "Ximena", "Yusuf", "Zelda", "Amara", "Bram", "Celeste", "Desmond",
    "Elke", "Fionn", "Greta", "Hugo", "Ingrid", "Jasper", "Kiara", "Leon", "Maren", "Nikolai",
]
LAST_NAMES = [
    "Alvarado", "Bennett", "Castellano", "Dorsey", "Eriksen", "Fairweather", "Gutierrez", "Hollis",
    "Ibarra", "Jankowski", "Kellerman", "Lindqvist", "Moreau", "Nakamura", "Okafor", "Pemberton",
    "Quiroga", "Ruiz", "Sandoval", "Thibodeaux", "Ulrich", "Vasquez", "Whitcombe", "Yamamoto",
    "Zimmerman", "Bennett", "Moreau", "Okafor", "Ruiz", "Sandoval",
]
def _openemr_base() -> dict:
    """The OpenEMR base dataset is the single source of truth for people.

    Both apps in the world must describe the same providers and patients, so the
    portal derives them from ``../openemr/base_data.json`` (patient names, DOBs,
    insurance policy numbers → portal member IDs).
    """
    import json

    here = Path(__file__).resolve().parent
    data = json.loads((here.parent / "openemr" / "base_data.json").read_text())
    return data


def _openemr_providers() -> list[tuple[str, str, str]]:
    import json

    here = Path(__file__).resolve().parent
    rows = json.loads((here.parent / "openemr" / "providers.json").read_text())
    return [(f"Dr. {r['fname']} {r['lname']}", r["specialty"], r["npi"]) for r in rows]


PAYER_PLANS = [
    "Meridian PPO Gold",
    "Meridian HMO Silver",
    "Northstar Choice Plus",
    "Harbor Health Standard",
    "Cascade Blue Basic",
]
CPT_CODES = [
    ("99213", 12500), ("99214", 18500), ("99203", 15000), ("93000", 6500), ("80053", 4200),
    ("71046", 9800), ("36415", 1500), ("97110", 7200), ("20610", 11000), ("99385", 21000),
]
DENIALS = [
    ("CO-197", "Precertification/authorization/notification absent."),
    ("CO-4", "The procedure code is inconsistent with the modifier used or a required modifier is missing."),
    ("CO-18", "Exact duplicate claim/service."),
    ("CO-22", "This care may be covered by another payer per coordination of benefits."),
    ("CO-29", "The time limit for filing has expired."),
    ("CO-31", "Patient cannot be identified as our insured."),
]
STATUS_COUNTS = {
    "SUBMITTED": 25,
    "PAID": 45,
    "DENIED": 30,
    "APPEAL_SUBMITTED": 8,
    "RESUBMITTED": 8,
    "VOID": 4,
}
APPEAL_REASON_FOR_DENIAL = {
    "CO-197": "PRECERT_OBTAINED",
    "CO-4": "CODING_CORRECTION",
    "CO-18": "DUPLICATE_ERROR",
    "CO-22": "COB_UPDATED",
    "CO-29": "TIMELY_FILING",
    "CO-31": "COB_UPDATED",
}
MESSAGES = [
    ("Welcome to the Meridian Provider Portal", "Your provider portal account is active. Use the Claims tab to review claim status, file appeals on denied claims, and resubmit corrected claims. Contact provider services with any questions.", "2026-06-02T13:05:00Z", 1),
    ("Scheduled maintenance: Saturday 02:00-04:00 UTC", "The portal will be unavailable for scheduled maintenance this Saturday between 02:00 and 04:00 UTC. Claims submitted during the window will be queued and processed afterwards.", "2026-06-14T16:30:00Z", 1),
    ("Timely filing reminder", "A reminder that initial claims must be received within 90 days of the date of service. Claims received after that window will be denied with code CO-29 unless an exception applies.", "2026-06-28T09:12:00Z", 0),
    ("Remittance advice for cycle 2026-27 is available", "The remittance advice for payment cycle 2026-27 has been posted. Paid claims from this cycle now show status PAID in the claims list.", "2026-07-06T11:45:00Z", 1),
    ("Updated prior authorization requirements", "Effective 2026-08-01, the following procedure codes require prior authorization: 20610, 71046, 97110. Claims submitted without an authorization on file will be denied with code CO-197.", "2026-07-18T15:20:00Z", 0),
    ("Duplicate submissions detected", "Our system detected several claims that appear to duplicate previously adjudicated services. Please review claims denied with code CO-18 before resubmitting.", "2026-08-03T08:55:00Z", 0),
    ("Member ID format change for Northstar plans", "Northstar Choice Plus member IDs now begin with the prefix NST. Claims submitted with an outdated member ID will be denied with code CO-31 and should be resubmitted with the corrected ID.", "2026-08-12T14:10:00Z", 0),
    ("Portal survey", "We would appreciate two minutes of your time to tell us how the portal is working for your practice. The survey link has been sent to your practice administrator.", "2026-08-25T10:00:00Z", 0),
]

BASE_DATE = date(2026, 2, 1)
DATE_SPAN_DAYS = 205  # through 2026-08-25


def _iso_ts(d: date, hour: int, minute: int) -> str:
    return f"{d.isoformat()}T{hour:02d}:{minute:02d}:00Z"


def _member_id(rng: random.Random, plan: str) -> str:
    prefix = {"Meridian PPO Gold": "MER", "Meridian HMO Silver": "MER", "Northstar Choice Plus": "NST",
              "Harbor Health Standard": "HHS", "Cascade Blue Basic": "CBB"}[plan]
    return prefix + "".join(str(rng.randint(0, 9)) for _ in range(8))


def _swap_two_digits(rng: random.Random, member_id: str) -> str:
    """Return a member id with two adjacent digits transposed (a CO-31 mismatch)."""
    digits = list(member_id[3:])
    for _ in range(20):
        i = rng.randint(0, len(digits) - 2)
        if digits[i] != digits[i + 1]:
            digits[i], digits[i + 1] = digits[i + 1], digits[i]
            return member_id[:3] + "".join(digits)
    # all digits equal (astronomically unlikely); bump the last one instead
    digits[-1] = str((int(digits[-1]) + 1) % 10)
    return member_id[:3] + "".join(digits)


def build_base_data() -> dict:
    rng = random.Random(SEED)

    # users -----------------------------------------------------------------
    salt = hashlib.sha256(b"forkloop-portal-salt:agent").digest()[:16]
    users = [{
        "id": 1,
        "username": "agent",
        "password_hash": hash_password("agent", salt=salt),
        "display_name": "Agent User",
    }]

    # providers -------------------------------------------------------------
    providers = []
    for i, (name, specialty, npi) in enumerate(_openemr_providers(), start=1):
        providers.append({"id": i, "npi": npi, "name": name, "specialty": specialty})

    # patients --------------------------------------------------------------
    patients = []
    emr = _openemr_base()["tables"]
    ins_by_pid = {r["pid"]: r for r in emr["insurance_data"] if r.get("type") == "primary"}
    for i, pd in enumerate(sorted(emr["patient_data"], key=lambda r: r["pid"]), start=1):
        ins = ins_by_pid[pd["pid"]]
        created = date(2026, 1, 5) + timedelta(days=rng.randint(0, 120))
        patients.append({
            "id": i,
            "portal_patient_id": f"P-{10000 + i}",
            "first_name": pd["fname"],
            "last_name": pd["lname"],
            "dob": pd["DOB"],
            "member_id": ins["policy_number"],
            "payer_plan": ins["plan_name"],
            "group_number": ins["group_number"],
            "created_at": _iso_ts(created, rng.randint(8, 17), rng.choice([0, 15, 30, 45])),
            "openemr_pid": pd["pid"],
        })
    patients_by_id = {p["id"]: p for p in patients}

    # claims ----------------------------------------------------------------
    statuses: list[str] = []
    for status, count in STATUS_COUNTS.items():
        statuses.extend([status] * count)
    rng.shuffle(statuses)
    assert len(statuses) == 120

    claims = []
    appeals = []
    resubmissions = []
    for i in range(1, 121):
        status = statuses[i - 1]
        patient = patients_by_id[rng.randint(1, 40)]
        provider_id = rng.randint(1, 6)
        service_date = BASE_DATE + timedelta(days=rng.randint(0, DATE_SPAN_DAYS))
        cpt, base_amount = rng.choice(CPT_CODES)
        amount_cents = base_amount + rng.randint(-15, 40) * 25
        updated = service_date + timedelta(days=rng.randint(3, 40))
        updated_at = _iso_ts(updated, rng.randint(8, 17), rng.choice([0, 10, 20, 30, 40, 50]))

        denial_code = None
        denial_reason = None
        submitted_member_id = patient["member_id"]
        if status in ("DENIED", "APPEAL_SUBMITTED", "RESUBMITTED"):
            denial_code, denial_reason = rng.choice(DENIALS)
            if denial_code == "CO-31":
                submitted_member_id = _swap_two_digits(rng, patient["member_id"])

        claim_number = f"C-{1000 + i}"
        claims.append({
            "id": i,
            "claim_number": claim_number,
            "patient_id": patient["id"],
            "provider_id": provider_id,
            "service_date": service_date.isoformat(),
            "cpt_code": cpt,
            "amount_cents": amount_cents,
            "status": status,
            "denial_code": denial_code,
            "denial_reason": denial_reason,
            "submitted_member_id": submitted_member_id,
            "updated_at": updated_at,
        })

        if status == "APPEAL_SUBMITTED":
            reason_code = APPEAL_REASON_FOR_DENIAL[denial_code]
            auth = f"AUTH{rng.randint(100000, 999999)}" if reason_code == "PRECERT_OBTAINED" else None
            appeals.append({
                "id": len(appeals) + 1,
                "claim_id": i,
                "reason_code": reason_code,
                "authorization_number": auth,
                "narrative": f"Appeal for claim {claim_number}: the denial ({denial_code}) is disputed; supporting documentation is on file with the practice.",
                "attachment_name": None,
                "attachment_sha256": None,
                "created_at": updated_at,
            })
        elif status == "RESUBMITTED":
            resubmissions.append({
                "id": len(resubmissions) + 1,
                "claim_id": i,
                "member_id": submitted_member_id,
                "note": f"Corrected resubmission of {claim_number}.",
                "created_at": updated_at,
            })

    # messages --------------------------------------------------------------
    messages = [
        {"id": i, "subject": subject, "body": body, "received_at": received_at, "is_read": is_read}
        for i, (subject, body, received_at, is_read) in enumerate(MESSAGES, start=1)
    ]

    return {
        "seed": SEED,
        "users": users,
        "providers": providers,
        "patients": patients,
        "claims": claims,
        "appeals": appeals,
        "resubmissions": resubmissions,
        "messages": messages,
    }


def write_base_data(path: Path | None = None) -> Path:
    out = path if path is not None else OUTPUT_PATH
    data = build_base_data()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return out


if __name__ == "__main__":
    written = write_base_data()
    data = build_base_data()
    print(
        f"wrote {written}: users={len(data['users'])} providers={len(data['providers'])} "
        f"patients={len(data['patients'])} claims={len(data['claims'])} messages={len(data['messages'])} "
        f"appeals={len(data['appeals'])} resubmissions={len(data['resubmissions'])}"
    )
