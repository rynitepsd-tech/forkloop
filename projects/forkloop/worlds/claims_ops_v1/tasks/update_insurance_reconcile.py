"""Family 2 — update insurance in OpenEMR, then resubmit the claim in the portal.

Both systems change. Randomisation: whether OpenEMR is already partially
updated (one-system variant), similar-member-id distractor claims, a second
claim for the same patient that must NOT be resubmitted.

v2 splits (``common.V2_SPLITS``, 2026-09-29; legacy splits are byte-identical): with
probability ``PRIOR_RESUB_P`` the claim was already resubmitted once with a mistyped new
member ID and denied again, so the resubmit form is prefilled with a near-miss value; the
agent files one new resubmission with the right ID. v2 tasks also use the write-rows-only
audit match (``V2_UI_PATH_OP``) and record ``same_surname_distractor`` in ``difficulty``.
"""

from __future__ import annotations

import datetime as dt

from forkloop.oracle import Check, OracleSpec
from forkloop.tasks import Seeding, TaskInstance, make_task_id

from ..openemr import openemr_sql as osql
from .common import (APPS_HINT, ANCHOR, PAYERS, V2_UI_PATH_OP, BaseData, episode_id_base, insert_resubmission, is_v2, iso_ts,
                     load_base, make_claim, make_person, member_id, noise_messages, order_v2_invariants, rng_for, similar_member_id,
                     sql_ts, update_claim)

FAMILY = "update_insurance_reconcile"
#: v2 only: share of tasks whose claim was already resubmitted once with a mistyped member ID
PRIOR_RESUB_P = 0.25
PRIOR_RESUB_OFFSET = 950  # resubmissions.id = episode block + 950


