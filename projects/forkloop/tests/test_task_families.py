"""Task families: legacy byte-identity, generator purity, SQL portability and id blocks, the v2
decision-structure variations (and that each is verifiable), and instruction hygiene.

The pinned hashes below were computed from the generators at commit 91f0f7a, before the
2026-09-29 changes (v2 splits, compositions): every task of the legacy splits `train`,
`heldout_seeds` and `heldout_compositions` must stay byte-identical, because recorded
experiments ran exactly those tasks.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from tests import claims_harness as H
from worlds.claims_ops_v1 import seed_world
from worlds.claims_ops_v1.openemr.openemr_sql import assert_portable
from worlds.claims_ops_v1.tasks import common
from worlds.claims_ops_v1.tasks.common import LEGACY_SPLITS, SPLITS, SURNAMES, V2_SPLITS, episode_id_base, load_base

PINNED = {  # sha256(task.to_json()) at 91f0f7a, computed before the 2026-09-29 changes
    "reschedule_constrained|heldout_compositions|200000": "b536bf4a21ec46530e82548623e61b6214b087a1ad7ec2140432651845a142ce",
    "reschedule_constrained|heldout_compositions|200001": "736d61516572807a24126c1e9c5cb66fffeb7a0c44a36eff45a76d60c643f543",
    "reschedule_constrained|heldout_compositions|200002": "2491de7b969ab580b8fe5c66b244ee096b6ec3d1d71ab3ad09fec24d1bb313ef",
    "reschedule_constrained|heldout_compositions|200003": "388fa9efaea00d42ce41bb7b6dbd0c2f79d717e64ac360447f52ea8dfa8105de",
    "reschedule_constrained|heldout_seeds|100000": "72743463c228801b9762ca6f9f6624548b7c878bd1d121c7c73be5e02de640a3",
    "reschedule_constrained|heldout_seeds|100003": "fa96283ce979802e83255e3fbceb5c7f5c42b27f3982704147000ae6e71c2ec8",
    "reschedule_constrained|heldout_seeds|100314": "292f3481833ad89e0fdec99bd8e6569350e6e9cf9ca789215d9386cc44ad0e92",
    "reschedule_constrained|heldout_seeds|100328": "30c0626a8d3f1a8cf280f4bb57645aa85f1042768363228e2d17a2b3a4fa4ae3",
    "reschedule_constrained|heldout_seeds|100500": "cf88113fbf33735c5018fc85fcf48d6e4f8bcd8caf0cf0470e2ef992187b3753",
    "reschedule_constrained|train|0": "c20d218e22c1bf6e61b99990b63b7b20105e5430d6294b29c87f77dbd7e14598",
    "reschedule_constrained|train|1": "cac4434159e230c31266875d399c841105fb43c422bf0d350d52cc3349adb807",
    "reschedule_constrained|train|1234": "c5758e52af7be363dc47d69dd53eac30ddd0f8c21e3b6c1b0a911240125170c5",
    "reschedule_constrained|train|200": "2b3333846909a748fa0d862a967518780fda99d4107064fd961884c6d6a6da37",
    "reschedule_constrained|train|229": "3495e86975de84d9ee019a2d35b55b4747b13e2c0e56dae96b3c6491e4f1e464",
    "reschedule_constrained|train|3": "9170fb874c9cf310e51e759b14141aac0b7074740cd355b9842f10bb1f0284aa",
    "reschedule_constrained|train|42": "945552832f335de6a49dd92b7c1af69acf45ce1125afa3c79f26771e1ed79a49",
    "reschedule_constrained|train|7": "57fce3ac6b8b7f1f6874540c65c9759876a6bcf699fbe78d6b603cc5929d8a61",
    "resolve_denial_easy|heldout_compositions|200000": "ebdd04b538821cd62369f856e2168b95e63142f5e7859517996fb46813b8716f",
    "resolve_denial_easy|heldout_compositions|200001": "d8ec1424e2d58f789ee4ba8ce34b826909dfaf6aa47b788b3067ba517a029702",
    "resolve_denial_easy|heldout_compositions|200002": "b9ae09ef7a927a7c644ea9e110b6faa34842abb8275abdb1f73d4a98028e5e88",
    "resolve_denial_easy|heldout_compositions|200003": "f5d167dc16e9967ee20008c0e61878596378ae80e2bb1efa26573eb4d8982f50",
    "resolve_denial_easy|heldout_seeds|100000": "a9718559fbaf022c34af3e5a5acfcda9918bb5b09c0e3f98bdb1a52687b14e72",
    "resolve_denial_easy|heldout_seeds|100003": "022822311fd59685f8db48b3c0263b81eaafc1e5e99eca8579be3bd794433a73",
    "resolve_denial_easy|heldout_seeds|100314": "dd8b361b065c1f90219962395b0142e6a6043caa0e5fc7f8abf049f0b50f8110",
    "resolve_denial_easy|heldout_seeds|100328": "725c2d0133077130b3f9aefe4fae7276fa400ebbf7cca29979623fa54aac2b30",
    "resolve_denial_easy|heldout_seeds|100500": "d17bf5ad9374dab9face942e9a958c52b9fb45fd28ae06b15d00c1d08a9b6678",
    "resolve_denial_easy|train|0": "379623e890eca64ac40eca4c32c6bd23b267a1f4d4631d2181344f93ac2a75e9",
    "resolve_denial_easy|train|1": "820035f06fd1e8253643e8acc8c9eece1f7fac80ee3e34c846067eee45589540",
    "resolve_denial_easy|train|1234": "7a957d91bb71e65107f78b9caeb7a0fddcdd6309e81ae95665a89d1d6b4e0136",
    "resolve_denial_easy|train|200": "5de781c3b01039ae8e511f0cf3e2b234c52761c507c7183b0220b3d53d347e24",
    "resolve_denial_easy|train|229": "924f77a1ea13e05ffe81f28ff1e14761d62f5f6d201944ee9cefbff233d72763",
    "resolve_denial_easy|train|3": "0119fa3f20f1c437cdb6a24028f0792c42d25fd7acb4e6d3d796064f71c1be6a",
    "resolve_denial_easy|train|42": "007e37b8320eb3a94ba9a76333ca5023f03b82937aff44a967d070b4b60ca9bc",
    "resolve_denial_easy|train|7": "b836c32de37ec21d86491c8fc4209963e28ebc53321d825dd0f72e9cfce464a3",
    "resolve_denial|heldout_compositions|200000": "b2a08b9117eefc8327089ed013fdd8b58cb9d6c504c59fe7bc761f1e61b1c66c",
    "resolve_denial|heldout_compositions|200001": "f67400dab663395ed7ed583cff59d4d76883f23290ce4dc2ef9d05f5d35b5267",
    "resolve_denial|heldout_compositions|200002": "5590a5fa1e2885519dd50013d923f80ff2fed430b965c569f4f262a7788ff3b2",
    "resolve_denial|heldout_compositions|200003": "5f88cd942a4532964db143f071a30b490ecf19807a2af0fe87c2cb007c272936",
    "resolve_denial|heldout_seeds|100000": "3ab1419205b20278268e9c9b5d1848e379bc2bac1c16d6a57e21cf711c1e43e4",
    "resolve_denial|heldout_seeds|100003": "304d9464c7f4771ef4c06ee70766db29d817bee90a54aa7dc5083314c5bbae59",
    "resolve_denial|heldout_seeds|100314": "cc6a10f42d0a8c6e1e73764e773d21242f5b9337fd1c120b3fe7ca1967429206",
    "resolve_denial|heldout_seeds|100328": "6883e910d76c014d13249bb908e3dfa9423929d99ba8d888188b3ea2868cc8be",
    "resolve_denial|heldout_seeds|100500": "7e0139c671d77e0885aba7b07b8dfdd07b4694ef7efe36584c256d895e11071f",
    "resolve_denial|train|0": "dcea3f0b19db7355be9c324b4f66f88fa76c913ebf8b569204509188b3f7a43e",
    "resolve_denial|train|1": "21103810209ce6a342348f9396cffe1c66edaf3895bfda228aa5985bcd864b90",
    "resolve_denial|train|1234": "19b6de456bf5c34e9d47b5789bd87beb2694d180848ec9bf7f71d3bc94f76622",
    "resolve_denial|train|200": "5dd1cb5bf8877ef8890b90ad94e2db2e78bf3cae23c39c8fbfe16ebe87a38a70",
    "resolve_denial|train|229": "d61e33cb3be9beff51d97d6b8c93aec44e26c80e30655d57d7c49c352c2d797f",
    "resolve_denial|train|3": "b8f3057b6e2b4ac7223d8d57dfedd8f8f37d526b2446e075864f3378c45e80a6",
    "resolve_denial|train|42": "d3aa7509c44b817294f42bb44352f790270c10c72039c6c144094566ac50bcd7",
    "resolve_denial|train|7": "7763c37750d2517a97aec63a8441eb6623ceee585208ae68770326730e7b44cd",
    "update_insurance_reconcile|heldout_compositions|200000": "ab8957aa3c647eaf056577d217c4bd41a15964e8fb0c01cc95e4bcd67a3bcd2f",
    "update_insurance_reconcile|heldout_compositions|200001": "3ebf43658bfce2d650b37bc2db4c5afab27396bddcf7e81eee29c531d032188c",
    "update_insurance_reconcile|heldout_compositions|200002": "511b5afa6defd97678eac35489f914d137dfecebc6d5afccab9525a80cefc986",
    "update_insurance_reconcile|heldout_compositions|200003": "a62a2bb8f4df0d1f6092a3cbe3911565cd78423a3578c8d8a7918ec738545371",
    "update_insurance_reconcile|heldout_seeds|100000": "9a8f8726e0149723ba7ee7fbc09929843f9d59925a49768323c2244bf42c1eab",
    "update_insurance_reconcile|heldout_seeds|100003": "6b04aac2dfb0485c6dbd77ef22aff3b7a631b9081ddbd720278bcdc15013cd88",
    "update_insurance_reconcile|heldout_seeds|100314": "fd606e4cce70324e22651b5058eaf068b686f6a44b7ef1bc6f709f88e35c910b",
    "update_insurance_reconcile|heldout_seeds|100328": "15ea814f242762b9973f7be1cf29da0f15cf571639d9304ea634511282465831",
    "update_insurance_reconcile|heldout_seeds|100500": "5ef6bc5394051a6b43cc6ce988840a6f4d5af6065f6e3d443b5022f39d30c267",
    "update_insurance_reconcile|train|0": "16087b2ebe9b4517edca12dbbb940369a42b7ca006174e9ba509185a7db9b757",
    "update_insurance_reconcile|train|1": "614283c2385ff3b61d85d938bdee48b8915511d88b7ef53148ea88e622618e42",
    "update_insurance_reconcile|train|1234": "12ec60ccc28d147b0028426f094f43b4276a68440b42dfed2082ff8c800bacb5",
    "update_insurance_reconcile|train|200": "f5c7764de83620a7ab35f2007ae0edc9d67f570d7ee50f6063f5a2a4a9ee015f",
    "update_insurance_reconcile|train|229": "b7efaceadc29c4ba1302255b6d21246cb3b3a529c3c517f20152ba0a5798d70e",
    "update_insurance_reconcile|train|3": "72313f14776b71f4896b3013bcedf40c3970d49a95f1cc443b47775ebe531a4a",
    "update_insurance_reconcile|train|42": "b3cfe5430a32280d52cd2bdaa6de49b5cca8c17036186291e88fc4c709127615",
    "update_insurance_reconcile|train|7": "3d3627b1f7ebb43f196a131ac7d950356255b81afff0849a2135d60842ff519b",
}
PINNED_RANGES = {  # sha256 over the '\n'-joined to_json() of 60 consecutive seeds
    "reschedule_constrained|heldout_compositions|200000-200059": "d6e8874d8cf53890a8dcd53615c903f443f647ad411d3e4ae376e1914d66ba51",
    "reschedule_constrained|heldout_seeds|100000-100059": "8fad919e1de45af9d53e174fa756198adfea985db8031415e164e2079e03adb5",
    "reschedule_constrained|train|0-59": "550c883fbd0547603fa81d05c6456d91c7b3b764fb7514bc475081b59e7317f9",
    "resolve_denial_easy|heldout_compositions|200000-200059": "d3e057da6ad1b7385893eb7b7a3961766ee36a74dcc802ab8e2a709e59bfc5b9",
    "resolve_denial_easy|heldout_seeds|100000-100059": "0703ba2a257e1a1aefcb9acddccf61ccfa9dde82bfff745f6cafae876b60c18b",
    "resolve_denial_easy|train|0-59": "864e43934e66e0ad91c04ee91207690f7dc7287198e747813c17caf39f2c6c89",
    "resolve_denial|heldout_compositions|200000-200059": "2aba82aa3838fd58e06511648894088acb13510604e0b782f6e0981205f75982",
    "resolve_denial|heldout_seeds|100000-100059": "54b529ca473b5a956855eedd6a42369b9f55a58e54c0b09f451d65a3d4bead19",
    "resolve_denial|train|0-59": "d389e78090fd02f61129239b081fdf72bcff2346c53896e41a510c04485685a2",
    "update_insurance_reconcile|heldout_compositions|200000-200059": "151d7ae65065d6931ead564c5b7f3180a6c668a4abff82ff37af3c154826a20a",
    "update_insurance_reconcile|heldout_seeds|100000-100059": "84b0deb156b3d8e6e9e583db70f001bbba3381d7bc660d0b107292adf4be5eca",
    "update_insurance_reconcile|train|0-59": "3d2aed961ff0bb2646b5cd8709a3871b5aeef2f8346025c37f9e98595504edbc",
}

BASE_FAMILIES = ("reschedule_constrained", "update_insurance_reconcile", "resolve_denial")
ALL_FAMILIES = (*BASE_FAMILIES, "resolve_denial_easy", "compose_claims")
FIRST_SEED = {"train": 0, "heldout_seeds": 100000, "heldout_compositions": 200000, "train_v2": 10000, "val_v2": 300000,
              "final_test": 350000}


def _sha(task) -> str:
    return hashlib.sha256(task.to_json().encode()).hexdigest()


# ------------------------------------------------------------------ legacy byte-identity


def test_legacy_tasks_are_byte_identical_to_the_pinned_hashes():
    changed = [k for k, h in PINNED.items()
               if _sha(seed_world.generate(k.split("|")[0], int(k.split("|")[2]), k.split("|")[1])) != h]
    assert not changed, f"legacy tasks changed: {changed}"


def test_legacy_seed_ranges_are_byte_identical():
    changed = []
    for key, expected in PINNED_RANGES.items():
        family, split, rng = key.split("|")
        lo, hi = map(int, rng.split("-"))
        h = hashlib.sha256()
        for seed in range(lo, hi + 1):
            h.update(seed_world.generate(family, seed, split).to_json().encode())
            h.update(b"\n")
        if h.hexdigest() != expected:
            changed.append(key)
    assert not changed, f"legacy seed ranges changed: {changed}"


def test_legacy_split_names_and_ranges_stay_valid():
    assert set(LEGACY_SPLITS) <= set(SPLITS) and set(V2_SPLITS) <= set(SPLITS)
    assert seed_world.SEED_RANGES == {"train": (0, 99999), "heldout_seeds": (100000, 199999),
                                      "heldout_compositions": (200000, 299999)}
    with pytest.raises(ValueError, match="unknown split"):
        common.rng_for("resolve_denial", 1, "bogus")


def test_world_registers_every_generator_family():
    assert list(H.WORLD.config.families) == list(seed_world.FAMILIES)
    assert H.WORLD.config.families[0] == "reschedule_constrained"  # Env's default family is unchanged


# ------------------------------------------------------------------ purity, portability, ids


@pytest.mark.parametrize("split", SPLITS)
def test_every_family_is_pure_on_every_split(split):
    for fam in ALL_FAMILIES:
        for seed in (FIRST_SEED[split], FIRST_SEED[split] + 7):
            a, b = H.generate(fam, seed, split), H.generate(fam, seed, split)
            assert a.to_json() == b.to_json() and a.task_id == f"{fam}-{split}-{seed:06d}"
            assert "expected" not in a.public_info and "seeding" not in a.public_info


_INSERT = re.compile(r"INSERT INTO (\w+) \(([^)]*)\) VALUES \((.*)\);$")
_PK = {"patients": "id", "claims": "id", "appeals": "id", "resubmissions": "id", "messages": "id",
       "patient_data": "id", "insurance_data": "id", "openemr_postcalendar_events": "pc_eid", "documents": "id",
       "categories_to_documents": "document_id", "log": "id"}


def _values(text: str) -> list[str]:
    out, buf, quoted, i = [], [], False, 0
    while i < len(text):
        ch = text[i]
        if quoted:
            buf.append(ch)
            if ch == "'":
                if text[i + 1:i + 2] == "'":
                    buf.append("'")
                    i += 1
                else:
                    quoted = False
        elif ch == "'":
            quoted = True
            buf.append(ch)
        elif ch == ",":
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf).strip())
    return out


def explicit_keys(task) -> dict[str, list[int]]:
    """``{db.table: [explicit primary keys]}`` of every INSERT in the task's seeding."""
    out: dict[str, list[int]] = {}
    for db, script in (("portal", task.seeding.portal_sql), ("openemr", task.seeding.openemr_sql)):
        for stmt in script.splitlines():
            m = _INSERT.match(stmt.strip())
            if not m:
                continue
            cols = [c.strip() for c in m.group(2).split(",")]
            vals = _values(m.group(3))
            assert len(cols) == len(vals), stmt[:120]
            out.setdefault(f"{db}.{m.group(1)}", []).append(int(vals[cols.index(_PK[m.group(1)])]))
    return out


