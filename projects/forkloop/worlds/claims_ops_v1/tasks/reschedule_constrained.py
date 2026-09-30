"""Family 1 — reschedule under constraints (OpenEMR only).

"Move <patient>'s appointment with <provider> to the next available <weekday>
<AM|PM> slot; do not change the provider." The oracle checks the event's date
and start time window, that the provider is unchanged, that exactly one event
exists for that patient+provider, and that nothing else moved.

v2 splits (``common.V2_SPLITS``, 2026-09-29; legacy splits are byte-identical): the
zero-distractor draw of ``train`` also applies (legacy held-out splits always had 1-3), with
probability ``OCCUPIED_P`` the provider already has another patient's appointment on the
target day at the time the agent would naturally pick (the old time of day if it is in the
target half, else the start of the half), the instruction asks for a time that does not
double-book the provider, and every v2 task checks ``no_overlap`` (``WRONG_SLOT``).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from forkloop.oracle import Check, OracleSpec
from forkloop.tasks import Seeding, TaskInstance, make_task_id

from ..openemr import openemr_sql as osql
from .common import (APPS_HINT, ANCHOR, LEGACY_HELDOUT_SPLITS, V2_UI_PATH_OP, WEEKDAYS, BaseData, episode_id_base, is_v2,
                     load_base, make_person, order_v2_invariants, rng_for, sql_ts)

FAMILY = "reschedule_constrained"
#: v2 only: share of tasks where the provider is already booked at the natural target time
OCCUPIED_P = 0.3
OCCUPANT_OFFSET = 60  # pc_eid = episode block + 60

#: The moved event overlaps another event of the same provider on the same day (portable on MariaDB and SQLite).
NO_OVERLAP_SQL = ("SELECT COUNT(*) FROM openemr_postcalendar_events o, openemr_postcalendar_events m WHERE m.pc_eid = ? "
                  "AND o.pc_eid != m.pc_eid AND o.pc_aid = m.pc_aid AND o.pc_eventDate = m.pc_eventDate "
                  "AND o.pc_startTime < m.pc_endTime AND o.pc_endTime > m.pc_startTime")


def _next_weekday(start: dt.date, weekday_idx: int, *, strictly_after: bool = True) -> dt.date:
    d = start + dt.timedelta(days=1 if strictly_after else 0)
    while d.weekday() != weekday_idx:
        d += dt.timedelta(days=1)
    return d


def generate(family: str, seed: int, split: str, base: BaseData | None = None) -> TaskInstance:
    base = base or load_base()
    rng = rng_for(family, seed, split)
    ids = base.next_ids()
    ids["pid"] = ids["portal_patient"] = episode_id_base(seed)  # keep both apps' ids aligned per episode
    ids["portal_patient"] = ids["pid"]
    eid = episode_id_base(seed)

    provider = rng.choice(base.providers)
    person = make_person(rng, split, ids, base, provider=provider)
    # distractors: same surname patient with the same provider, and a second appointment for our patient with another provider
    # (legacy: `split != "train" or ...`; identical for the legacy splits, train-like for the v2 ones)
    n_distractors = rng.randint(1, 3) if split in LEGACY_HELDOUT_SPLITS or rng.random() < 0.7 else 0
    distractors = [make_person(rng, split, ids, base, last=person.last if rng.random() < 0.6 else None,
                               provider=provider if rng.random() < 0.5 else None) for _ in range(n_distractors)]
    other_provider = rng.choice([p for p in base.providers if p["openemr_id"] != provider["openemr_id"]])

    # current appointment: within the next two weeks, on a weekday
    cur_date = _next_weekday(ANCHOR + dt.timedelta(days=rng.randint(0, 6)), rng.randint(0, 4), strictly_after=False)
    cur_min = rng.choice([9 * 60, 9 * 60 + 30, 10 * 60, 11 * 60, 13 * 60 + 30, 14 * 60, 15 * 60 + 30])
    cur_time = f"{cur_min // 60:02d}:{cur_min % 60:02d}:00"

    # constraint: next <weekday> <AM|PM> strictly after the current date
    wd_idx = rng.randint(0, 4)
    half = rng.choice(["morning", "afternoon"])
    target_date = _next_weekday(cur_date, wd_idx)
    window = ("08:00:00", "11:59:59") if half == "morning" else ("12:00:00", "17:59:59")
    # "next" is relative to the appointment's own date, not to today: say so, or an agent that
    # has not found the appointment yet reads it as "next <weekday> from now" (Luna, 2026-09-03).
    slot_desc = f"the next {WEEKDAYS[wd_idx]} {half} after its current date (the appointment is within the next two weeks)"

    statements: list[str] = [person.openemr_sql(rng, eid)]
    for i, d in enumerate(distractors, start=1):
        statements.append(d.openemr_sql(rng, eid + i))
    ev_id = eid
    statements.append(osql.insert_appointment(pc_eid=ev_id, pid=person.pid, provider_id=provider["openemr_id"],
                                              event_date=cur_date, start_time=cur_time, title="Office Visit"))
    # decoys: the patient's other appointment (different provider) and distractor appointments with our provider
    decoy_ids = []
    if rng.random() < 0.7:
        decoy_ids.append(ev_id + 1)
        statements.append(osql.insert_appointment(pc_eid=ev_id + 1, pid=person.pid, provider_id=other_provider["openemr_id"],
                                                  event_date=cur_date + dt.timedelta(days=rng.randint(1, 5)), start_time="10:30:00"))
    for i, d in enumerate(distractors, start=2):
        decoy_ids.append(ev_id + i)
        statements.append(osql.insert_appointment(pc_eid=ev_id + i, pid=d.pid, provider_id=d.provider["openemr_id"],
                                                  event_date=cur_date + dt.timedelta(days=rng.randint(-3, 6)), start_time=cur_time))
    # log rows for the seed
    statements.append(osql.insert_log(id=eid + 50, event="scheduling-insert", category="Scheduling", user="admin",
                                      patient_id=person.pid, comments=f"forkloop seed: event {ev_id}", date=sql_ts(ANCHOR - dt.timedelta(days=2))))

    v2 = is_v2(split)
    occupant: dict[str, Any] | None = None
    if v2 and rng.random() < OCCUPIED_P:  # v2 draws come after every legacy draw
        occ_pid = rng.choice(sorted(p["pid"] for p in base.openemr["tables"]["patient_data"]))
        occ_time = cur_time if window[0] <= cur_time <= window[1] else window[0]
        occupant = {"event_id": eid + OCCUPANT_OFFSET, "pid": occ_pid, "time": occ_time, "duration": 1800}
        statements.append(osql.insert_appointment(pc_eid=occupant["event_id"], pid=occ_pid, provider_id=provider["openemr_id"],
                                                  event_date=target_date, start_time=occ_time, duration_sec=1800))

    instruction = (f"In OpenEMR (log in as admin / pass if asked), move {person.name}'s (DOB {person.dob.isoformat()}) appointment with "
                   f"{provider['name']} to {slot_desc}, keeping the same provider and visit type. "
                   f"Do not touch any other appointment. {APPS_HINT}")
    if v2:
        instruction = (f"In OpenEMR (log in as admin / pass if asked), move {person.name}'s (DOB {person.dob.isoformat()}) appointment "
                       f"with {provider['name']} to {slot_desc}, at a time when that provider has no other appointment, keeping "
                       f"the same provider and visit type. Do not touch any other appointment. {APPS_HINT}")

    effects = [
        Check(id="event_date", kind="query", db="openemr", sql="SELECT pc_eventDate FROM openemr_postcalendar_events WHERE pc_eid = ?",
              params=[ev_id], equals=target_date.isoformat(), reason_code="WRONG_SLOT"),
        Check(id="event_time_window", kind="query", db="openemr",
              sql="SELECT COUNT(*) FROM openemr_postcalendar_events WHERE pc_eid = ? AND pc_startTime >= ? AND pc_startTime <= ?",
              params=[ev_id, window[0], window[1]], equals=1, reason_code="WRONG_SLOT"),
        Check(id="provider_unchanged", kind="query", db="openemr", sql="SELECT pc_aid FROM openemr_postcalendar_events WHERE pc_eid = ?",
              params=[ev_id], equals=str(provider["openemr_id"]), reason_code="PROVIDER_CHANGED"),
    ]
    invariants = [
        Check(id="visit_type_unchanged", kind="query", db="openemr",
              sql="SELECT pc_catid FROM openemr_postcalendar_events WHERE pc_eid = ?",
              params=[ev_id], equals=osql.CAT_OFFICE_VISIT, reason_code="WRONG_VALUE"),
        Check(id="event_end_date", kind="query", db="openemr",
              sql="SELECT pc_endDate FROM openemr_postcalendar_events WHERE pc_eid = ?",
              params=[ev_id], equals=target_date.isoformat(), reason_code="WRONG_SLOT"),
        Check(id="event_duration_consistent", kind="count", db="openemr",
              sql="SELECT COUNT(*) FROM openemr_postcalendar_events WHERE pc_eid = ? AND "
                  "(SUBSTR(pc_endTime,1,2)*3600 + SUBSTR(pc_endTime,4,2)*60 + SUBSTR(pc_endTime,7,2)) - "
                  "(SUBSTR(pc_startTime,1,2)*3600 + SUBSTR(pc_startTime,4,2)*60 + SUBSTR(pc_startTime,7,2)) = pc_duration",
              params=[ev_id], equals=1, reason_code="WRONG_SLOT"),
        Check(id="event_fields_preserved", kind="preserve_fields", db="openemr",
              sql="SELECT * FROM openemr_postcalendar_events WHERE pc_eid = ?", params=[ev_id],
              mutable_fields=["pc_eventDate", "pc_endDate", "pc_startTime", "pc_endTime"], reason_code="COLLATERAL_EDIT"),
        Check(id="single_event", kind="count", db="openemr",
              sql="SELECT COUNT(*) FROM openemr_postcalendar_events WHERE pc_pid = ? AND pc_aid = ?",
              params=[str(person.pid), str(provider["openemr_id"])], equals=1, reason_code="DUPLICATE_SIDE_EFFECT"),
        Check(id="no_collateral", kind="baseline_checksum", allow={"openemr.openemr_postcalendar_events": [ev_id]},
              reason_code="COLLATERAL_EDIT"),
        Check(id="ui_path", kind="ui_path_only", reason_code="DIRECT_DB_WRITE", op=V2_UI_PATH_OP if v2 else "eq"),
        Check(id="no_forbidden", kind="forbidden_screens", reason_code="FORBIDDEN_SCREEN"),
    ]
    if v2:
        # an untouched appointment is missing work, not a wrong slot
        effects.insert(0, Check(id="event_moved", kind="query", db="openemr",
                                sql="SELECT pc_eventDate FROM openemr_postcalendar_events WHERE pc_eid = ?", params=[ev_id],
                                op="ne", equals=cur_date.isoformat(), reason_code="NOT_DONE"))
        invariants.insert(5, Check(id="no_overlap", kind="count", db="openemr", sql=NO_OVERLAP_SQL, params=[ev_id], equals=0,
                                   reason_code="WRONG_SLOT"))
        invariants = order_v2_invariants(invariants)
    expected = {"event_id": ev_id, "target_date": target_date.isoformat(), "window": list(window),
                "provider_openemr_id": provider["openemr_id"], "patient_pid": person.pid, "decoy_event_ids": decoy_ids}
    difficulty = {"distractors": n_distractors, "has_other_provider_appt": (ev_id + 1) in decoy_ids,
                  "same_surname_distractor": any(d.last == person.last for d in distractors), "half": half}
    if v2:
        expected.update({"occupant": occupant, "current_date": cur_date.isoformat(), "current_time": cur_time})
        difficulty.update({"generator": "v2", "occupied_slot": occupant is not None,
                           "same_provider_distractor": any(d.provider["openemr_id"] == provider["openemr_id"] for d in distractors)})
    return TaskInstance(
        world="claims-ops-v1", family=family, seed=seed, split=split, task_id=make_task_id(family, split, seed),
        instruction=instruction,
        initial_screen={"app": "openemr", "url": "http://localhost/openemr/interface/login/login.php?site=default"},
        seeding=Seeding(portal_sql="", openemr_sql="\n".join(statements), files=[], post_commands=[]),
        expected=expected,
        oracle=OracleSpec(effects=effects, invariants=invariants),
        budget={"max_steps": 60, "max_seconds": 600},
        difficulty=difficulty,
    )