def generate(family: str, seed: int, split: str, base: BaseData | None = None) -> TaskInstance:
    base = base or load_base()
    rng = rng_for(family, seed, split)
    ids = base.next_ids()
    eid = episode_id_base(seed)
    ids["pid"] = ids["portal_patient"] = eid
    ids["portal_claim"] = eid
    ids["claim_number"] = 40000 + (seed % 50000)

    person = make_person(rng, split, ids, base)
    old_member = person.member
    new_plan = rng.choice([p for p in sorted(PAYERS) if p != person.plan] or sorted(PAYERS))
    new_member = member_id(rng, new_plan)
    partially_updated = split != "heldout_compositions" and rng.random() < 0.3  # OpenEMR already correct → portal-only task

    # the denied claim (CO-31: member not identified) submitted with the OLD member id
    target = make_claim(rng, ids, person, status="DENIED", denial_code="CO-31", submitted_member=old_member)
    # a second claim for the same patient that must not be touched
    other = make_claim(rng, ids, person, status=rng.choice(["PAID", "SUBMITTED"]))
    # distractor patient with a similar member id and a denied claim
    n_distractors = rng.randint(0, 2)
    distractors = []
    for _ in range(n_distractors):
        d = make_person(rng, split, ids, base, last=person.last if rng.random() < 0.5 else None, plan=person.plan)
        d.member = similar_member_id(rng, old_member)
        distractors.append((d, make_claim(rng, ids, d, status="DENIED", denial_code="CO-31", submitted_member=d.member)))

    portal_sql = [person.portal_sql(ANCHOR - dt.timedelta(days=90)), target.portal_sql(), other.portal_sql()]
    openemr_sql = [person.openemr_sql(rng, eid)]
    for i, (d, c) in enumerate(distractors, start=1):
        portal_sql += [d.portal_sql(ANCHOR - dt.timedelta(days=80)), c.portal_sql()]
        openemr_sql.append(d.openemr_sql(rng, eid + i))
    if partially_updated:
        openemr_sql.append(osql.update_insurance_policy(pid=person.pid, policy_number=new_member, plan_name=new_plan,
                                                        company_id=PAYERS[new_plan][0]))
    v2 = is_v2(split)
    noise = rng.randint(0, 3)
    if noise:
        # legacy splits keep their historical message ids (below the 500000 episode floor); v2 uses the episode block
        portal_sql.append(noise_messages(rng, noise, (eid + 400) if v2 else 1000 + (seed % 100000) * 10))
    # v2 draws come after every legacy draw
    prior_resub: dict | None = None
    if v2 and rng.random() < PRIOR_RESUB_P:
        prior_resub = {"id": eid + PRIOR_RESUB_OFFSET, "member_id": similar_member_id(rng, new_member)}
        when = iso_ts(target.service_date + dt.timedelta(days=13))
        portal_sql.append(insert_resubmission(id=prior_resub["id"], claim_id=target.id, member_id=prior_resub["member_id"],
                                              note="Corrected member ID", created_at=when))
        portal_sql.append(update_claim(target.id, submitted_member_id=prior_resub["member_id"],
                                       denial_reason="Member ID on the resubmitted claim does not match our records.",
                                       updated_at=when))

    if partially_updated:
        instruction = (f"{person.name}'s (DOB {person.dob.isoformat()}) insurance is now {new_plan}, member ID {new_member}, "
                       f"and OpenEMR already reflects this. In the payer portal, resubmit claim {target.number} with the corrected member ID. "
                       f"Leave every other claim untouched. {APPS_HINT}")
    else:
        instruction = (f"{person.name}'s (DOB {person.dob.isoformat()}) insurance changed to {new_plan}, member ID {new_member}. "
                       f"Update the primary insurance in OpenEMR (log in as admin / pass; plan name and policy number), then in the payer portal resubmit "
                       f"claim {target.number} with the corrected member ID. Do not resubmit or appeal any other claim. {APPS_HINT}")
    if prior_resub:
        instruction += (f" Note: an earlier resubmission of {target.number} used a mistyped member ID and was denied again; "
                        f"file exactly one new resubmission.")

    effects = [
        Check(id="openemr_policy", kind="query", db="openemr",
              sql="SELECT policy_number FROM insurance_data WHERE pid = ? AND type = 'primary' ORDER BY date DESC, id DESC",
              params=[person.pid], equals=new_member, reason_code="WRONG_VALUE"),
        Check(id="openemr_plan", kind="query", db="openemr",
              sql="SELECT plan_name FROM insurance_data WHERE pid = ? AND type = 'primary' ORDER BY date DESC, id DESC",
              params=[person.pid], equals=new_plan, reason_code="WRONG_VALUE"),
        Check(id="claim_status", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?", params=[target.id],
              equals="RESUBMITTED", reason_code="NOT_DONE"),
        Check(id="claim_member", kind="query", db="portal", sql="SELECT submitted_member_id FROM claims WHERE id = ?",
              params=[target.id], equals=new_member, reason_code="WRONG_VALUE"),
    ]
    allow = {"portal.claims": [target.id], "portal.resubmissions": ["*"], "openemr.insurance_data": [person.insurance_id]}
    if v2 and partially_updated:
        del allow["openemr.insurance_data"]  # OpenEMR is already right: its row must not change at all
    invariants = [
        Check(id="insurance_fields_preserved", kind="preserve_fields", db="openemr",
              sql="SELECT * FROM insurance_data WHERE id = ?", params=[person.insurance_id],
              mutable_fields=[] if partially_updated else ["plan_name", "policy_number"], reason_code="COLLATERAL_EDIT"),
        Check(id="claim_fields_preserved", kind="preserve_fields", db="portal",
              sql="SELECT * FROM claims WHERE id = ?", params=[target.id],
              mutable_fields=["submitted_member_id", "status", "updated_at"], reason_code="COLLATERAL_EDIT"),
        Check(id="other_resubmissions_preserved", kind="preserve_fields", db="portal",
              sql="SELECT * FROM resubmissions WHERE claim_id != ? ORDER BY id", params=[target.id], reason_code="COLLATERAL_EDIT"),
        Check(id="resubmission_member", kind="count", db="portal",
              sql="SELECT COUNT(*) FROM resubmissions WHERE claim_id = ? AND member_id = ?",
              params=[target.id, new_member], equals=1, reason_code="WRONG_VALUE"),
        Check(id="single_resubmission", kind="count", db="portal",
              sql=("SELECT COUNT(*) FROM resubmissions WHERE claim_id = ? AND id != ?" if prior_resub else
                   "SELECT COUNT(*) FROM resubmissions WHERE claim_id = ?"),
              params=[target.id, prior_resub["id"]] if prior_resub else [target.id], equals=1,
              reason_code="DUPLICATE_SIDE_EFFECT"),
        Check(id="other_claim_untouched", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?",
              params=[other.id], equals=other.status, reason_code="WRONG_RECORD"),
        Check(id="no_appeal", kind="count", db="portal", sql="SELECT COUNT(*) FROM appeals WHERE claim_id IN (?, ?)",
              params=[target.id, other.id], equals=0, reason_code="COLLATERAL_EDIT"),
        Check(id="no_collateral", kind="baseline_checksum", allow=allow, exempt_tables=["portal.resubmissions"],
              reason_code="COLLATERAL_EDIT"),
        Check(id="ui_path", kind="ui_path_only", exempt_tables=["portal.resubmissions"], reason_code="DIRECT_DB_WRITE",
              op=V2_UI_PATH_OP if v2 else "eq"),
        Check(id="no_forbidden", kind="forbidden_screens", reason_code="FORBIDDEN_SCREEN"),
    ]
    if v2:
        # an untouched OpenEMR policy is missing work (NOT_DONE); a changed but different one stays WRONG_VALUE
        effects.insert(0, Check(id="openemr_updated", kind="query", db="openemr",
                                sql="SELECT policy_number FROM insurance_data WHERE pid = ? AND type = 'primary' "
                                    "ORDER BY date DESC, id DESC",
                                params=[person.pid], op="ne", equals=old_member, reason_code="NOT_DONE"))
    if prior_resub:
        invariants.insert(5, Check(id="prior_resubmission_preserved", kind="preserve_fields", db="portal",
                                   sql="SELECT * FROM resubmissions WHERE id = ?", params=[prior_resub["id"]],
                                   reason_code="COLLATERAL_EDIT"))
    for i, (d, c) in enumerate(distractors):
        invariants.append(Check(id=f"distractor_{i}_untouched", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?",
                                params=[c.id], equals="DENIED", reason_code="WRONG_RECORD"))
    if v2:
        invariants = order_v2_invariants(invariants)
    expected = {"patient_pid": person.pid, "claim_id": target.id, "claim_number": target.number, "old_member": old_member,
                "new_member": new_member, "new_plan": new_plan, "other_claim": other.number,
                "distractor_claims": [c.number for _, c in distractors]}
    difficulty = {"distractors": n_distractors, "partially_updated": partially_updated, "noise_messages": noise,
                  "both_systems": not partially_updated}
    if v2:
        expected["prior_resubmission"] = prior_resub
        difficulty.update({"generator": "v2", "prior_wrong_resubmission": prior_resub is not None,
                           "same_surname_distractor": any(d.last == person.last for d, _ in distractors)})
    return TaskInstance(
        world="claims-ops-v1", family=family, seed=seed, split=split, task_id=make_task_id(family, split, seed),
        instruction=instruction,
        initial_screen={"app": "portal", "url": "http://localhost:8080/claims?status=DENIED"},
        seeding=Seeding(portal_sql="\n".join(portal_sql), openemr_sql="\n".join(openemr_sql), files=[], post_commands=[]),
        expected=expected,
        oracle=OracleSpec(effects=effects, invariants=invariants),
        budget={"max_steps": 60, "max_seconds": 600},
        difficulty=difficulty,
    )
