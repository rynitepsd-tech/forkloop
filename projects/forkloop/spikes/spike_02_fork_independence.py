"""Spike 2 — fork independence.

Question: does create_desktop(from_snapshot=...) give N *independent* copies
of one snapshot (separate disks), and how long does a fork take to be ready?

Method: build a base desktop, snapshot it, kill the base (Starter allows 2
live VMs and the base would count), create --forks desktops from the
snapshot, write a file in fork A, confirm it is absent in fork B, and compare
a sha256 of a home-dir listing before and after the write.

Run:  SOLARI_API_KEY=... python spikes/spike_02_fork_independence.py [--forks 2]
"""

from __future__ import annotations

import time

from solari_core.errors import ConcurrencyLimitError, NoCapacityError

from _common import (
    base_parser, create_desktop, delete_snapshot_quietly, explain, kill_quietly,
    log_result, print_table, run, sh, wait_ready,
)

LISTING = "ls -la --time-style=+ ~ | sha256sum | cut -c1-16"
PROBE = "test -f ~/spike02_only_in_a.txt && echo yes || echo no"


async def main(args, client) -> None:
    base = None
    forks = []
    snap = None
    try:
        base = await create_desktop(client, timeout_min=args.timeout_min)
        print("base:", base.id)
        print(f"base ready in {await wait_ready(base):.1f}s")
        await sh(base, "echo base > ~/spike02_origin.txt")
        t0 = time.perf_counter()
        snap = await base.snapshot("spike02-golden")
        print(f"snapshot {snap} in {time.perf_counter() - t0:.1f}s")

        # The snapshot outlives the VM. Kill the base so the forks fit under the
        # concurrency cap (Free 1, Starter 2, Pro 10).
        await base.kill()
        base = None

        ready_s = []
        for k in range(args.forks):
            try:
                t0 = time.perf_counter()
                f = await create_desktop(client, from_snapshot=snap, timeout_min=args.timeout_min,
                                         extra_meta={"fork": str(k)})
                forks.append(f)
                ready_s.append(await wait_ready(f))
                print(f"fork {k} {f.id} ready in {time.perf_counter() - t0:.1f}s "
                      f"(create→health {ready_s[-1]:.1f}s)")
            except (ConcurrencyLimitError, NoCapacityError) as exc:
                print(f"fork {k} not created: {explain(exc)}")
                break
        log_result(2, "fork_count", len(forks), "count", f"requested {args.forks}")
        for k, t in enumerate(ready_s):
            log_result(2, "fork_ready_s", round(t, 3), "s", f"fork {k}")
        if len(forks) < 2:
            print("need 2 forks to test independence; got", len(forks))
            return

        a, b = forks[0], forks[1]
        before = [(await sh(f, LISTING)).stdout.strip() for f in forks]
        await sh(a, "echo 'written in fork A' > ~/spike02_only_in_a.txt")
        seen = [(await sh(f, PROBE)).stdout.strip() for f in forks]
        after = [(await sh(f, LISTING)).stdout.strip() for f in forks]
        origin = [(await sh(f, "cat ~/spike02_origin.txt 2>/dev/null")).stdout.strip() for f in forks]

        print_table(
            ["fork", "id", "ready_s", "origin_file", "listing_before", "listing_after", "has_A_file"],
            [[k, f.id, ready_s[k], origin[k], before[k], after[k], seen[k]] for k, f in enumerate(forks)],
        )
        same_start = len(set(before)) == 1 and all(o == "base" for o in origin)
        independent = seen[0] == "yes" and seen[1] == "no" and after[0] != after[1]
        print(f"same_start={same_start}  independent={independent}")
        log_result(2, "forks_same_start", same_start, "bool", "identical listing hash + origin file on every fork")
        log_result(2, "fork_independent", independent, "bool", "file written in A absent in B, listing hashes differ")
    finally:
        for f in forks:
            await kill_quietly(f)
        await kill_quietly(base)
        await delete_snapshot_quietly(client, snap)  # refused while any fork is alive; that's why forks go first


if __name__ == "__main__":
    p = base_parser(__doc__)
    p.add_argument("--forks", type=int, default=2, help="forks to create (Starter cap is 2 live VMs)")
    run(2, p, main)
