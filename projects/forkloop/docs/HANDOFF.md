# Handoff — Forkloop, as of 2026-09-06 (after the fair-conventions student session)

Read this first, then `CLAUDE.md`. The deep references are `docs/contracts.md`
(every interface), `system.md` (every module), `docs/spikes.md` (every real
measurement), `docs/limitations.md` (everything unproven or broken), and
**`docs/overnight-2026-09-04.md`** (the ledger of the 2026-09-04 overnight session:
every run, every dollar, every failure class, the Solari changelog mapping).

**One line:** a snapshot-native training loop for vision-only GUI agents on
Solari desktops — build a payer-portal + OpenEMR world once, snapshot it, and
every episode reset is one API call. The library, the world, the oracle and the
training scripts are built and tested (210 offline tests); the world runs for real
on Solari; GPT-5.6 Luna is the volume teacher on all three task families
(fresh-seed rates: family 1 60 %, family 2 100 %, family 3 96 %); revert-mode
pools, best-of-N fork search and checkpoint deletes all run for real; **the base
student has run twice** — `microsoft/Fara1.5-4B` served on this Mac with mlx-vlm
scores **0/30 on family 3** (seeds 200–229) as shipped, with the `visit_url` macro,
and on the easier variant, every episode dying at the OpenEMR login
(`docs/student-2026-09-05.md`); with two fair, convention-level changes (a note
that spells out username/password, a Fara prompt without the ask-the-user rule)
it **logs in 30/30 and still scores 0/30**, dying between the login and the
patient chart; the 9B with the same flags also scores 0/30 and reaches the chart
in 4/30 (`docs/student-2026-09-06.md`). The 4B fair run is the like-for-like floor
for the SFT ladder, and SFT on a rented GPU is the next rung.

