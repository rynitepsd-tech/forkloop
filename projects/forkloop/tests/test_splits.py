"""Split policy (forkloop/splits.py): disjoint pools, historical seeds are dev, the structure
partition, the guards used by training/export code, and the hash-verified manifest."""

from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path

import pytest
import yaml

from forkloop import splits as S
from tests import claims_harness as H
from worlds.claims_ops_v1.tasks.common import LEGACY_SPLITS

ROOT = Path(__file__).resolve().parents[1]


def _overlap(a: S.Block, b: S.Block) -> bool:
    if a.split != b.split:
        return False
    fams_overlap = a.families is None or b.families is None or bool(set(a.families) & set(b.families))
    a_hi = a.hi if a.hi is not None else float("inf")
    b_hi = b.hi if b.hi is not None else float("inf")
    return fams_overlap and a.lo <= b_hi and b.lo <= a_hi


def test_pools_are_disjoint_per_family_and_numerically():
    blocks = [(name, b) for name in S.POOL_NAMES for b in S.POOLS[name]]
    for (n1, b1), (n2, b2) in itertools.combinations(blocks, 2):
        if n1 != n2:
            assert not _overlap(b1, b2), (n1, b1, n2, b2)
    # the learning pools also use numerically disjoint seed ranges, so a bare seed names its pool
    ranges = [(b.lo, b.hi) for name in ("train", "val") for b in S.POOLS[name]] + [(S.POOLS["final_test"][0].lo,
                                                                                    S.POOLS["final_test"][0].hi)]
    for (lo1, hi1), (lo2, hi2) in itertools.combinations(ranges, 2):
        assert hi1 < lo2 or hi2 < lo1
    # the learning pools draw from fresh generator splits (their own random streams)
    assert {b.split for n in ("train", "val") for b in S.POOLS[n]} == {"train_v2", "val_v2"}
    assert S.POOLS["final_test"][0].split == "final_test" and S.is_final_split("final_test")
    assert not any(S.is_final_split(s) for s in (*LEGACY_SPLITS, "train_v2", "val_v2"))


def _config_triples():
    for path in sorted((ROOT / "configs").glob("*.y*ml")) + sorted((ROOT / "configs").glob("*.json")) + \
            sorted((ROOT / "runs").glob("*/*.yaml")):
        cfg = yaml.safe_load(path.read_text()) if path.suffix != ".json" else json.loads(path.read_text())
        if not isinstance(cfg, dict) or "seeds" not in cfg:
            continue
        for seed in cfg["seeds"]:
            yield path.name, cfg["family"], cfg.get("split", "train"), int(seed)


def test_every_historical_seed_is_dev_or_the_sealed_block():
    seen = 0
    for name, family, split, seed in _config_triples():
        assert S.pool_of(family, split, seed) == "dev", (name, family, split, seed)
        seen += 1
    assert seen >= 60
    for key, ranges in S.HISTORICAL_TASKS.items():
        family, split = key.split("|")
        for lo, hi in ranges:
            for seed in (lo, hi):
                assert S.pool_of(family, split, seed) == "dev", (key, seed)
    # the lead's flagship/dev demonstrations of 2026-09-29 and the reset benchmark: train >= 900000, every family
    for family in (*S.LEARNING_FAMILIES, "resolve_denial_easy"):
        for seed in (900000, 900001, 900099, 900500, 999999, 5_000_000):
            assert S.pool_of(family, "train", seed) == "dev"
    for split, seed in (("train", 42), ("train", 1234), ("heldout_seeds", 100314), ("heldout_compositions", 200002)):
        assert S.pool_of("resolve_denial", split, seed) == "dev"


def test_the_sealed_heldout_block_and_the_final_split_are_final():
    for seed in (100500, 100529):
        assert S.pool_of("resolve_denial", "heldout_seeds", seed) == "final_test"
        with pytest.raises(S.FinalTestLeak, match="final_test block"):
            S.assert_not_final(("resolve_denial", "heldout_seeds", seed))
    assert S.pool_of("resolve_denial", "heldout_seeds", 100499) == "dev"
    assert S.pool_of("resolve_denial", "heldout_seeds", 100530) is None  # unassigned: not usable without a revision
    # any task of the final_test generator split is final, whatever its seed
    with pytest.raises(S.FinalTestLeak, match="final-test only"):
        S.assert_not_final(H.generate("reschedule_constrained", 7, "final_test"))


