# Solari desktop idle timeout: measured, 2026-09-23

**Result: a desktop created with a 5-minute kill-on-idle timeout and no client activity was
never idle-killed. Its idle deadline renewed itself at each expiry, three times in a row,
until the operator killed it 16.5 minutes after creation.** This explains the two desktops
seen still running about ten hours after creation on September 15. The idle timeout is not
a lifetime bound, so Forkloop now enforces its own (see
[contracts §15](contracts.md#15-session-recovery-and-current-spending-bounds)).

## Method

`runs/lifetime-probe-20260923/probe.py` (local) did the following:

- created one desktop from the claims-ops golden snapshot (`snap_dlft9omnpkyw`, Starter, 2 vCPU / 4 GB) with `timeout_ms=300000` and `lifecycle={"onTimeout": "kill"}`;
- closed the control channel immediately;
- then observed it only through the account-wide list endpoint, `GET /sandboxes?metadata.forkloop_probe=<tag>`, once a minute.

It never used a GET-by-ID call, which the API documents as activity, and it opened no stream,
command or connection. The list call does not count as activity: `expiresAt` stayed fixed
between list calls within each window.

## Timeline (UTC)

| Time | Observation | `expiresAt` |
| --- | --- | --- |
| 12:51:31 | created (40.6 s), channel closed | — |
| 12:52:31 → 12:56:32 | running (5 observations) | 12:57:27.647 |
| 12:57:35 | running | **13:02:25.949** (renewed at expiry) |
| 13:02:34 | running | **13:07:31.097** (renewed at expiry) |
| 13:07:35 | running | **13:12:28.645** (renewed at expiry) |
| 13:08:04 | killed by the operator | — |

Each renewal happened within a few seconds of the previous `expiresAt` and pushed it about
five minutes forward. The most likely cause is something inside the desktop, such as its
agent or display stream, counting as activity. We cannot see which from outside.

## What Forkloop does about it

- Creates refuse unless `FORKLOOP_SOLARI_MAX_LIFETIME_MIN` and `FORKLOOP_SOLARI_ACCEPT_BALANCE_BOUND=1` are set.
- The backend kills each machine at its hard deadline in-process.
- `forkloop reap --older-than-min N` does the same from a second process.
- Live comparisons use `reset_mode: fork`, so no machine outlives one cell.
- The prepaid balance is the acknowledged last-resort cap.

Cost of the probe: one Starter desktop for 16.5 minutes, about $0.04.
