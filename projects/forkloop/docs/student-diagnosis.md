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
- `note_from_reply` strips Fara's `<think>` and `<tool_call>` wrappers, so a Fara reply and a compact training target with the same reasoning produce the same note. `tests/test_observation_contract.py` checks that the training and serving prompts are identical. At serving time it also keeps a `pause_and_memorize_fact` fact as `Memorized: …`; the teacher never used that action, so training notes never contain one.
- Under exactly the serving-time format, all 25 typing steps in the training set have the number in their last 8 notes.

`data/sft_f3_25_v4notes.jsonl` is the v3 dataset (sha256 `194022b8…`) with one change: the `notes` field. The training command is v3's main command with only the data and output directory changed, capped at v3's 411 optimizer steps. So v3 → v4 is a one-variable ablation.

## Offline evaluation (frozen before inference)

The evaluation covers 38 teacher episodes from seeds 100–139. Training used seeds 20–45. For each episode, two recorded states are tested:

- **type:** the teacher's authorization-typing step. It is run twice: with the previous and current screenshots as recorded (`type_paired`), and with only the current screenshot, where the letter is not visible (`type_current_only`).
- **read:** the teacher's first mention of the number, with the letter visible. The metric is whether the exact number appears in the model's reply, which is what its notes would carry forward.

Base and v3 get no notes, as trained. v4 gets the teacher-derived notes of the previous steps; in the read states those notes never contain the answer. As a secondary condition declared before inference, base and v3 are also given the same notes at inference only (`+notes`). Decoding is greedy, best-of-one, with the v3 serving prompt and settings. The protocol and case hashes are in `runs/v4-notes-20260922/eval/` (local).

## Results

The table shows exact authorization typing, or for the read condition the number appearing in
the reply, out of 38 held-out states. "Wrong" counts typed authorizations that do not match.

| Model | Notes given | Type, paired | Type, current only | Read | Wrong typed (paired) |
| --- | --- | ---: | ---: | ---: | ---: |
| base | no | 6 | 0 | 10 | 9 |
| base | yes (inference only) | 33 | 33 | 13 | 1 |
| v3 | no | 27 | 0 | 26 | 11 |
| v3 | yes (inference only) | **37** | **37** | 25 | 1 |
| v4-notes | yes (as trained) | **37** | 0 | 27 | 1 |
| v4-notes | no | 21 | 0 | 27 | 17 |

What this shows:

1. **The memory channel fixes carrying, even without training.** Given the notes, every model types the number it cannot see: base 33/38 and v3 37/38 with the letter off screen, against 0/38 without notes.
2. **SFT is what improved reading.** v3 wrote the exact number when the letter was on screen in 26/38 states, against 10/38 for base. Notes do not change reading: v3 read 25/38 with notes and 26/38 without.
3. **Training on notes (v4) added nothing over giving v3 notes at inference.** v4-notes reads about the same (27/38), and with notes it types as well as v3 when the previous screenshot is shown. With only the current screenshot it clicks back to OpenEMR to "verify the number before submission" in all 38 states. That is the teacher's habit, and a cautious one, but it costs steps. Without notes it is out of distribution and makes 17 wrong entries.
4. **Live, none of this reached task success yet.** The matched live comparison of v3 with and without notes ([live-notes-comparison.md](live-notes-comparison.md)) scored 1/10 each. Most episodes ran out of the 900-second budget at about 10.5 s per step. On one seed both arms submitted the same misread digit, and the notes arm carried it faithfully.

**Recommendation for the next model iteration.**

- Use v3 with notes at inference; do not retrain for notes.
- Spend effort on reading (a verification or magnification step on the letter) and on speed: vLLM serving, one client per server.
- Re-run the live comparison with a longer budget before trying another model change.

v4-notes trained for exactly v3's 411 steps (1.87 epochs, seed 0; final loss 0.132 vs 0.139).
The adapter is kept locally at `checkpoints/v4-notes-main25/final`
(sha256 `1e9d5e02…6d42`) and is not distributed. Training ran 7.2 h. The whole H100 session
lasted about 8.3 h at $3.29/h, about $27, and the base and v3 evaluations shared the GPU with
training.
