"""Snapshot, revert, fork — reset a desktop in seconds instead of rebuilding it.

`snapshot()` freezes the whole VM (disk *and* memory: open windows, running
processes). `revert()` puts the same machine back to that instant, and
`create_desktop(from_snapshot=...)` boots an independent copy of it. One
hand-built desktop becomes as many identical starting points as you need —
the reset() primitive of an RL environment.
"""

import asyncio
import os
import pathlib
import time

from solari_sandbox import SandboxClient

BASE_URL = "https://api.getsolari.com"
SIZE = dict(template="default", resolution="1280x720", cpu=2, mem_mb=4096)


async def wait_ready(desktop) -> None:
    # Wait for X11 (and, after a revert, for the guest to take our connection
    # again). The guest accepts a control connection only briefly after a
    # restore, so connect() is retried here rather than called once.
    for _ in range(60):
        try:
            if not desktop.connected:
                await desktop.connect()
            if (await desktop.health()).ready:
                return
        except Exception:
            pass
        await asyncio.sleep(1)
    raise TimeoutError(f"{desktop.id} never became ready")


async def main() -> None:
    # Snapshot-capable desktops come from SandboxClient.create_desktop (the
    # unified /sandboxes route, kind="desktop"). DesktopClient.create in
    # solari-desktop has no from_snapshot parameter, so the fork step below
    # is not possible with it.
    async with SandboxClient(
        api_key=os.environ["SOLARI_API_KEY"],
        base_url=BASE_URL,
    ) as client:
        desktop = await client.create_desktop(
            **SIZE,
            timeout_ms=10 * 60_000,
            lifecycle={"onTimeout": "kill"},
        )
        print("desktop:", desktop.id)
        print("watch  :", desktop.streamUrl)
        fork = None
        snapshot_id = None

        try:
            await wait_ready(desktop)

            await desktop.open("mousepad")
            await asyncio.sleep(4)
            # (320, 300) is inside mousepad's text area; screen-centre is not.
            await desktop.mouse.click(320, 300)
            await desktop.keyboard.type("state A")
            await asyncio.sleep(1)

            # Captures memory + disk of the running VM. Returns a snapshot id
            # that outlives this desktop (delete it below when done).
            snapshot_id = await desktop.snapshot("mousepad-state-a")
            print("snapshot:", snapshot_id)

            await desktop.keyboard.type(" -- then more typing that should vanish")
            await asyncio.sleep(1)

            t0 = time.perf_counter()
            await desktop.revert(snapshot_id)
            # The guest resumes from the memory image and the old control
            # socket is dead, even though the handle may still say connected
            # (which makes reconnect() a no-op). Drop it and dial again.
            await desktop.close()
            await wait_ready(desktop)
            print(f"revert : {time.perf_counter() - t0:.1f}s until healthy again")

            shot = await desktop.screenshot(format="png")
            out = pathlib.Path("after_revert.png")
            out.write_bytes(shot)
            print(f"screenshot after revert: {out} (should show only 'state A')")

            # Fork: a second, independent VM booted from the same snapshot.
            # Original + fork = 2 live VMs, which is exactly the Starter cap;
            # a third would raise ConcurrencyLimitError (429).
            fork = await client.create_desktop(
                **SIZE,
                from_snapshot=snapshot_id,
                timeout_ms=10 * 60_000,
                lifecycle={"onTimeout": "kill"},
            )
            print("fork   :", fork.id)
            await wait_ready(fork)

            await fork.files.write("/home/user/only-in-fork.txt", "hello from the fork")
            # commands.run takes argv, not a shell line — no quoting needed.
            in_fork = await fork.commands.run("test", args=["-f", "/home/user/only-in-fork.txt"])
            in_orig = await desktop.commands.run("test", args=["-f", "/home/user/only-in-fork.txt"])
            print("file in fork    :", in_fork.exitCode == 0)   # True
            print("file in original:", in_orig.exitCode == 0)   # False — separate disks
        finally:
            # close() only drops the local channel; kill() destroys the VM.
            # Kill the fork first: a snapshot can't be deleted while a VM
            # created from it is alive.
            if fork is not None:
                await fork.kill()
            await desktop.kill()
            if snapshot_id is not None:
                await client.delete_snapshot(snapshot_id)


if __name__ == "__main__":
    asyncio.run(main())
