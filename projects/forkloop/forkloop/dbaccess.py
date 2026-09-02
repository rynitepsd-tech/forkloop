"""Controller-side database access through a machine's exec channel.

The controller never opens a network connection to the VM's databases; it
runs the ``sqlite3`` / ``mysql`` CLIs (or a python3 one-liner) inside the VM
over the control channel and parses the output. On the fake backend the same
commands run locally against files under the fake machine root.
"""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING, Any, Optional, Sequence

from .oracle import row_hash
from .util.sql import ident, substitute

if TYPE_CHECKING:  # pragma: no cover
    from .backends.base import Machine


class DbError(RuntimeError):
    pass


_PY_HASH_SCRIPT = r'''
import sqlite3, sys, json, hashlib
path, tables, pks = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
ignore = set(json.loads(sys.argv[4])) if len(sys.argv) > 4 else set()
con = sqlite3.connect(path)
out = {}
for t in tables:
    pk = pks.get(t, "id")
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})") if r[1] not in ignore or r[1] == pk]
    if not cols:
        out[t] = {}
        continue
    rows = {}
    for r in con.execute(f"SELECT {pk}, {', '.join(cols)} FROM {t} ORDER BY {pk}"):
        rows[str(r[0])] = hashlib.md5(json.dumps(list(r[1:]), default=str, ensure_ascii=False).encode()).hexdigest()
    out[t] = rows
print(json.dumps(out))
'''

_PY_QUERY_SCRIPT = r'''
import sqlite3, sys, json
path, sql = sys.argv[1], sys.argv[2]
con = sqlite3.connect(path)
con.row_factory = sqlite3.Row
cur = con.execute(sql)
rows = [dict(r) for r in cur.fetchall()]
print(json.dumps(rows, default=str))
'''

_PY_EXEC_SCRIPT = r"""
import sqlite3, sys
path, script_path = sys.argv[1], sys.argv[2]
sql = open(script_path, encoding="utf-8").read()

def split(sql):
    out, buf, q, i = [], [], False, 0
    while i < len(sql):
        c = sql[i]
        if q:
            buf.append(c)
            if c == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    buf.append("'"); i += 1
                else:
                    q = False
        elif c == "'":
            q = True; buf.append(c)
        elif c == ";":
            s = "".join(buf).strip()
            if s: out.append(s)
            buf = []
        elif c == "-" and sql[i:i + 2] == "--":
            j = sql.find("\n", i)
            i = len(sql) if j < 0 else j
            continue
        else:
            buf.append(c)
        i += 1
    s = "".join(buf).strip()
    if s: out.append(s)
    return out

con = sqlite3.connect(path, isolation_level=None)
con.execute("BEGIN")
try:
    for s in split(sql):
        con.execute(s)
    con.execute("COMMIT")
except Exception as e:
    con.execute("ROLLBACK")
    print("ERROR: %s" % e, file=sys.stderr)
    sys.exit(1)
"""