def test_guards_accept_tasks_manifest_dicts_and_triples():
    train_task = next(S.iter_pool("train", "resolve_denial"))
    S.assert_not_final(train_task)
    S.assert_not_final(json.loads(train_task.to_json()))
    S.assert_not_final((train_task.family, train_task.split, train_task.seed))
    S.assert_trainable(train_task)
    val_task = next(S.iter_pool("val", "compose_claims"))
    S.assert_not_final(val_task)
    with pytest.raises(S.FinalTestLeak, match="pool 'val'"):
        S.assert_trainable(val_task)
    dev = ("resolve_denial", "train", 200)
    assert not S.heldout_structures(dev)
    S.assert_trainable(dev, allow_dev=True)
    with pytest.raises(S.FinalTestLeak, match="pool 'dev'"):
        S.assert_trainable(dev)
    # a historical task with a held-out structure (in every data/sft_f3_* set): refused for training
    with pytest.raises(S.FinalTestLeak, match="H1"):
        S.assert_not_final(("resolve_denial", "train", 23))


def test_the_correction_cli_guard_uses_this_policy():
    from forkloop.correction.cli import _guard_final

    with pytest.raises(SystemExit):
        _guard_final("final_test", False)
    _guard_final("final_test", True)
    _guard_final("train_v2", False)


def test_train_and_val_never_carry_a_heldout_structure_and_final_has_the_quotas():
    for task in S.pool_tasks("train", limit_per_family=40):
        assert not S.heldout_structures(task), task.task_id
        assert S.pool_of(task.family, task.split, task.seed) == "train"
    m = S.load_manifest()
    val = m["pools"]["val"]["frozen"]
    assert len(val) == sum(S.VAL_SIZES.values()) and not any(e["heldout"] for e in val)
    final = m["pools"]["final_test"]["frozen"]
    for fam, (n, quotas) in S.FINAL_SIZES.items():
        entries = [e for e in final if e["family"] == fam]
        assert len(entries) == n
        for h, q in quotas.items():
            assert sum(h in {x.split("@")[0] for x in e["heldout"]} for e in entries) >= q, (fam, h)
        assert sum(not e["heldout"] for e in entries) == n - sum(quotas.values())
    assert len(m["pools"]["final_test"]["legacy_sealed"]) == 30
    # every structure is somewhere in the final list; none of the v2-only ones exists in historical tasks
    got = {h.split("@")[0] for e in final for h in e["heldout"]}
    assert got == {h.id for h in S.HELDOUT_STRUCTURES}
    historical = set(",".join(m["historical_tasks_with_heldout_structures"]).split(","))
    assert not historical & {"H3", "H4", "H5", "H6"}


def test_structure_holdouts_cover_composition_pairings_orderings_and_parts():
    seen: dict[str, int] = {}
    for seed in range(350000, 350300):
        for h in S.heldout_structures(H.generate("compose_claims", seed, "final_test")):
            seen[h] = seen.get(h, 0) + 1
    assert seen.get("H3") and seen.get("H4")
    assert any(k.endswith(("@INS", "@APL", "@RSC")) for k in seen)  # part-level combinations count too
    t = next(t for t in (H.generate("compose_claims", s, "train_v2") for s in range(10000, 10100))
             if t.difficulty["pairing"] == "APL+RSC" and t.difficulty["order"] == ["APL", "RSC"])
    assert "H4" not in S.heldout_structures(t)  # only the reverse order is held out


def test_legacy_same_surname_detection_agrees_with_the_v2_flag():
    for fam in ("update_insurance_reconcile", "resolve_denial"):
        for seed in range(10000, 10150):
            t = H.generate(fam, seed, "train_v2")
            assert S._same_surname_from_sql(t.seeding.portal_sql) == t.difficulty["same_surname_distractor"], t.task_id


def test_manifest_is_current_and_tamper_evident(tmp_path):
    assert S.check_manifest() == []
    m = S.load_manifest()
    assert m["policy_version"] == S.POLICY_VERSION and m["sha256"] == S.canonical_sha256({k: v for k, v in m.items() if k != "sha256"})
    bad = copy.deepcopy(m)
    bad["pools"]["final_test"]["frozen"][0]["seed"] += 1
    p = tmp_path / "m.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(RuntimeError, match="sha256 does not match"):
        S.load_manifest(p)
    # pool_tasks regenerates each frozen task and refuses a hash mismatch
    tasks = S.pool_tasks("final_test", ["resolve_denial"], limit_per_family=3, include_legacy_sealed=False)
    assert [t.task_id for t in tasks] == [e["task_id"] for e in m["pools"]["final_test"]["frozen"]
                                          if e["family"] == "resolve_denial"][:3]
    wrong = copy.deepcopy(m)
    next(e for e in wrong["pools"]["final_test"]["frozen"] if e["family"] == "resolve_denial")["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="regenerated sha256"):
        S.pool_tasks("final_test", ["resolve_denial"], limit_per_family=1, manifest=wrong)
    with pytest.raises(ValueError, match="limit_per_family"):
        S.pool_tasks("train")


def test_cli_reports_pool_and_reasons(capsys):
    assert S.main(["pool-of", "resolve_denial", "heldout_seeds", "100510"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pool"] == "final_test" and out["final_reasons"]
