# Cost ledger

This is a **dated pricing/estimate index, not an invoice ledger**. “Verified
price” below means read from <https://docs.getsolari.com/pricing> on 2026-09-01
(storage updated 2026-09-04), not re-verified today. Original planning ranges and
early build estimates are preserved as history. Later sessions have nonempty
SQLite reservation ledgers and result receipts, summarized below.

Installing dependencies requires downloads. Offline report/control execution
requires no account, model endpoint or VM and makes no paid service calls.
See the [dated verification record](product-handoff.md) for the commands exercised;
it is not a fresh provider inventory or billing reconciliation.

`python -m forkloop.bench.cost_model` regenerates the derived tables;
`tests/test_cost_model.py` pins the arithmetic.

## Verified price table

Source: <https://docs.getsolari.com/pricing> (September 2026).

| Plan | Monthly | Included credits | Concurrent |
| --- | --- | --- | --- |
| Free | $0 | $3 | 1 |
| Starter | $20 | $20 | 2 VMs/sandboxes, 20 browsers |
| Pro | $200 | not stated on the page | 10 |

Compute, USD per hour:

| Shape | Starter | Pro | Free |
| --- | --- | --- | --- |
| 1 vCPU / 2 GB | 0.057 | 0.040 | 0.086 |
| 2 vCPU / 4 GB | 0.114 | 0.080 | 0.171 |
| 4 vCPU / 8 GB | 0.228 | 0.160 | 0.342 |

VMs (desktops) add **$0.02/h** for the live screen. Snapshot storage (pricing
page, read 2026-09-04): **the first 10 GB are free, then $0.05 per GB-month**
(`SNAPSHOT_STORAGE_USD_PER_GB_MONTH = 0.05`, `SNAPSHOT_STORAGE_FREE_GB = 10`).
The 2026-09-04 inventory estimate was roughly 60 GB across goldens/ancestors and
checkpoints, or about $2.50/month under that tariff. This is not a current
inventory or storage invoice. Desktops require a paid plan (402 on Free).

## Formulas (`forkloop/bench/cost_model.py`)

```
vm_hour_cost(plan, vcpu, mem_gb, screen)  = compute[vcpu,mem_gb][plan] + (0.02 if screen)
vm_hours_per_credit(plan, ...)            = included_credits[plan] / vm_hour_cost
cost_per_1k_resets(method, plan, s, size, overhead)
                                          = 1000 · (s + overhead) / 3600 · vm_hour_cost   (0 for method="local")
episode_cost(...)                         = seconds/3600 · vm_hour_cost
                                          + (tokens_in · $/M_in + tokens_out · $/M_out) / 1e6
snapshot_storage_cost(gb, months, rate)   = max(0, gb − 10) · months · rate                 (rate 0.05, first 10 GB free)
```

Derived (verified inputs, exact arithmetic):

- Starter, 2 vCPU / 4 GB with screen: **$0.134/h** → **≈ 149 VM-hours per $20** of credit.
- Pro, same shape: $0.100/h.
- USD per 1,000 resets on Starter (2 vCPU / 4 GB, no overhead), as a sensitivity
  table rather than an observed reset bill; measured stages are in [spikes](spikes.md):

| seconds/reset | 2 | 5 | 10 | 20 | 30 | 60 | 120 | 300 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| revert / from_snapshot / rebuild | 0.074 | 0.186 | 0.372 | 0.744 | 1.117 | 2.233 | 4.467 | 11.167 |
| local | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Historical all-in planning estimate

These pre-experiment ranges are preserved from the plan; they are not a new
budget authorization or a forecast based on completed v3 work. Every row
except the then-current Solari plan price is an **estimate**.

| Item | Low | High | Basis |
| --- | --- | --- | --- |
| Environment (Solari) | $0 (month-1 promo) / $20 | $180 | Starter $20 ≈ 149 VM-hours at 2 vCPU/4 GB + screen; high = Starter plus ~$160 of credit top-ups (≈ 1,200 more VM-hours), or Pro |
| Teacher API | $30 | $80 | teacher-policy tokens for SFT data + eval — estimate |
| GPU rental | $30 | $80 | LoRA fine-tune of the student — estimate |
| Local box | $0 | $10 | electricity / Docker baseline runs — estimate |
| Snapshot storage | $1.50 | $6 | 10 GB free then $0.05/GB-month; 40 GB for one month to 70 GB for two |
| **Total** | **≈ $80** | **≈ $350** | sum of the rows (with the $20 Starter month; $60 if the promo makes month 1 free) |

