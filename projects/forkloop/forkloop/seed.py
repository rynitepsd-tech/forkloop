"""PRIVILEGED per-episode seeding over the controller channel (§11 step 2).

Nothing here is reachable by the agent. The expected-state manifest stays on
the controller; only the seeding side effects enter the VM.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .dbaccess import DbAccess
from .tasks import Seeding

if TYPE_CHECKING:  # pragma: no cover
    from .backends.base import Machine


class SeedError(RuntimeError):
    pass


@dataclass
class SeedReport:
    seconds: float
    files: int = 0
    portal_statements: int = 0
    openemr_statements: int = 0
    commands: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"seconds": round(self.seconds, 4), "files": self.files, "portal_statements": self.portal_statements,
                "openemr_statements": self.openemr_statements, "commands": self.commands}


def _count_statements(sql: str) -> int:
    return sum(1 for s in sql.split(";") if s.strip() and not s.strip().startswith("--"))


async def apply_seeding(machine: "Machine", dbs: dict[str, DbAccess], seeding: Seeding) -> SeedReport:
    t0 = time.monotonic()
    rep = SeedReport(seconds=0.0)
    for f in seeding.files:
        await machine.write_file(f.path, f.content, f.mode)
        rep.files += 1
    if seeding.portal_sql.strip():
        if "portal" not in dbs:
            raise SeedError("task seeds the portal but the world has no 'portal' database")
        await dbs["portal"].execute_script(seeding.portal_sql, tmp_path="/tmp/forkloop_seed_portal.sql")
        rep.portal_statements = _count_statements(seeding.portal_sql)
    if seeding.openemr_sql.strip():
        if "openemr" not in dbs:
            raise SeedError("task seeds openemr but the world has no 'openemr' database")
        await dbs["openemr"].execute_script(seeding.openemr_sql, tmp_path="/tmp/forkloop_seed_openemr.sql")
        rep.openemr_statements = _count_statements(seeding.openemr_sql)
    for db_name, script in seeding.extra_sql.items():
        if not script.strip():
            continue
        if db_name not in dbs:
            raise SeedError(f"task seeds database {db_name!r} but the world has no such database")
        await dbs[db_name].execute_script(script, tmp_path=f"/tmp/forkloop_seed_{db_name}.sql")
        rep.commands.append({"extra_sql": db_name, "statements": _count_statements(script)})
    for argv in seeding.post_commands:
        if not argv:
            continue
        r = await machine.exec(argv[0], list(argv[1:]), timeout_ms=60_000)
        rep.commands.append({"argv": argv, "exit_code": r.exit_code, "stderr": r.stderr[-300:]})
        if r.exit_code != 0:
            raise SeedError(f"post command failed ({r.exit_code}): {' '.join(argv)} :: {r.stderr[-300:]}")
    rep.seconds = time.monotonic() - t0
    return rep


__all__ = ["apply_seeding", "SeedReport", "SeedError"]