@pytest.mark.parametrize("split", SPLITS)
def test_seeding_sql_is_portable_and_keys_are_unique(split):
    for fam in ALL_FAMILIES:
        for seed in range(FIRST_SEED[split], FIRST_SEED[split] + 25):
            task = H.generate(fam, seed, split)
            assert_portable(task.seeding.portal_sql)
            assert_portable(task.seeding.openemr_sql)
            for table, keys in explicit_keys(task).items():
                assert len(keys) == len(set(keys)), (task.task_id, table)


@pytest.mark.parametrize("split", V2_SPLITS)
def test_v2_and_composition_keys_stay_inside_the_episode_block(split):
    """Every explicit key of a v2 or composed task is >= 500000 and inside its seed's 1000-id block
    (legacy tasks keep their historical low portal message ids; the block rule is new)."""
    for fam in ALL_FAMILIES:
        for seed in range(FIRST_SEED[split], FIRST_SEED[split] + 25):
            task = H.generate(fam, seed, split)
            eid = episode_id_base(seed)
            for table, keys in explicit_keys(task).items():
                if table == "openemr.categories_to_documents":
                    continue  # keyed by the document id, checked through openemr.documents
                assert all(eid <= k < eid + 1000 for k in keys), (task.task_id, table, keys)
    for seed in range(0, 25):  # compositions on the legacy splits too (a new family, so no legacy bytes)
        task = H.generate("compose_claims", seed, "train")
        assert all(episode_id_base(seed) <= k < episode_id_base(seed) + 1000
                   for t, ks in explicit_keys(task).items() if t != "openemr.categories_to_documents" for k in ks)