Main is pushed and in sync with origin as of 2026-09-06 (the session's commits end with the wrap-up);
push only when asked. `runs/` is git-ignored; the trajectories live only on this
Mac. Repo: `rynitepsd-tech/forkloop` (fork of `solari-sdk/solari-cookbook`),
cloned at `~/Desktop/Solari/repo`, project under `projects/forkloop/`.

---

## First five minutes

**Recreate the `venv` before anything else** (it does not survive reliably
between sessions):

```bash
cd ~/Desktop/Solari/repo/projects/forkloop
rm -rf venv && python3.11 -m venv venv && ./venv/bin/pip install -q -e ".[dev,world,teacher]"
```

Credentials and snapshot ids live outside the repo in `~/.config/forkloop/env`
(mode 600, never commit it). It exports `SOLARI_API_KEY`, `SOLARI_PLAN=starter`,
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` + `ANTHROPIC_WORKSPACE_ID`,
`FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1` (desktop golden) and
`FORKLOOP_GOLDEN_SANDBOX_CLAIMS_OPS_V1` (headless golden).

```bash
source ~/.config/forkloop/env
export PYTHONPATH=. FORKLOOP_CONCURRENCY=2
./venv/bin/pytest -q                              # 210 offline tests, ~1 min (tests/conftest.py scrubs the golden ids)
./venv/bin/python -m forkloop.cli reap --dry-run  # must list 0 machines; drop --dry-run to kill leftovers
```

The Solari account is **Starter** with a hard $30 cap: two concurrent machines,
desktops allowed. Four snapshots exist (the v5 golden desktop `snap_dl4e90g095y2`,
its two ancestors, and the sandbox golden), about 34 GB; snapshot storage is
billed from 2026-10-01 (10 GB free, $0.05/GB/month). Every `cp-*` checkpoint was
deleted on 2026-09-04.

**Serving the student on this Mac** (M5 Max, 64 GB; measured 2026-09-04, 1.5 s per
1280×720 call, bf16, 9 GB resident). mlx-vlm lives in its own venv:

```bash
python3.11 -m venv venv-mlx && ./venv-mlx/bin/pip install -q mlx-vlm jinja2   # jinja2: the chat template needs it
nohup caffeinate -i ./venv-mlx/bin/python -m mlx_vlm.server --model microsoft/Fara1.5-4B \
  --host 127.0.0.1 --port 8001 --max-num-seqs 2 --max-tokens 512 --log-level INFO \
  > runs/logs/mlx-server-fara15-4b.log 2>&1 &
PYTHONPATH=. ./venv/bin/python scripts/student_click_check.py --student-url http://127.0.0.1:8001/v1 \
  --model microsoft/Fara1.5-4B --prompt-style fara --seed 200      # one fork, one click; look at the crosshair PNG
```

The student collect (family 3, 30 fresh seeds, ≈ 2 h and $0.40 of VM at 120 steps):

```bash
./venv/bin/python -u -m forkloop.cli collect --world claims-ops-v1 --policy student \
  --student-url http://127.0.0.1:8001/v1 --model microsoft/Fara1.5-4B \
  --prompt-style fara --history-k 8 --nav-macro --max-steps 120 --max-seconds 900 \
  --retry-failed 0 --pool-mode revert --concurrency 2 --split train \
  --families resolve_denial --seeds 200-229 --run-id <run-id>
```

`--nav-macro` is required with base Fara: it emits `visit_url` whatever the tool enum says, and without the macro
the 10-invalid limit ends every episode. `scripts/classify_failures.py runs/<id>` gives the failure-class table.

The standard Luna collect (change only prompt file, family, seeds, run id):

```bash
./venv/bin/python -u -m forkloop.cli collect --world claims-ops-v1 --policy student \
  --student-url https://api.openai.com/v1 --model gpt-5.6-luna --effort high \
  --history-k 16 --history-notes --prev-shot --max-steps 150 --max-seconds 1200 \
  --retry-failed 1 --pool-mode revert --concurrency 2 \
  --system-prompt-file forkloop/policies/prompts/hosted_gui_agent_v10.md \
  --families reschedule_constrained --seeds 35-59 --run-id luna-v10-fam1-s35-59
```

Family 3 uses `hosted_gui_agent_v5.md`, `--max-steps 120 --max-seconds 900`, no
`--history-notes` (the measured configuration). Run one `collect` at a time, under
`nohup` into `runs/logs/<run-id>.log`; `forkloop metrics --run runs/<id> --model
gpt-5.6-luna` prices it; `scripts/inspect_episode.py --failed-only runs/<id>` reads
the failures. Keep the Mac plugged in and awake: an ~80 min sleep on 2026-09-04
stalled a run and dropped a revert.

---

## What is true today

**Built and green offline.** Core library (backends, env, pool, reset, oracle,
recorder, exporters, search, metrics, CLI), the `claims-ops-v1` world (portal +
OpenEMR 8.3.0 + three task families + held-out compositions), the `toy-counter`
world, teacher and student policies, and the training ladder. 210 tests, all
offline, no key required.

**Verified on real Solari, measured (`docs/spikes.md`, `docs/overnight-2026-09-04.md`).**

| Measurement | Result |
| --- | --- |
| Family 3 (resolve_denial), Luna v5, seeds 0–99 | 94/100 verified, $0.061 per verified; seeds 20–99 fresh: 77/80 = 96.2 % [89.5, 98.7] |
| Family 3, **fresh seeds 100–139** (2026-09-04, `runs/luna-v5-f3-s100-139`) | **38/40 = 95 % [83.5, 98.6]**, first pass 35/40, $0.081 per verified; seeds 0–139 combined 132/140 |
| **Student, base `microsoft/Fara1.5-4B`, family 3 seeds 200–229, as shipped** (2026-09-04, `runs/fara15-4b-base-f3-s200-229`) | **0/30 = 0 % [0, 11.3]**; invalid-action rate 20.8 % (all `visit_url`), 24/30 ended by the invalid limit; $0.22 VM |
| **Student + `--nav-macro`, same seeds** (`runs/fara15-4b-base-f3-s200-229-nav`) | **0/30 = 0 % [0, 11.3]**; invalid 0.0 % (2,825 steps); 30/30 stuck at the OpenEMR login, 10 stopped via `ask_user_question`; $0.40 VM |
| **Student + `--nav-macro`, `resolve_denial_easy`, same seeds** (`runs/fara15-4b-base-f3easy-s200-229-nav`) | **0/30 = 0 % [0, 11.3]**; invalid 0.0 % (1/2,983); same login wall, 19/30 guessing passwords, 10 `ask_user_question`; $0.43 VM |
| **Student probes, 2026-09-05 (`docs/student-2026-09-06.md`)**: seeds 200–204, 60 steps, `--nav-macro` plus one policy-side option each | OpenEMR login rung: `--instruction-note` (username/password spelled out) **5/5**; `fara_no_user_v1.md` prompt (no critical points, v5 conventions) **4/5**, 0 `ask_user_question`; `--history-notes` 1/5; note + prompt **5/5**. $0.04–0.05 each |
| **Student, base 4B, the fair configuration** (`--nav-macro` + `fara_no_user_v1.md` + `--instruction-note`, seeds 200–229, 120 steps; `runs/fara15-4b-fair-f3-s200-229`) | **0/30 = 0 % [0, 11.3]** — **the like-for-like floor for the SFT ladder.** Staircase: openemr_login **30/30**, openemr_chart 0/30, everything after 0/30; invalid 0.0 % (1/3,643); 0 `ask_user_question`; 25/30 navigate OpenEMR by invented URLs and drop the session, 12/30 hit the post-login "Aw, Snap!"; 3.78 s per call; $0.54 VM |
| **Student, base `microsoft/Fara1.5-9B`, same fair configuration and seeds** (`runs/fara15-9b-fair-f3-s200-229`, mlx-vlm bf16 on this Mac, 8.1 s per call shared) | **0/30 = 0 % [0, 11.3]**; openemr_login 30/30, **openemr_chart 4/30** (Documents tab reached 4/30, no letter opened), auth_typed 0/30; 6/30 cut by the 900 s wall; 28/30 repeat one click position ≥ 10 times; 19/30 post-login "Aw, Snap!"; $0.95 VM |
| Family 1 (reschedule_constrained), Luna **v10 + `--history-notes`**, seeds 0–9 | 10/10, first pass 9/10, median 48.5 actions, $0.089 per verified |
| Family 1, **fresh seeds 10–34** | **15/25 = 60 % [40.7, 76.6]**, first pass 12/25, $0.20 per verified |
| Family 2 (update_insurance_reconcile), v10, **fresh seeds 10–34**, both variants | **25/25 = 100 % [86.7, 100]**, first pass 24/25, $0.07 per verified |
| Best-of-2 fork search on the 10 double-failed family-1 seeds | 2/10 recovered, $0.53 per recovered seed; checkpoint deletes work |
| `revert()` to golden, desktop, n=10 (2026-09-03) | works, same machine id; p50 100.9 s; restores bimodal ≈ 22 s or 70–160 s |
| Revert-mode pool over 121 restores on 2026-09-04 (six runs) | held revert mode through ten 503 reverts (each replaced in place, 200–245 s); 0 fork deaths |
| Observe-act-observe loop | 0.45 s p50 |

**SFT data on disk (`runs/exports/`).** Family 3: `sft_luna_v5_f3_s20-99.jsonl`
(5,232 records) + `sft_luna_v5_s0-19.jsonl` (1,122) + **`sft_luna-v5-f3-s100-139.jsonl` (2,377 from 38 episodes)**. Families 1–2 combined:
**`sft_luna-v10-fam12.jsonl`, 2,890 records from 52 episodes** (27 family-1, 25
family-2), built from the v10 runs and the search winners. Per-run files next to it.

**Harness findings of 2026-09-04, all fixed and tested:**

- The student's history was compact actions only, so a "remember X" instruction
  never reached the next turn; `--history-notes` shows the model's own reasoning
  line next to each previous action (its memory). `collect` also never passed
  `--history-k` to the `Env`, so 16 showed 8. These two turned family 1 from a
  one-week date drift into 10/10 on seeds 0–9.
- OpenEMR 8.3 base64-encodes `log.comments`; the `ui_path` tripwire now decodes
  before matching. Two perfect calendar-only episodes had scored `DIRECT_DB_WRITE`.
  A `ui_path` failure now stores `audit_rows_after_watermark` in the verdict.
- The `before_episode` Chrome relaunch raced its own `pkill` (new Chrome attached
  to the dying one → no browser); it now waits, clears the profile lock, verifies,
  and fails the reset stage if Chrome is missing.
- The pool flipped to fork mode on a bare `ConnectionError` during revert; only a
  real refusal (409 / "Not revertable" / paused) flips it now.
- `best_of_n` leaked the checkpoint when the two candidates deduplicated to one.

---

## What is blocked or open

**Family 1's remaining failure classes on fresh seeds (23 failed attempts of 38):**
window 13 (right date, old time kept when the requested half-day differs — the
rule is in v10 but never acted on; the notes carry dates, not times), add-event 7
(v10's "click the TIME" lands on the calendar's hour labels, which open *Add New
Event*; a second appointment gets saved), date arithmetic 3. Neither plain retries
(3/13) nor best-of-2 search (2/10) recover the window class. Seeds 0–9 were easy by
construction: the appointment already sat in the requested window for 8/10 of them
(11/25 on seeds 10–34). **v11 candidates:** (a) "CURRENT time → TARGET window"
in the note, and change the time whenever the current time is outside the window;
(b) "click the appointment's own text, never the hour labels; if a form titled
Add New Event opens, click Cancel". Not written, per the overnight plan's v10 cap.

**Chrome crashes** remain the other loss (re-login loops after "Aw, Snap!";
median 2 crashpad reports per attempt, up to 14). `scripts/chrome_crash_probe.py`
and the swiftshader flag experiment are unchanged from 2026-09-03.

**Solari items** (changelog of 2026-09-04, mapped in the ledger): `disk_gb` up to
20 GB on fresh machines (needs a golden v6 rebuild), `recordingUrl` now in
responses (not re-measured), revert = fresh machine swapped in (machine id
constancy not re-measured), idempotency keys (not adopted), ancestors deletable
and storage billed from October. Still unaddressed: bimodal restores, guest RCU
stalls / tab crashes, `cpu`/`mem_mb` ignored on forks.

**The base student cannot do family 3; the login wall is gone and the navigation wall is next (2026-09-05,
`docs/student-2026-09-06.md`).** With `--instruction-note "Credentials for OpenEMR: username admin, password pass.
Click the Username field, type admin, click the Password field, type pass, click Login."` and
`--system-prompt-file forkloop/policies/prompts/fara_no_user_v1.md` (Fara's identity and tool schema kept through
the `{fara_identity}` / `{fara_tools}` placeholders, the critical-points text replaced by a no-user rule, the v5 world
conventions appended) the base 4B logs into OpenEMR in 30/30 episodes and never asks a user; then, in 25/30
episodes, it navigates OpenEMR by invented URLs (`/openemr/login.php`, `/openemr/patient/1`,
`login.php?username=admin&password=pass`) that drop the session, and 12/30 meet Chrome's post-login "Aw, Snap!"; no
episode opens the patient chart. `--history-notes` made the login worse (1/5). The 9B (same flags, 8 s per call on this Mac) knows the Finder and the
search box, opens the chart and the Documents tab in 4/30 episodes, then clicks one spot ten-plus times or re-runs the
login checklist; it never opens a letter. The staircase that shows this lives in every verdict now
(`details.ui_milestones`, `scripts/milestone_staircase.py`; the document rung counts only a real
`controller.php?document&retrieve/view`, not the help pages or the patient-picture fetch); the three 2026-09-05 runs
predate it. Serving on this Mac: mlx-vlm holds one model per process on port 8001 (`runs/logs/serve_9b.sh` swaps
them); the 9B's 8 s per call with two episodes puts 120-step episodes over the 900 s wall in 6/30 cases.
The 2026-09-04 findings, for the record:

1. *Action format:* base Fara 1.5 calls `visit_url` (its browser harness action) on ~10 of its first 30 steps
   regardless of the tool enum. Fixed: `StudentPolicy(nav_macro=True)` / `collect --nav-macro` expands it into
   click(640, 90) → key(ctrl+a) → type(url) → key(Return) over four env steps with one model call, and
   `history_back` into alt+Left. Invalid rate 20.8 % → 0.0 %.
2. *OpenEMR login:* with navigation working, 30/30 episodes reach the login page and 0/30 get past it. The model
   clicks Username, types `admin`, then types `pass` into the same field ("Invalid username or password"), does
   not read `pass` as the password, and starts guessing (`OpenEMR`, `password`, `Password123`, `12345`…).
3. *Critical points:* 10/30 episodes end with Fara's `ask_user_question` ("I need the OpenEMR admin password"),
   which the parser maps to `done(success=false)`. There is no user to answer; the rule fires exactly where the
   model is unsure.

All three of the probes proposed here were run on 2026-09-05 (see above): the login failure was reading, not motor.
Not tried: `--max-invalid` raised (moot at 0 % invalid), `--prompt-style compact` (Fara was not trained on it), a
convention line about OpenEMR having no guessable URLs (would be task-adjacent; SFT teaches the same thing).

**No GPU.** LoRA training needs a rented card; `train/` is wired and the SFT ladder plan is in
`docs/student-2026-09-05.md`.

---

## Next steps, in order

1. **SFT the student on a rented GPU — the family-3 ladder, evaluated like for like.** The base number to beat is
   the *fair* configuration's result in `docs/student-2026-09-06.md` (base `microsoft/Fara1.5-4B`, family 3 seeds
   200–229, `--nav-macro --system-prompt-file forkloop/policies/prompts/fara_no_user_v1.md --instruction-note "…"`,
   120 steps / 900 s; run `fara15-4b-fair-f3-s200-229`). Every rung is trained on family-3 episodes drawn in
   `task_id` order so the subsets nest (25 ⊂ 50 ⊂ 94 ⊂ 115), from the two verified v5 runs (77 + 38 episodes,
   7,609 records); SFT records reference screenshots by absolute path under `runs/`, so the two run directories
   (≈ 1.9 GB of PNGs) travel with the repo. Do not train on the Mac.

   ```bash
   # on this Mac: ship the repo and the two runs (runs/ is git-ignored)
   rsync -a --exclude venv --exclude venv-mlx ~/Desktop/Solari/repo/projects/forkloop/  gpu:~/forkloop/
   rsync -a runs/luna-v5-f3-s20-99 runs/luna-v5-f3-s100-139  gpu:~/forkloop/runs/

   # on the GPU box (one 80 GB card; python3.11, pip install -e ".[train]")
   cd ~/forkloop && export PYTHONPATH=.
   for N in 25 50 94; do
     python -m train.make_sft --run-dir runs/luna-v5-f3-s20-99 --run-dir runs/luna-v5-f3-s100-139 \
       --families resolve_denial --exclude-split 'heldout_*' --history-k 8 --limit $N --out data/sft_f3_$N.jsonl
   done
   python -m train.make_sft --run-dir runs/luna-v5-f3-s20-99 --run-dir runs/luna-v5-f3-s100-139 \
     --families resolve_denial --exclude-split 'heldout_*' --history-k 8 --out data/sft_f3_all.jsonl   # 115 episodes

   for N in 25 50 94 all; do
     python -m train.train_lora --model microsoft/Fara1.5-4B --data data/sft_f3_$N.jsonl \
       --output-dir ckpt/fara_f3_$N --prompt-style fara --history-k 8 --max-image-side 1280 --coord-space norm1000 \
       --epochs 2 --lr 1e-4 --lora-r 16 --lora-alpha 32 --lora-dropout 0.05 --batch-size 1 --grad-accum 8 \
       --dtype bf16 --save-steps 200 --merge-out ckpt/fara_f3_$N/merged
     vllm serve ckpt/fara_f3_$N/merged --port 8011 --served-model-name fara-f3-$N --dtype bfloat16 --max-model-len 16384
   done
   ```

   Evaluate every rung with **exactly the step-3 flags** of the fair base run, so the base number is the like-for-like
   floor (the note and the no-user prompt stay on: a model that no longer needs them costs nothing by having them, and
   removing them would change two things at once; `--nav-macro` stays on and `classify_failures.py`'s macro count
   shows whether the SFT model still emits `visit_url`):

   ```bash
   python -u -m forkloop.cli collect --world claims-ops-v1 --policy student \
     --student-url http://<gpu-host>:8011/v1 --model fara-f3-$N \
     --prompt-style fara --history-k 8 --nav-macro --max-steps 120 --max-seconds 900 \
     --retry-failed 0 --pool-mode revert --concurrency 2 --split train \
     --families resolve_denial --seeds 200-229 \
     --system-prompt-file forkloop/policies/prompts/fara_no_user_v1.md \
     --instruction-note "Credentials for OpenEMR: username admin, password pass. Click the Username field, type admin, click the Password field, type pass, click Login." \
     --run-id fara15-4b-sft$N-fair-f3-s200-229
   PYTHONPATH=. python scripts/milestone_staircase.py runs/fara15-4b-fair-f3-s200-229 runs/fara15-4b-sft$N-fair-f3-s200-229
   ```

   Read each rung with the staircase before the success rate: the base model dies between `openemr_login` and
   `openemr_chart`, so the first rung that moves the chart / document / `auth_typed` percentages is the one that
   matters even while success is still 0. Budget: four rungs × 30 seeds ≈ 4 × $0.45 of Solari VM at the base's
   120-step median (less as episodes succeed and end early); GPU time about an hour per rung at 2 epochs. Held-out
   seeds (`--split heldout_seeds`, 100000+) only after the train-split curve exists.
