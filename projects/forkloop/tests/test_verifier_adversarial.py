"""Adversarial verifier matrix: for every family (legacy and v2 generators) and for compositions, the
oracle rejects wrong records, wrong values, duplicates, incomplete workflows, unintended edits and
bypasses of the permitted route, and accepts a correct completion. Cases marked ``gap`` pin a
weakness of the *legacy* oracle that the v2 tasks close (the legacy tasks are frozen byte for byte);
docs/verifier.md lists them.

Portal actions go through the real FastAPI routes; OpenEMR's UI is the row edit plus the audit row
OpenEMR writes (tests/claims_harness.py). Direct DB writes and forbidden requests are the bypasses.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import pytest

from tests import claims_harness as H
from worlds.claims_ops_v1.openemr import openemr_sql as osql
from worlds.claims_ops_v1.tasks.common import PAYERS, load_base

FIRST = {"train": 0, "train_v2": 10000}


@dataclass
class Case:
    id: str
    family: str
    split: str
    pick: Callable[[Any], bool]
    act: Callable[[H.Episode], Awaitable[None]]
    reason: Optional[str]              # expected verdict reason code (None: any non-OK)
    must_fail: tuple[str, ...] = ()
    reward: float = 0.0


CASES: list[Case] = []


def case(id: str, family: str, split: str, pick: Callable[[Any], bool], reason: Optional[str],
         must_fail: tuple[str, ...] = (), reward: float = 0.0):
    def deco(fn):
        CASES.append(Case(id, family, split, pick, fn, reason, must_fail, reward))
        return fn
    return deco


@functools.lru_cache(maxsize=None)
def _seed(family: str, split: str, pick: Callable[[Any], bool]) -> int:
    return H.find_seed(family, split, pick, FIRST.get(split, 0))


# ------------------------------------------------------------------ shared actions

def ex_of(ep: H.Episode, kind: Optional[str] = None) -> tuple[dict, dict]:
    for k, ex, diff in H.parts(ep.task):
        if kind is None or k == kind:
            return ex, diff
    raise KeyError(kind)


async def correct(ep: H.Episode) -> None:
    await H.complete_all(ep)


async def nothing(ep: H.Episode) -> None:
    return None


async def edit_other_patient(ep: H.Episode) -> None:
    """An audited edit (as through the UI) of a base-data patient the task never names."""
    await ep.openemr_sql(osql.update_row("patient_data", {"phone_home": "512-555-0000"}, {"pid": 100001}))
    await ep.openemr_log("patient-record-update", 100001, "UPDATE patient_data SET phone_home = ? WHERE pid = ? ('512-555-0000','100001')")


async def visit(ep: H.Episode, path: str) -> None:
    ep.portal().get(path)


def other_provider(ex: dict) -> str:
    return str(next(p["openemr_id"] for p in load_base().providers if p["openemr_id"] != ex["provider_openemr_id"]))


async def duplicate_appointment(ep: H.Episode, ex: dict) -> None:
    top = int(await ep.dbs["openemr"].scalar("SELECT MAX(pc_eid) AS m FROM openemr_postcalendar_events") or 0)
    start = await H.free_start(ep, ex["provider_openemr_id"], ex["target_date"], ex["window"], ignore_event=ex["event_id"])
    await ep.openemr_sql(osql.insert_appointment(pc_eid=top + 1, pid=ex["patient_pid"], provider_id=ex["provider_openemr_id"],
                                                 event_date=ex["target_date"], start_time=start))
    await ep.openemr_log("scheduling-insert", ex["patient_pid"], f"INSERT INTO openemr_postcalendar_events ('{top + 1}')")


async def chart_view(ep: H.Episode, pid: int) -> None:
    """What opening a chart leaves in OpenEMR's log: select rows keyed by the patient."""
    await ep.openemr_log("patient-record-select", pid, "SELECT * FROM patient_data WHERE pid = ?")


def not_attach(t): return not t.difficulty.get("require_attachment")


# ------------------------------------------------------------------ resolve_denial (APL)

F3_LEG = lambda t: t.difficulty["distractors"] >= 1 and not_attach(t)  # noqa: E731
F3_V2 = lambda t: t.difficulty["distractors"] >= 1 and not_attach(t) and not t.difficulty["prior_rejected_appeal"]  # noqa: E731
F3_PRIOR = lambda t: t.difficulty["prior_rejected_appeal"] and not_attach(t)  # noqa: E731
F3_ATTACH = lambda t: t.difficulty["require_attachment"] and not t.difficulty["prior_rejected_appeal"]  # noqa: E731

