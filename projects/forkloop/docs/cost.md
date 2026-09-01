# Cost ledger

Two kinds of numbers live here. **Verified** = read from
<https://docs.getsolari.com/pricing> on 2026-09-01. **Estimate** = a range
from the plan (§10) that nothing has confirmed yet. The "Actual spend" table
at the bottom is empty and gets a row per receipt during the build.

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

VMs (desktops) add **$0.02/h** for the live screen. Snapshot storage is **not
published**; the model carries it as `SNAPSHOT_STORAGE_USD_PER_GB_MONTH = 0`
until a number exists. Desktops require a paid plan (402 on Free).

## Formulas (`forkloop/bench/cost_model.py`)

```
vm_hour_cost(plan, vcpu, mem_gb, screen)  = compute[vcpu,mem_gb][plan] + (0.02 if screen)
vm_hours_per_credit(plan, ...)            = included_credits[plan] / vm_hour_cost
cost_per_1k_resets(method, plan, s, size, overhead)
                                          = 1000 · (s + overhead) / 3600 · vm_hour_cost   (0 for method="local")
episode_cost(...)                         = seconds/3600 · vm_hour_cost
                                          + (tokens_in · $/M_in + tokens_out · $/M_out) / 1e6
snapshot_storage_cost(gb, months, rate)   = gb · months · rate                              (rate defaults to 0)
```

Derived (verified inputs, exact arithmetic):

- Starter, 2 vCPU / 4 GB with screen: **$0.134/h** → **≈ 149 VM-hours per $20** of credit.
- Pro, same shape: $0.100/h.
- USD per 1,000 resets on Starter (2 vCPU / 4 GB, no overhead) — `seconds_per_reset` is what spikes 1–2 will measure:

| seconds/reset | 2 | 5 | 10 | 20 | 30 | 60 | 120 | 300 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| revert / from_snapshot / rebuild | 0.074 | 0.186 | 0.372 | 0.744 | 1.117 | 2.233 | 4.467 | 11.167 |
| local | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## All-in estimate (plan §10)

Every row except the Solari plan price is an **estimate**.

| Item | Low | High | Basis |
| --- | --- | --- | --- |
| Environment (Solari) | $0 (month-1 promo) / $20 | $180 | Starter $20 ≈ 149 VM-hours at 2 vCPU/4 GB + screen; high = Starter plus ~$160 of credit top-ups (≈ 1,200 more VM-hours), or Pro |
| Teacher API | $30 | $80 | teacher-policy tokens for SFT data + eval — estimate |
| GPU rental | $30 | $80 | LoRA fine-tune of the student — estimate |
| Local box | $0 | $10 | electricity / Docker baseline runs — estimate |
| Snapshot storage | $0 | $0 | price not published; $0 until known |
| **Total** | **≈ $80** | **≈ $350** | sum of the rows (with the $20 Starter month; $60 if the promo makes month 1 free) |

VM-hour sanity check against the plan: 149 VM-hours on Starter is, at a
measured 10 s reset + 60 s episode, roughly 7,600 episodes of environment
time on the included credits — before any top-up. That figure moves with the
spike-1 and spike-5 numbers; treat it as an estimate until they exist.

## Actual spend

Filled during the build. One row per charge; running total in USD.

| Date | Item | Amount | Running total |
| --- | --- | --- | --- |
| | | | |