def test_v2_splits_are_fresh_streams_with_the_intended_surname_pools():
    base_surnames = {p["lname"] for p in load_base().openemr["tables"]["patient_data"]}
    legacy = set().union(*(SURNAMES[s] for s in LEGACY_SPLITS))
    assert SURNAMES["train_v2"] is SURNAMES["train"] and SURNAMES["val_v2"] is SURNAMES["train"]
    assert not set(SURNAMES["final_test"]) & (legacy | base_surnames)
    for fam in BASE_FAMILIES:
        a = H.generate(fam, 12345, "train")
        b = H.generate(fam, 12345, "train_v2")
        c = H.generate(fam, 12345, "final_test")
        assert len({a.instruction, b.instruction, c.instruction}) == 3
    for seed in range(350000, 350040):
        task = H.generate("resolve_denial", seed, "final_test")
        last = task.instruction.split(" (DOB ")[0].split()[-1]
        assert last in SURNAMES["final_test"], last


# ------------------------------------------------------------------ v2 variations


def _rate(fam: str, key: str, split: str = "train_v2", n: int = 400) -> float:
    return sum(bool(H.generate(fam, s, split).difficulty.get(key)) for s in range(10000, 10000 + n)) / n


def test_v2_variations_occur_at_their_design_rates_and_never_on_legacy_splits():
    assert 0.17 < _rate("resolve_denial", "prior_rejected_appeal") < 0.33
    assert 0.17 < _rate("update_insurance_reconcile", "prior_wrong_resubmission") < 0.33
    assert 0.2 < _rate("reschedule_constrained", "occupied_slot") < 0.4
    for fam in BASE_FAMILIES:
        for seed in range(0, 60):
            d = H.generate(fam, seed, "train").difficulty
            assert "generator" not in d and "prior_rejected_appeal" not in d and "occupied_slot" not in d
    # train-like reschedule on v2 splits: some tasks without distractors (legacy held-out splits always had 1-3)
    assert any(H.generate("reschedule_constrained", s, "final_test").difficulty["distractors"] == 0 for s in range(350000, 350060))