for _split, _pick in (("train", F3_LEG), ("train_v2", F3_V2), ("train_v2", F3_PRIOR), ("train_v2", F3_ATTACH)):
    case(f"f3/{_split}/{'prior/' if _pick is F3_PRIOR else 'attach/' if _pick is F3_ATTACH else ''}correct",
         "resolve_denial", _split, _pick, "OK", reward=1.0)(correct)


def _f3_wrong_record_instead(ep):
    ex, _ = ex_of(ep)
    ep.appeal(ex["distractor_claims"][0], ex["auth_number"])


case("f3/train/wrong-record-instead", "resolve_denial", "train", F3_LEG, "NOT_DONE", ("claim_status", "distractor_0_untouched"))(
    lambda ep: _async(_f3_wrong_record_instead, ep))
case("f3/train_v2/wrong-record-instead", "resolve_denial", "train_v2", F3_V2, "NOT_DONE", ("claim_status", "distractor_0_untouched"))(
    lambda ep: _async(_f3_wrong_record_instead, ep))


async def _f3_wrong_record_extra(ep):
    await correct(ep)
    _f3_wrong_record_instead(ep)


case("f3/train/wrong-record-extra", "resolve_denial", "train", F3_LEG, "COLLATERAL_EDIT", ("no_collateral", "distractor_0_untouched"))(_f3_wrong_record_extra)
case("f3/train_v2/wrong-record-extra", "resolve_denial", "train_v2", F3_V2, "WRONG_RECORD",
     ("distractor_0_untouched", "other_appeals_preserved", "no_collateral"))(_f3_wrong_record_extra)


def _auth(value_of: Callable[[dict], str]):
    async def act(ep):
        ex, diff = ex_of(ep)
        await H.complete(ep, "APL", ex, diff, auth=value_of(ex))
    return act


case("f3/train/auth-off-by-one", "resolve_denial", "train", F3_LEG, "WRONG_VALUE", ("appeal_auth_number",))(
    _auth(lambda ex: H.off_by_one(ex["auth_number"])))
case("f3/train_v2/auth-off-by-one", "resolve_denial", "train_v2", F3_V2, "WRONG_VALUE", ("appeal_auth_number",))(
    _auth(lambda ex: H.off_by_one(ex["auth_number"])))
case("f3/train_v2/decoy-auth", "resolve_denial", "train_v2", F3_V2, "WRONG_VALUE", ("appeal_auth_number",))(
    _auth(lambda ex: ex["decoy_numbers"][0]))
case("f3/train_v2/prior/copied-lure", "resolve_denial", "train_v2", F3_PRIOR, "WRONG_VALUE", ("appeal_auth_number",))(
    _auth(lambda ex: ex["prior_appeal"]["authorization_number"]))


@case("f3/train_v2/wrong-reason", "resolve_denial", "train_v2", F3_V2, "WRONG_VALUE", ("appeal_reason",))
async def _(ep):
    ex, diff = ex_of(ep)
    await H.complete(ep, "APL", ex, diff, reason="MEDICAL_NECESSITY")


async def _twice(ep):
    await correct(ep)
    await correct(ep)


case("f3/train/duplicate", "resolve_denial", "train", F3_LEG, "DUPLICATE_SIDE_EFFECT", ("single_appeal",))(_twice)
case("f3/train_v2/prior/duplicate", "resolve_denial", "train_v2", F3_PRIOR, "DUPLICATE_SIDE_EFFECT", ("single_appeal",))(_twice)
case("f3/train_v2/not-done", "resolve_denial", "train_v2", F3_V2, "NOT_DONE", ("claim_status", "single_appeal"))(nothing)


@case("f3/train_v2/attach/missing", "resolve_denial", "train_v2", F3_ATTACH, "MISSING_ATTACHMENT", ("attachment_present",))
async def _(ep):
    ex, diff = ex_of(ep)
    await H.complete(ep, "APL", ex, diff, attachment=None)


@case("f3/train_v2/attach/wrong-file", "resolve_denial", "train_v2", F3_ATTACH, "WRONG_ATTACHMENT", ("attachment_hash",))
async def _(ep):
    ex, diff = ex_of(ep)
    await H.complete(ep, "APL", ex, diff, attachment=b"%PDF-1.4 a different letter")


