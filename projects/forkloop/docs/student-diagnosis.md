# Why small-model SFT stayed at 0/30, and the notes experiment

*2026-09-22.* This summarizes a code-level review of the student pipeline (Fara1.5-4B, LoRA SFT on verified teacher trajectories, family 3 `resolve_denial`) and the follow-up experiment. The dated diaries it draws on are [student-2026-09-05.md](student-2026-09-05.md), [student-2026-09-06.md](student-2026-09-06.md), [lambda-v3-handoff.md](lambda-v3-handoff.md), [live-paired-v3-results.md](live-paired-v3-results.md) and [frozen-v3-evaluation-results.md](frozen-v3-evaluation-results.md).

## The main cause: the number was not in the input when the model had to type it

The task requires reading an authorization number in an OpenEMR document, then typing it into the payer portal many steps later. In the 25 training episodes, the teacher first wrote the number in its reasoning 5 to 41 steps before typing it (median 20). The student, though, had no memory:

- its history is the last 8 compact actions, with no reasoning;
- `history_notes` was off in every rung, and training passed no notes;
- Fara's own memory action, `pause_and_memorize_fact`, was mapped to a 0.1-second wait, which discarded the fact. In the v3 probes, base Fara had put the *correct* number into that action on 6 of 20 recorded states.

The v1 and v2 rungs saw only the current screenshot. There, **every one of the 25 `type("AUTH-…")` training targets had no source in the input**, so the model could only learn to produce a plausible string. It did exactly that:

- **v1:** 23 episodes typed a number, all of them invented, and 14 were the same string.
- **v2:** 14 of 15 typed an exact training-set number (`AUTH-60S48411` ×5, `AUTH-27W69512` ×4, `AUTH-51X75495` ×4). That is memorization.

v3 added the previous screenshot. In the teacher trajectories the step before typing is usually a tab switch, so that previous screenshot often still showed the letter. That is why v3's recorded-state authorization typing rose from 2/20 to 14/20. In the one complete live v3 episode, the adapter did the whole workflow and dropped one character.

## Secondary causes (fixed in v3)

- **v1/v2 trained and served on different prompts.** Training put the image first and serving put the text first. History coordinates were rescaled to 0–1000 in training but sent in raw screen pixels when serving. v3 routes both through one builder (`forkloop/policies/observation.py`).
- **Label masking.** Fara's generation prompt ends in `<think>\n`, which merged with the target's first newline when the full text was tokenized, so the first target token was never trained correctly. Gradient accumulation also miscounted across epochs.
- **The teacher saw more than the student:** a previous screenshot and 16 history steps, against the student's current screenshot and 8 steps. v3 closed half of this gap.

The image-detail result (19/20 vs 0/20) applies to hosted GPT-5.6 Luna, not Fara. Fara was served locally at full 1280×720 resolution, and no low-detail setting was involved.

## The fix: recipe v4-notes

`StudentPolicy(history_notes=True)` already shows the policy's own earlier reasoning next to each history action. Recipe v4-notes trains on that same input:

- `make_sft --with-notes` gives each record `notes`: the reasoning line the teacher wrote on each of the previous ≤8 steps, extracted with the same function serving uses (`note_from_reply`).
- `note_from_reply` strips Fara's `<think>` and `<tool_call>` wrappers, and keeps a memorized fact as `Memorized: …`. A Fara reply and a compact training target with the same reasoning therefore produce the same note. `tests/test_observation_contract.py` checks that the training and serving prompts are identical.
- Under exactly the serving-time format, all 25 typing steps in the training set have the number in their last 8 notes.

`data/sft_f3_25_v4notes.jsonl` is the v3 dataset (sha256 `194022b8…`) with one change: the `notes` field. The training command is v3's main command with only the data and output directory changed, capped at v3's 411 optimizer steps. So v3 → v4 is a one-variable ablation.

## Offline evaluation (frozen before inference)

The evaluation covers 38 teacher episodes from seeds 100–139. Training used seeds 20–45. For each episode, two recorded states are tested:

- **type:** the teacher's authorization-typing step. It is run twice: with the previous and current screenshots as recorded (`type_paired`), and with only the current screenshot, where the letter is not visible (`type_current_only`).
- **read:** the teacher's first mention of the number, with the letter visible. The metric is whether the exact number appears in the model's reply, which is what its notes would carry forward.

Base and v3 get no notes, as trained. v4 gets the teacher-derived notes of the previous steps; in the read states those notes never contain the answer. As a secondary condition declared before inference, base and v3 are also given the same notes at inference only (`+notes`). Decoding is greedy, best-of-one, with the v3 serving prompt and settings. The protocol and case hashes are in `runs/v4-notes-20260922/eval/` (local).

RESULTS_PLACEHOLDER
