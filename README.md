# Forkloop

Compare computer-use policies on a seeded **denial appeal across real OpenEMR 8.3
and a synthetic payer portal**. Run matched attempts on Solari desktops, verify what
persisted in the databases, and inspect the evidence behind each regression.
All patient and claims data is synthetic.

**[Open the evidence report](https://rynitepsd-tech.github.io/forkloop/)**
— no installation or account is needed. A [downloadable HTML copy](projects/forkloop/docs/worked-example/report.html)
is also included. The portal said “Appeal submitted”; the database held the wrong authorization.
The report connects the failed check to the typed value and retained screenshot.

**[Inspect the recovered prompt comparison](https://rynitepsd-tech.github.io/forkloop/navigation-comparison.html)**
— two completed matched development seeds: compact prompt 0/2, workflow prompt
2/2. The original four-pair experiment was interrupted, so the report retains
every planned cell and withholds a winner. This is check-summary evidence, not
a complete replay or a held-out reliability claim.

**[Read the recorded-observation study](projects/forkloop/docs/frozen-v3-evaluation-results.md)**
— high image detail selected exact authorization typing on 19/20 states, versus
0/20 with low detail. A separate 18-state development validation made 16 exact
proposals with no wrong entries and two abstentions under a two-view agreement
rule. These are saved-state decisions, not live task completion; negative
verification and magnification findings are included.

The supported product now includes:

- **`forkloop demo`**: five no-account verifier controls, each with an HTML report.
- **`forkloop doctor`**: actionable setup checks without a VM allocation or model call.
- **`forkloop compare`**: two identified policies, matched seeds/resets/budgets, every
  attempted or missing cell retained, and no successful-retry selection.
- **`forkloop compare-report`**: paired outcomes, changed settings and inspectable
  episode evidence. Incomplete or non-comparable evidence cannot recommend a winner.

**Live allocation hold:** new Forkloop Solari desktops and sandboxes are paused
until billable lifetime can be bounded. A new ledger or pricing review cannot
clear this hold. Offline workflows, report inspection and resource cleanup remain available.

**[Install and run Forkloop](projects/forkloop/README.md)**
· [Live comparison configuration](projects/forkloop/configs/denial-navigation.json)
· [33-second evidence explainer](projects/forkloop/docs/worked-example/demo.mp4)
· [Verification receipts](projects/forkloop/docs/product-handoff.md)
· [Scope and limitations](projects/forkloop/docs/limitations.md)

Solari snapshots, revert and fork make the world reusable. Forkloop additionally
seeds application state and checks equivalence; restore alone is not a full reset.
Checks cover configured tables, not global safety. Training remains research, and
external adoption is not claimed.

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
| [forkloop](projects/forkloop) | Seeded `resolve_denial` policy evaluation across OpenEMR and a synthetic payer portal, with scoped SQL verification and text/HTML artifact reports. Snapshot restore is one stage of reset; training and other task families remain research paths. |

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
