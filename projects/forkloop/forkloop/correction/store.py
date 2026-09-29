"""Durable state for the failure-to-correction workflow (docs/correction.md).

One SQLite file (WAL, fsync'd commits) per project. Rows are keyed by stable ids that are
assigned before any paid or stateful operation starts, so a crashed run can be resumed without
guessing what already happened:

* an attempt/branch row is written as ``running`` *before* the episode starts; a process that
  dies leaves it ``running``, and :meth:`Store.mark_interrupted` turns it into ``interrupted`` —
  never into a success, never deleted;
* cumulative charges (model calls, machine lifetime, snapshots, replays) are append-only rows in
  ``charges``; restoring a checkpoint rewinds the *episode*, never these rows;
* datasets are immutable: a manifest row stores the sha256 of the exported file.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY, world TEXT NOT NULL, family TEXT NOT NULL, split TEXT NOT NULL,
    seed INTEGER NOT NULL, manifest_sha256 TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS policies (
    policy_id TEXT PRIMARY KEY, role TEXT NOT NULL, identity_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
    policy_id TEXT NOT NULL REFERENCES policies(policy_id), experiment_id TEXT, cell TEXT,
    status TEXT NOT NULL, reward REAL, reason_code TEXT, n_steps INTEGER, run_dir TEXT NOT NULL,
    checkpoint_strategy TEXT, started_at REAL NOT NULL, finished_at REAL, info_json TEXT NOT NULL DEFAULT '{}',
    runner TEXT);
CREATE INDEX IF NOT EXISTS attempts_task ON attempts(task_id);
CREATE INDEX IF NOT EXISTS attempts_cell ON attempts(experiment_id, cell);
CREATE TABLE IF NOT EXISTS checkpoints (
    ckpt_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), step INTEGER NOT NULL,
    strategy TEXT NOT NULL, world_ref TEXT, status TEXT NOT NULL, digest_json TEXT NOT NULL,
    oracle_json TEXT NOT NULL, policy_state_ref TEXT NOT NULL, agent_state_json TEXT NOT NULL,
    elapsed_s REAL NOT NULL, budget_steps INTEGER NOT NULL, invalid INTEGER NOT NULL, history_json TEXT NOT NULL,
    cost_json TEXT NOT NULL, created_at REAL NOT NULL, retained INTEGER NOT NULL DEFAULT 0, deleted_at REAL,
    UNIQUE(attempt_id, step));
CREATE TABLE IF NOT EXISTS repairs (
    repair_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), mode TEXT NOT NULL,
    teacher_policy_id TEXT NOT NULL, config_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}',
    experiment_id TEXT, started_at REAL NOT NULL, finished_at REAL, runner TEXT);
CREATE TABLE IF NOT EXISTS branches (
    branch_id TEXT PRIMARY KEY, repair_id TEXT NOT NULL REFERENCES repairs(repair_id),
    ckpt_id TEXT NOT NULL REFERENCES checkpoints(ckpt_id), idx INTEGER NOT NULL, status TEXT NOT NULL,
    reward REAL, reason_code TEXT, n_steps INTEGER, run_dir TEXT NOT NULL, restore_json TEXT NOT NULL DEFAULT '{}',
    started_at REAL NOT NULL, finished_at REAL, info_json TEXT NOT NULL DEFAULT '{}', runner TEXT);
CREATE INDEX IF NOT EXISTS branches_repair ON branches(repair_id);
CREATE TABLE IF NOT EXISTS charges (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, amount REAL NOT NULL,
    unit TEXT NOT NULL, usd REAL, ref TEXT, experiment_id TEXT, evidence_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
    n_records INTEGER NOT NULL, manifest_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, ref TEXT, detail_json TEXT NOT NULL);
"""

#: Attempt/branch statuses. ``running`` rows found by a new process are interrupted, not failed.
FINISHED = "finished"
RUNNING = "running"
INTERRUPTED = "interrupted"
INFRA_ERROR = "infra_error"
RESTORE_FAILED = "restore_failed"


def _j(x: Any) -> str:
    return json.dumps(x, sort_keys=True, default=str, ensure_ascii=False)


def _runner() -> Optional[str]:
    try:
        from .runner import RUNNER_ID
        return RUNNER_ID
    except Exception:  # noqa: BLE001
        return None


