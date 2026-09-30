"""Family 3 — resolve a CO-197 denial with the authorization number found in
the patient's OpenEMR document. Attachment is required only when
``difficulty.require_attachment`` (harder variant).

``resolve_denial_easy`` is the same generator with the difficulty flag ``easy``: the
authorization number is always on page 1 of a one-page letter and there are no
distractor claims (no same-surname patient with an adjacent denied claim). Every
other draw is shared with the standard task of the same seed — same patient, same
claim, same authorization number, same decoys, same document count — so the two
variants are directly comparable seed for seed. Added 2026-09-04 as the diagnostic
rung of the student bake-off (docs/archive/student-2026-09-05.md).

Randomisation: which document (and page) holds the number, distractor numbers
on the same page, a same-surname distractor with its own denied claim that must
stay untouched, off-by-one claim numbers, inbox noise.

v2 splits (``common.V2_SPLITS``, 2026-09-29; legacy splits are byte-identical): with
probability ``PRIOR_APPEAL_P`` the claim already carries a *rejected* appeal that cited a
decoy number (a lure on the claim page; the agent files one new appeal). Every v2 task
also checks the claim's other fields, that the claim was not resubmitted, that no other
appeal changed or appeared, and uses the write-rows-only audit match (``V2_UI_PATH_OP``).
"""

from __future__ import annotations

import datetime as dt

from forkloop.oracle import Check, OracleSpec
from forkloop.tasks import Seeding, TaskInstance, make_task_id

from ..openemr import openemr_sql as osql
from .common import (APPS_HINT, ANCHOR, V2_UI_PATH_OP, BaseData, auth_number, authorization_letter, document_seed_file,
                     episode_id_base, insert_appeal, is_v2, iso_ts, load_base, make_claim, make_person, noise_messages,
                     order_v2_invariants, rng_for, sha256, sql_ts, update_claim)

FAMILY = "resolve_denial"
#: v2 only: share of tasks whose claim already has a rejected appeal citing a decoy number
PRIOR_APPEAL_P = 0.25
PRIOR_APPEAL_OFFSET = 900  # appeals.id = episode block + 900