Historical VM-hour sanity check: 149 VM-hours on Starter would cover roughly
7,600 episodes **if** reset took 10 s and execution 60 s, before overhead and
top-ups. Those were hypothetical inputs, not measured end-to-end timings.
Later desktop reset measurements were bimodal (~22 s or 70–160 s); full
reset/setup and resource lifetime must be counted, not only the restore call.

## Early build estimates — 2026-09-02 (historical)

These are the original rough account notes, **not verified charges**. The
running-total basis is incomplete; do not add them to later session ledgers
as if every amount were disjoint or invoiced.

| Date | Item | Estimated amount | Historical running note |
| --- | --- | --- | --- |
| 2026-09-02 | Starter desktop build (3 attempts), spikes, fork tests, 2 GUI episodes and reset benchmark | ≈ $0.30 | ≈ $0.55, incomplete prior basis |
| 2026-09-02 | 3 snapshots ≈ 24.7 GB | Unknown at the time | Storage price was then unverified; September 4 tariff above supersedes “unpublished” |

## Later recorded sessions and reservations

Source records remain authoritative for their own dates; most linked `runs/`
receipts are local/gitignored and are not bundled with the portable worked example.

| Session / record | Priced usage or compute estimate | Pending reservation / boundary |
| --- | --- | --- |
| Overnight repair, September 6 (locally retained diary) | OpenAI $0.1277158 from 160 calls' returned token usage; Solari compute estimate $0.0216692 | Solari $1.3846667 reserved pending billing; no GPU rented in that session |
| [V3 training](archive/lambda-v3-handoff.md) | Training did occur: 411/440 optimizer steps, 380.87 minutes including save | Consult its own training/provider receipts; the older overnight “GPU $0” is not an all-project total |
| [Frozen v3 evaluation](frozen-v3-evaluation-results.md) | Lambda conservative compute estimate ≈ $2.03 | Lambda $12.4362 pending invoice; instance provider-confirmed terminated |
| [Paired live evaluation](live-paired-v3-results.md#spending-reservations-and-cleanup-receipts) | Lambda $2.943227 and Solari $0.088467 compute estimates | Lambda $11.844 and new Solari $1.384667 reservations pending; confirmed cleanup does not reconcile billing |
| [Stopped magnification, September 7](document-magnification-results.md#resources-spending-cleanup) | Solari compute estimate $0.132140; no Lambda launch or model calls | New Solari $1.384667 reserved; cumulative Solari accounted upper **$8.308000**, not compute spend or invoice |

The September 6–7 evaluation GPU instances were provider-confirmed terminated;
the magnification session launched none. Session-owned Solari machines were
cleaned up according to those dated receipts; the golden and pre-existing
filesystems were preserved. This is not a fresh provider-inventory check.
Snapshot/filesystem retention can still incur storage charges.

## September 22–23 session

These are usage-derived estimates, not invoices.

| Service | Item | Estimate |
| --- | --- | ---: |
| Lambda | H100 PCIe, 22:39–06:55 HST (8.3 h at $3.29/h): v4-notes training (7.2 h) and the base/v3/v4 offline evaluations | ≈ $27.20 |
| Lambda | A100 SXM4, 02:52–05:32 HST (2.7 h at $1.99/h): serving v3 for the live comparison | ≈ $5.30 |
| Solari | Idle-timeout probe: one desktop for 16.5 min, outside the ledger | ≈ $0.04 |
| Solari | Live comparison: 20 fork desktops, ~15 min each | ≈ $0.70 compute; ledger upper bound $2.46 |
| OpenAI / Anthropic | none | $0 |

Both Lambda instances were terminated by API and confirmed; each also had a local
terminate-at-deadline watchdog. No Solari machines remained (`reap --dry-run`: 0 selected;
account inventory: 0 machines). Two Lambda filesystems (about 18.5 GB each) and five Solari
snapshots (42.7 GB) remained at the end of that session; both were pruned on September 23 (below).

## September 23 image-detail session

Usage-derived estimates, not invoices. Two session ledgers, both local:
`runs/image-detail-live-20260923/` (Solari $6, OpenAI $25) and `runs/image-detail-live2-20260923/`
(Solari $5, OpenAI $23.79).

| Service | Item | Estimate |
| --- | --- | ---: |
| OpenAI | `gpt-6-luna`, run 1 (28 cells) plus the two-request compatibility check | $1.20 |
| OpenAI | `gpt-6-luna`, run 2 (33 cells, including retries and one unplanned cell) | $1.00, plus ≤ $0.53 kept reserved for two failed requests |
| Solari | Run 1: 28 fork desktops, 4.15 recorded hours | ≈ $0.56 compute; ledger upper bound $3.69 (with 2 smoke desktops) |
| Solari | Run 2: 33 fork desktops, 4.03 recorded hours, plus two desktops orphaned by a controller sleep (~34 min each) | ≈ $0.54 + $0.15 compute; ledger upper bound $3.52 |
| Solari | Upstream example and SDK checks: 6 short desktops outside the ledger, including one orphaned for 16 s by the broken `solari-sandbox` 0.2.2 | ≈ $0.02 |
| Lambda / Anthropic | none | $0 |

Afterwards the account listed 0 machines and a single snapshot, `snap_dlft9omnpkyw` (9.6 GB, inside
the 10 GB free tier). The owner deleted the four older snapshots (33.1 GB) and both Lambda
filesystems (37 GB) on 2026-09-23; a read-only listing afterwards showed no Lambda instances or
filesystems.

## Guard and accounting boundaries

**Public-release continuation.** The new authorization is a $100 total ceiling,
not a spending target. New Solari allocations are disabled across all ledgers
because no verified hard lifetime bound was found; the historical five-hour
reservation formula is no longer used. Doctor reports the capability hold and
hourly prices without claiming a finite total. This continuation made no model,
Solari allocation or GPU calls. Read-only provider metadata found no
Forkloop-tagged desktops or sandboxes. Prior uncertain charges remain untouched.

**September 15 recovery update.** The same session ledger retains OpenAI
**$0.82271164** in usage-priced responses and **$0.53237280** for one uncertain
request, totaling **$1.35508444** accounted. No further model requests or GPU
allocations were made during recovery.

Solari's original three reservations totaled **$2.077**. Two desktops were still
reported running after approximately 10.25 and 10.01 hours and were explicitly
killed. Their full elapsed-time compute estimates exceeded the original assumed
bounds. Accounting now retains **$3.40627836** pending, without inventing an
invoice or releasing the build reservation. The service is blocked in that ledger
even though its authorized $20 ceiling has not been reached. This marked amount
is retained exposure, not a newly established guaranteed maximum.

The SDK timeout is an **idle** timeout, reset by activity and open connections;
it is not a hard deadline. The [pricing page](https://docs.getsolari.com/pricing)
still lists five hours for Starter, but the observed running resources mean this
cannot be treated as verified enforcement. [Provider idle semantics](https://docs.getsolari.com/sandboxes)
were read during recovery. Resolve the lifetime discrepancy before further paid
experiments; do not create a replacement ledger to bypass the hold.

The historical guard description below records the assumption that was used.
The current implementation also retains larger observed exposure and refuses new
reservations for a service with an observed bound violation.

- `forkloop ledger PATH --create --solari-usd 10` creates a persistent reservation
  ledger; the README uses a fixed session path. Never recreate it to clear spend.
  Its summary is not an account balance or invoice.
- Solari create reserves the verified Starter hourly shape rate for **5 h 10 m**,
  despite requesting a ≤30-minute kill-on-idle timeout. It refuses other plans,
  recording and longer requested idle windows. The built-in pricing review
  expires **2026-10-01**; explicit pricing renewal does not clear an observed
  bound violation or provide an absolute provider lifetime guarantee.
- Student-policy reservations cover only exact host **`api.openai.com`** and
  **`gpt-5.6-luna`**, with bounded output/candidate counts. Arbitrary compatible
  endpoints, Anthropic and GPU rental are **not guarded by that path**. They need
  separate authorization, price bounds and shutdown controls; a local URL can
  proxy a paid provider. The CLI ledger is not a universal spending cap.
- Returned token counts are authoritative usage. Multiplying by published rates
  is priced usage, **not an invoice**. A ledger field called `actual_usd` does not
  change that. Solari `actual_usd: 0` can mean unreconciled, not zero cost.
- Uncertain calls and confirmed machine kills do not justify releasing pending
  reserves. Keep them until authoritative billing reconciliation. Include losing
  branches, failed calls, setup, idle time and full resource lifetime; legacy
  artifacts may lack some of these. Do not add cumulative upper bounds to their
  constituent reservations, or add estimates to reserves as separate charges.
- Model results and cost records are not demand/adoption evidence. No savings
  against other frameworks or general price/performance advantage is established.