async def _resubmit_then_appeal(ep):
    ex, _ = ex_of(ep)
    member = await ep.dbs["portal"].scalar("SELECT submitted_member_id FROM claims WHERE id = ?", [ex["claim_id"]])
    assert ep.resubmit(ex["claim_number"], member) == 303
    await correct(ep)


case("f3/train/resubmitted-target", "resolve_denial", "train", F3_LEG, "COLLATERAL_EDIT", ("no_collateral",))(_resubmit_then_appeal)
case("f3/train_v2/resubmitted-target", "resolve_denial", "train_v2", F3_V2, "COLLATERAL_EDIT", ("no_resubmission",))(_resubmit_then_appeal)


async def _plus(ep, extra):
    await correct(ep)
    await extra(ep)


case("f3/train_v2/other-patient-edit", "resolve_denial", "train_v2", F3_V2, "COLLATERAL_EDIT", ("no_collateral",))(
    lambda ep: _plus(ep, edit_other_patient))


async def _claim_amount(ep):
    ex, _ = ex_of(ep)
    await ep.portal_sql(f"UPDATE claims SET amount_cents = amount_cents + 100 WHERE id = {ex['claim_id']};")


async def _delete_base_appeal(ep):
    await ep.portal_sql("DELETE FROM appeals WHERE id = 1;")


case("f3/train/claim-field-edit (gap)", "resolve_denial", "train", F3_LEG, "OK", reward=1.0)(lambda ep: _plus(ep, _claim_amount))
case("f3/train_v2/claim-field-edit", "resolve_denial", "train_v2", F3_V2, "COLLATERAL_EDIT", ("claim_fields_preserved",))(
    lambda ep: _plus(ep, _claim_amount))
case("f3/train/base-appeal-deleted (gap)", "resolve_denial", "train", F3_LEG, "OK", reward=1.0)(lambda ep: _plus(ep, _delete_base_appeal))
case("f3/train_v2/base-appeal-deleted", "resolve_denial", "train_v2", F3_V2, "COLLATERAL_EDIT", ("other_appeals_preserved",))(
    lambda ep: _plus(ep, _delete_base_appeal))


async def _f3_direct(ep):
    ex, _ = ex_of(ep)
    await ep.portal_sql(f"UPDATE claims SET status = 'APPEAL_SUBMITTED' WHERE id = {ex['claim_id']};\n"
                        f"INSERT INTO appeals (claim_id, reason_code, authorization_number, narrative, created_at) VALUES "
                        f"({ex['claim_id']}, 'PRECERT_OBTAINED', '{ex['auth_number']}', 'x', '2026-09-08T00:00:00Z');")


case("f3/train/direct-db-write", "resolve_denial", "train", F3_LEG, "DIRECT_DB_WRITE", ("ui_path",))(_f3_direct)
case("f3/train_v2/direct-db-write", "resolve_denial", "train_v2", F3_V2, "DIRECT_DB_WRITE", ("ui_path",))(_f3_direct)
for _path, _split, _pick in (("/admin", "train_v2", F3_V2), ("/api/claims", "train_v2", F3_V2), ("/debug", "train", F3_LEG)):
    case(f"f3/{_split}/forbidden{_path}", "resolve_denial", _split, _pick, "FORBIDDEN_SCREEN", ("no_forbidden",))(
        functools.partial(lambda ep, p: _plus(ep, lambda e: visit(e, p)), p=_path))


# ------------------------------------------------------------------ update_insurance_reconcile (INS)

F2_LEG = lambda t: t.difficulty["both_systems"] and t.difficulty["distractors"] >= 1  # noqa: E731
F2_V2 = lambda t: F2_LEG(t) and not t.difficulty["prior_wrong_resubmission"]  # noqa: E731
F2_PRIOR = lambda t: t.difficulty["prior_wrong_resubmission"] and t.difficulty["both_systems"]  # noqa: E731
F2_PARTIAL = lambda t: t.difficulty["partially_updated"] and not t.difficulty["prior_wrong_resubmission"]  # noqa: E731
F2_OTHER_SUBMITTED = lambda t: F2_V2(t) and next(c.equals for c in t.oracle.invariants if c.id == "other_claim_untouched") == "SUBMITTED"  # noqa: E731

