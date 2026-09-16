# Forkloop

**A regression environment for computer-use agents working across healthcare software.**

Run two policies against the same seeded denial appeal in **real OpenEMR 8.3 and a synthetic payer portal**. Check what actually persisted, compare the outcomes, and open the screenshot and database evidence behind a failure. All patient and claims data is synthetic.

The recurring question is simple: **did this model, prompt or memory change improve the workflow—or merely make the agent sound more confident?**

Solari provides the desktop, snapshot, revert and fork primitives. Forkloop seeds both applications, checks reset equivalence, evaluates persisted outcomes and produces portable evidence. A snapshot restore alone is not a complete reset.

## Inspect the evidence first

[Open the hosted evidence report](https://rynitepsd-tech.github.io/forkloop/). No installation, account or live agent connection is needed. A [self-contained HTML copy](docs/worked-example/report.html) is also available to download and open locally.

The portal said **“Appeal submitted.”** The database contained `AUTH-3614538`, not the required `AUTH-36G14538`. Forkloop rejected the episode as **WRONG_VALUE**. The report retains six screenshots from the 74-step episode; it does not reconstruct the 142 unavailable screenshot references.

[Evidence guide](docs/worked-example/) · [33-second explainer](docs/worked-example/demo.mp4) · [Verification receipts](docs/product-handoff.md)

[Recovered prompt comparison](docs/worked-example/navigation-comparison.html):
all eight planned cells, recorded checks and controlled settings from the
interrupted September 15 experiment. This public edition has no episode images;
the full cropped HTML-only bundle remains available locally.

## Try it without an account

From a complete source checkout:

```bash
git clone https://github.com/rynitepsd-tech/forkloop.git
cd forkloop/projects/forkloop
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install '.[world]'

forkloop doctor --backend fake
forkloop demo --out runs/demo
```

The demo exercises the real portal HTTP routes and SQL oracle using local SQLite stand-ins. It writes an HTML report for each scenario:

| Scenario | Required observation |
| --- | --- |
| Correct appeal | Accepted: exactly one appeal with the right authorization and reason. |
| Wrong authorization | Rejected: the incorrect value really persisted. |
| Wrong record | Rejected: a distractor claim was changed. |
| Duplicate appeal | Rejected: two appeals instead of one. |
| Interrupted recording | Unscored: no verifier result was written. |

Open `runs/demo/wrong_authorization/report.html`, or any sibling report. These are **constructed verifier controls, not measured policy performance**. Their blank simulator screenshots do not represent OpenEMR navigation. The demo works from an installed wheel as well as a source checkout; it does not require the research scripts.

Output directories are deliberately new-only. Choose another `--out` for a later run instead of overwriting evidence.

## Compare a policy change

The comparison command replaces manual pairing of unrelated collections and the research-only frozen-Fara comparison script.

```bash
# Offline plumbing example: both deliberately incomplete policies fail.
forkloop compare --config configs/offline-comparison.yaml --out runs/offline-comparison
forkloop compare-report runs/offline-comparison
```

Open `runs/offline-comparison/comparison.html`. The directory includes the declared protocol, every planned cell, ordinary Recorder episodes, per-episode HTML and complete screenshots when captured.

The runner:

- Uses the same world, family, split, seeds and action/time budgets for both arms.
- Alternates A/B and B/A ordering by seed, with one fresh policy instance per cell.
- Makes exactly one policy attempt per cell. It does not select a successful retry.
- Records setup/provider errors and missing cells separately from scored task failures.
- Checks task fingerprints, baseline table hashes, watermarks, preserved rows and actual reset methods before calling a pair comparable.
- Reports both-pass, A-only, B-only and neither-pass outcomes, changed settings, usage and the exact evidence behind discordant seeds.

A descriptive leader is withheld for incomplete or non-comparable evidence. Even a complete small sample is not a reliability guarantee or deployment recommendation. Clock pixels may differ despite equivalent task state; table checks cover configured scope, not the entire VM.

The September 15 runs were interrupted. Of four planned pairs per experiment,
one memory pair and two navigation pairs are comparable. Memory A and B each
scored **0/2**; another completed A attempt is excluded after a backend action
error. Navigation A scored **0/2**, B **2/2**. Missing and interrupted attempts
remain unscored. This is promising development evidence for the navigation
prompt, not a declared winner. [Recovery receipts](docs/product-handoff.md).

`--fail-on-regression` makes the comparison command exit **1** if B fails a comparable seed that A passes. An incomplete/non-comparable comparison exits **2**. A fully executed comparison normally exits **0**, even if neither policy succeeds. Individual episode `run` retains its separate success/failure exit behavior.

### Bring your policies

Configuration uses `version: 1` and exactly two named variants. See [the complete live example](configs/denial-memory.yaml): it changes only `history_notes`, retaining the same model, image settings, action history and budgets.

Built-in variants use `policy: student`, `teacher`, `scripted` or `random`, with constructor keyword arguments under `options`. The student accepts an OpenAI-compatible image-chat endpoint. `api_key_env` names an environment variable; credential values must never appear in YAML. Authenticated URLs and credential-like option fields are rejected before allocation.

For your own agent, use `factory: your_importable_module:your_factory`, a nonempty `revision`, and JSON-compatible `options`. The factory receives those options as keyword arguments and returns a fresh policy with `async act(observation) -> (Action | None, metadata)`. Optional `reset()` and `aclose()` lifecycle methods are supported. A factory can also be async. Custom factories are **trusted local Python code**, not sandboxed plugins.

Policies receive the task instruction, screenshots and action history. They do not receive the expected authorization, SQL, seeding state or oracle specification. Factory identities are caller-attested; recording a model label cannot prove what a remote server actually serves. File-based prompts are captured by content, and the CLI records configuration and policy-source fingerprints.

Policy metadata reserves a truthy `error` for provider/runtime failures that
prevent measurement. Invalid model output returns `None` with a diagnostic
`note`; giving up returns a terminal action. Those policy behaviors consume their
normal budgets and remain scored, rather than disappearing from the denominator.

Validate configuration before a live run:

```bash
forkloop compare --config configs/denial-memory.yaml --out runs/memory-check --check
```

This makes no model request or VM allocation. A custom module import is still trusted code and can have its own side effects. Configuration validation is not endpoint-health verification.

For Python integration, use `forkloop.comparison.PolicyVariant` and `run_comparison`. The caller owns backend cleanup. [The contract](docs/contracts.md) describes identities, output layout and equivalence boundaries.

## Run on Solari

**New Solari allocations are paused in this release.** Two desktops outlived the
five-hour reservation assumption. The documented timeout is an idle window,
not a hard lifetime limit; a local watchdog also depends on its host surviving.
Neither a new session ledger nor an acknowledged pricing JSON clears this
release-wide capability hold. This applies to desktops, sandboxes and the
historical Forkloop spike allocators.

Use `forkloop doctor --backend solari` to inspect prerequisites without spending.
It reports `solari.lifetime` as a failure even with a fresh funded ledger.
`--remote` additionally permits read-only snapshot metadata requests; it does not
allocate a VM, contact a model or establish credits, capacity or application health.
Existing-resource cleanup remains available with `forkloop reap`.

The live configuration examples remain inspectable with `compare --check`, but
cannot allocate until an enforceable provider lifetime or spending bound is
established and the guard is deliberately revised. There is no runtime bypass.
Do not restart historical build, collection or training commands to evade it.

The September 15 build and interrupted comparisons ran on an existing account,
not a fresh-account deployment. Their development seeds 200–203 were reused;
final research seeds 100500–100529 remain sealed. See the [dated receipts](docs/product-handoff.md).

### Spending and resource lifetime

Solari's old five-hour-plus-setup reservation arithmetic is no longer offered as a finite spending bound. New allocations fail before a reservation or provider request. Existing accounting retains larger observed exposure and unknown charges; cleanup is not a refund. OpenAI reservations still occur before each guarded request, include long-context/cache-write premiums, fix the service tier to standard pricing, and retain uncertain charges. Service budgets are separate, not transferable.

The built-in Solari price review expires October 1. Renew it through an explicitly acknowledged pricing JSON in `FORKLOOP_SOLARI_PRICING_FILE`; do not remove the guard. [Pricing-review fields and storage boundaries](docs/contracts.md) are part of the contract. Once storage billing begins, creating new snapshots remains blocked without a provider-enforced retention bound. Existing snapshots must be reviewed or deleted before they incur unwanted storage charges. Forkloop never changes subscriptions or organization billing settings.

`forkloop reap --ledger runs/session/session-ledger.sqlite --dry-run` lists machines owned by that ledger, including uncertain creates identifiable by their operation metadata. Omit `--dry-run` to kill them and check for survivors. `FORKLOOP_SESSION_LEDGER` is the default scope; account-wide cleanup requires explicit `--all-sessions`. Do not reap a session that is still running. A budget hold never prevents cleanup, and cleanup is not a refund.

## Inspect or share a result

```bash
forkloop report runs/demo/wrong_authorization --all
forkloop report runs/demo/wrong_authorization --all --format html --out runs/wrong-authorization.html
forkloop compare-report runs/live-memory --format json --out runs/live-memory/inspection.json
```

To share a comparison without copying its raw controller artifacts:

```bash
forkloop compare-report runs/live-memory --format html \
  --bundle runs/shared-memory --crop-top 114
```

Open `runs/shared-memory/comparison.html`. The new-only directory contains just
HTML reports with embedded cropped images and working relative episode links.
Raw JSON, logs and source PNGs are excluded. The crop is explicit and disclosed;
114 pixels fits the recorded desktop's browser chrome, not every browser layout.
Review the exported pixels and free text before sharing the entire directory.

Episode HTML is self-contained and script-free. It supports failed-check links, a complete action trace, available/missing screenshot distinctions and original-pixel inspection. Model narration is visibly separate from verifier conclusions.

Comparison HTML embeds the check summaries but links to episode HTML in its directory. Keep that directory structure intact when moving it. HTML exports stay inside the comparison directory so those links remain valid. Text/JSON exports can be written elsewhere. Raw run metadata and JSON are controller evidence, **not automatically safe public exports**. Review screenshots and free text before sharing; automatic redaction is not a secret scanner.

A reward of 1 means the configured effects and invariants passed on that backend. Checksums cover selected tables, audit records are a coarse provenance tripwire, and neither establishes global safety, UI-only proof, HIPAA compliance or production readiness. Never use real patient data.

## Evidence and research boundaries

The product is the evaluation workflow, not a claimed small-model training breakthrough.

[The September 16 recorded-observation study](docs/frozen-v3-evaluation-results.md)
found exact authorization typing on 19/20 states with high image detail versus
0/20 with low detail. Additional verbal verification did not repair the error;
uniform magnification repaired it but corrupted a previously correct answer.
A rule frozen before a separate 18-state development validation withheld
disagreeing readings: 16 exact entries, zero wrong entries, two abstentions,
including one withheld correct high-detail answer. None of these proposed actions
was executed. No production-safe submission policy or live success is claimed.

| Historical evidence | Observation | Boundary |
| --- | --- | --- |
| [Retained live adapter episode](docs/worked-example/) | A submitted appeal contained the wrong authorization; WRONG_VALUE. | One selected failure; only six screenshot references retained. |
| [Paired base/v3 run](docs/live-paired-v3-results.md) | Base 0/2; adapter 0/2, on development seeds 200/201. | No positive workflow learning curve. |
| [Frozen Fara saved-observation diagnostic](docs/frozen-v3-evaluation-results.md#september-6-frozen-fara-adapter-comparison) | Exact authorization selected for typing improved from 2/20 to 14/20. | Teacher-reached states, not independent navigation or persisted correct entry. |
| Earlier student development runs | Base, SFT-v1 and SFT-v2 each 0/30. | Different experiments; do not pool them with later results. |
| [Magnification diagnostic](docs/document-magnification-results.md) | Stopped before scoring after renderer crashes. | 0/0 matched pairs is missing evidence, not model failure. |

Historical hosted teacher runs demonstrate that the task can be solved; they are not matched baselines for new experiments. Search, training and other task families remain research paths. [system.md](system.md) describes the implementation; [limitations](docs/limitations.md) and [cost](docs/cost.md) bound the claims.

The intended user is a GUI-agent builder deciding whether to keep a model, prompt, observation or memory change. **External adoption and demand are not established.** An assisted walkthrough, independent use, repeat use and a changed research decision require separate evidence; none is manufactured by this repository.

Built on [Solari](https://getsolari.com), [OpenEMR](https://www.open-emr.org/) and the original [Solari Cookbook](../../examples/). Related work includes [OSWorld](https://github.com/xlang-ai/OSWorld), [Gym-Anything / CUA-World](https://arxiv.org/abs/2604.06126), [HealthAdminBench](https://arxiv.org/abs/2604.09937) and [MedCUA-Bench](https://arxiv.org/abs/2606.03203); [Fara](https://github.com/microsoft/fara) underlies the student research. Forkloop does not claim to have invented database verification or GUI environments, nor to outperform those benchmarks. MIT.