def generate(family: str, seed: int, split: str, base: BaseData | None = None) -> TaskInstance:
    base = base or load_base()
    easy = family.endswith("_easy")
    # the easy variant shares the standard task's random stream so the two are comparable seed for seed
    rng = rng_for(family[:-len("_easy")] if easy else family, seed, split)
    ids = base.next_ids()
    eid = episode_id_base(seed)
    ids["pid"] = ids["portal_patient"] = eid
    ids["portal_claim"] = eid
    ids["claim_number"] = 60000 + (seed % 50000)

    person = make_person(rng, split, ids, base)
    target = make_claim(rng, ids, person, status="DENIED", denial_code="CO-197")
    # same-surname distractor with an adjacent claim number and its own CO-197 denial
    n_distractors = rng.randint(0, 2)
    distractors = []
    for _ in range(n_distractors):
        d = make_person(rng, split, ids, base, last=person.last if rng.random() < 0.7 else None)
        distractors.append((d, make_claim(rng, ids, d, status="DENIED", denial_code=rng.choice(["CO-197", "CO-29"]))))
    if easy:
        # drawn (so the rng stream stays aligned with the standard variant), then dropped
        distractors, n_distractors = [], 0

    real = auth_number(rng)
    decoys = [auth_number(rng) for _ in range(rng.randint(1, 4))]
    n_docs = rng.randint(1, 3)
    which = rng.randrange(n_docs)
    n_pages = rng.choice([1, 1, 2])
    page = rng.randrange(n_pages) + 1
    if easy:
        n_pages, page = 1, 1
    require_attachment = split != "train" and rng.random() < 0.3 if split == "heldout_compositions" else rng.random() < 0.15
    service_desc = f"CPT {target.cpt} on {target.service_date.isoformat()}"

    files, openemr_sql = [], [person.openemr_sql(rng, eid)]
    doc_hash = ""
    doc_name_real = ""
    for i in range(n_docs):
        name = rng.choice(["authorization_letter", "um_determination", "precert_notice", "payer_correspondence"]) + f"_{i + 1}.pdf"
        if i == which:
            pdf = authorization_letter(rng, person, real, decoys, service_desc, page_of=(page, n_pages),
                                       service_date=target.service_date)
            doc_hash = sha256(pdf)
            doc_name_real = name
        else:
            pdf = authorization_letter(rng, person, rng.choice(decoys), decoys, "a different service (not this claim)",
                                       page_of=(1, 1), service_date=target.service_date)
        files.append(document_seed_file(person, name, pdf))
        openemr_sql.append(osql.insert_document(doc_id=eid + i, pid=person.pid, name=name, size=len(pdf), content_hash=sha256(pdf),
                                                docdate=ANCHOR - dt.timedelta(days=rng.randint(20, 60))))
    for i, (d, c) in enumerate(distractors, start=1):
        openemr_sql.append(d.openemr_sql(rng, eid + 10 + i))
    portal_sql = [person.portal_sql(ANCHOR - dt.timedelta(days=100)), target.portal_sql()]
    for d, c in distractors:
        portal_sql += [d.portal_sql(ANCHOR - dt.timedelta(days=95)), c.portal_sql()]
    v2 = is_v2(split)
    noise = rng.randint(0, 3)
    if noise:
        # legacy splits keep their historical message ids (below the 500000 episode floor); v2 uses the episode block
        portal_sql.append(noise_messages(rng, noise, (eid + 400) if v2 else 2000 + (seed % 100000) * 10))
    # v2 draws come after every legacy draw
    prior_appeal: dict | None = None
    if v2 and rng.random() < PRIOR_APPEAL_P:
        prior_appeal = {"id": eid + PRIOR_APPEAL_OFFSET, "authorization_number": rng.choice(decoys)}
        when = iso_ts(target.service_date + dt.timedelta(days=13))
        portal_sql.append(insert_appeal(id=prior_appeal["id"], claim_id=target.id, reason_code="PRECERT_OBTAINED",
                                        authorization_number=prior_appeal["authorization_number"],
                                        narrative="Prior authorization was obtained before the service date.",
                                        created_at=when))
        portal_sql.append(update_claim(target.id, denial_reason="Appeal rejected: the authorization number cited is not on file.",
                                       updated_at=when))

    instruction = (f"Claim {target.number} for {person.name} (DOB {person.dob.isoformat()}) was denied with code CO-197 "
                   f"(prior authorization missing). Find the authorization number in the patient's documents in OpenEMR (log in as admin / pass), "
                   f"then in the payer portal file an appeal on {target.number} with reason 'Prior authorization was obtained' "
                   f"and enter that authorization number. {APPS_HINT}")
    if require_attachment:
        instruction += " Attach the authorization letter to the appeal."
    if prior_appeal:
        instruction += (f" An earlier appeal on {target.number} was rejected because the authorization number it cited is "
                        f"not on file. File exactly one new appeal and do not touch any other claim.")
    else:
        instruction += " File exactly one appeal and do not touch any other claim."

    effects = [
        Check(id="claim_status", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?", params=[target.id],
              equals="APPEAL_SUBMITTED", reason_code="NOT_DONE"),
        Check(id="appeal_reason", kind="query", db="portal", sql="SELECT reason_code FROM appeals WHERE claim_id = ? ORDER BY id DESC",
              params=[target.id], equals="PRECERT_OBTAINED", reason_code="WRONG_VALUE"),
        Check(id="appeal_auth_number", kind="query", db="portal",
              sql="SELECT UPPER(TRIM(authorization_number)) FROM appeals WHERE claim_id = ? ORDER BY id DESC",
              params=[target.id], equals=real.upper(), reason_code="WRONG_VALUE"),
    ]
    if require_attachment:
        # Presence first: the first failed effect names the verdict, so a missing file must
        # not be reported as the wrong file.
        effects.append(Check(id="attachment_present", kind="query", db="portal",
                             sql="SELECT COUNT(*) FROM appeals WHERE claim_id = ? AND attachment_sha256 IS NOT NULL",
                             params=[target.id], equals=1, reason_code="MISSING_ATTACHMENT"))
        effects.append(Check(id="attachment_hash", kind="query", db="portal",
                             sql="SELECT attachment_sha256 FROM appeals WHERE claim_id = ? ORDER BY id DESC",
                             params=[target.id], equals=doc_hash, reason_code="WRONG_ATTACHMENT"))
    distractor_checks = [Check(id=f"distractor_{i}_untouched", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?",
                               params=[c.id], equals="DENIED", reason_code="WRONG_RECORD") for i, (d, c) in enumerate(distractors)]
    if not v2:
        invariants = [
            Check(id="single_appeal", kind="count", db="portal", sql="SELECT COUNT(*) FROM appeals WHERE claim_id = ?",
                  params=[target.id], equals=1, reason_code="DUPLICATE_SIDE_EFFECT"),
            Check(id="no_collateral", kind="baseline_checksum", allow={"portal.claims": [target.id]},
                  exempt_tables=["portal.appeals"], reason_code="COLLATERAL_EDIT"),
            Check(id="ui_path", kind="ui_path_only", exempt_tables=["portal.appeals"], reason_code="DIRECT_DB_WRITE"),
            Check(id="no_forbidden", kind="forbidden_screens", reason_code="FORBIDDEN_SCREEN"),
            *distractor_checks,
        ]
    else:
        new_appeals = ("SELECT COUNT(*) FROM appeals WHERE claim_id = ? AND id != ?", [target.id, prior_appeal["id"]]) \
            if prior_appeal else ("SELECT COUNT(*) FROM appeals WHERE claim_id = ?", [target.id])
        invariants = [
            Check(id="single_appeal", kind="count", db="portal", sql=new_appeals[0], params=new_appeals[1], equals=1,
                  reason_code="DUPLICATE_SIDE_EFFECT"),
            *distractor_checks,
            Check(id="claim_fields_preserved", kind="preserve_fields", db="portal", sql="SELECT * FROM claims WHERE id = ?",
                  params=[target.id], mutable_fields=["status", "updated_at"], reason_code="COLLATERAL_EDIT"),
            Check(id="no_resubmission", kind="count", db="portal", sql="SELECT COUNT(*) FROM resubmissions WHERE claim_id = ?",
                  params=[target.id], equals=0, reason_code="COLLATERAL_EDIT"),
        ]
        if prior_appeal:
            invariants.append(Check(id="prior_appeal_preserved", kind="preserve_fields", db="portal",
                                    sql="SELECT * FROM appeals WHERE id = ?", params=[prior_appeal["id"]],
                                    reason_code="COLLATERAL_EDIT"))
        invariants += [
            # appeals are exempt from the checksum (the new row is legitimate): guard every other appeal here
            Check(id="other_appeals_preserved", kind="preserve_fields", db="portal",
                  sql="SELECT * FROM appeals WHERE claim_id != ? ORDER BY id", params=[target.id], reason_code="COLLATERAL_EDIT"),
            Check(id="no_collateral", kind="baseline_checksum", allow={"portal.claims": [target.id]},
                  exempt_tables=["portal.appeals"], reason_code="COLLATERAL_EDIT"),
            Check(id="ui_path", kind="ui_path_only", exempt_tables=["portal.appeals"], op=V2_UI_PATH_OP,
                  reason_code="DIRECT_DB_WRITE"),
            Check(id="no_forbidden", kind="forbidden_screens", reason_code="FORBIDDEN_SCREEN"),
        ]
        invariants = order_v2_invariants(invariants)
    expected = {"patient_pid": person.pid, "claim_id": target.id, "claim_number": target.number, "auth_number": real,
                "decoy_numbers": decoys, "doc_name": doc_name_real, "doc_page": page, "doc_hash": doc_hash,
                "distractor_claims": [c.number for _, c in distractors]}
    difficulty = {"distractors": n_distractors, "n_docs": n_docs, "n_pages": n_pages, "require_attachment": require_attachment,
                  "decoys": len(decoys), "noise_messages": noise, "variant": "easy" if easy else "standard"}
    if v2:
        expected["prior_appeal"] = prior_appeal
        difficulty.update({"generator": "v2", "prior_rejected_appeal": prior_appeal is not None,
                           "same_surname_distractor": any(d.last == person.last for d, _ in distractors)})
    return TaskInstance(
        world="claims-ops-v1", family=family, seed=seed, split=split, task_id=make_task_id(family, split, seed),
        instruction=instruction,
        initial_screen={"app": "portal", "url": "http://localhost:8080/claims?status=DENIED"},
        seeding=Seeding(portal_sql="\n".join(portal_sql), openemr_sql="\n".join(openemr_sql), files=files, post_commands=[]),
        expected=expected,
        oracle=OracleSpec(effects=effects, invariants=invariants),
        budget={"max_steps": 60, "max_seconds": 600},
        difficulty=difficulty,
    )
