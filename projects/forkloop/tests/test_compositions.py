"""compose_claims: structure coverage, id hygiene, the union oracle, and end-to-end verdicts.

Verdicts use tests/claims_harness.py (production DbAccess/Baseline/Oracle/portal routes, helper
scripts in-process); one test runs a composition through Env on the fake backend as a cross-check.
"""

from __future__ import annotations

import collections
import itertools

import pytest

from forkloop.actions import Action
from forkloop.backends.fake import FakeBackend
from forkloop.env import Env
from tests import claims_harness as H
from tests.test_task_families import explicit_keys
from worlds.claims_ops_v1.tasks.common import episode_id_base
from worlds.claims_ops_v1.tasks.compose_claims import LINKS, PAIRINGS

SPLIT, LO = "train_v2", 10000


def _find(pairing: str, link: str, order_first: str | None = None, **part_knobs) -> int:
    def ok(t):
        d = t.difficulty
        if d["pairing"] != pairing or d["link"] != link or (order_first and d["order"][0] != order_first):
            return False
        return all(d["parts"][k].get(knob) == v for (k, knob), v in part_knobs.items())
    return H.find_seed("compose_claims", SPLIT, ok, LO)


# ------------------------------------------------------------------ structure


def test_every_pairing_order_and_link_occurs_and_parts_come_from_different_families():
    seen = collections.Counter()
    for seed in range(LO, LO + 400):
        t = H.generate("compose_claims", seed, SPLIT)
        d = t.difficulty
        kinds = d["pairing"].split("+")
        assert len(set(kinds)) == 2 and sorted(d["order"]) == sorted(kinds)
        assert [p["family"] for p in t.expected["parts"]] == [{"INS": "update_insurance_reconcile", "APL": "resolve_denial",
                                                                "RSC": "reschedule_constrained"}[k] for k in d["order"]]
        seen[(d["pairing"], tuple(d["order"]), d["link"])] += 1
        if "RSC" in kinds:
            seen[("provider_ref", d["provider_ref"])] += 1
    for pairing, link in itertools.product(PAIRINGS, LINKS):
        a, b = pairing.split("+")
        assert seen[(pairing, (a, b), link)] and seen[(pairing, (b, a), link)], (pairing, link)
    assert seen[("provider_ref", "via_claim")] and seen[("provider_ref", "named")]


def test_ids_of_the_two_parts_never_collide_and_stay_in_the_block():
    for seed in range(LO, LO + 200):
        t = H.generate("compose_claims", seed, SPLIT)
        eid = episode_id_base(seed)
        for table, keys in explicit_keys(t).items():
            assert len(keys) == len(set(keys)), (t.task_id, table)
            if table != "openemr.categories_to_documents":
                assert all(eid <= k < eid + 1000 for k in keys), (t.task_id, table)
        numbers = [int(n.split("-")[1]) for n in _claim_numbers(t)]
        assert len(numbers) == len(set(numbers)) and min(numbers) > 1120  # base claims are C-1001..C-1120
        pids = [p["patient_pid"] for p in t.expected["parts"]]
        assert (pids[0] == pids[1]) == (t.difficulty["link"] == "same")


def _claim_numbers(task) -> list[str]:
    import re
    return re.findall(r"INSERT INTO claims \([^)]*\) VALUES \(\d+, '(C-\d+)'", task.seeding.portal_sql)