class DbAccess:
    """One database inside one machine.

    :param dialect: ``sqlite`` or ``mysql``.
    :param path: sqlite file path (VM path).
    :param database/user/password_file: mysql connection (password read inside the VM).
    """

    def __init__(self, machine: "Machine", dialect: str, *, path: Optional[str] = None,
                 database: Optional[str] = None, user: Optional[str] = None,
                 password_file: Optional[str] = None, name: str = "") -> None:
        if dialect not in ("sqlite", "mysql"):
            raise ValueError(f"unsupported dialect {dialect!r}")
        self.machine = machine
        self.dialect = dialect
        self.path = path
        self.database = database
        self.user = user
        self.password_file = password_file
        self.name = name
        if dialect == "sqlite" and not path:
            raise ValueError("sqlite DbAccess requires path")
        if dialect == "mysql" and not (database and user and password_file):
            raise ValueError("mysql DbAccess requires database, user, password_file")

    # ----------------------------------------------------------------- basics
    def _mysql_prefix(self) -> str:
        return (f'mysql --batch --raw -u {shlex.quote(self.user or "")} '
                f'--password="$(cat {shlex.quote(self.password_file or "")})" {shlex.quote(self.database or "")}')

    async def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        full = substitute(sql, params)
        if self.dialect == "sqlite":
            r = await self.machine.exec("python3", ["-c", _PY_QUERY_SCRIPT, self.path or "", full], timeout_ms=60_000)
            if r.exit_code != 0:
                raise DbError(f"sqlite query failed: {r.stderr.strip()[:500]} :: {full[:200]}")
            return json.loads(r.stdout or "[]")
        cmd = f"{self._mysql_prefix()} -e {shlex.quote(full)}"
        r = await self.machine.exec("sh", ["-c", cmd], timeout_ms=60_000)
        if r.exit_code != 0:
            raise DbError(f"mysql query failed: {r.stderr.strip()[:500]} :: {full[:200]}")
        return _parse_tsv(r.stdout)

    async def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        rows = await self.query(sql, params)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    async def execute_script(self, sql_text: str, *, tmp_path: str = "/tmp/forkloop_seed.sql") -> None:
        """Run a multi-statement script inside one transaction."""
        if not sql_text.strip():
            return
        await self.machine.write_file(tmp_path, sql_text)
        if self.dialect == "sqlite":
            r = await self.machine.exec("python3", ["-c", _PY_EXEC_SCRIPT, self.path or "", tmp_path], timeout_ms=120_000)
        else:
            wrapped = "/tmp/forkloop_seed_tx.sql"
            await self.machine.write_file(wrapped, "START TRANSACTION;\n" + sql_text + "\nCOMMIT;\n")
            r = await self.machine.exec("sh", ["-c", f"{self._mysql_prefix()} < {shlex.quote(wrapped)}"], timeout_ms=120_000)
        if r.exit_code != 0:
            raise DbError(f"{self.dialect} script failed: {r.stderr.strip()[:800]}")

    async def ping(self) -> bool:
        try:
            return _norm_int(await self.scalar("SELECT 1 AS one")) == 1
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------- checksums
    async def tables(self) -> list[str]:
        if self.dialect == "sqlite":
            rows = await self.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            return [r["name"] for r in rows]
        rows = await self.query("SELECT table_name AS name FROM information_schema.tables WHERE table_schema = ? ORDER BY table_name",
                                [self.database])
        return [r["name"] for r in rows]

    async def columns(self, table: str) -> list[str]:
        ident(table)
        if self.dialect == "sqlite":
            rows = await self.query(f"PRAGMA table_info({table})")
            return [r["name"] for r in rows]
        rows = await self.query("SELECT column_name AS name FROM information_schema.columns WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
                                [self.database, table])
        return [r["name"] for r in rows]

    async def max_pk(self, table: str, pk: str = "id") -> int:
        v = await self.scalar(f"SELECT MAX({ident(pk)}) AS m FROM {ident(table)}")
        return _norm_int(v) or 0

    async def row_hashes(self, tables: Sequence[str], primary_keys: dict[str, str],
                         ignore_columns: Optional[Sequence[str]] = None) -> dict[str, dict[str, str]]:
        """``{table: {pk: md5}}`` for every table listed.

        ``ignore_columns`` names columns the application maintains on its own
        (OpenEMR backfills ``uuid`` the first time a row is displayed); they are
        left out of every row hash so that merely viewing a record is not an edit.
        """
        for t in tables:
            ident(t)
        ignored = {c for c in (ignore_columns or []) if ident(c)}
        pks = {t: primary_keys.get(f"{self.name}.{t}", primary_keys.get(t, "id")) for t in tables}
        if self.dialect == "sqlite":
            r = await self.machine.exec("python3", ["-c", _PY_HASH_SCRIPT, self.path or "", json.dumps(list(tables)), json.dumps(pks),
                                                    json.dumps(sorted(ignored))], timeout_ms=120_000)
            if r.exit_code != 0:
                raise DbError(f"sqlite hash failed: {r.stderr.strip()[:500]}")
            return json.loads(r.stdout or "{}")
        # mysql: one information_schema query for columns, then one UNION ALL query with MD5 in SQL.
        col_rows = await self.query(
            "SELECT table_name AS t, column_name AS c FROM information_schema.columns WHERE table_schema = ? "
            "ORDER BY table_name, ordinal_position", [self.database])
        cols: dict[str, list[str]] = {}
        for r in col_rows:
            if r["c"] in ignored and r["c"] != pks.get(r["t"]):
                continue
            cols.setdefault(r["t"], []).append(r["c"])
        selects = []
        for t in tables:
            cs = cols.get(t)
            if not cs:
                continue
            pk = pks[t]
            concat = ", ".join(f"IFNULL(CAST({c} AS CHAR), '<NULL>')" for c in cs)
            selects.append(f"SELECT '{t}' AS t, CAST({pk} AS CHAR) AS pk, MD5(CONCAT_WS('|', {concat})) AS h FROM {t}")
        out: dict[str, dict[str, str]] = {t: {} for t in tables}
        if not selects:
            return out
        rows = await self.query(" UNION ALL ".join(selects))
        for r in rows:
            out.setdefault(r["t"], {})[str(r["pk"])] = r["h"]
        return out


def _norm_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _parse_tsv(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        if not line:
            continue
        vals = [_unescape(v) for v in line.split("\t")]
        rows.append({h: (None if v == "NULL" else v) for h, v in zip(header, vals)})
    return rows


def _unescape(v: str) -> str:
    return v.replace("\\t", "\t").replace("\\n", "\n").replace("\\\\", "\\")


__all__ = ["DbAccess", "DbError", "row_hash"]
