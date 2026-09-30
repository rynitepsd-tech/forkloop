# The correction loop: failures → verified corrections → dataset → student

This is the contract for `forkloop/correction/` and the commands `forkloop record`, `failures`,
`repair`, `dataset`, `evaluate`, `status`, `inspect` and `ops`. It extends the environment,
pool, recorder and verifier described in [contracts.md](contracts.md); nothing here changes the
agent channel.

## The workflow

1. **Record.** Your agent attempts tasks in the world. Forkloop records every step (screenshots,
   actions, the agent's visible reply and explicit memory) and takes **bound checkpoints** at
   declared boundaries. The verifier scores the final application state.
2. **Inspect failures.** For each scored failure Forkloop proposes restart points from recorded
   evidence (below).
3. **Repair.** From each restart point, `k` **independent branches** restore the world and the
   agent state and let a teacher continue. Each branch runs to its own end and is scored by the
   same verifier on its own machine.
4. **Export.** Steps of verified paths become action demonstrations with the exact input the
   acting policy saw; failed branches become labelled preference pairs; restart points and
   verifier details stay in a controller-only diagnostics file.
5. **Train and evaluate.** Fine-tune a student on the dataset; `forkloop evaluate` runs the
   student alone — no teacher, no search, one attempt per task — on tasks it never trained on.

## Records and identities

| Object | Id | Written |
| --- | --- | --- |
| task | `task_id` from `(family, split, seed)`; manifest sha256 stored and re-checked on every use | first use |
| policy | `pol-…` = hash(role, identity: class, model, options, system-prompt sha256) | first use |
| attempt | `att-…` = hash(experiment, cell, attempt number) | **before** the world is reset (`running`) |
| checkpoint | `ck-…` = hash(attempt, step) | before the action at that step is applied |
| repair | `rep-…` = hash(attempt, mode, teacher, config, repair number) | before any branch starts |
| branch | `br-…` = hash(repair, checkpoint, index) | before its restore starts |
| dataset | `ds-` + sha256(records.jsonl)[:12] | after all files are written; files made read-only |

A cell is one planned unit of evidence, `task/role/replicate`. A finished cell is never re-run. An
unscored cell (infrastructure error, interruption, failed restore) gets a new attempt number, up
to `1 + infra_retries` attempts; every earlier attempt stays in the store and in reports. Rows
left `running` by a runner whose heartbeat stopped become `interrupted` — never a success,
never deleted.

## What a checkpoint binds

| Part | Content |
| --- | --- |
| world | `strategy` + `world_ref`: `reset` (step 0, the seeded initial state), `snapshot` (provider VM snapshot: memory and disk of the running desktop), or `replay` (reset, then re-execute the recorded action prefix through the agent channel) |
| world digest | sha256 per checksummed table of `{primary key: row hash}` with ignored and wall-clock (`volatile_columns`) columns left out, row counts, and a 64×36 grayscale screen thumbnail with the desktop panel masked |
| policy | full `snapshot_state()` (screenshots as content-addressed blobs), declared `agent_state()` (explicit memory), policy identity, trajectory counters (step, history, budget steps, invalid count, elapsed seconds) |
| verifier status | `clean`, `damaged` or `unknown` from the task's oracle evaluated at that moment (controller-side) |

`damaged` means a safety violation already happened (collateral edit, duplicate, wrong record,
direct DB write, forbidden screen), or a record that did not exist at reset now exists with a
wrong checked value (an appeal filed with the wrong authorization). A field that existed at reset
and now holds a different value is progress, not damage, because it can still be edited in the UI.

**What each strategy actually preserves.** A Solari snapshot restores the running desktop
(browser memory, tabs, unsaved form text). A replay restores whatever re-executing the same
actions reproduces; the fidelity check verifies persisted tables and the visible screen, not
every byte of process memory. A Docker `commit` is a filesystem checkpoint only (services
restart). Reports name the strategy of every restore.

**Fidelity.** After every restore the branch's digest is compared with the checkpoint's: all
table digests must match and the screen distance must be ≤ `max_screen_distance`
(default 0.10 of thumbnail cells). A failed check marks the branch `restore_failed` (unscored,
kept) and is retried up to `restore_retries` times.

## Restart points (`forkloop failures`)

Controller-side analysis; it may read hidden task facts because it only decides *where* to
restore, and nothing it computes reaches a policy. In priority order:

1. **origin** — the first step where the agent wrote or typed a near miss (1–2 edits) of a hidden
   expected value, or a planted decoy: restart at the last clean checkpoint at or before it;
2. **damage** — restart at the last clean checkpoint before the first damaged one;
3. **stall** — restart before the first run of three identical actions;
4. **latest** — the latest clean checkpoint;
5. **start** — step 0 (a full restart; always the last resort).

`repair.max_restart_points` bounds how many are tried; with `stop_on_success` later points run
only if no branch verified.

## The information boundary

The acting policy — student or teacher — sees only the instruction, its screenshots, its own
action history and its explicit memory (`Memory:` lines it wrote itself). A teacher continuing a
student's checkpoint adopts only the checkpoint's declared agent state (the memory). Repairs use
`feedback: none`: the teacher is not told why the attempt failed, and hidden values (authorization
numbers, expected dates, member ids that are not in the instruction) are never placed in any
prompt or memory. Dataset export audits this mechanically:

- `memory_provenance_ok` — counted per acted step (so it can exceed the record count: records also
  need a valid action and a screenshot): each step's input memory equals the fold of the `Memory:`
  facts written by the policy on earlier steps of the same path (at a branch's first step the fold is
  seeded from that step itself, so the check is trivially true there);
- `hidden_in_text_input` — hidden values present in the text input (allowed only when they came
  from the policy's own earlier outputs; reported);
- `type_target_from_memory` / `type_target_needs_screen` — whether each typed hidden value was in
  memory or had to be read from the current screenshot.

## Dataset files

| File | Kind | Used for |
| --- | --- | --- |
| `records.jsonl` | action demonstrations from verified paths only (`origin`: `correction_suffix`, `restart_demo`, `initial_state_demo`) | SFT |
| `preferences.jsonl` | chosen (verified branch) vs rejected (failed branch, or the failed attempt's own step) at a restart point | preference training / analysis; never SFT |
| `diagnostics.jsonl` | restart points, reasons, branch outcomes | controller-only; never rendered into an input |
| `images/` | content-addressed screenshots | inputs |
| `manifest.json` | sha256 of every file and every source trajectory file, selection rules, splits, audit, code version | lineage |

A record's `input` holds the instruction, the action history (screen-pixel compact actions),
the memory before the step, and the previous/current screenshots **of that same path**; its
`target` holds the visible reasoning, the `Memory:` facts written and the executed action in
screen pixels. `render_target()` rescales the action into the student's coordinate frame.
Export refuses tasks from final-test splits.

## Accounting

Charges are append-only rows (`store.charges`): model tokens with USD for hosted models,
attempt/branch wall seconds, checkpoint and snapshot seconds, snapshot counts, restore seconds,
replayed steps. Restoring a checkpoint rewinds the episode, never these rows. Provider-level
spend is reserved and reconciled in the session ledger (`forkloop ledger`); billable resources
are in the resource registry with leases (`forkloop ops`).

## Limitations

- The damage classifier counts a wrong value as damage only for a record that did not exist at
  reset; a wrong write to a field that already held a value (a resubmission over the old member id)
  is classed as progress, so a restart point after it can waste branches (the repair still falls
  back to earlier points and step 0). Found by the independent review, 2026-09-29.
- Charges are written when an episode or branch ends (also on errors); a process killed hard
  (SIGKILL, SIGHUP from a closed tmux session, power loss) loses the charges of its in-flight
  episodes, though their rows stay
  `running` → `interrupted`.

- Replay restores assume the applications are deterministic for the same action sequence;
  the fidelity check detects divergence in persisted tables and on screen, not in hidden
  process state.
- The damage classifier knows the checks of the task's oracle; a harmful change outside checked
  tables is invisible to it (see [verifier.md](verifier.md)).
- Restart-point evidence is heuristic. How often each reason yields a verified branch is reported
  per experiment rather than assumed.