2. **Prompt v11 for family 1** (window rule with a time note; hour-label rule),
   run seeds 35–59 fresh (≈ $3), compare with the 60 % of v10 on 10–34.
3. **Golden v6** with `disk_gb` 20 and Chrome started with `--disable-gpu`
   (`forkloop build-world`, ~10 min); re-measure `recordingUrl` and the revert
   machine-id behaviour on the way (`docs/solari-repro.md` scripts, cents). The post-login "Aw, Snap!"
   (`interface/main/tabs/main.php`, error code 5) still fires on the v5 golden with `--disable-gpu` set by
   `ensure_chrome_gpu_flag` (3/5 episodes of probe (a), 2026-09-05).
4. Keep `forkloop reap --dry-run` at 0 and the account at four snapshots.

## Gotchas that cost real time yesterday

These are all fixed in the scripts, but they will bite again if you touch the
same areas.

- **`revert()` destroyed a running machine** on this account, twice, before Solari's 2026-09-03 fix; since then a refused revert leaves the machine alive and a successful one is a real in-place restore (10/10 on the golden). The pool now catches a refused revert,
  logs `revert_unsupported_fell_back_to_fork`, replaces the machine and switches
  to fork mode for the rest of the run (verified live), so a wrong `--pool-mode`
  costs time rather than the run.