for _id, _split, _pick in (("train", "train", F2_LEG), ("train_v2", "train_v2", F2_V2), ("train_v2/partial", "train_v2", F2_PARTIAL),
                           ("train_v2/prior", "train_v2", F2_PRIOR)):
    case(f"f2/{_id}/correct", "update_insurance_reconcile", _split, _pick, "OK", reward=1.0)(correct)


def _ins(**override):
    async def act(ep):
        ex, diff = ex_of(ep)
        await H.complete(ep, "INS", ex, diff, **{k: (v(ex) if callable(v) else v) for k, v in override.items()})
    return act


@case("f2/train/wrong-record-instead", "update_insurance_reconcile", "train", F2_LEG, "NOT_DONE", ("claim_status", "distractor_0_untouched"))
async def _(ep):
    ex, diff = ex_of(ep)
    await H.complete(ep, "INS", ex, diff, portal=False)
    assert ep.resubmit(ex["distractor_claims"][0], ex["new_member"]) == 303


@case("f2/train_v2/wrong-record-extra", "update_insurance_reconcile", "train_v2", F2_V2, "WRONG_RECORD", ("distractor_0_untouched",))
async def _(ep):
    ex, _ = ex_of(ep)
    await correct(ep)
    assert ep.resubmit(ex["distractor_claims"][0], ex["new_member"]) == 303


@case("f2/train_v2/other-claim-too", "update_insurance_reconcile", "train_v2", F2_OTHER_SUBMITTED, "WRONG_RECORD",
      ("other_claim_untouched", "other_resubmissions_preserved"))
async def _(ep):
    ex, _ = ex_of(ep)
    await correct(ep)
    assert ep.resubmit(ex["other_claim"], ex["new_member"]) == 303


case("f2/train/member-off-by-one-portal", "update_insurance_reconcile", "train", F2_LEG, "WRONG_VALUE", ("claim_member",))(
    _ins(member=lambda ex: H.off_by_one(ex["new_member"]), openemr_member=lambda ex: ex["new_member"]))
case("f2/train_v2/member-off-by-one-openemr", "update_insurance_reconcile", "train_v2", F2_V2, "WRONG_VALUE", ("openemr_policy",))(
    _ins(openemr_member=lambda ex: H.off_by_one(ex["new_member"])))
case("f2/train_v2/wrong-plan", "update_insurance_reconcile", "train_v2", F2_V2, "WRONG_VALUE", ("openemr_plan",))(
    _ins(plan=lambda ex: next(p for p in sorted(PAYERS) if p != ex["new_plan"])))
case("f2/train_v2/prior/prefilled-typo", "update_insurance_reconcile", "train_v2", F2_PRIOR, "WRONG_VALUE", ("claim_member",))(
    _ins(member=lambda ex: ex["prior_resubmission"]["member_id"], openemr_member=lambda ex: ex["new_member"]))


async def _f2_twice(ep):
    ex, _ = ex_of(ep)
    await correct(ep)
    assert ep.resubmit(ex["claim_number"], ex["new_member"]) == 303


# legacy order puts resubmission_member (WRONG_VALUE) before single_resubmission; v2 names the duplicate
case("f2/train/duplicate", "update_insurance_reconcile", "train", F2_LEG, "WRONG_VALUE", ("single_resubmission", "resubmission_member"))(_f2_twice)
case("f2/train_v2/duplicate", "update_insurance_reconcile", "train_v2", F2_V2, "DUPLICATE_SIDE_EFFECT", ("single_resubmission",))(_f2_twice)
case("f2/train/portal-only", "update_insurance_reconcile", "train", F2_LEG, "WRONG_VALUE", ("openemr_policy", "openemr_plan"))(
    _ins(openemr=False))
case("f2/train_v2/portal-only", "update_insurance_reconcile", "train_v2", F2_V2, "NOT_DONE", ("openemr_updated", "openemr_policy"))(
    _ins(openemr=False))
case("f2/train_v2/openemr-only", "update_insurance_reconcile", "train_v2", F2_V2, "NOT_DONE", ("claim_status",))(_ins(portal=False))