def test_instructions_never_carry_hidden_values():
    for split in ("train", "train_v2", "final_test"):
        for fam in ALL_FAMILIES:
            for seed in range(FIRST_SEED[split], FIRST_SEED[split] + 30):
                task = H.generate(fam, seed, split)
                hidden: list[str] = []
                for kind, ex, _ in H.parts(task):
                    if kind == "APL":
                        hidden += [ex["auth_number"], *ex.get("decoy_numbers", []), ex.get("doc_name", "")]
                        if (ex.get("prior_appeal") or {}).get("authorization_number"):
                            hidden.append(ex["prior_appeal"]["authorization_number"])
                    if kind == "RSC":
                        hidden += [ex["target_date"], ex["window"][0][:5]]
                    if kind == "INS" and (ex.get("prior_resubmission") or {}).get("member_id"):
                        hidden.append(ex["prior_resubmission"]["member_id"])
                leaked = [h for h in hidden if h and h in task.instruction]
                assert not leaked, (task.task_id, leaked)


async def test_prior_rejected_appeal_is_a_lure_the_verifier_rejects():
    seed = H.find_seed("resolve_denial", "train_v2", lambda t: t.difficulty["prior_rejected_appeal"]
                       and not t.difficulty["require_attachment"], 10000)
    task = H.generate("resolve_denial", seed, "train_v2")
    assert "An earlier appeal" in task.instruction
    ex = task.expected
    ep = await H.start(task)
    try:
        page = ep.portal().get(f"/claims/{ex['claim_number']}").text
        assert ex["prior_appeal"]["authorization_number"] in page  # the lure is on the claim page
        ep.appeal(ex["claim_number"], ex["prior_appeal"]["authorization_number"])
        v = await ep.verify()
        assert v.reason_code == "WRONG_VALUE" and "appeal_auth_number" in v.failed, v.to_dict()
    finally:
        ep.close()
    ep = await H.start(task)
    try:
        await H.complete_all(ep)
        assert (await ep.verify()).reward == 1.0
        await ep.portal_sql(f"UPDATE appeals SET authorization_number = 'EDITED' WHERE id = {ex['prior_appeal']['id']};")
        v = await ep.verify()
        assert v.reward == 0 and "prior_appeal_preserved" in v.failed, v.to_dict()
    finally:
        ep.close()


