# Operations: resources, leases, accounting and cleanup

How Forkloop keeps billable resources bounded and every charge visible while spending is
authorized without a monetary cap. Nothing here relies on a provider's idle timeout.

## Ownership: the resource registry

`~/.forkloop/resources.jsonl` (or `$FORKLOOP_REGISTRY`) is an append-only log with one row per state
change of every billable resource Forkloop creates: Lambda instances and filesystems, Solari
machines and snapshots. A row is written **before** the create request is sent, with a stable id,
a purpose, an owner and a lease; the provider id is added when the create returns.

```bash
forkloop ops inventory --provider     # registry state + live provider listings side by side
forkloop ops renew RID --hours 6      # the owning job extends its lease while healthy
forkloop ops retain RID --reason "…"  # keep past the lease, with a stated reason
forkloop ops reap [--dry-run]         # terminate/delete resources whose lease expired (+10 min)
```

- **Ambiguous creates.** A Lambda launch that times out or returns 5xx is reconciled by its unique
  instance name against the provider list before any retry, so a timeout cannot create a
  duplicate GPU (`forkloop/ops/lambda_cloud.py`, tested in `tests/test_ops_and_failures.py`).
- **Out-of-process reaper.** A macOS LaunchAgent (`com.forkloop.reaper`) runs `forkloop ops reap`
  every 10 minutes on the controller. Remove it with
  `launchctl bootout gui/$(id -u)/com.forkloop.reaper`.
- **Lifetimes.** Lambda instances have no idle timeout: their lease is the bound, renewed by the
  operator while a job needs the box. Solari machines are additionally killed in-process at
  `FORKLOOP_SOLARI_MAX_LIFETIME_MIN` and by `forkloop reap --older-than-min` from a second process;
  the Starter plan's 5-hour maximum session is the provider-side bound.

## Machines of a dead runner

Every world machine carries its runner's id (`runner` tag). Runners heartbeat under
`<store>/runners/`. `forkloop reap-machines --config P` kills machines whose runner stopped
heartbeating; a new runner marks that runner's `running` rows `interrupted` (kept, never scored).

## Snapshots and storage

Solari bills retained snapshot storage ($0.05/GB-month beyond 10 GB per organization from
2026-10-01; each desktop snapshot reports ~9.6 GB). Policy:

- every snapshot is registered with a purpose and a 24 h lease before it is requested;
- `forkloop cleanup --config P` deletes snapshots behind checkpoints no repair still needs
  (retrying the provider's intermittent "Not found"), marking the checkpoint row deleted;
- snapshots kept as evidence are `retain`ed with a reason and listed in the final inventory.

Docker `commit` checkpoints are local images; `forkloop cleanup` removes them the same way.

## Accounting

- **Provider spend.** Paid calls reserve their worst case in a session ledger
  (`forkloop ledger PATH --create --uncapped openai,solari,gpu --authorization "…"`) and reconcile
  with provider usage. "Uncapped" lifts only the ceiling check: every operation is still reserved,
  reconciled and reported, and the authorization statement is stored in the ledger.
- **Experiment accounting.** The correction store keeps append-only `charges` (model tokens and
  USD, attempt/branch wall time, checkpoint and snapshot seconds, restores and replayed steps).
  Restoring a checkpoint rewinds the episode, never a charge.
- **Compute.** Lambda instance hours are the registry lease/terminate times × the listed hourly
  price; the invoice is authoritative and may differ.

## Durability of running programs

A program's SQLite store stays on the box's local disk (WAL does not work on NFS) and is copied to
the region's persistent filesystem every 5 minutes with the SQLite online-backup API
(`scripts/program_sync.sh`), together with attempt/branch directories and datasets. The
controller pulls durable copies to the Mac. A crash loses at most the last interval; resumption
re-plans cells from the store (finished cells are never re-run).