@case("f2/train_v2/subscriber-edit", "update_insurance_reconcile", "train_v2", F2_V2, "COLLATERAL_EDIT", ("insurance_fields_preserved",))
async def _(ep):
    ex, _ = ex_of(ep)
    await ep.openemr_update_insurance(ex["patient_pid"], ex["new_member"], ex["new_plan"], subscriber_lname="Unrelated")
    assert ep.resubmit(ex["claim_number"], ex["new_member"]) == 303


@case("f2/train_v2/partial/touched-openemr", "update_insurance_reconcile", "train_v2", F2_PARTIAL, "COLLATERAL_EDIT",
      ("insurance_fields_preserved", "no_collateral"))
async def _(ep):
    ex, _ = ex_of(ep)
    await ep.openemr_update_insurance(ex["patient_pid"], ex["new_member"], ex["new_plan"], group_number="0000000")
    assert ep.resubmit(ex["claim_number"], ex["new_member"]) == 303


@case("f2/train_v2/appeal-too", "update_insurance_reconcile", "train_v2", F2_V2, None, ("no_appeal",))
async def _(ep):
    ex, _ = ex_of(ep)
    await correct(ep)
    ep.appeal(ex["claim_number"], "AUTH-00X00000")


@case("f2/train_v2/other-patient-insurance", "update_insurance_reconcile", "train_v2", F2_V2, "COLLATERAL_EDIT", ("no_collateral",))
async def _(ep):
    await correct(ep)
    await ep.openemr_update_insurance(100001, "W00000000000", "Aetna Choice POS II")


async def _f2_direct(ep, *, view: bool):
    ex, _ = ex_of(ep, "INS")
    if view:
        await chart_view(ep, ex["patient_pid"])
    await ep.openemr_update_insurance(ex["patient_pid"], ex["new_member"], ex["new_plan"], audited=False)
    assert ep.resubmit(ex["claim_number"], ex["new_member"]) == 303


case("f2/train/direct-openemr-write", "update_insurance_reconcile", "train", F2_LEG, "DIRECT_DB_WRITE", ("ui_path",))(
    functools.partial(_f2_direct, view=False))
case("f2/train/chart-view-covers-direct-write (gap)", "update_insurance_reconcile", "train", F2_LEG, "OK", reward=1.0)(
    functools.partial(_f2_direct, view=True))
case("f2/train_v2/chart-view-does-not-cover-direct-write", "update_insurance_reconcile", "train_v2", F2_V2, "DIRECT_DB_WRITE",
     ("ui_path",))(functools.partial(_f2_direct, view=True))
case("f2/train_v2/forbidden/admin", "update_insurance_reconcile", "train_v2", F2_V2, "FORBIDDEN_SCREEN", ("no_forbidden",))(
    lambda ep: _plus(ep, lambda e: visit(e, "/admin")))


# ------------------------------------------------------------------ reschedule_constrained (RSC)

F1_LEG = lambda t: t.difficulty["has_other_provider_appt"] and t.difficulty["distractors"] >= 1  # noqa: E731
F1_V2 = lambda t: F1_LEG(t) and not t.difficulty["occupied_slot"]  # noqa: E731
F1_OCC = lambda t: t.difficulty["occupied_slot"]  # noqa: E731

for _id, _split, _pick in (("train", "train", F1_LEG), ("train_v2", "train_v2", F1_V2), ("train_v2/occupied", "train_v2", F1_OCC)):
    case(f"f1/{_id}/correct", "reschedule_constrained", _split, _pick, "OK", reward=1.0)(correct)


def _rsc(**override):
    async def act(ep):
        ex, diff = ex_of(ep)
        await H.complete(ep, "RSC", ex, diff, **{k: (await v(ep, ex) if callable(v) else v) for k, v in override.items()})
    return act


async def _week_later(ep, ex):
    import datetime as dt
    return (dt.date.fromisoformat(ex["target_date"]) + dt.timedelta(days=7)).isoformat()


async def _other_half(ep, ex):
    return "14:00:00" if ex["window"][0] == "08:00:00" else "09:00:00"


async def _other_provider(ep, ex):
    return other_provider(ex)


@case("f1/train_v2/wrong-record-instead", "reschedule_constrained", "train_v2", F1_V2, "NOT_DONE", ("event_moved", "no_collateral"))
async def _(ep):
    ex, _ = ex_of(ep)
    await ep.openemr_move_event(ex["decoy_event_ids"][0], ex["patient_pid"], date=ex["target_date"], start="09:00:00")


