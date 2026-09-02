"""Family 3 — resolve a CO-197 denial with the authorization number found in
the patient's OpenEMR document. Attachment is required only when
``difficulty.require_attachment`` (harder variant).

Randomisation: which document (and page) holds the number, distractor numbers
on the same page, a same-surname distractor with its own denied claim that must
stay untouched, off-by-one claim numbers, inbox noise.
"""

from __future__ import annotations

import datetime as dt

from forkloop.oracle import Check, OracleSpec
from forkloop.tasks import Seeding, TaskInstance, make_task_id

from ..openemr import openemr_sql as osql
from .common import (ANCHOR, BaseData, auth_number, authorization_letter, document_seed_file, episode_id_base,
                     load_base, make_claim, make_person, noise_messages, rng_for, sha256, sql_ts)

FAMILY = "resolve_denial"


def generate(family: str, seed: int, split: str, base: BaseData | None = None) -> TaskInstance:
    base = base or load_base()
    rng = rng_for(family, seed, split)
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

    real = auth_number(rng)
    decoys = [auth_number(rng) for _ in range(rng.randint(1, 4))]
    n_docs = rng.randint(1, 3)
    which = rng.randrange(n_docs)
    n_pages = rng.choice([1, 1, 2])
    page = rng.randrange(n_pages) + 1
    require_attachment = split != "train" and rng.random() < 0.3 if split == "heldout_compositions" else rng.random() < 0.15
    service_desc = f"CPT {target.cpt} on {target.service_date.isoformat()}"

    files, openemr_sql = [], [person.openemr_sql(rng, eid)]
    doc_hash = ""
    doc_name_real = ""
    for i in range(n_docs):
        name = rng.choice(["authorization_letter", "um_determination", "precert_notice", "payer_correspondence"]) + f"_{i + 1}.pdf"
        if i == which:
            pdf = authorization_letter(rng, person, real, decoys, service_desc, page_of=(page, n_pages))
            doc_hash = sha256(pdf)
            doc_name_real = name
        else:
            pdf = authorization_letter(rng, person, rng.choice(decoys), decoys, "a different service (not this claim)", page_of=(1, 1))
        files.append(document_seed_file(person, name, pdf))
        openemr_sql.append(osql.insert_document(doc_id=eid + i, pid=person.pid, name=name, size=len(pdf), content_hash=sha256(pdf),
                                                docdate=ANCHOR - dt.timedelta(days=rng.randint(20, 60))))
    for i, (d, c) in enumerate(distractors, start=1):
        openemr_sql.append(d.openemr_sql(rng, eid + 10 + i))
    portal_sql = [person.portal_sql(ANCHOR - dt.timedelta(days=100)), target.portal_sql()]
    for d, c in distractors:
        portal_sql += [d.portal_sql(ANCHOR - dt.timedelta(days=95)), c.portal_sql()]
    noise = rng.randint(0, 3)
    if noise:
        portal_sql.append(noise_messages(rng, noise, 2000 + (seed % 100000) * 10))

    instruction = (f"Claim {target.number} for {person.name} (DOB {person.dob.isoformat()}) was denied with code CO-197 "
                   f"(prior authorization missing). Find the authorization number in the patient's documents in OpenEMR (log in as admin / pass), "
                   f"then in the payer portal file an appeal on {target.number} with reason 'Prior authorization was obtained' "
                   f"and enter that authorization number.")
    if require_attachment:
        instruction += " Attach the authorization letter to the appeal."
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
        effects.append(Check(id="attachment_hash", kind="query", db="portal",
                             sql="SELECT attachment_sha256 FROM appeals WHERE claim_id = ? ORDER BY id DESC",
                             params=[target.id], equals=doc_hash, reason_code="WRONG_ATTACHMENT"))
        effects.append(Check(id="attachment_present", kind="query", db="portal",
                             sql="SELECT COUNT(*) FROM appeals WHERE claim_id = ? AND attachment_sha256 IS NOT NULL",
                             params=[target.id], equals=1, reason_code="MISSING_ATTACHMENT"))
    invariants = [
        Check(id="single_appeal", kind="count", db="portal", sql="SELECT COUNT(*) FROM appeals WHERE claim_id = ?",
              params=[target.id], equals=1, reason_code="DUPLICATE_SIDE_EFFECT"),
        Check(id="no_collateral", kind="baseline_checksum", allow={"portal.claims": [target.id]},
              exempt_tables=["portal.appeals"], reason_code="COLLATERAL_EDIT"),
        Check(id="ui_path", kind="ui_path_only", exempt_tables=["portal.appeals"], reason_code="DIRECT_DB_WRITE"),
        Check(id="no_forbidden", kind="forbidden_screens", reason_code="FORBIDDEN_SCREEN"),
    ]
    for i, (d, c) in enumerate(distractors):
        invariants.append(Check(id=f"distractor_{i}_untouched", kind="query", db="portal", sql="SELECT status FROM claims WHERE id = ?",
                                params=[c.id], equals="DENIED", reason_code="WRONG_RECORD"))
    return TaskInstance(
        world="claims-ops-v1", family=family, seed=seed, split=split, task_id=make_task_id(family, split, seed),
        instruction=instruction,
        initial_screen={"app": "portal", "url": "http://localhost:8080/claims?status=DENIED"},
        seeding=Seeding(portal_sql="\n".join(portal_sql), openemr_sql="\n".join(openemr_sql), files=files, post_commands=[]),
        expected={"patient_pid": person.pid, "claim_id": target.id, "claim_number": target.number, "auth_number": real,
                  "decoy_numbers": decoys, "doc_name": doc_name_real, "doc_page": page, "doc_hash": doc_hash,
                  "distractor_claims": [c.number for _, c in distractors]},
        oracle=OracleSpec(effects=effects, invariants=invariants),
        budget={"max_steps": 60, "max_seconds": 600},
        difficulty={"distractors": n_distractors, "n_docs": n_docs, "n_pages": n_pages, "require_attachment": require_attachment,
                    "decoys": len(decoys), "noise_messages": noise},
    )
