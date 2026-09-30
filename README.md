# Forkloop

**Turn your computer-use agent's failures into verified training data, then test the retrained agent on held-out tasks.**

Forkloop records an agent working in real software, restores an earlier checkpoint of the
world and the agent's memory, and lets a teacher try alternative continuations on independent
copies. Only paths verified against the applications' databases become training data, with
hashes and lineage back to the recorded evidence. The retrained student is evaluated alone.

The loop runs on **OpenEMR 8.3 plus a synthetic payer portal**, using Solari desktops with VM
snapshots or Docker with fidelity-checked replay. **Kanboard** is a second world built through
the same public interface. All patient and claims data is synthetic.

**[See a repaired failure](https://rynitepsd-tech.github.io/forkloop/)** ·
**[Watch the 66-second walkthrough](https://rynitepsd-tech.github.io/forkloop/forkloop-exp1.mp4)** ·
**[Get the datasets, adapters and results](https://github.com/rynitepsd-tech/forkloop/releases/tag/v0.3.0)**

## What the experiment found

The [registered experiment](projects/forkloop/docs/protocol-learning-experiment.md), with dated
deviations, evaluated a Qwen3.8-27B student on **150 held-out tasks** across four task families.
All **1,650 planned evaluation cells** were scored: one baseline and one warm-start model,
plus three training runs for each of the three trained arms. Infrastructure replacements are
retained in the released attempt records.

| Student | Family-balanced success |
| --- | ---: |
| Untrained | 3.3% |
| Shared warm start only | 20.8% |
| Warm start + teacher demonstrations | 26.8% |
| Warm start + Forkloop corrections | 26.0% |
| Warm start + full-restart repairs | 26.9% |

**The hypothesis that corrections beat demonstrations at matched collection cost was not
supported.** Most of the gain came from the shared warm-start data. The trained arms used
matched budgets for counted work; those budgets excluded voided work. Counting all recorded
work, correction data cost roughly an order of magnitude more per verified path than
demonstrations. Replay restores passed their fidelity checks in 53% of attempts, and no model
solved the rescheduling family. These results establish the working loop, not a learning or
cost advantage over the simpler alternatives.

[Full results and sensitivity analyses](projects/forkloop/docs/results-exp1.md) ·
[Final report, limitations and spend](projects/forkloop/docs/final-report-20260929.md) ·
[Independent reviews](projects/forkloop/docs/reviews/)

## Try the loop without an account

```bash
git clone https://github.com/rynitepsd-tech/forkloop.git
cd forkloop/projects/forkloop
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[world]'
forkloop demo-loop --out runs/demo-loop
```

Open `runs/demo-loop/evidence/index.html`. This is a labelled offline simulation with a toy
world and scripted agents; it exercises recording, checkpoints, repairs, verification and
dataset export. It makes no model calls and needs no cloud account.

For real runs, supply an agent with `async act(observation) -> (action, metadata)`, a teacher,
and a world with a verifier. The workflow is `record` → `failures` → `repair` → `dataset` →
train → `evaluate`. See the [project README](projects/forkloop/README.md),
[correction contract](projects/forkloop/docs/correction.md) and
[current system overview](projects/forkloop/system.md).

## Regression testing is still part of the tool

Forkloop also compares policies on matched tasks. In an earlier registered comparison,
changing only the model from `gpt-5.6-luna` to `gpt-6-luna` reduced verified success from
**23/24 to 4/24** on that agent and workflow. The portal accepted 17 appeals from the newer
model, but database checks rejected 13 of them for incorrect authorization numbers.

[Read the comparison](projects/forkloop/docs/live-model-upgrade-comparison.md) or
[inspect a recorded wrong-value failure](https://rynitepsd-tech.github.io/forkloop/report.html).
The `compare` and `compare-report` commands preserve matched evidence and CI-friendly exit codes.

Live Solari runs require an explicit, controller-enforced lifetime bound because desktop idle
timeouts [renewed themselves in the recorded probe](projects/forkloop/docs/solari-lifetime-probe.md).
Resource leases, cleanup and accounting are described in
[operations](projects/forkloop/docs/operations.md).

## Solari Cookbook provenance

This repository grew from the [Solari Cookbook](https://github.com/solari-sdk/solari-cookbook):
short examples for [Solari](https://getsolari.com) cloud browsers, sandboxes and
desktops. The original examples below remain intact and self-contained; Forkloop
is the larger project under `projects/`, not a replacement for them.

## Examples

### Cloud browser

| Example | Language | What it shows |
| --- | --- | --- |
| [browser-quickstart-ts](examples/browser-quickstart-ts) | TypeScript | Launch a browser, open a page, read it |
| [browser-quickstart-py](examples/browser-quickstart-py) | Python | Launch a browser, open a page, read it |
| [browser-stealth-proxy-ts](examples/browser-stealth-proxy-ts) | TypeScript | Stealth mode + residential proxy egress |
| [browser-profiles-ts](examples/browser-profiles-ts) | TypeScript | Log in once, reuse the session forever |
| [browser-session-recording-py](examples/browser-session-recording-py) | Python | Record a session, download the replay |

### Sandbox

| Example | Language | What it shows |
| --- | --- | --- |
| [sandbox-quickstart-ts](examples/sandbox-quickstart-ts) | TypeScript | Run a command, write and read files |
| [sandbox-code-interpreter-py](examples/sandbox-code-interpreter-py) | Python | Stateful Python kernel for agent loops |
| [sandbox-port-preview-ts](examples/sandbox-port-preview-ts) | TypeScript | Expose a server in the VM on a public URL |

### Desktop

| Example | Language | What it shows |
| --- | --- | --- |
| [desktop-computer-use-py](examples/desktop-computer-use-py) | Python | Screenshot, click, and type on a Linux GUI |
| [desktop-snapshot-revert-py](examples/desktop-snapshot-revert-py) | Python | Snapshot a desktop, `revert()` it, fork an independent copy with `fromSnapshot` |

## Projects

Larger builds that use the API end-to-end live under `projects/`. They keep
the upstream examples untouched.

| Project | What it is |
| --- | --- |
| [forkloop](projects/forkloop) | A verified correction and evaluation loop for computer-use agents: checkpoints, teacher repairs, datasets, student training and held-out evaluation. OpenEMR + payer portal and Kanboard worlds; Solari, Docker and offline backends. |

## Running an example

Each directory is self-contained.

```bash
git clone https://github.com/solari-sdk/solari-cookbook.git
cd solari-cookbook/examples/browser-quickstart-ts

npm install                          # or: pip install -r requirements.txt
export SOLARI_API_KEY=slr_live_...   # grab one at console.getsolari.com
npm start                            # or: python main.py
```

One `slr_live_` key works across browsers, sandboxes, and desktops, and every
product bills to the same balance.

## Which product do I want?

- **Cloud browser** — you need a *web page*: scraping, testing, filling forms,
  anything Playwright or Puppeteer would do locally. Adds stealth, managed
  proxies, captcha solving, profiles, and session recording.
- **Sandbox** — you need to *run code*: an LLM's Python, an untrusted build, a
  data job. A headless microVM that boots from a snapshot in about a second.
- **Desktop** — you need a *screen*: computer-use agents, GUI apps, anything
  that has to be clicked. A sandbox plus X11 and a live VNC stream.

## Gotchas the examples encode

Things that cost you an afternoon if you meet them cold:

- **TypeScript: call `await solari.close()`.** The browser client keeps a
  loopback proxy open for connection retries. Skip the close and your script
  prints its output and then hangs forever instead of exiting.
- **Recording is per session, not per account.** Pass `recording: true` when you
  create the session; without it the replay endpoint 404s forever. The upload is
  async after release, so poll for ~30s before giving up.
- **Sandbox commands are not shell-interpreted.** `run("ls -la")` looks for a
  binary named `ls -la`. Put argv in `args`, or run `sh -c` explicitly.
- **`kill()`, not `close()`, ends a VM.** `close()` drops your local control
  channel; the VM keeps running until its idle timeout.
- **`timeoutMs` is a rolling idle window**, not a hard deadline — it resets on
  every use.

## Links

- Docs — [docs.getsolari.com](https://docs.getsolari.com)
- Console — [console.getsolari.com](https://console.getsolari.com)
- Changelog — [changelog.getsolari.com](https://changelog.getsolari.com)
- Questions — [hello@getsolari.com](mailto:hello@getsolari.com)

## Contributing

New examples are welcome. Keep them small, make them run end-to-end against the
real API, and put anything surprising in a comment right where it bites.

MIT licensed.