def test_regeneration_is_byte_identical_and_the_oracle_is_one_union():
    for seed in range(LO, LO + 60):
        a, b = H.generate("compose_claims", seed, SPLIT), H.generate("compose_claims", seed, SPLIT)
        assert a.to_json() == b.to_json()
        checks = a.oracle.effects + a.oracle.invariants
        kinds = collections.Counter(c.kind for c in checks)
        assert kinds["baseline_checksum"] == kinds["ui_path_only"] == kinds["forbidden_screens"] == 1
        ui = next(c for c in checks if c.kind == "ui_path_only")
        assert ui.op == "writes_only"
        allow = next(c for c in checks if c.kind == "baseline_checksum").allow
        want: dict[str, set] = collections.defaultdict(set)
        for kind, ex, diff in H.parts(a):
            if kind in ("INS", "APL"):
                want["portal.claims"].add(ex["claim_id"])
            if kind == "INS" and not diff["partially_updated"]:
                want["openemr.insurance_data"].add(ex["patient_pid"])
            if kind == "RSC":
                want["openemr.openemr_postcalendar_events"].add(ex["event_id"])
        assert {k: set(v) for k, v in allow.items()} == dict(want), (a.task_id, allow)
        # every part's effects come in instruction order, prefixed by their part
        prefixes = [c.id.split("_")[0] for c in a.oracle.effects]
        assert prefixes == sorted(prefixes, key=[k.lower() for k in a.difficulty["order"]].index)


def test_instruction_states_both_parts_in_the_drawn_order_and_nothing_hidden():
    for seed in range(LO, LO + 100):
        t = H.generate("compose_claims", seed, SPLIT)
        text = t.instruction
        first, second = text.index("(1)"), text.index("(2)")
        for i, (kind, ex, diff) in enumerate(H.parts(t)):
            anchor = {"INS": ex.get("claim_number"), "APL": ex.get("claim_number"), "RSC": "move "}[kind]
            pos = text.index(anchor, first if i == 0 else second)
            assert (pos < second) == (i == 0), (t.task_id, kind)
            if kind == "RSC" and diff["provider_ref"] == "via_claim":
                assert f"billed claim {ex['provider_claim']}" in text
        assert "admin / pass" in text and t.initial_screen["app"] == "portal"


# ------------------------------------------------------------------ verdicts


@pytest.mark.parametrize("pairing,link", list(itertools.product(PAIRINGS, LINKS)))
async def test_correct_completion_of_both_parts_is_accepted(pairing, link):
    for order_first in pairing.split("+"):
        task = H.generate("compose_claims", _find(pairing, link, order_first), SPLIT)
        ep = await H.start(task)
        try:
            assert ep.feasibility.ok, ep.feasibility.checks
            await H.complete_all(ep)
            v = await ep.verify()
            assert v.reward == 1.0 and v.reason_code == "OK" and v.milestones == 1.0, (task.task_id, v.to_dict())
        finally:
            ep.close()


@pytest.mark.parametrize("pairing", PAIRINGS)
async def test_one_part_alone_is_not_done(pairing):
    task = H.generate("compose_claims", _find(pairing, "same"), SPLIT)
    for skipped in pairing.split("+"):
        ep = await H.start(task)
        try:
            await H.complete_all(ep, **{skipped: True})
            v = await ep.verify()
            assert v.reward == 0.0 and v.reason_code == "NOT_DONE", (skipped, v.to_dict())
            assert 0 < v.milestones < 1
            assert any(c.startswith(skipped.lower() + "_") for c in v.failed)
            assert not any(c.startswith(k.lower() + "_") for k in pairing.split("+") if k != skipped for c in v.failed)
        finally:
            ep.close()


async def test_a_composition_through_env_on_the_fake_backend(tmp_path):
    """Cross-check of the harness: the same completion through Env (reset stages, recorder-free) is accepted."""
    world = H.WORLD
    backend = FakeBackend(base_dir=tmp_path / "fake")
    env = Env(world, backend, family="compose_claims", split=SPLIT, settle_s=0)
    seed = _find("INS+APL", "household")
    try:
        _, info = await env.reset(seed)
        stage = next(s for s in info["reset"]["stages"] if s["name"] == "feasibility")
        assert stage["ok"]
        ep = H.Episode(task=env.ep.task, machine=env.ep.machine, dbs=env.ep.dbs, baseline=env.ep.baseline,
                       root=tmp_path, feasibility=None)
        await H.complete_all(ep)
        await env.step(Action.done())
        v = await env.verify()
        assert v.reward == 1.0 and v.reason_code == "OK", v.to_dict()
        assert v.details["ui_milestones"]["rungs"]["appeal_submitted"]
    finally:
        await env.close()
        backend.cleanup()