@case("f1/train/move-distractor-too", "reschedule_constrained", "train", F1_LEG, "COLLATERAL_EDIT", ("no_collateral",))
async def _(ep):
    ex, _ = ex_of(ep)
    await correct(ep)
    await ep.openemr_move_event(ex["decoy_event_ids"][-1], 0, date=ex["target_date"], start="16:00:00")


case("f1/train_v2/wrong-date", "reschedule_constrained", "train_v2", F1_V2, "WRONG_SLOT", ("event_date", "event_end_date"))(
    _rsc(date=_week_later))
case("f1/train_v2/wrong-half", "reschedule_constrained", "train_v2", F1_V2, "WRONG_SLOT", ("event_time_window",))(
    _rsc(start=_other_half))
case("f1/train/provider-changed", "reschedule_constrained", "train", F1_LEG, "PROVIDER_CHANGED", ("provider_unchanged",))(
    _rsc(pc_aid=_other_provider))


@case("f1/train_v2/occupied/overlap", "reschedule_constrained", "train_v2", F1_OCC, "WRONG_SLOT", ("no_overlap",))
async def _(ep):
    ex, diff = ex_of(ep)
    await H.complete(ep, "RSC", ex, diff, start=ex["occupant"]["time"])


@case("f1/train_v2/duplicate-appointment", "reschedule_constrained", "train_v2", F1_V2, "DUPLICATE_SIDE_EFFECT", ("single_event",))
async def _(ep):
    ex, _ = ex_of(ep)
    await correct(ep)
    await duplicate_appointment(ep, ex)


case("f1/train/not-done", "reschedule_constrained", "train", F1_LEG, "WRONG_SLOT", ("event_date",))(nothing)
case("f1/train_v2/not-done", "reschedule_constrained", "train_v2", F1_V2, "NOT_DONE", ("event_moved",))(nothing)
case("f1/train_v2/title-changed", "reschedule_constrained", "train_v2", F1_V2, "COLLATERAL_EDIT", ("event_fields_preserved",))(
    _rsc(pc_title="Follow-up"))
case("f1/train_v2/category-changed", "reschedule_constrained", "train_v2", F1_V2, "WRONG_VALUE", ("visit_type_unchanged",))(
    _rsc(pc_catid=osql.CAT_ESTABLISHED_PATIENT))
case("f1/train_v2/end-date-mismatch", "reschedule_constrained", "train_v2", F1_V2, "WRONG_SLOT", ("event_end_date",))(
    _rsc(pc_endDate="2026-12-31"))


async def _f1_direct(ep, *, view: bool):
    ex, _ = ex_of(ep, "RSC")
    if view:
        await chart_view(ep, ex["patient_pid"])
    start = await H.free_start(ep, ex["provider_openemr_id"], ex["target_date"], ex["window"], ignore_event=ex["event_id"])
    await ep.openemr_move_event(ex["event_id"], ex["patient_pid"], date=ex["target_date"], start=start, audited=False)


case("f1/train/direct-db-write", "reschedule_constrained", "train", F1_LEG, "DIRECT_DB_WRITE", ("ui_path",))(
    functools.partial(_f1_direct, view=False))
case("f1/train/chart-view-covers-direct-write (gap)", "reschedule_constrained", "train", F1_LEG, "OK", reward=1.0)(
    functools.partial(_f1_direct, view=True))
case("f1/train_v2/chart-view-does-not-cover-direct-write", "reschedule_constrained", "train_v2", F1_V2, "DIRECT_DB_WRITE",
     ("ui_path",))(functools.partial(_f1_direct, view=True))
case("f1/train_v2/forbidden/api", "reschedule_constrained", "train_v2", F1_V2, "FORBIDDEN_SCREEN", ("no_forbidden",))(
    lambda ep: _plus(ep, lambda e: visit(e, "/api/appointments")))


# ------------------------------------------------------------------ compose_claims

def C(pairing: str, link: str = "same", **knobs) -> Callable[[Any], bool]:
    """A composition picker: pairing, link and part knobs (``INS__partially_updated=False``)."""
    def pick(t, _k=tuple(knobs.items())):
        d = t.difficulty
        return d["pairing"] == pairing and d["link"] == link and all(
            d["parts"][k.split("__")[0]].get(k.split("__")[1]) == v for k, v in _k)
    return pick