- **Chrome will not run as root.** The desktop session user is `desktop` on Xvfb
  `:0`; launch through
  `runuser -u desktop -- env DISPLAY=:0 HOME=/home/desktop XDG_RUNTIME_DIR=/run/desktop`.
- **Key chords must be one string.** The SDK presses a list sequentially, so
  `["ctrl", "a"]` types the letter a. The backend now joins them into `"ctrl+a"`.
- **Keyboard focus is not guaranteed after a fork.** Navigation clicks the
  omnibox at (640, 90) before typing a URL.
- **The control WebSocket drops mid-episode now and then** (close code 1000/1006;
  the SDK then raises `ConnectionError: Not connected`). `SolariMachine` re-dials
  and retries the operation once (`_call`, `reconnects` counter); a drop that
  does not recover within 30 s surfaces as `BackendError`.
- **A fresh venv needs the `teacher` extra** or every model call fails with
  `ModuleNotFoundError: anthropic` while the desktops keep billing; `collect`
  now refuses to start without it. **A killed `collect` leaves its machines
  running** (SIGTERM skips `pool.close()`): run `forkloop reap` before the
  next run, or let the pool's 429 handler reap them.
- **The desktop disk is 4 GB and `disk_gb` is ignored.** The build purges VS Code
  and LibreOffice and trims caches; a full disk breaks OpenEMR with
  "table 'log' is full", which is exactly the one benchmark failure.
