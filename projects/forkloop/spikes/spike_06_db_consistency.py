"""Spike 6 — is MariaDB consistent after a snapshot taken under write load?

Question: OpenEMR lives on MariaDB inside the VM. If a snapshot is taken while
InnoDB is writing, does revert() land on a clean database, or does it need
crash recovery / show corruption? Does `FLUSH TABLES WITH READ LOCK` at
snapshot time make a difference?

Method: apt-get install mariadb-server; create spike.t; run a writer loop;
snapshot "hot" (writer running) and "locked" (writer blocked behind FLUSH
TABLES WITH READ LOCK); revert to each; wait for mysqld; stop the writer;
mysqlcheck; count rows; grep the error log for recovery messages.

Run:  SOLARI_API_KEY=... python spikes/spike_06_db_consistency.py
"""

from __future__ import annotations

import asyncio
import time

from _common import (
    base_parser, create_desktop, delete_snapshot_quietly, kill_quietly, log_result,
    print_table, reattach, run, sh, wait_ready,
)

INSTALL = """set -e
export DEBIAN_FRONTEND=noninteractive
{S}apt-get update -qq >/dev/null
{S}apt-get install -y -qq mariadb-server >/dev/null
{S}service mariadb start >/dev/null 2>&1 || {S}service mysql start >/dev/null 2>&1 || ({S}mysqld_safe >/dev/null 2>&1 &)
for i in $(seq 1 60); do {S}mysqladmin ping >/dev/null 2>&1 && break; sleep 1; done
{S}mysql -e "CREATE DATABASE IF NOT EXISTS spike; CREATE TABLE IF NOT EXISTS spike.t (id INT AUTO_INCREMENT PRIMARY KEY, v DOUBLE, ts DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)) ENGINE=InnoDB"
{S}mysql -N -e "SELECT VERSION()"
"""
WRITER = "while true; do echo 'INSERT INTO spike.t (v) VALUES (RAND());'; sleep 0.005; done | {S}mysql spike"
LOCKER = "printf 'FLUSH TABLES WITH READ LOCK; SELECT SLEEP(300);\\n' | {S}mysql"
STOP = "pkill -f 'INSERT INTO spike'; pkill -f 'SELECT SLEEP(300)'; true"


async def count(d, S) -> int:
    r = await sh(d, f"{S}mysql -N -e 'SELECT COUNT(*) FROM spike.t'")
    return int(r.stdout.strip() or -1)


async def wait_db(d, S, timeout_s=90.0) -> float:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        if (await sh(d, f"{S}mysqladmin ping >/dev/null 2>&1 && echo 1 || echo 0")).stdout.strip() == "1":
            return time.perf_counter() - t0
        await asyncio.sleep(1)
    return float("nan")


async def revert_and_check(d, S, snap: str, mode: str, rows_at_snapshot: int) -> dict:
    t0 = time.perf_counter()
    await d.revert(snap)
    ready_s = await reattach(d)
    # mysqld is part of the memory image, so it should already be answering.
    db_s = await wait_db(d, S)
    writer_alive = (await sh(d, "pgrep -f 'INSERT INTO spike' >/dev/null && echo 1 || echo 0")).stdout.strip() == "1"
    await sh(d, STOP)
    rows = await count(d, S)
    chk = await sh(d, f"{S}mysqlcheck --check spike t")
    check_ok = chk.exitCode == 0 and "OK" in chk.stdout and "error" not in chk.stdout.lower()
    recov = (await sh(d, "grep -ci 'recovery\\|crash' /var/log/mysql/error.log 2>/dev/null || echo 0")).stdout.strip()
    return {"mode": mode, "ready_s": ready_s, "db_answer_s": db_s, "writer_alive": writer_alive,
            "rows": rows, "rows_at_snapshot": rows_at_snapshot, "mysqlcheck_ok": check_ok,
            "recovery_log_lines": int(recov or 0), "total_s": time.perf_counter() - t0,
            "mysqlcheck": (chk.stdout + chk.stderr).strip().replace("\n", " | ")[:120]}


async def main(args, client) -> None:
    d = None
    snaps: dict[str, str] = {}
    try:
        d = await create_desktop(client, timeout_min=args.timeout_min)
        print("desktop:", d.id)
        print(f"ready in {await wait_ready(d):.1f}s")
        S = "" if (await sh(d, "id -u")).stdout.strip() == "0" else "sudo -n "
        t0 = time.perf_counter()
        r = await sh(d, INSTALL.format(S=S))
        if r.exitCode != 0:
            print("install failed:", r.stderr[-800:])
            log_result(6, "error", "mariadb install failed: " + r.stderr[-200:], "text")
            return
        print(f"mariadb {r.stdout.strip()} installed in {time.perf_counter() - t0:.0f}s")

        await d.process.start("sh", args=["-c", WRITER.format(S=S)])
        await asyncio.sleep(3)
        c1, c2 = await count(d, S), await count(d, S)
        print(f"writer running: {c1} → {c2} rows")

        snaps["hot"] = await d.snapshot("spike06-hot")
        rows_hot = await count(d, S)
        print(f"hot snapshot {snaps['hot']} at ~{rows_hot} rows")

        await d.process.start("sh", args=["-c", LOCKER.format(S=S)])
        await asyncio.sleep(3)
        l1 = await count(d, S)
        await asyncio.sleep(1)
        l2 = await count(d, S)
        print(f"read lock held: rows {l1} → {l2} (writer {'blocked' if l1 == l2 else 'NOT blocked'})")
        snaps["locked"] = await d.snapshot("spike06-locked")
        await sh(d, "pkill -f 'SELECT SLEEP(300)'; true")

        results = [await revert_and_check(d, S, snaps["hot"], "hot", rows_hot),
                   await revert_and_check(d, S, snaps["locked"], "locked", l2)]
        keys = ["mode", "ready_s", "db_answer_s", "writer_alive", "rows", "rows_at_snapshot",
                "mysqlcheck_ok", "recovery_log_lines", "total_s"]
        print_table(keys, [[x[k] for k in keys] for x in results])
        for x in results:
            print(f"{x['mode']}: mysqlcheck → {x['mysqlcheck']}")
            log_result(6, f"{x['mode']}_mysqlcheck_ok", x["mysqlcheck_ok"], "bool", x["mysqlcheck"])
            log_result(6, f"{x['mode']}_rows_after_revert", x["rows"], "count", f"at snapshot ~{x['rows_at_snapshot']}")
            log_result(6, f"{x['mode']}_db_answer_s", round(x["db_answer_s"], 3), "s", "mysqladmin ping after reattach")
            log_result(6, f"{x['mode']}_recovery_log_lines", x["recovery_log_lines"], "count", "grep recovery|crash error.log")
        log_result(6, "lock_needed", not results[0]["mysqlcheck_ok"] and results[1]["mysqlcheck_ok"], "bool",
                   "True → golden snapshot must be taken under FLUSH TABLES WITH READ LOCK")
    finally:
        await kill_quietly(d)
        for s in snaps.values():
            await delete_snapshot_quietly(client, s)


if __name__ == "__main__":
    p = base_parser(__doc__)
    p.set_defaults(timeout_min=30)
    run(6, p, main)