async def test_prior_wrong_resubmission_prefills_a_near_miss_the_verifier_rejects():
    seed = H.find_seed("update_insurance_reconcile", "train_v2", lambda t: t.difficulty["prior_wrong_resubmission"], 10000)
    task = H.generate("update_insurance_reconcile", seed, "train_v2")
    ex = task.expected
    typo = ex["prior_resubmission"]["member_id"]
    assert typo != ex["new_member"] and len(typo) == len(ex["new_member"])
    ep = await H.start(task)
    try:
        form = ep.portal().get(f"/claims/{ex['claim_number']}/resubmit").text
        assert f'value="{typo}"' in form  # the form is prefilled with the mistyped member id
        await H.complete(ep, "INS", ex, task.difficulty, member=typo, openemr_member=ex["new_member"])
        v = await ep.verify()
        assert v.reason_code == "WRONG_VALUE" and "claim_member" in v.failed, v.to_dict()
    finally:
        ep.close()


async def test_occupied_slot_forces_a_free_time():
    seed = H.find_seed("reschedule_constrained", "train_v2", lambda t: t.difficulty["occupied_slot"], 10000)
    task = H.generate("reschedule_constrained", seed, "train_v2")
    ex = task.expected
    occ = ex["occupant"]
    ep = await H.start(task)
    try:
        await H.complete(ep, "RSC", ex, task.difficulty, start=occ["time"])
        v = await ep.verify()
        assert v.reason_code == "WRONG_SLOT" and v.failed == ["no_overlap"], v.to_dict()
    finally:
        ep.close()
    ep = await H.start(task)
    try:
        await H.complete_all(ep)
        assert (await ep.verify()).reward == 1.0
    finally:
        ep.close()
