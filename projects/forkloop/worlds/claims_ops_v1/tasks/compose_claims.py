"""Compositions — two subtasks from *different* families in one episode (added 2026-09-29).

Parts (each mirrors its base family, with the v2 hardening):

- ``INS`` (``update_insurance_reconcile``): update the primary insurance in OpenEMR, resubmit
  the CO-31 claim in the portal with the new member ID (OpenEMR may already be updated).
- ``APL`` (``resolve_denial``): appeal a CO-197 claim in the portal with the authorization
  number that only the patient's OpenEMR documents contain (attachment sometimes required).
- ``RSC`` (``reschedule_constrained``): move an OpenEMR appointment to the next <weekday>
  <morning|afternoon> after its current date without double-booking the provider.

Structure knobs, drawn in this order from the ``(compose_claims, split, seed)`` stream:
``pairing`` (``APL+RSC`` | ``INS+APL`` | ``INS+RSC``), ``order`` (which part the
instruction states first), ``link`` (``same`` patient for both parts, or ``household``: a
second patient with the same surname and provider), and, for ``RSC``, ``provider_ref``
(``named``, or ``via_claim``: "the provider who billed claim C-…", which the agent must read
in the portal before it can act in OpenEMR — a cross-application dependency).

The oracle is the union of the parts: every part's effects (in instruction order) and
part-specific invariants (values, duplicates, wrong records, field preservation), then one
set of global guards built from the merged parts — ``other_appeals_preserved`` and
``other_resubmissions_preserved`` exclude only the claims some part legitimately touches,
one ``baseline_checksum`` whose ``allow`` is the union of the parts' rows, one
``ui_path_only`` (write rows only) and one ``forbidden_screens``. Check ids are prefixed
``ins_``/``apl_``/``rsc_`` so the parts never collide.

Ids: one episode block of 1000 ids per seed (``episode_id_base``, >= 500000), split per
table so the parts cannot collide: patients/claims from +0, documents +100, OpenEMR seed
log rows +200, appointments +300, portal messages +400. Claim numbers are
``C-<20000 + seed % 20000 + k>`` (base claims are C-1001..C-1120).

The instruction states only what the base families state (names, DOBs, claim numbers, the
new plan and member ID, provider names); never the authorization number, the document or
page, or the target date. The episode always starts on the portal claims list (the reset
re-establishes the portal session there; OpenEMR credentials are in the instruction).
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from forkloop.oracle import Check, OracleSpec
from forkloop.tasks import Seeding, TaskInstance, make_task_id

from ..openemr import openemr_sql as osql
from .common import (ANCHOR, APPS_HINT, FIRST_NAMES, PAYERS, V2_UI_PATH_OP, WEEKDAYS, BaseData, Claim, Person, auth_number,
                     authorization_letter, document_seed_file, episode_id_base, load_base, make_claim, make_person,
                     member_id, noise_messages, order_v2_invariants, rng_for, sha256, similar_member_id, sql_ts)
from .reschedule_constrained import NO_OVERLAP_SQL, _next_weekday

FAMILY = "compose_claims"
PAIRINGS = ("APL+RSC", "INS+APL", "INS+RSC")
PART_FAMILIES = {"INS": "update_insurance_reconcile", "APL": "resolve_denial", "RSC": "reschedule_constrained"}
LINKS = ("same", "household")
CLAIM_NUMBER_BASE = 20000

# per-table offsets inside the episode block
_OFF = {"documents": 100, "log": 200, "events": 300, "messages": 400}


@dataclass
class _Ctx:
    rng: random.Random
    split: str
    seed: int
    base: BaseData
    eid: int
    ids: dict[str, int]
    openemr_sql: list[str] = field(default_factory=list)
    portal_sql: list[str] = field(default_factory=list)
    files: list[Any] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=lambda: dict(_OFF))

    def take(self, table: str) -> int:
        n = self.counters[table]
        if n >= _OFF.get(table, 0) + 100 or n >= 1000:
            raise RuntimeError(f"episode id block exhausted for {table}")
        self.counters[table] = n + 1
        return self.eid + n

    def person(self, first_not: Optional[str] = None, **kw: Any) -> Person:
        """A new patient in both apps (seeded immediately, so its SQL precedes anything that refers to it).
        ``first_not``: a household member gets a different first name than the patient it is related to."""
        p = make_person(self.rng, self.split, self.ids, self.base, **kw)
        if first_not is not None and p.first == first_not:
            p.first = FIRST_NAMES[(FIRST_NAMES.index(p.first) + 1) % len(FIRST_NAMES)]
        if self.ids["pid"] - self.eid > 100:
            raise RuntimeError("episode id block exhausted for patients")
        self.openemr_sql.append(p.openemr_sql(self.rng, self.take("log")))
        self.portal_sql.append(p.portal_sql(ANCHOR - dt.timedelta(days=90)))
        return p

    def claim(self, person: Person, **kw: Any) -> Claim:
        c = make_claim(self.rng, self.ids, person, **kw)
        self.portal_sql.append(c.portal_sql())
        return c


@dataclass
class _Part:
    kind: str
    person: Person
    text: str
    effects: list[Check]
    invariants: list[Check]
    allow: dict[str, list[Any]]
    exempt: list[str]
    appeal_claims: list[int] = field(default_factory=list)       # claim ids this part may appeal
    resubmit_claims: list[int] = field(default_factory=list)     # claim ids this part may resubmit
    expected: dict[str, Any] = field(default_factory=dict)
    difficulty: dict[str, Any] = field(default_factory=dict)
    hidden: list[str] = field(default_factory=list)              # values that must never appear in the instruction
    anchor_claim: Optional[Claim] = None                         # a claim of this part billed by the person's provider


def _status_untouched(cid: str, claim: Claim) -> Check:
    return Check(id=cid, kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?", params=[claim.id],
                 equals=claim.status, reason_code="WRONG_RECORD")


# ----------------------------------------------------------------------------- INS


def _ins(ctx: _Ctx, person: Person) -> _Part:
    rng = ctx.rng
    old_member = person.member
    new_plan = rng.choice([p for p in sorted(PAYERS) if p != person.plan])
    new_member = member_id(rng, new_plan)
    partially_updated = rng.random() < 0.25
    target = ctx.claim(person, status="DENIED", denial_code="CO-31", submitted_member=old_member)
    other = ctx.claim(person, status=rng.choice(["PAID", "SUBMITTED"]))
    distractors: list[tuple[Person, Claim]] = []
    for _ in range(rng.randint(0, 2)):
        last = person.last if rng.random() < 0.5 else None
        # the member id is fixed before the patient is seeded, so both apps carry the near-miss id
        d = make_person(rng, ctx.split, ctx.ids, ctx.base, last=last, plan=person.plan)
        d.member = similar_member_id(rng, old_member)
        ctx.openemr_sql.append(d.openemr_sql(rng, ctx.take("log")))
        ctx.portal_sql.append(d.portal_sql(ANCHOR - dt.timedelta(days=80)))
        distractors.append((d, ctx.claim(d, status="DENIED", denial_code="CO-31", submitted_member=d.member)))
    if partially_updated:
        ctx.openemr_sql.append(osql.update_insurance_policy(pid=person.pid, policy_number=new_member, plan_name=new_plan,
                                                            company_id=PAYERS[new_plan][0]))
        text = (f"{person.name}'s (DOB {person.dob.isoformat()}) insurance is now {new_plan}, member ID {new_member}, and "
                f"OpenEMR already reflects this: in the payer portal, resubmit claim {target.number} with the corrected member ID.")
    else:
        text = (f"{person.name}'s (DOB {person.dob.isoformat()}) insurance changed to {new_plan}, member ID {new_member}: update "
                f"the primary insurance in OpenEMR (plan name and policy number), then in the payer portal resubmit claim "
                f"{target.number} with the corrected member ID.")
    policy_sql = "SELECT {col} FROM insurance_data WHERE pid = ? AND type = 'primary' ORDER BY date DESC, id DESC"
    effects = [
        Check(id="ins_claim_status", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?", params=[target.id],
              equals="RESUBMITTED", reason_code="NOT_DONE"),
        Check(id="ins_claim_member", kind="query", db="portal", sql="SELECT submitted_member_id FROM claims WHERE id = ?",
              params=[target.id], equals=new_member, reason_code="WRONG_VALUE"),
        # unchanged policy = the OpenEMR half was not done; changed but different = a wrong value
        Check(id="ins_openemr_updated", kind="query", db="openemr", sql=policy_sql.format(col="policy_number"),
              params=[person.pid], op="ne", equals=old_member, reason_code="NOT_DONE"),
        Check(id="ins_openemr_policy", kind="query", db="openemr", sql=policy_sql.format(col="policy_number"),
              params=[person.pid], equals=new_member, reason_code="WRONG_VALUE"),
        Check(id="ins_openemr_plan", kind="query", db="openemr", sql=policy_sql.format(col="plan_name"),
              params=[person.pid], equals=new_plan, reason_code="WRONG_VALUE"),
    ]
    invariants = [
        Check(id="ins_single_resubmission", kind="count", db="portal", sql="SELECT COUNT(*) FROM resubmissions WHERE claim_id = ?",
              params=[target.id], equals=1, reason_code="DUPLICATE_SIDE_EFFECT"),
        Check(id="ins_resubmission_member", kind="count", db="portal",
              sql="SELECT COUNT(*) FROM resubmissions WHERE claim_id = ? AND member_id = ?", params=[target.id, new_member],
              equals=1, reason_code="WRONG_VALUE"),
        _status_untouched("ins_other_claim_untouched", other),
        *[_status_untouched(f"ins_distractor_{i}_untouched", c) for i, (_, c) in enumerate(distractors)],
        Check(id="ins_insurance_fields_preserved", kind="preserve_fields", db="openemr",
              sql="SELECT * FROM insurance_data WHERE id = ?", params=[person.insurance_id],
              mutable_fields=[] if partially_updated else ["plan_name", "policy_number"], reason_code="COLLATERAL_EDIT"),
        Check(id="ins_claim_fields_preserved", kind="preserve_fields", db="portal", sql="SELECT * FROM claims WHERE id = ?",
              params=[target.id], mutable_fields=["submitted_member_id", "status", "updated_at"], reason_code="COLLATERAL_EDIT"),
        Check(id="ins_no_appeal", kind="count", db="portal", sql="SELECT COUNT(*) FROM appeals WHERE claim_id IN (?, ?)",
              params=[target.id, other.id], equals=0, reason_code="COLLATERAL_EDIT"),
    ]
    return _Part(
        kind="INS", person=person, text=text, effects=effects, invariants=invariants,
        allow={"portal.claims": [target.id], **({} if partially_updated else {"openemr.insurance_data": [person.insurance_id]})},
        exempt=["portal.resubmissions"], resubmit_claims=[target.id], anchor_claim=target,
        expected={"patient_pid": person.pid, "claim_id": target.id, "claim_number": target.number, "old_member": old_member,
                  "new_member": new_member, "new_plan": new_plan, "other_claim": other.number,
                  "distractor_claims": [c.number for _, c in distractors]},
        difficulty={"distractors": len(distractors), "partially_updated": partially_updated,
                    "same_surname_distractor": any(d.last == person.last for d, _ in distractors)},
    )


# ----------------------------------------------------------------------------- APL


def _apl(ctx: _Ctx, person: Person) -> _Part:
    rng = ctx.rng
    target = ctx.claim(person, status="DENIED", denial_code="CO-197")
    other_denial = ctx.claim(person, status="DENIED", denial_code=rng.choice(["CO-29", "CO-4"])) if rng.random() < 0.5 else None
    distractors: list[tuple[Person, Claim]] = []
    for _ in range(rng.randint(0, 2)):
        d = ctx.person(last=person.last if rng.random() < 0.7 else None)
        distractors.append((d, ctx.claim(d, status="DENIED", denial_code=rng.choice(["CO-197", "CO-29"]))))
    real = auth_number(rng)
    decoys = [auth_number(rng) for _ in range(rng.randint(1, 4))]
    n_docs = rng.randint(1, 3)
    which = rng.randrange(n_docs)
    n_pages = rng.choice([1, 1, 2])
    page = rng.randrange(n_pages) + 1
    require_attachment = rng.random() < 0.15
    service_desc = f"CPT {target.cpt} on {target.service_date.isoformat()}"
    doc_hash = doc_name = ""
    names: list[str] = []
    for i in range(n_docs):
        name = rng.choice(["authorization_letter", "um_determination", "precert_notice", "payer_correspondence"]) + f"_{i + 1}.pdf"
        names.append(name)
        if i == which:
            pdf = authorization_letter(rng, person, real, decoys, service_desc, page_of=(page, n_pages),
                                       service_date=target.service_date)
            doc_hash, doc_name = sha256(pdf), name
        else:
            pdf = authorization_letter(rng, person, rng.choice(decoys), decoys, "a different service (not this claim)",
                                       page_of=(1, 1), service_date=target.service_date)
        ctx.files.append(document_seed_file(person, name, pdf))
        ctx.openemr_sql.append(osql.insert_document(doc_id=ctx.take("documents"), pid=person.pid, name=name, size=len(pdf),
                                                    content_hash=sha256(pdf),
                                                    docdate=ANCHOR - dt.timedelta(days=rng.randint(20, 60))))
    text = (f"Claim {target.number} for {person.name} (DOB {person.dob.isoformat()}) was denied with code CO-197 (prior "
            f"authorization missing): find the authorization number in the patient's documents in OpenEMR, then in the payer "
            f"portal file an appeal on {target.number} with reason 'Prior authorization was obtained' and that authorization number.")
    if require_attachment:
        text += " Attach the authorization letter to the appeal."
    if other_denial is not None:
        text += f" Claim {other_denial.number} must be left as it is."
    effects = [
        Check(id="apl_claim_status", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?", params=[target.id],
              equals="APPEAL_SUBMITTED", reason_code="NOT_DONE"),
        Check(id="apl_appeal_reason", kind="query", db="portal",
              sql="SELECT reason_code FROM appeals WHERE claim_id = ? ORDER BY id DESC", params=[target.id],
              equals="PRECERT_OBTAINED", reason_code="WRONG_VALUE"),
        Check(id="apl_appeal_auth_number", kind="query", db="portal",
              sql="SELECT UPPER(TRIM(authorization_number)) FROM appeals WHERE claim_id = ? ORDER BY id DESC",
              params=[target.id], equals=real.upper(), reason_code="WRONG_VALUE"),
    ]
    if require_attachment:
        effects += [
            Check(id="apl_attachment_present", kind="query", db="portal",
                  sql="SELECT COUNT(*) FROM appeals WHERE claim_id = ? AND attachment_sha256 IS NOT NULL", params=[target.id],
                  equals=1, reason_code="MISSING_ATTACHMENT"),
            Check(id="apl_attachment_hash", kind="query", db="portal",
                  sql="SELECT attachment_sha256 FROM appeals WHERE claim_id = ? ORDER BY id DESC", params=[target.id],
                  equals=doc_hash, reason_code="WRONG_ATTACHMENT"),
        ]
    invariants = [
        Check(id="apl_single_appeal", kind="count", db="portal", sql="SELECT COUNT(*) FROM appeals WHERE claim_id = ?",
              params=[target.id], equals=1, reason_code="DUPLICATE_SIDE_EFFECT"),
        *([_status_untouched("apl_other_denial_untouched", other_denial)] if other_denial is not None else []),
        *[_status_untouched(f"apl_distractor_{i}_untouched", c) for i, (_, c) in enumerate(distractors)],
        Check(id="apl_claim_fields_preserved", kind="preserve_fields", db="portal", sql="SELECT * FROM claims WHERE id = ?",
              params=[target.id], mutable_fields=["status", "updated_at"], reason_code="COLLATERAL_EDIT"),
        Check(id="apl_no_resubmission", kind="count", db="portal", sql="SELECT COUNT(*) FROM resubmissions WHERE claim_id = ?",
              params=[target.id], equals=0, reason_code="COLLATERAL_EDIT"),
    ]
    return _Part(
        kind="APL", person=person, text=text, effects=effects, invariants=invariants, allow={"portal.claims": [target.id]},
        exempt=["portal.appeals"], appeal_claims=[target.id], anchor_claim=target,
        expected={"patient_pid": person.pid, "claim_id": target.id, "claim_number": target.number, "auth_number": real,
                  "decoy_numbers": decoys, "doc_name": doc_name, "doc_page": page, "doc_hash": doc_hash, "doc_names": names,
                  "other_denial_claim": other_denial.number if other_denial else None,
                  "distractor_claims": [c.number for _, c in distractors]},
        difficulty={"distractors": len(distractors), "n_docs": n_docs, "n_pages": n_pages, "require_attachment": require_attachment,
                    "decoys": len(decoys), "other_denial": other_denial is not None,
                    "same_surname_distractor": any(d.last == person.last for d, _ in distractors)},
        hidden=[real, *decoys, *names],
    )


# ----------------------------------------------------------------------------- RSC


def _rsc(ctx: _Ctx, person: Person, provider_claim: Optional[Claim]) -> _Part:
    """``provider_claim`` set: the instruction names the provider only as "who billed claim <n>"."""
    rng, base = ctx.rng, ctx.base
    provider = person.provider
    distractors = [ctx.person(last=person.last if rng.random() < 0.6 else None, provider=provider if rng.random() < 0.5 else None)
                   for _ in range(rng.randint(0, 3))]
    other_provider = rng.choice([p for p in base.providers if p["openemr_id"] != provider["openemr_id"]])
    cur_date = _next_weekday(ANCHOR + dt.timedelta(days=rng.randint(0, 6)), rng.randint(0, 4), strictly_after=False)
    cur_min = rng.choice([9 * 60, 9 * 60 + 30, 10 * 60, 11 * 60, 13 * 60 + 30, 14 * 60, 15 * 60 + 30])
    cur_time = f"{cur_min // 60:02d}:{cur_min % 60:02d}:00"
    wd_idx = rng.randint(0, 4)
    half = rng.choice(["morning", "afternoon"])
    target_date = _next_weekday(cur_date, wd_idx)
    window = ("08:00:00", "11:59:59") if half == "morning" else ("12:00:00", "17:59:59")
    ev_id = ctx.take("events")
    ctx.openemr_sql.append(osql.insert_appointment(pc_eid=ev_id, pid=person.pid, provider_id=provider["openemr_id"],
                                                   event_date=cur_date, start_time=cur_time, title="Office Visit"))
    decoy_ids: list[int] = []
    has_other = rng.random() < 0.7
    if has_other:
        decoy_ids.append(ctx.take("events"))
        ctx.openemr_sql.append(osql.insert_appointment(pc_eid=decoy_ids[-1], pid=person.pid, provider_id=other_provider["openemr_id"],
                                                       event_date=cur_date + dt.timedelta(days=rng.randint(1, 5)),
                                                       start_time="10:30:00"))
    for d in distractors:
        decoy_ids.append(ctx.take("events"))
        ctx.openemr_sql.append(osql.insert_appointment(pc_eid=decoy_ids[-1], pid=d.pid, provider_id=d.provider["openemr_id"],
                                                       event_date=cur_date + dt.timedelta(days=rng.randint(-3, 6)),
                                                       start_time=cur_time))
    occupant = None
    if rng.random() < 0.3:
        occ_pid = rng.choice(sorted(p["pid"] for p in base.openemr["tables"]["patient_data"]))
        occ_time = cur_time if window[0] <= cur_time <= window[1] else window[0]
        occupant = {"event_id": ctx.take("events"), "pid": occ_pid, "time": occ_time, "duration": 1800}
        ctx.openemr_sql.append(osql.insert_appointment(pc_eid=occupant["event_id"], pid=occ_pid,
                                                       provider_id=provider["openemr_id"], event_date=target_date,
                                                       start_time=occ_time, duration_sec=1800))
    ctx.openemr_sql.append(osql.insert_log(id=ctx.take("log"), event="scheduling-insert", category="Scheduling", user="admin",
                                           patient_id=person.pid, comments=f"forkloop seed: event {ev_id}",
                                           date=sql_ts(ANCHOR - dt.timedelta(days=2))))
    who = f"the provider who billed claim {provider_claim.number}" if provider_claim is not None else provider["name"]
    text = (f"In OpenEMR, move {person.name}'s (DOB {person.dob.isoformat()}) appointment with {who} to the next "
            f"{WEEKDAYS[wd_idx]} {half} after its current date (the appointment is within the next two weeks), at a time when "
            f"that provider has no other appointment, keeping the same provider and visit type.")
    ev_sql = "SELECT {col} FROM openemr_postcalendar_events WHERE pc_eid = ?"
    effects = [
        Check(id="rsc_event_moved", kind="query", db="openemr", sql=ev_sql.format(col="pc_eventDate"), params=[ev_id],
              op="ne", equals=cur_date.isoformat(), reason_code="NOT_DONE"),
        Check(id="rsc_event_date", kind="query", db="openemr", sql=ev_sql.format(col="pc_eventDate"), params=[ev_id],
              equals=target_date.isoformat(), reason_code="WRONG_SLOT"),
        Check(id="rsc_event_time_window", kind="query", db="openemr",
              sql="SELECT COUNT(*) FROM openemr_postcalendar_events WHERE pc_eid = ? AND pc_startTime >= ? AND pc_startTime <= ?",
              params=[ev_id, window[0], window[1]], equals=1, reason_code="WRONG_SLOT"),
        Check(id="rsc_provider_unchanged", kind="query", db="openemr", sql=ev_sql.format(col="pc_aid"), params=[ev_id],
              equals=str(provider["openemr_id"]), reason_code="PROVIDER_CHANGED"),
    ]
    invariants = [
        Check(id="rsc_visit_type_unchanged", kind="query", db="openemr", sql=ev_sql.format(col="pc_catid"), params=[ev_id],
              equals=osql.CAT_OFFICE_VISIT, reason_code="WRONG_VALUE"),
        Check(id="rsc_event_end_date", kind="query", db="openemr", sql=ev_sql.format(col="pc_endDate"), params=[ev_id],
              equals=target_date.isoformat(), reason_code="WRONG_SLOT"),
        Check(id="rsc_event_duration_consistent", kind="count", db="openemr",
              sql="SELECT COUNT(*) FROM openemr_postcalendar_events WHERE pc_eid = ? AND "
                  "(SUBSTR(pc_endTime,1,2)*3600 + SUBSTR(pc_endTime,4,2)*60 + SUBSTR(pc_endTime,7,2)) - "
                  "(SUBSTR(pc_startTime,1,2)*3600 + SUBSTR(pc_startTime,4,2)*60 + SUBSTR(pc_startTime,7,2)) = pc_duration",
              params=[ev_id], equals=1, reason_code="WRONG_SLOT"),
        Check(id="rsc_no_overlap", kind="count", db="openemr", sql=NO_OVERLAP_SQL, params=[ev_id], equals=0, reason_code="WRONG_SLOT"),
        Check(id="rsc_event_fields_preserved", kind="preserve_fields", db="openemr",
              sql="SELECT * FROM openemr_postcalendar_events WHERE pc_eid = ?", params=[ev_id],
              mutable_fields=["pc_eventDate", "pc_endDate", "pc_startTime", "pc_endTime"], reason_code="COLLATERAL_EDIT"),
        Check(id="rsc_single_event", kind="count", db="openemr",
              sql="SELECT COUNT(*) FROM openemr_postcalendar_events WHERE pc_pid = ? AND pc_aid = ?",
              params=[str(person.pid), str(provider["openemr_id"])], equals=1, reason_code="DUPLICATE_SIDE_EFFECT"),
    ]
    return _Part(
        kind="RSC", person=person, text=text, effects=effects, invariants=invariants,
        allow={"openemr.openemr_postcalendar_events": [ev_id]}, exempt=[],
        expected={"patient_pid": person.pid, "event_id": ev_id, "target_date": target_date.isoformat(), "window": list(window),
                  "current_date": cur_date.isoformat(), "current_time": cur_time,
                  "provider_openemr_id": provider["openemr_id"], "decoy_event_ids": decoy_ids, "occupant": occupant,
                  "provider_claim": provider_claim.number if provider_claim is not None else None},
        difficulty={"distractors": len(distractors), "has_other_provider_appt": has_other, "half": half,
                    "same_surname_distractor": any(d.last == person.last for d in distractors),
                    "same_provider_distractor": any(d.provider["openemr_id"] == provider["openemr_id"] for d in distractors),
                    "occupied_slot": occupant is not None, "provider_ref": "via_claim" if provider_claim is not None else "named"},
        hidden=[target_date.isoformat(), window[0][:5], window[1][:5]],
    )


# ----------------------------------------------------------------------------- merge


#: app-level steps a part needs: INS = OpenEMR edit + portal resubmission (portal only when OpenEMR is
#: already updated), APL = OpenEMR document read + portal appeal, RSC = calendar edit (+ a portal read via_claim)
_STEPS = {"INS": lambda d: 1 if d["partially_updated"] else 2, "APL": lambda d: 2,
          "RSC": lambda d: 2 if d["provider_ref"] == "via_claim" else 1}


def _merge_allow(parts: list[_Part]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for p in parts:
        for table, pks in p.allow.items():
            out.setdefault(table, [])
            out[table] += [k for k in pks if k not in out[table]]
    return out


def _global_invariants(parts: list[_Part]) -> list[Check]:
    appeal_ok = sorted({c for p in parts for c in p.appeal_claims})
    resub_ok = sorted({c for p in parts for c in p.resubmit_claims})
    exempt = sorted({t for p in parts for t in p.exempt})

    def others(table: str, keep: list[int]) -> tuple[str, list[int]]:
        if not keep:
            return f"SELECT * FROM {table} ORDER BY id", []
        return f"SELECT * FROM {table} WHERE claim_id NOT IN ({', '.join('?' for _ in keep)}) ORDER BY id", list(keep)

    a_sql, a_params = others("appeals", appeal_ok)
    r_sql, r_params = others("resubmissions", resub_ok)
    return [
        # appeals/resubmissions are exempt from the checksum where a part legitimately adds one: every other row is guarded here
        Check(id="other_appeals_preserved", kind="preserve_fields", db="portal", sql=a_sql, params=a_params,
              reason_code="COLLATERAL_EDIT"),
        Check(id="other_resubmissions_preserved", kind="preserve_fields", db="portal", sql=r_sql, params=r_params,
              reason_code="COLLATERAL_EDIT"),
        Check(id="no_collateral", kind="baseline_checksum", allow=_merge_allow(parts), exempt_tables=exempt,
              reason_code="COLLATERAL_EDIT"),
        Check(id="ui_path", kind="ui_path_only", exempt_tables=exempt, op=V2_UI_PATH_OP, reason_code="DIRECT_DB_WRITE"),
        Check(id="no_forbidden", kind="forbidden_screens", reason_code="FORBIDDEN_SCREEN"),
    ]


def generate(family: str, seed: int, split: str, base: BaseData | None = None) -> TaskInstance:
    base = base or load_base()
    rng = rng_for(family, seed, split)
    eid = episode_id_base(seed)
    ids = base.next_ids()
    ids["pid"] = ids["portal_patient"] = eid
    ids["portal_claim"] = eid
    ids["claim_number"] = CLAIM_NUMBER_BASE + (seed % 20000)
    ctx = _Ctx(rng=rng, split=split, seed=seed, base=base, eid=eid, ids=ids)

    # structure draws first, so the pairing/order/link distribution never depends on part contents
    pairing = rng.choice(PAIRINGS)
    kinds = pairing.split("+")                      # canonical (generation) order
    order = kinds if rng.random() < 0.5 else kinds[::-1]
    link = "same" if rng.random() < 0.6 else "household"
    via_claim = rng.random() < 0.5

    first = ctx.person()
    second = first if link == "same" else ctx.person(first_not=first.first, last=first.last, provider=first.provider)
    persons = {kinds[0]: first, kinds[1]: second}
    parts: dict[str, _Part] = {}
    for kind in kinds:
        if kind == "INS":
            parts[kind] = _ins(ctx, persons[kind])
        elif kind == "APL":
            parts[kind] = _apl(ctx, persons[kind])
        else:
            other = next(p for k, p in parts.items() if k != "RSC")
            # both patients share the provider, so a claim of either names the appointment's provider
            parts[kind] = _rsc(ctx, persons[kind], other.anchor_claim if via_claim else None)
    noise = rng.randint(0, 3)
    if noise:
        ctx.portal_sql.append(noise_messages(rng, noise, eid + _OFF["messages"]))

    presented = [parts[k] for k in order]
    instruction = (f"You have two tasks. (1) {presented[0].text} (2) {presented[1].text} Log in to OpenEMR as admin / pass. "
                   f"Leave every other claim, patient record and appointment as it is, and submit each item exactly once. "
                   f"{APPS_HINT}")
    for p in presented:
        for value in p.hidden:
            if value and value in instruction:
                raise AssertionError(f"compose_claims: hidden value {value!r} leaked into the instruction")

    effects = [c for p in presented for c in p.effects]
    invariants = order_v2_invariants([c for p in presented for c in p.invariants] + _global_invariants(presented))

    # top level: the keys the world's feasibility gate and ui_milestones read, for the patient of the anchor claim
    anchor = parts["APL"] if "APL" in parts else parts["INS"]
    top: dict[str, Any] = {"patient_pid": anchor.person.pid, "claim_id": anchor.expected["claim_id"],
                           "claim_number": anchor.expected["claim_number"]}
    if "APL" in parts:
        top.update({k: parts["APL"].expected[k] for k in ("auth_number", "decoy_numbers", "doc_name", "doc_hash")})
    if "RSC" in parts and parts["RSC"].person.pid == anchor.person.pid:
        top["event_id"] = parts["RSC"].expected["event_id"]
    expected = {**top, "pairing": pairing, "order": list(order), "link": link,
                "parts": [{"kind": p.kind, "family": PART_FAMILIES[p.kind], **p.expected} for p in presented]}
    difficulty = {"composition": True, "generator": "v2", "pairing": pairing, "order": list(order), "link": link,
                  "provider_ref": parts["RSC"].difficulty["provider_ref"] if "RSC" in parts else None,
                  "steps_required": sum(_STEPS[p.kind](p.difficulty) for p in presented),
                  "noise_messages": noise, "parts": {p.kind: p.difficulty for p in presented}}
    return TaskInstance(
        world="claims-ops-v1", family=family, seed=seed, split=split, task_id=make_task_id(family, split, seed),
        instruction=instruction, initial_screen={"app": "portal", "url": "http://localhost:8080/claims?status=DENIED"},
        seeding=Seeding(portal_sql="\n".join(ctx.portal_sql), openemr_sql="\n".join(ctx.openemr_sql), files=ctx.files,
                        post_commands=[]),
        expected=expected, oracle=OracleSpec(effects=effects, invariants=invariants),
        budget={"max_steps": 120, "max_seconds": 1200}, difficulty=difficulty,
    )


__all__ = ["generate", "FAMILY", "PAIRINGS", "PART_FAMILIES", "LINKS"]
