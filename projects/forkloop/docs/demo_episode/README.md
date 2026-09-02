# Demo episode — scripted GUI run on a real Solari desktop (2026-09-02)

`scripts/gui_episode.py --seed 1234` on a fork of the golden desktop snapshot.
The "policy" is a deterministic click/type script (a test fixture, not a
model) so this proves the plumbing, not intelligence: reset with screen
stages, 16 recorded steps through the agent channel, and the oracle's verdict.

| | |
| --- | --- |
| reset | 20.9 s total: restore 16.9 · seed 0.6 · health 0.6 · baseline 0.7 · initial screen 1.2 · stable screen 0.8 |
| episode wall-clock | 46 s |
| verdict | reward 1.0, reason `OK`, milestones 1.00 (`verdict.json`) |
| negative control | same script with a decoy number → reward 0.0, `WRONG_VALUE`, milestones 0.67 (`verdict_wrong_number.json`) |

`shots/` holds six of the 32 before/after frames (initial screen, appeal form,
reason chosen, number typed, narrative typed, "Appeal submitted"). The full run
directory layout is in `docs/contracts.md` §10.
