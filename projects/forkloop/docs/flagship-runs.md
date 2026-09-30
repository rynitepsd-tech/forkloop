# Flagship runs: the loop on real software (2026-09-29)

Two live runs of the full loop outside the controlled experiment. Both are development evidence:
small, not pre-registered, and reported with every failed branch.

## Solari desktops, VM-snapshot checkpoints

`configs/loop-solari-student.yaml` — the untrained Qwen3.8-27B student (served on Lambda, reached
through an SSH tunnel) attempted six `resolve_denial` / `update_insurance_reconcile` dev tasks
(legacy split `train`, seeds 900011–900014) on Solari desktops restored from the Sept 15 golden.
Checkpoints: provider snapshots at up to five boundaries per attempt (every 8 steps, before `type`,
before `Return`), replay checkpoints after that. Teacher: gpt-5.6-luna, `agent_memory_v2`, no
feedback.

| | Result |
| --- | --- |
| Student attempts | 6/6 failed (3 NOT_DONE on denial: 900012 and 900014 stuck at the OpenEMR login; 900011 logged in and stalled on the calendar; 3 insurance tasks with nothing done, labelled `WRONG_VALUE` by the legacy oracle) |
| Restart points (evidence: the start of a stall — the login loop, or the calendar for 900011) | step 8 (×4), 2, 16 |
| Restores from mid-episode VM snapshots | 4 branches from two step-8 snapshots: 79 s each, every checksummed table equal, screen distance 0.0 |
| Solari "Snapshot not found" on fork | 12 branches (four other snapshots, 4 tries each); those repairs fell back to step-0 restarts |
| Verified repairs | 4 of 6 (2 from step-8 snapshots, 2 from step 0); both denial restarts failed because the teacher typed a decoy authorization (fixed in `agent_memory_v3`) |
| Dataset | `ds-fc98852a715d`: 425 demonstration records (189 correction suffixes, 236 restart demonstrations), 6 preference pairs, memory provenance 425/425 |
| Snapshots | 30 created (68–93 s each, ~9.6 GB each); all deleted after export (a second delete round was needed) |

Evidence bundle: [`evidence/solari-flagship/index.html`](evidence/solari-flagship/index.html).
Platform measurements: [solari-platform-notes.md](solari-platform-notes.md).

## Kanboard, a second world through the public interface

`worlds/kanboard_v1` ([second-world.md](second-world.md)) wraps Kanboard v1.2.54 (MIT) in the same
Docker desktop, with two task families and a SQL oracle, using only `world.yaml`, a `World`
subclass and pure generators. Weak student (gpt-6-luna at 640×360 screenshots), teacher
gpt-5.6-luna:

| | Result |
| --- | --- |
| Student attempts | 30 in three configurations; 6 failures in the main one |
| Repairs | 8 repairs, 5 verified; 28 branches (9 verified, 15 NOT_DONE, 4 WRONG_RECORD); three verified branches continue from mid-episode replay checkpoints (steps 4, 4, 36) |
| Restores | 29/29 with equal tables; screen distance 0.0 in 28/29 (one 39-step replay at 0.274, retried and matched) |
| Dataset | `ds-a504e6fca3fb`: 136 records (42 correction suffixes, 94 restart demonstrations), 10 preference pairs |
| Spend | OpenAI $0.63 |

Evidence bundle: [`evidence/kanboard/index.html`](evidence/kanboard/index.html).