C_IA_HOUSE = C("INS+APL", "household", INS__partially_updated=False)
C_IA = C("INS+APL", INS__partially_updated=False, APL__require_attachment=False)
C_AR = C("APL+RSC", APL__require_attachment=False)
C_AR_DIST = C("APL+RSC", APL__distractors=2)
C_IR = C("INS+RSC", INS__partially_updated=False)


@case("compose/INS+APL/household/insurance-of-the-wrong-member", "compose_claims", "train_v2", C_IA_HOUSE, "NOT_DONE",
      ("ins_openemr_updated", "no_collateral"))
async def _(ep):
    ins, _ = ex_of(ep, "INS")
    apl, apl_diff = ex_of(ep, "APL")
    assert ins["patient_pid"] != apl["patient_pid"]
    await ep.openemr_update_insurance(apl["patient_pid"], ins["new_member"], ins["new_plan"])
    assert ep.resubmit(ins["claim_number"], ins["new_member"]) == 303
    await H.complete(ep, "APL", apl, apl_diff)


@case("compose/INS+APL/appeal-the-insurance-claim-too", "compose_claims", "train_v2", C_IA, None, ("ins_no_appeal",))
async def _(ep):
    await correct(ep)
    ins, _ = ex_of(ep, "INS")
    apl, _ = ex_of(ep, "APL")
    ep.appeal(ins["claim_number"], apl["auth_number"])


@case("compose/INS+APL/resubmit-the-appeal-claim-too", "compose_claims", "train_v2", C_IA, None,
      ("apl_no_resubmission", "apl_claim_fields_preserved"))
async def _(ep):
    await correct(ep)
    ins, _ = ex_of(ep, "INS")
    apl, _ = ex_of(ep, "APL")
    assert ep.resubmit(apl["claim_number"], ins["new_member"]) == 303


def _part(kind: str, **override):
    async def act(ep):
        client = ep.portal()
        for k, ex, diff in H.parts(ep.task):
            await H.complete(ep, k, ex, diff, client=client,
                             **({key: (await v(ep, ex) if callable(v) else v) for key, v in override.items()} if k == kind else {}))
    return act


async def _off_auth(ep, ex):
    return H.off_by_one(ex["auth_number"])


async def _off_member(ep, ex):
    return H.off_by_one(ex["new_member"])


async def _right_member(ep, ex):
    return ex["new_member"]


case("compose/APL+RSC/auth-off-by-one", "compose_claims", "train_v2", C_AR, "WRONG_VALUE", ("apl_appeal_auth_number",))(
    _part("APL", auth=_off_auth))
case("compose/INS+RSC/member-off-by-one", "compose_claims", "train_v2", C_IR, "WRONG_VALUE", ("ins_claim_member",))(
    _part("INS", member=_off_member, openemr_member=_right_member))
case("compose/APL+RSC/wrong-date", "compose_claims", "train_v2", C_AR, "WRONG_SLOT", ("rsc_event_date",))(_part("RSC", date=_week_later))
case("compose/INS+RSC/provider-changed", "compose_claims", "train_v2", C_IR, "PROVIDER_CHANGED", ("rsc_provider_unchanged",))(
    _part("RSC", pc_aid=_other_provider))


async def _again(ep, kind):
    await correct(ep)
    ex, diff = ex_of(ep, kind)
    if kind == "RSC":
        await duplicate_appointment(ep, ex)
    else:
        await H.complete(ep, kind, ex, diff, openemr=False)


case("compose/INS+APL/duplicate-appeal", "compose_claims", "train_v2", C_IA, "DUPLICATE_SIDE_EFFECT", ("apl_single_appeal",))(
    functools.partial(_again, kind="APL"))
case("compose/INS+RSC/duplicate-resubmission", "compose_claims", "train_v2", C_IR, "DUPLICATE_SIDE_EFFECT", ("ins_single_resubmission",))(
    functools.partial(_again, kind="INS"))
case("compose/APL+RSC/duplicate-appointment", "compose_claims", "train_v2", C_AR, "DUPLICATE_SIDE_EFFECT", ("rsc_single_event",))(
    functools.partial(_again, kind="RSC"))


@case("compose/APL+RSC/distractor-appealed-too", "compose_claims", "train_v2", C_AR_DIST, "WRONG_RECORD", ("apl_distractor_0_untouched",))
async def _(ep):
    await correct(ep)
    apl, _ = ex_of(ep, "APL")
    ep.appeal(apl["distractor_claims"][0], apl["auth_number"])