def stable_id(prefix: str, *parts: Any, n: int = 12) -> str:
    """Deterministic id: the same logical object gets the same id in every process."""
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:n]
    return f"{prefix}-{h}"


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.root = self.path.parent
        db = sqlite3.connect(self.path, timeout=60)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)  # executescript manages its own transaction
            db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', 'forkloop.correction.v1')")
            db.commit()
        finally:
            db.close()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            db.close()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._db() as db:
            return [dict(r) for r in db.execute(sql, params)]

    # ----------------------------------------------------------------- tasks / policies
    def put_task(self, task: Any) -> str:
        manifest = _j(task.to_dict())
        sha = hashlib.sha256(manifest.encode()).hexdigest()
        with self._db() as db:
            row = db.execute("SELECT manifest_sha256 FROM tasks WHERE task_id=?", (task.task_id,)).fetchone()
            if row and row[0] != sha:
                raise ValueError(f"task {task.task_id} changed since it was first stored (generator not pure?)")
            if not row:
                db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
                           (task.task_id, task.world, task.family, task.split, int(task.seed), sha, time.time()))
        return task.task_id

    def put_policy(self, role: str, identity: dict[str, Any]) -> str:
        pid = stable_id("pol", role, _j(identity))
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO policies VALUES (?,?,?,?)", (pid, role, _j(identity), time.time()))
        return pid

    def policy(self, policy_id: str) -> dict[str, Any]:
        rows = self._rows("SELECT * FROM policies WHERE policy_id=?", (policy_id,))
        if not rows:
            raise KeyError(policy_id)
        r = rows[0]
        r["identity"] = json.loads(r.pop("identity_json"))
        return r

    # ----------------------------------------------------------------- attempts
    def start_attempt(self, *, attempt_id: str, task_id: str, policy_id: str, run_dir: str, strategy: str,
                      experiment_id: Optional[str] = None, cell: Optional[str] = None, info: Optional[dict] = None) -> None:
        with self._db() as db:
            if db.execute("SELECT 1 FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone():
                raise ValueError(f"attempt {attempt_id} already exists; never reuse an attempt id")
            db.execute("INSERT INTO attempts (attempt_id, task_id, policy_id, experiment_id, cell, status, run_dir, "
                       "checkpoint_strategy, started_at, info_json, runner) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (attempt_id, task_id, policy_id, experiment_id, cell, RUNNING, run_dir, strategy, time.time(),
                        _j(info or {}), _runner()))
            self._event(db, "attempt_started", attempt_id, {"task_id": task_id, "cell": cell})

    def finish_attempt(self, attempt_id: str, *, status: str, reward: Optional[float], reason_code: Optional[str],
                       n_steps: int, info: Optional[dict] = None) -> None:
        with self._db() as db:
            cur = db.execute("SELECT status, info_json FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if cur is None:
                raise KeyError(attempt_id)
            if cur["status"] != RUNNING:
                raise ValueError(f"attempt {attempt_id} is {cur['status']}, not running")
            merged = {**json.loads(cur["info_json"]), **(info or {})}
            db.execute("UPDATE attempts SET status=?, reward=?, reason_code=?, n_steps=?, finished_at=?, info_json=? "
                       "WHERE attempt_id=?", (status, reward, reason_code, n_steps, time.time(), _j(merged), attempt_id))
            self._event(db, "attempt_finished", attempt_id, {"status": status, "reward": reward, "reason": reason_code})

    def attempts(self, **where: Any) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM attempts", []
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
            params = list(where.values())
        rows = self._rows(sql + " ORDER BY started_at", tuple(params))
        for r in rows:
            r["info"] = json.loads(r.pop("info_json"))
        return rows

    def attempt(self, attempt_id: str) -> dict[str, Any]:
        rows = self.attempts(attempt_id=attempt_id)
        if not rows:
            raise KeyError(attempt_id)
        return rows[0]

    def mark_interrupted(self, *, older_than_s: float = 0.0, alive_runners: Optional[set[str]] = None) -> list[str]:
        """Rows left ``running`` by a dead process become ``interrupted`` (kept, never scored).
        With ``alive_runners``, rows owned by a runner that is still heartbeating are left alone."""
        cutoff = time.time() - older_than_s
        alive = alive_runners or set()
        out = []
        with self._db() as db:
            for table, key in (("attempts", "attempt_id"), ("branches", "branch_id"), ("repairs", "repair_id")):
                rows = db.execute(f"SELECT * FROM {table} WHERE status=? AND started_at<=?", (RUNNING, cutoff)).fetchall()
                for r in rows:
                    owner = r["runner"] if "runner" in r.keys() else None
                    if owner and owner in alive:
                        continue
                    db.execute(f"UPDATE {table} SET status=?, finished_at=? WHERE {key}=?", (INTERRUPTED, time.time(), r[key]))
                    self._event(db, f"{table[:-1]}_interrupted", r[key], {"runner": owner})
                    out.append(r[key])
        return out

    # ----------------------------------------------------------------- checkpoints
    def put_checkpoint(self, row: dict[str, Any]) -> None:
        cols = ("ckpt_id", "attempt_id", "step", "strategy", "world_ref", "status", "digest_json", "oracle_json",
                "policy_state_ref", "agent_state_json", "elapsed_s", "budget_steps", "invalid", "history_json",
                "cost_json", "created_at")
        with self._db() as db:
            db.execute(f"INSERT INTO checkpoints ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       tuple(row[c] for c in cols))

    def checkpoints(self, attempt_id: str) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM checkpoints WHERE attempt_id=? ORDER BY step", (attempt_id,))
        for r in rows:
            for k in ("digest_json", "oracle_json", "agent_state_json", "history_json", "cost_json"):
                r[k[:-5]] = json.loads(r.pop(k))
        return rows

    def checkpoint(self, ckpt_id: str) -> dict[str, Any]:
        rows = self._rows("SELECT attempt_id FROM checkpoints WHERE ckpt_id=?", (ckpt_id,))
        if not rows:
            raise KeyError(ckpt_id)
        return next(c for c in self.checkpoints(rows[0]["attempt_id"]) if c["ckpt_id"] == ckpt_id)

    def mark_checkpoint_deleted(self, ckpt_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE checkpoints SET deleted_at=? WHERE ckpt_id=?", (time.time(), ckpt_id))

    def retain_checkpoint(self, ckpt_id: str, retained: bool = True) -> None:
        with self._db() as db:
            db.execute("UPDATE checkpoints SET retained=? WHERE ckpt_id=?", (int(retained), ckpt_id))

    # ----------------------------------------------------------------- repairs / branches
    def start_repair(self, *, repair_id: str, attempt_id: str, mode: str, teacher_policy_id: str, config: dict,
                     experiment_id: Optional[str] = None) -> None:
        with self._db() as db:
            if db.execute("SELECT 1 FROM repairs WHERE repair_id=?", (repair_id,)).fetchone():
                raise ValueError(f"repair {repair_id} already exists")
            db.execute("INSERT INTO repairs (repair_id, attempt_id, mode, teacher_policy_id, config_json, status, "
                       "experiment_id, started_at, runner) VALUES (?,?,?,?,?,?,?,?,?)",
                       (repair_id, attempt_id, mode, teacher_policy_id, _j(config), RUNNING, experiment_id, time.time(),
                        _runner()))
            self._event(db, "repair_started", repair_id, {"attempt_id": attempt_id, "mode": mode})

    def finish_repair(self, repair_id: str, *, status: str, result: dict) -> None:
        with self._db() as db:
            db.execute("UPDATE repairs SET status=?, result_json=?, finished_at=? WHERE repair_id=?",
                       (status, _j(result), time.time(), repair_id))
            self._event(db, "repair_finished", repair_id, {"status": status})

    def repairs(self, **where: Any) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM repairs", []
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
            params = list(where.values())
        rows = self._rows(sql + " ORDER BY started_at", tuple(params))
        for r in rows:
            r["config"] = json.loads(r.pop("config_json"))
            r["result"] = json.loads(r.pop("result_json"))
        return rows

    def start_branch(self, *, branch_id: str, repair_id: str, ckpt_id: str, idx: int, run_dir: str) -> None:
        with self._db() as db:
            if db.execute("SELECT 1 FROM branches WHERE branch_id=?", (branch_id,)).fetchone():
                raise ValueError(f"branch {branch_id} already exists")
            db.execute("INSERT INTO branches (branch_id, repair_id, ckpt_id, idx, status, run_dir, started_at, runner) "
                       "VALUES (?,?,?,?,?,?,?,?)", (branch_id, repair_id, ckpt_id, idx, RUNNING, run_dir, time.time(),
                                                    _runner()))

    def finish_branch(self, branch_id: str, *, status: str, reward: Optional[float], reason_code: Optional[str],
                      n_steps: int, restore: dict, info: Optional[dict] = None) -> None:
        with self._db() as db:
            db.execute("UPDATE branches SET status=?, reward=?, reason_code=?, n_steps=?, restore_json=?, info_json=?, "
                       "finished_at=? WHERE branch_id=?", (status, reward, reason_code, n_steps, _j(restore),
                                                           _j(info or {}), time.time(), branch_id))
            self._event(db, "branch_finished", branch_id, {"status": status, "reward": reward, "reason": reason_code})

    def branches(self, **where: Any) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM branches", []
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
            params = list(where.values())
        rows = self._rows(sql + " ORDER BY started_at, idx", tuple(params))
        for r in rows:
            r["restore"] = json.loads(r.pop("restore_json"))
            r["info"] = json.loads(r.pop("info_json"))
        return rows

    # ----------------------------------------------------------------- accounting
    def charge(self, kind: str, amount: float, unit: str, *, usd: Optional[float] = None, ref: Optional[str] = None,
               experiment_id: Optional[str] = None, evidence: Optional[dict] = None) -> None:
        with self._db() as db:
            db.execute("INSERT INTO charges (ts, kind, amount, unit, usd, ref, experiment_id, evidence_json) "
                       "VALUES (?,?,?,?,?,?,?,?)", (time.time(), kind, float(amount), unit, usd, ref, experiment_id,
                                                    _j(evidence or {})))

    def charges(self, **where: Any) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM charges", []
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
            params = list(where.values())
        rows = self._rows(sql + " ORDER BY id", tuple(params))
        for r in rows:
            r["evidence"] = json.loads(r.pop("evidence_json"))
        return rows

    def cost_summary(self, experiment_id: Optional[str] = None) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for c in self.charges(**({"experiment_id": experiment_id} if experiment_id else {})):
            k = f"{c['kind']}[{c['unit']}]"
            d = out.setdefault(k, {"amount": 0.0, "usd": 0.0, "n": 0})
            d["amount"] += c["amount"]
            d["usd"] += c["usd"] or 0.0
            d["n"] += 1
        return out

    # ----------------------------------------------------------------- datasets
    def put_dataset(self, *, dataset_id: str, kind: str, path: str, sha256: str, n_records: int, manifest: dict) -> None:
        with self._db() as db:
            row = db.execute("SELECT sha256 FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
            if row:
                if row[0] != sha256:
                    raise ValueError(f"dataset {dataset_id} exists with different content; datasets are immutable")
                return
            db.execute("INSERT INTO datasets VALUES (?,?,?,?,?,?,?)",
                       (dataset_id, kind, path, sha256, n_records, _j(manifest), time.time()))

    def datasets(self) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM datasets ORDER BY created_at")
        for r in rows:
            r["manifest"] = json.loads(r.pop("manifest_json"))
        return rows

    # ----------------------------------------------------------------- events
    def _event(self, db: sqlite3.Connection, kind: str, ref: Optional[str], detail: dict) -> None:
        db.execute("INSERT INTO events (ts, kind, ref, detail_json) VALUES (?,?,?,?)", (time.time(), kind, ref, _j(detail)))

    def event(self, kind: str, ref: Optional[str] = None, **detail: Any) -> None:
        with self._db() as db:
            self._event(db, kind, ref, detail)

    def events(self, ref: Optional[str] = None) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM events" + (" WHERE ref=?" if ref else "") + " ORDER BY id", (ref,) if ref else ())
        for r in rows:
            r["detail"] = json.loads(r.pop("detail_json"))
        return rows


__all__ = ["Store", "stable_id", "file_sha256", "FINISHED", "RUNNING", "INTERRUPTED", "INFRA_ERROR", "RESTORE_FAILED"]
