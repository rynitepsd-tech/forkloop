# Tasks and splits (claims-ops-v1, split policy v1, 2026-09-29)

This page is the reference for the task distribution of the learning program: the families and
their decision-structure knobs, the composition family, the generator splits, and the split policy
(`forkloop/splits.py`, manifest `worlds/claims_ops_v1/tasks/splits_manifest.json`). What the
verifier checks is in `docs/verifier.md`.

## Generator splits

`generate(family, seed, split)` seeds its rng from `"claims-ops-v1:<family>:<split>:<seed>"`, so the
split string is part of the random stream: the same seed under two split names gives two unrelated
tasks.

| Generator split | Status | Surnames | Behaviour |
| --- | --- | --- | --- |
| `train`, `heldout_seeds`, `heldout_compositions` | legacy, **frozen** | one pool each | exactly what recorded experiments ran; `tests/test_task_families.py` pins 68 task hashes and 12 60-seed ranges computed at 91f0f7a before these changes |
| `train_v2` | new | training pool | v2 variations and verifier hardening; the training distribution |
| `val_v2` | new | training pool | same distribution as `train_v2`, its own stream |
| `final_test` | new, final only | its own pool (disjoint from every other pool and from the base-data patients) | v2; any task of this split is final |

Legacy quirks kept for byte-identity: reschedule tasks on the legacy held-out splits always have 1–3
distractor patients (v2 splits use the `train` rule: 30 % have none); the legacy `heldout_compositions`
split turns `resolve_denial`/`update_insurance_reconcile` into the one fixed composition of
`seed_world._composition`; legacy inbox-noise messages use portal ids below the 500000 episode floor
(v2 tasks keep every explicit key inside the seed's 1000-id block, `episode_id_base`).

## Families and their decision-structure knobs

Recorded in each task's `difficulty` (v2 tasks also carry `"generator": "v2"`).

| Family | Knobs (legacy) | Added on v2 splits |
| --- | --- | --- |
| `reschedule_constrained` | distractor patients 0–3 (same surname 60 %, same provider 50 %), the patient's other appointment with another provider (70 %), weekday, morning/afternoon, current date and time | **occupied slot** (30 %): the provider already has another patient's 30-minute appointment on the target day at the natural time (the old time of day if it is in the target half, else the start of the half); the instruction asks for a time that does not double-book the provider; `no_overlap` is checked on every v2 task |
| `update_insurance_reconcile` | OpenEMR already updated (30 %, portal-only), distractor patients 0–2 with near-miss member IDs (same surname 50 %), the patient's other claim, inbox noise | **prior wrong resubmission** (25 %): the claim was resubmitted once with a mistyped new member ID and denied again, so the resubmit form is prefilled with a near miss; `same_surname_distractor` is recorded |
| `resolve_denial` | document count 1–3 and which holds the number, page 1–2, decoy numbers 1–4, distractor patients 0–2 with denied claims (same surname 70 %), attachment required (15 %), inbox noise | **prior rejected appeal** (25 %): the claim already carries an appeal citing a decoy number, rejected, visible on the claim page; the agent files one new appeal |
| `resolve_denial_easy` | diagnostic variant (page 1, no distractors), dev only | — |
| `compose_claims` | new family, see below | (always v2) |

Each v2 variation is verified in `tests/test_task_families.py`: the lure is on screen (the prior
appeal's number on the claim page; the typo prefilled in the form) and copying it is `WRONG_VALUE`;
the occupied time is `WRONG_SLOT` (`no_overlap`); a correct completion is `OK`.

The portal needed no schema change: prior appeals and resubmissions are ordinary seeded rows
(ids at block + 900 / + 950) and the claim's `denial_reason`/`submitted_member_id` are updated in
the same seeding script.

## Compositions (`compose_claims`)

Two subtasks from different families in one episode (`worlds/claims_ops_v1/tasks/compose_claims.py`):

- `pairing`: `INS+APL` (update insurance and resubmit + appeal a CO-197 claim), `APL+RSC`
  (appeal + reschedule), `INS+RSC` (update insurance and resubmit + reschedule), uniform;
- `order`: which part the instruction states first, uniform;
- `link`: `same` patient (60 %) or `household` — a second patient with the same surname and provider
  and a different first name (40 %);
- `provider_ref` for the reschedule part: the provider's name, or **"the provider who billed claim
  C-…"** (50 %) — the agent must read the claim in the portal before it can act in OpenEMR;
- each part keeps its base family's knobs (distractors, document location, partial update, attachment,
  occupied slot, a second denied claim to leave alone), recorded under `difficulty.parts`.

Ids never collide: each seed owns one block of 1000 ids (≥ 500000) split per table — patients and
claims from +0, documents +100, OpenEMR seed log rows +200, appointments +300, portal messages +400 —
and claim numbers are `C-<20000 + seed % 20000 + k>` (base claims are C-1001..C-1120). Seeding SQL
passes `openemr_sql.assert_portable`; regeneration is byte-identical (tests).

The instruction carries only what the base families state (patient names and DOBs, claim numbers,
the new plan and member ID, provider names); the generator refuses to emit an instruction that
contains the authorization number, a decoy, a document name or the target date. The episode starts
on the portal claims list (the reset re-establishes the portal session there) and the instruction
gives the OpenEMR login. Budget: 120 actions / 1200 s.

Example (`compose_claims-train_v2-010004`, in the `train` pool):

> You have two tasks. (1) Claim C-30007 for Noah Gutierrez (DOB 1990-07-19) was denied with code
> CO-197 (prior authorization missing): find the authorization number in the patient's documents in
> OpenEMR, then in the payer portal file an appeal on C-30007 with reason 'Prior authorization was
> obtained' and that authorization number. (2) Victor Gutierrez's (DOB 1989-04-08) insurance changed
> to UnitedHealthcare Choice Plus, member ID 921666695845: update the primary insurance in OpenEMR
> (plan name and policy number), then in the payer portal resubmit claim C-30004 with the corrected
> member ID. Log in to OpenEMR as admin / pass. Leave every other claim, patient record and
> appointment as it is, and submit each item exactly once. Apps: …

Its checks (pairing `INS+APL`, order APL→INS, link `household`):
effects `apl_claim_status`, `apl_appeal_reason`, `apl_appeal_auth_number`, `ins_claim_status`,
`ins_claim_member`, `ins_openemr_updated`, `ins_openemr_policy`, `ins_openemr_plan`; invariants
`apl_single_appeal`, `ins_single_resubmission`, `ins_resubmission_member`, `ins_other_claim_untouched`,
`ins_distractor_0_untouched`, `apl_claim_fields_preserved`, `apl_no_resubmission`,
`ins_insurance_fields_preserved`, `ins_claim_fields_preserved`, `ins_no_appeal`,
`other_appeals_preserved` (all appeals except on C-30007), `other_resubmissions_preserved` (all
except on C-30004), `no_collateral` (allow: both claims and Victor's insurance row; appeals and
resubmissions exempt), `ui_path` (writes only), `no_forbidden`.

## Split policy

Pools (`forkloop.splits.POOLS`); seed ranges are inclusive. A triple outside every block is
*unassigned*: do not use it without a policy revision.

| Pool | Generator split | Seeds | Families | Notes |
| --- | --- | --- | --- | --- |
| `dev` | `train` | 0–9999 | all | every seed used before 2026-09-29 (below) |
| `dev` | `train` | ≥ 900000 | all | reset benchmark (900000+) and the flagship/dev demonstrations of 2026-09-29 (`resolve_denial` 900001–900099) |
| `dev` | `heldout_seeds` | 100000–100499 | all | live comparisons and smoke checks |
| `dev` | `heldout_compositions` | 200000–200999 | all | test fixtures |
| `train` | `train_v2` | 10000–99999 | learning families | held-out structures skipped |
| `val` | `val_v2` | 300000–304999 | learning families | held-out structures skipped; frozen list of 100 in the manifest |
| `final_test` | `final_test` | 350000–359999 | learning families | frozen list of 150 with structure quotas |
| `final_test` | `heldout_seeds` | 100500–100529 | `resolve_denial` | sealed since 2026-09-06 (`docs/contracts.md` §13) |

Learning families: `reschedule_constrained`, `update_insurance_reconcile`, `resolve_denial`,
`compose_claims` (`resolve_denial_easy` stays a dev diagnostic).

**Historical seeds (now dev).** A scan of `runs/`, `data/`, `docs/`, `configs/`, `checkpoints/`,
`bench/`, `tests/` and `train/` on 2026-09-29 found these task ids: `resolve_denial` `train` 0–141,
200–229, 1234 and `heldout_seeds` 100000, 100300–100351, 100400; `resolve_denial_easy` `train`
200–229; `reschedule_constrained` and `update_insurance_reconcile` `train` 0–34. Config seed lists
(`configs/*.yaml|json`, `runs/*/*.yaml`) are the same set. Tests and doc examples use `train` seeds
below 10000 and `heldout_seeds` 100000–100003, 100314; `heldout_compositions` 200000–200003. The
lead's flagship demonstration uses `train` 900001–900099. All are `dev`
(`tests/test_splits.py::test_every_historical_seed_is_dev_or_the_sealed_block`).

### Structure partition

The final test is not only fresh random ids: these structures may appear **only** in `final_test`
(`forkloop.splits.HELDOUT_STRUCTURES`). `train`/`val` skip any task that carries one, and
`assert_not_final` refuses it whatever its split. Composition parts are checked under their base
family (e.g. `H1@APL`).

| Id | Family | Structure | Share of train_v2 seeds skipped |
| --- | --- | --- | --- |
| H1 | resolve_denial | attachment required **and** two distractor patients | resolve_denial 7.4 % (H1 + H6) |
| H2 | update_insurance_reconcile | OpenEMR already updated **and** a same-surname distractor | 13 % |
| H3 | compose_claims | the pairing insurance + reschedule (`INS+RSC`) | compose 59 % (H3 + H4 + part-level) |
| H4 | compose_claims | the ordering reschedule-before-appeal (`APL+RSC`, RSC first) | (above) |
| H5 | reschedule_constrained | occupied slot **and** the patient's other-provider appointment **and** a same-surname distractor (v2 only) | 11.4 % |
| H6 | resolve_denial | a previously rejected appeal **and** attachment required (v2 only) | (in H1 row) |

Final-test quotas (frozen list, seeds from 350000 up): reschedule 30 (≥ 10 H5), insurance 30
(≥ 10 H2), denial 30 (≥ 8 H1, ≥ 8 H6), compositions 60 (≥ 20 H3, ≥ 12 H4); the rest of each family
carries no held-out structure. Plus the 30 sealed legacy tasks.

**Dev tasks that already carry a held-out structure.** H3–H6 do not exist in any historical task
(compositions and the v2 variations are new). H1 and H2 do: H1 in `resolve_denial` `train` 5, 10, 23,
74, 78, 112, 203, 210, 217 and `heldout_seeds` 100306–100308, 100311, 100318; H2 in
`update_insurance_reconcile` `train` 9, 15, 17, 23, 29 (manifest
`historical_tasks_with_heldout_structures`). `resolve_denial-train-000023` is in every
`data/sft_f3_*` dataset and `sft_f3_all` also has 74, 78 and 112. `checkpoints/f3-ckpt-025` and
`f3-ckpt-025-v2` were trained on `sft_f3_25.jsonl` / `sft_f3_25_v2.jsonl` (their `run.json`), so they
have seen H1 and their H1 final-test results are not structure-held-out; `v4-notes-main25` has no
`run.json` (by its name, `sft_f3_25_v4notes.jsonl`, which also contains seed 23 — not verified). Models
trained only on the `train` pool have seen none of H1–H6.

### Using it

```python
from forkloop import splits

tasks = splits.pool_tasks("val")                                  # frozen, hash-verified
final = splits.pool_tasks("final_test", include_legacy_sealed=True)
train = splits.pool_tasks("train", limit_per_family=500)         # generated in seed order, structures skipped
splits.assert_not_final(task_or_manifest_dict_or_triple)          # raises FinalTestLeak
splits.assert_trainable(task)                                     # must be in the train pool (allow_dev=True for dev)
splits.pool_of("resolve_denial", "heldout_seeds", 100510)         # "final_test"
```

Training, SFT/dataset export and model selection must call `assert_not_final` on every task they
consume (a manifest dict from a run directory works); building a training set should use
`assert_trainable`. `forkloop.correction.cli` already refuses the `final_test` split through
`splits.is_final_split`.

`python -m forkloop.splits check` verifies the manifest (its own sha256 over the canonical JSON, and
that every frozen task regenerates to its recorded sha256); `write` regenerates it;
`pool-of FAMILY SPLIT SEED` prints the pool and the reasons a task is final.

### Changing the policy

Never move a `final_test` seed or structure into `train`/`val`. Any generator change that alters a
v2 task changes the manifest hashes: bump `POLICY_VERSION`, rewrite the manifest, and record why here.
Legacy splits must stay byte-identical (the pinned-hash tests fail otherwise).