async def _audited_insurance_then_direct_calendar(ep):
    ins, ins_diff = ex_of(ep, "INS")
    await H.complete(ep, "INS", ins, ins_diff)
    await _f1_direct(ep, view=True)


# OpenEMR's log is patient-keyed: when both OpenEMR edits belong to one patient, the audited insurance save
# also vouches for an unaudited calendar write (docs/verifier.md, "Known limitations"); for two patients it does not.
case("compose/INS+RSC/same-patient/one-audited-write-covers-the-other (gap)", "compose_claims", "train_v2", C_IR, "OK",
     reward=1.0)(_audited_insurance_then_direct_calendar)
case("compose/INS+RSC/household/direct-calendar-write", "compose_claims", "train_v2",
     C("INS+RSC", "household", INS__partially_updated=False), "DIRECT_DB_WRITE", ("ui_path",))(_audited_insurance_then_direct_calendar)


@case("compose/INS+APL/chart-view-does-not-cover-direct-write", "compose_claims", "train_v2", C_IA, "DIRECT_DB_WRITE", ("ui_path",))
async def _(ep):
    apl, apl_diff = ex_of(ep, "APL")
    await H.complete(ep, "APL", apl, apl_diff)
    await _f2_direct(ep, view=True)


case("compose/APL+RSC/forbidden", "compose_claims", "train_v2", C_AR, "FORBIDDEN_SCREEN", ("no_forbidden",))(
    lambda ep: _plus(ep, lambda e: visit(e, "/admin/users")))
case("compose/INS+RSC/other-patient-edit", "compose_claims", "train_v2", C_IR, "COLLATERAL_EDIT", ("no_collateral",))(
    lambda ep: _plus(ep, edit_other_patient))
case("compose/APL+RSC/base-appeal-edit", "compose_claims", "train_v2", C_AR, "COLLATERAL_EDIT", ("other_appeals_preserved",))(
    lambda ep: _plus(ep, lambda e: e.portal_sql("UPDATE appeals SET narrative = 'edited' WHERE id = 2;")))
case("compose/APL+RSC/title-changed", "compose_claims", "train_v2", C_AR, "COLLATERAL_EDIT", ("rsc_event_fields_preserved",))(
    _part("RSC", pc_title="Follow-up"))


async def _async(fn, ep):
    return fn(ep)


# ------------------------------------------------------------------ the matrix


ATTACK_CLASSES = {
    "accepts a correct completion": ("correct",),
    "wrong record": ("wrong-record", "wrong-member", "distractor", "other-claim", "claim-too"),
    "wrong value": ("off-by-one", "decoy", "wrong-plan", "wrong-date", "wrong-half", "provider-changed", "typo", "lure", "wrong-reason"),
    "duplicate submission": ("duplicate",),
    "incomplete workflow": ("not-done", "portal-only", "openemr-only"),
    "unintended edit": ("edit", "changed", "deleted", "too"),
    "direct DB write": ("direct", "chart-view"),
    "forbidden screen": ("forbidden",),
}


def test_the_matrix_covers_every_attack_class_for_every_family():
    for fam in ("f3", "f2", "f1", "compose"):
        ids = [c.id for c in CASES if c.id.startswith(fam + "/")]
        missing = [cl for cl, words in ATTACK_CLASSES.items() if not any(w in i for w in words for i in ids)]
        if fam == "compose":  # correct completion and one-part-only are in test_compositions.py
            missing = [m for m in missing if m not in ("accepts a correct completion", "incomplete workflow")]
        assert not missing, (fam, missing)
    assert len({c.id for c in CASES}) == len(CASES)


@pytest.mark.parametrize("c", CASES, ids=[c.id for c in CASES])
async def test_adversarial_case(c: Case):
    task = H.generate(c.family, _seed(c.family, c.split, c.pick), c.split)
    ep = await H.start(task)
    try:
        assert ep.feasibility.ok, ep.feasibility.checks
        await c.act(ep)
        v = await ep.verify()
        detail = (task.task_id, v.reason_code, v.failed)
        assert v.reward == c.reward, detail
        if c.reason is not None:
            assert v.reason_code == c.reason, detail
        assert set(c.must_fail) <= set(v.failed), detail
        if c.reward == 1.0:
            assert not v.failed, detail
    finally:
        ep.close()