- **OpenEMR 8.3 refuses to run CLI scripts as root** (`RootCliGuard`); the
  installer and the uuid backfill run as `www-data`.
- **`tr ... | head -c` under `pipefail` exits 141.** Cost an entire build.
- **The OpenEMR session is not in the snapshot.** That family starts on the
  login page and the instruction carries `admin / pass`.
- **Chrome needs `--disable-gpu` on this template**, and the calendar shows
  one provider column until "All Users" is clicked. Both were misread as a
  data bug for a whole session.
- **`apt-get update` fails on the template** until the VS Code apt source is
  removed (`build.sh` does it now).
- **`forkloop build-world --attach <id>`** resumes a failed build on the same
  machine instead of paying for a fresh one. The build scripts are idempotent.

---

### Student-serving gotchas (2026-09-04)

- **mlx-vlm 0.6.17 needs `jinja2`** for the Qwen3.5 chat template; without it every `/chat/completions` is a 500.
- **Fara's coordinate space is 1000×1000** and the shipped `coord_space=norm1000` / `image_max_side=1280` are
  right (hand-checked: the rescaled point sat on the *Patients* nav link). Do not "fix" it.
- **Base Fara emits `visit_url`**; run the student with `--nav-macro` or every episode ends at the 10-invalid limit.
- **The mlx-vlm server returns `<|im_end|>` inside `content`**; the parser ignores it, but it shows up in
  `raw_action` and in the last-actions column of `classify_failures.py`.
- **Two episodes share one server**: 1.5 s per call alone, 2.4–3.7 s with `--concurrency 2`; `--max-num-seqs 2`.

## House rules worth repeating

Two channels stay separate: the policy sees screenshots and emits actions,
nothing else. Expected values never enter the VM. `docs/contracts.md` is the
spec and changes with the code in the same commit. Nothing that has not been
measured gets stated as a result — unmeasured things stay "not yet measured" in
the README, and fake-backend timings are labelled as simulator numbers.
