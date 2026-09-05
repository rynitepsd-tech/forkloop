"""``generate(family, seed, split) -> TaskInstance`` — the world's pure task generator.

Held-out compositions (seeds 200000+): "update insurance, then appeal the
correct one of two denials" — built by composing family 2 and family 3 on the
same patient with a shared oracle.
"""

from __future__ import annotations

import datetime as dt

from forkloop.oracle import Check, OracleSpec
from forkloop.tasks import Seeding, TaskInstance, make_task_id

from .openemr import openemr_sql as osql
from .tasks import reschedule_constrained, resolve_denial, update_insurance_reconcile
from .tasks.common import (ANCHOR, PAYERS, auth_number, authorization_letter, document_seed_file, episode_id_base,
                           load_base, make_claim, make_person, member_id, rng_for, sha256)

FAMILIES = {
    "reschedule_constrained": reschedule_constrained.generate,
    "update_insurance_reconcile": update_insurance_reconcile.generate,
    "resolve_denial": resolve_denial.generate,
    # diagnostic variant: auth number on page 1 of a one-page letter, no distractor claims
    "resolve_denial_easy": resolve_denial.generate,
}
SEED_RANGES = {"train": (0, 99999), "heldout_seeds": (100000, 199999), "heldout_compositions": (200000, 299999)}


def split_for_seed(seed: int) -> str:
    for name, (lo, hi) in SEED_RANGES.items():
        if lo <= seed <= hi:
            return name
    return "train"


def generate(family: str, seed: int, split: str = "train") -> TaskInstance:
    if split == "heldout_compositions" and family in ("update_insurance_reconcile", "resolve_denial"):
        return _composition(family, seed, split)
    fn = FAMILIES.get(family)
    if fn is None:
        raise ValueError(f"unknown family {family!r}; have {sorted(FAMILIES)}")
    return fn(family, seed, split)


def _composition(family: str, seed: int, split: str) -> TaskInstance:
    """Update insurance in OpenEMR, resubmit the CO-31 claim, and appeal the CO-197 claim — leaving a third denial alone."""
    base = load_base()
    rng = rng_for("composition", seed, split)
    ids = base.next_ids()
    eid = episode_id_base(seed)
    ids["pid"] = ids["portal_patient"] = eid
    ids["portal_claim"] = eid
    ids["claim_number"] = 80000 + (seed % 50000)
    person = make_person(rng, split, ids, base)
    old_member = person.member
    new_plan = rng.choice([p for p in sorted(PAYERS) if p != person.plan])
    new_member = member_id(rng, new_plan)
    c31 = make_claim(rng, ids, person, status="DENIED", denial_code="CO-31", submitted_member=old_member)
    c197 = make_claim(rng, ids, person, status="DENIED", denial_code="CO-197")
    c29 = make_claim(rng, ids, person, status="DENIED", denial_code="CO-29")  # must stay untouched
    real = auth_number(rng)
    decoys = [auth_number(rng) for _ in range(2)]
    pdf = authorization_letter(rng, person, real, decoys, f"CPT {c197.cpt} on {c197.service_date.isoformat()}")
    name = "authorization_letter_1.pdf"
    openemr_sql = [person.openemr_sql(rng, eid),
                   osql.insert_document(doc_id=eid, pid=person.pid, name=name, size=len(pdf), content_hash=sha256(pdf),
                                        docdate=ANCHOR - dt.timedelta(days=30))]
    portal_sql = [person.portal_sql(ANCHOR - dt.timedelta(days=100)), c31.portal_sql(), c197.portal_sql(), c29.portal_sql()]
    instruction = (f"{person.name}'s (DOB {person.dob.isoformat()}) insurance changed to {new_plan}, member ID {new_member}. "
                   f"Update the primary insurance in OpenEMR (log in as admin / pass), then in the payer portal: resubmit claim {c31.number} with the corrected "
                   f"member ID, and file an appeal on claim {c197.number} with reason 'Prior authorization was obtained' using the "
                   f"authorization number from the patient's OpenEMR documents. Claim {c29.number} must be left as it is.")
    effects = [
        Check(id="openemr_policy", kind="query", db="openemr",
              sql="SELECT policy_number FROM insurance_data WHERE pid = ? AND type = 'primary' ORDER BY date DESC, id DESC",
              params=[person.pid], equals=new_member, reason_code="WRONG_VALUE"),
        Check(id="c31_resubmitted", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?", params=[c31.id],
              equals="RESUBMITTED", reason_code="NOT_DONE"),
        Check(id="c31_member", kind="query", db="portal", sql="SELECT submitted_member_id FROM claims WHERE id = ?", params=[c31.id],
              equals=new_member, reason_code="WRONG_VALUE"),
        Check(id="c197_appealed", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?", params=[c197.id],
              equals="APPEAL_SUBMITTED", reason_code="NOT_DONE"),
        Check(id="c197_auth", kind="query", db="portal", sql="SELECT UPPER(TRIM(authorization_number)) FROM appeals WHERE claim_id = ? ORDER BY id DESC",
              params=[c197.id], equals=real.upper(), reason_code="WRONG_VALUE"),
    ]
    invariants = [
        Check(id="c29_untouched", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?", params=[c29.id],
              equals="DENIED", reason_code="WRONG_RECORD"),
        Check(id="single_appeal", kind="count", db="portal", sql="SELECT COUNT(*) FROM appeals WHERE claim_id IN (?, ?, ?)",
              params=[c31.id, c197.id, c29.id], equals=1, reason_code="DUPLICATE_SIDE_EFFECT"),
        Check(id="single_resub", kind="count", db="portal", sql="SELECT COUNT(*) FROM resubmissions WHERE claim_id IN (?, ?, ?)",
              params=[c31.id, c197.id, c29.id], equals=1, reason_code="DUPLICATE_SIDE_EFFECT"),
        Check(id="no_collateral", kind="baseline_checksum",
              allow={"portal.claims": [c31.id, c197.id], "openemr.insurance_data": [person.insurance_id]},
              exempt_tables=["portal.appeals", "portal.resubmissions"], reason_code="COLLATERAL_EDIT"),
        Check(id="ui_path", kind="ui_path_only", exempt_tables=["portal.appeals", "portal.resubmissions"], reason_code="DIRECT_DB_WRITE"),
        Check(id="no_forbidden", kind="forbidden_screens", reason_code="FORBIDDEN_SCREEN"),
    ]
    return TaskInstance(
        world="claims-ops-v1", family=family, seed=seed, split=split, task_id=make_task_id(family, split, seed),
        instruction=instruction, initial_screen={"app": "portal", "url": "http://localhost:8080/claims?status=DENIED"},
        seeding=Seeding(portal_sql="\n".join(portal_sql), openemr_sql="\n".join(openemr_sql),
                        files=[document_seed_file(person, name, pdf)], post_commands=[]),
        expected={"patient_pid": person.pid, "new_member": new_member, "c31": c31.number, "c197": c197.number,
                  "c29": c29.number, "auth_number": real},
        oracle=OracleSpec(effects=effects, invariants=invariants),
        budget={"max_steps": 90, "max_seconds": 900},
        difficulty={"composition": True, "steps_required": 3},
    )


__all__ = ["generate", "FAMILIES", "SEED_RANGES", "split_for_seed"]
