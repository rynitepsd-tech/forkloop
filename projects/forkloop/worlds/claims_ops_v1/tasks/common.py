"""Shared building blocks for the claims-ops-v1 task families.

Everything here is a pure function of ``(family, seed, split)``; the base data
comes from the two JSON files that also seed the golden snapshot, so the
generator knows exactly which patients, providers and claims already exist.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from forkloop.util.minipdf import build_pdf, text_pages
from forkloop.util.sql import quote

from ..openemr import openemr_sql as osql
from ..openemr.docs_paths import document_fs_path

HERE = Path(__file__).resolve().parent
WORLD_DIR = HERE.parent
PORTAL_URL = "http://localhost:8080"
OPENEMR_URL = "http://localhost/openemr"

# Per-episode ids start well above the base data (100001..) and are derived from the seed so
# two episodes never collide even when their seeds are adjacent.
EP_ID_BASE = 500000

SPLITS = ("train", "heldout_seeds", "heldout_compositions")

# Disjoint name pools per split so held-out episodes never reuse a training surname.
SURNAMES = {
    "train": ["Alvarado", "Bennett", "Castellano", "Dorsey", "Eriksen", "Fairweather", "Gutierrez", "Hollis",
              "Ibarra", "Jankowski", "Kellerman", "Lindqvist", "Moreau", "Okafor", "Pemberton", "Quiroga",
              "Ruiz", "Sandoval", "Thibodeaux", "Ulrich", "Vasquez", "Whitcombe", "Yamamoto", "Zimmerman"],
    "heldout_seeds": ["Ashworth", "Bramble", "Coventry", "Delacroix", "Ellery", "Fontaine", "Greenaway", "Hartigan",
                      "Isherwood", "Joubert", "Kensington", "Lockhart", "Marchetti", "Northcott", "Oduya",
                      "Prendergast", "Quimby", "Rasmussen", "Sorensen", "Tremblay", "Underhill", "Vandermeer",
                      "Wexford", "Yardley"],
    "heldout_compositions": ["Abernathy", "Blakemore", "Carrington", "Dunmore", "Everly", "Farquhar", "Galloway",
                             "Hemsworth", "Ingalls", "Jessop", "Kirkland", "Lancaster", "Montague", "Norrington",
                             "Osgood", "Pattinson", "Quennell", "Rutherford", "Stirling", "Thornbury", "Upton",
                             "Villanueva", "Whitlock", "Yorke"],
}
FIRST_NAMES = ["Amelia", "Benjamin", "Charlotte", "Daniel", "Eleanor", "Felix", "Grace", "Henry", "Isla", "Jonah",
               "Kaia", "Liam", "Maeve", "Noah", "Olive", "Patrick", "Quinn", "Rosa", "Silas", "Talia", "Uma",
               "Victor", "Willa", "Xavier", "Yara", "Zane"]
PAYERS = {  # payer plan → (openemr insurance company id, member id prefix)
    "Aetna Choice POS II": (100001, "W"),
    "BCBS HMO Blue Select": (100002, "ZGP"),
    "Cigna Open Access Plus": (100003, "U"),
    "UnitedHealthcare Choice Plus": (100004, "9"),
}
CPT_CODES = [("99213", 12500), ("99214", 18500), ("99203", 15000), ("93000", 6500), ("80053", 4200),
             ("71046", 9800), ("97110", 7200), ("20610", 11000), ("99385", 21000)]
DENIALS = {
    "CO-197": ["Precertification/authorization/notification absent.",
               "Prior authorization was not obtained for this service.",
               "Service requires precertification; none on file."],
    "CO-4": ["The procedure code is inconsistent with the modifier used.",
             "Required modifier missing for the procedure code billed."],
    "CO-18": ["Exact duplicate claim/service.", "Duplicate of a previously adjudicated claim."],
    "CO-22": ["This care may be covered by another payer per coordination of benefits.",
              "Coordination of benefits information required."],
    "CO-29": ["The time limit for filing has expired.", "Claim received after the timely filing deadline."],
    "CO-31": ["Patient cannot be identified as our insured.",
              "Member ID on the claim does not match our records."],
}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
ANCHOR = dt.date(2026, 9, 7)  # Monday the base appointments start on


@dataclass
class BaseData:
    portal: dict[str, Any]
    openemr: dict[str, Any]

    @property
    def providers(self) -> list[dict[str, Any]]:
        """Merged provider view: portal id, openemr user id, name, npi."""
        out = []
        emr = {u["npi"]: u for u in self.openemr["tables"]["users"]}
        for p in self.portal["providers"]:
            u = emr[p["npi"]]
            out.append({"portal_id": p["id"], "openemr_id": u["id"], "name": p["name"], "fname": u["fname"],
                        "lname": u["lname"], "specialty": p["specialty"], "npi": p["npi"]})
        return out

    @property
    def patients(self) -> list[dict[str, Any]]:
        """Merged patient view with both ids."""
        return [dict(p) for p in self.portal["patients"]]

    @property
    def claims(self) -> list[dict[str, Any]]:
        return self.portal["claims"]

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.openemr["tables"]["openemr_postcalendar_events"]

    def next_ids(self) -> dict[str, int]:
        return {
            "portal_patient": max(p["id"] for p in self.portal["patients"]) + 1,
            "portal_claim": max(c["id"] for c in self.portal["claims"]) + 1,
            "claim_number": max(int(c["claim_number"].split("-")[1]) for c in self.portal["claims"]) + 1,
            "pid": max(p["pid"] for p in self.openemr["tables"]["patient_data"]) + 1,
        }


_BASE: Optional[BaseData] = None


def load_base() -> BaseData:
    global _BASE
    if _BASE is None:
        _BASE = BaseData(
            portal=json.loads((WORLD_DIR / "portal" / "base_data.json").read_text()),
            openemr=json.loads((WORLD_DIR / "openemr" / "base_data.json").read_text()),
        )
    return _BASE


def rng_for(family: str, seed: int, split: str) -> random.Random:
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}")
    return random.Random(f"claims-ops-v1:{family}:{split}:{seed}")


def episode_id_base(seed: int) -> int:
    """Per-episode id block: 1000 ids per seed, disjoint across seeds."""
    return EP_ID_BASE + (seed % 400000) * 1000


def member_id(rng: random.Random, plan: str) -> str:
    _, prefix = PAYERS[plan]
    return prefix + "".join(str(rng.randint(0, 9)) for _ in range(11 - len(prefix) + 1))


def similar_member_id(rng: random.Random, mid: str) -> str:
    """Same length, two digits swapped or changed — a realistic near-miss distractor."""
    chars = list(mid)
    digits = [i for i, c in enumerate(chars) if c.isdigit()]
    i, j = rng.sample(digits, 2)
    chars[i], chars[j] = chars[j], chars[i]
    if "".join(chars) == mid:
        chars[i] = str((int(chars[i]) + 1) % 10)
    return "".join(chars)


def random_dob(rng: random.Random) -> dt.date:
    return dt.date(1945, 1, 1) + dt.timedelta(days=rng.randint(0, 365 * 62))


def iso_ts(d: dt.date, hour: int = 10, minute: int = 0) -> str:
    return f"{d.isoformat()}T{hour:02d}:{minute:02d}:00Z"


def sql_ts(d: dt.date, hour: int = 10, minute: int = 0) -> str:
    return f"{d.isoformat()} {hour:02d}:{minute:02d}:00"


@dataclass
class Person:
    """A per-episode synthetic patient present in BOTH apps."""

    first: str
    last: str
    dob: dt.date
    plan: str
    member: str
    group: str
    pid: int                 # openemr patient_data.pid (= id)
    portal_id: int           # portal patients.id
    portal_patient_id: str   # "P-xxxxx"
    provider: dict[str, Any] # merged provider record
    insurance_id: int = 0

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"

    def portal_sql(self, created: dt.date) -> str:
        return ("INSERT INTO patients (id, portal_patient_id, first_name, last_name, dob, member_id, payer_plan, group_number, created_at) "
                f"VALUES ({self.portal_id}, {quote(self.portal_patient_id)}, {quote(self.first)}, {quote(self.last)}, "
                f"{quote(self.dob.isoformat())}, {quote(self.member)}, {quote(self.plan)}, {quote(self.group)}, {quote(iso_ts(created))});")

    def openemr_sql(self, rng: random.Random, log_id: int) -> str:
        company_id, _ = PAYERS[self.plan]
        stmts = [
            osql.insert_patient(pid=self.pid, fname=self.first, lname=self.last, dob=self.dob,
                                sex=rng.choice(["Female", "Male"]), street=f"{rng.randint(100, 9999)} {rng.choice(['Oak', 'Elm', 'Cedar', 'Maple'])} St",
                                city="Austin", state="TX", postal_code=f"787{rng.randint(10, 59)}",
                                phone_home=f"512-555-{rng.randint(100, 999):03d}{rng.randint(0, 9)}",
                                provider_id=self.provider["openemr_id"], pubpid=str(self.pid)),
            osql.insert_insurance(id=self.insurance_id, pid=self.pid, company_id=company_id, plan_name=self.plan,
                                  policy_number=self.member, group_number=self.group, subscriber_fname=self.first,
                                  subscriber_lname=self.last, subscriber_dob=self.dob, date="2025-01-01"),
            osql.insert_log(id=log_id, event="patient-record-insert", category="Patient Demographics", user="admin",
                            patient_id=self.pid, comments=f"forkloop seed: patient_data pid={self.pid}", date=sql_ts(ANCHOR - dt.timedelta(days=30))),
        ]
        return "\n".join(stmts)


def make_person(rng: random.Random, split: str, ids: dict[str, int], base: BaseData, *, last: Optional[str] = None,
                plan: Optional[str] = None, provider: Optional[dict[str, Any]] = None) -> Person:
    plan = plan or rng.choice(sorted(PAYERS))
    p = Person(
        first=rng.choice(FIRST_NAMES), last=last or rng.choice(SURNAMES[split]), dob=random_dob(rng), plan=plan,
        member=member_id(rng, plan), group=str(rng.randint(1000000, 9999999)),
        pid=ids["pid"], portal_id=ids["portal_patient"], portal_patient_id=f"P-{10000 + ids['portal_patient']}",
        provider=provider or rng.choice(base.providers), insurance_id=ids["pid"],
    )
    ids["pid"] += 1
    ids["portal_patient"] += 1
    return p


@dataclass
class Claim:
    id: int
    number: str
    person: Person
    provider: dict[str, Any]
    service_date: dt.date
    cpt: str
    amount_cents: int
    status: str
    denial_code: Optional[str] = None
    denial_reason: Optional[str] = None
    submitted_member: Optional[str] = None

    def portal_sql(self) -> str:
        return ("INSERT INTO claims (id, claim_number, patient_id, provider_id, service_date, cpt_code, amount_cents, status, "
                "denial_code, denial_reason, submitted_member_id, updated_at) VALUES ("
                f"{self.id}, {quote(self.number)}, {self.person.portal_id}, {self.provider['portal_id']}, {quote(self.service_date.isoformat())}, "
                f"{quote(self.cpt)}, {self.amount_cents}, {quote(self.status)}, {quote(self.denial_code)}, {quote(self.denial_reason)}, "
                f"{quote(self.submitted_member or self.person.member)}, {quote(iso_ts(self.service_date + dt.timedelta(days=12)))});")


def make_claim(rng: random.Random, ids: dict[str, int], person: Person, *, status: str = "DENIED",
               denial_code: Optional[str] = None, provider: Optional[dict[str, Any]] = None,
               submitted_member: Optional[str] = None, number: Optional[str] = None) -> Claim:
    cpt, amount = rng.choice(CPT_CODES)
    c = Claim(
        id=ids["portal_claim"], number=number or f"C-{ids['claim_number']}", person=person,
        provider=provider or person.provider, service_date=ANCHOR - dt.timedelta(days=rng.randint(14, 60)),
        cpt=cpt, amount_cents=amount + rng.randint(-10, 30) * 25, status=status,
        denial_code=denial_code, denial_reason=rng.choice(DENIALS[denial_code]) if denial_code else None,
        submitted_member=submitted_member,
    )
    ids["portal_claim"] += 1
    if number is None:
        ids["claim_number"] += 1
    return c


def noise_messages(rng: random.Random, n: int, next_id: int, subjects: Optional[list[str]] = None) -> str:
    subjects = subjects or [
        "Scheduled maintenance this weekend", "Updated timely filing policy", "New EOB format",
        "Reminder: verify member eligibility before service", "Portal password rotation notice",
        "Q3 provider newsletter", "Claims processing delays resolved",
    ]
    out = []
    for i in range(n):
        subj = rng.choice(subjects)
        out.append("INSERT INTO messages (id, subject, body, received_at, is_read) VALUES ("
                   f"{next_id + i}, {quote(subj)}, {quote('This is an automated notice from the payer portal. No action is required.')}, "
                   f"{quote(iso_ts(ANCHOR - dt.timedelta(days=rng.randint(1, 20)), rng.randint(8, 17)))}, 0);")
    return "\n".join(out)


def authorization_letter(rng: random.Random, person: Person, auth_number: str, distractors: list[str],
                         service_desc: str, page_of: tuple[int, int] = (1, 1)) -> bytes:
    """A one-or-more-page PDF whose page ``page_of[0]`` carries the real auth number."""
    pages: list[list[str]] = []
    total = page_of[1]
    for pg in range(1, total + 1):
        paras = [f"{person.plan.split()[0].upper()} UTILIZATION MANAGEMENT", "",
                 f"Member: {person.name}    DOB: {person.dob.isoformat()}    Member ID: {person.member}",
                 f"Provider: {person.provider['name']} (NPI {person.provider['npi']})", ""]
        if pg == page_of[0]:
            paras += [f"Determination: APPROVED for {service_desc}.",
                      f"Authorization number: {auth_number}",
                      f"Valid: {(ANCHOR - dt.timedelta(days=45)).isoformat()} through {(ANCHOR + dt.timedelta(days=45)).isoformat()}", ""]
        else:
            paras += ["This page intentionally summarises prior correspondence.", ""]
        for d in distractors[(pg - 1) * 2:(pg - 1) * 2 + 2]:
            paras.append(rng.choice([f"Reference number: {d}", f"Prior case number: {d} (closed)",
                                     f"Claim tracking id: {d}"]))
        paras += ["", f"Page {pg} of {total}"]
        pages.extend(text_pages(paras, lines_per_page=60))
    return build_pdf(pages, title=f"Authorization {auth_number}")


def auth_number(rng: random.Random) -> str:
    return f"AUTH-{rng.randint(10, 99)}{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{rng.randint(10000, 99999)}"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


APPS_HINT = f"Apps: OpenEMR at {OPENEMR_URL}, payer portal at {PORTAL_URL}."


def portal_url(path: str) -> str:
    return PORTAL_URL + path


def openemr_url(path: str) -> str:
    return OPENEMR_URL + path


def document_seed_file(person: Person, name: str, data: bytes):
    from forkloop.tasks import SeedFile

    return SeedFile.from_bytes(document_fs_path(person.pid, name), data, mode=0o644)


__all__ = [
    "BaseData", "Person", "Claim", "load_base", "rng_for", "episode_id_base", "make_person", "make_claim",
    "noise_messages", "authorization_letter", "auth_number", "sha256", "portal_url", "openemr_url",
    "document_seed_file", "DENIALS", "PAYERS", "WEEKDAYS", "ANCHOR", "SURNAMES", "similar_member_id", "sql_ts", "iso_ts",
]
