"""SQLite schema, connections, and seeding for the synthetic payer portal.

The schema below is copied verbatim from docs/contracts.md §7. Table and column
names, the ``claims.status`` enum, and the ``audit_log`` shape are contract
surfaces read by the oracle; do not rename anything here without updating the
contract in the same commit.

CLI::

    python -m worlds.claims_ops_v1.portal.db init --db PATH
    python -m worlds.claims_ops_v1.portal.db seed-base --db PATH
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

# --------------------------------------------------------------------------- #
# Schema (docs/contracts.md §7, verbatim; ``IF NOT EXISTS`` added so ``init`` is
# idempotent on an already-initialised file).
# --------------------------------------------------------------------------- #
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, display_name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS providers (id INTEGER PRIMARY KEY, npi TEXT UNIQUE NOT NULL, name TEXT NOT NULL, specialty TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS patients (id INTEGER PRIMARY KEY, portal_patient_id TEXT UNIQUE NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL, dob TEXT NOT NULL, member_id TEXT NOT NULL, payer_plan TEXT NOT NULL, group_number TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claims (id INTEGER PRIMARY KEY, claim_number TEXT UNIQUE NOT NULL, patient_id INTEGER NOT NULL REFERENCES patients(id), provider_id INTEGER NOT NULL REFERENCES providers(id), service_date TEXT NOT NULL, cpt_code TEXT NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL CHECK (status IN ('SUBMITTED','PAID','DENIED','APPEAL_SUBMITTED','RESUBMITTED','VOID')), denial_code TEXT, denial_reason TEXT, submitted_member_id TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS appeals (id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES claims(id), reason_code TEXT NOT NULL, authorization_number TEXT, narrative TEXT NOT NULL, attachment_name TEXT, attachment_sha256 TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resubmissions (id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES claims(id), member_id TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, subject TEXT NOT NULL, body TEXT NOT NULL, received_at TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS eligibility_checks (id INTEGER PRIMARY KEY, member_id TEXT NOT NULL, dob TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL, detail_json TEXT NOT NULL, via TEXT NOT NULL DEFAULT 'ui');
CREATE TABLE IF NOT EXISTS page_views (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, path TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_patient ON claims(patient_id);
CREATE INDEX IF NOT EXISTS idx_appeals_claim ON appeals(claim_id);
CREATE INDEX IF NOT EXISTS idx_resubmissions_claim ON resubmissions(claim_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity, entity_id);
CREATE INDEX IF NOT EXISTS idx_page_views_path ON page_views(path);
"""

CLAIM_STATUSES: tuple[str, ...] = (
    "SUBMITTED",
    "PAID",
    "DENIED",
    "APPEAL_SUBMITTED",
    "RESUBMITTED",
    "VOID",
)

AUDIT_ACTIONS: tuple[str, ...] = (
    "appeal.create",
    "claim.resubmit",
    "message.read",
    "eligibility.check",
)

BASE_DATA_PATH = Path(__file__).with_name("base_data.json")

PBKDF2_ITERATIONS = 100_000


# --------------------------------------------------------------------------- #
# Password hashing (hashlib only, no extra dependencies)
# --------------------------------------------------------------------------- #
def hash_password(password: str, salt: bytes | None = None) -> str:
    """Return ``pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>``.

    Pass an explicit ``salt`` for reproducible output (the base data does this so
    the golden snapshot is byte-identical across generations).
    """
    if salt is None:
        salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return hmac.compare_digest(actual, expected)


# --------------------------------------------------------------------------- #
# Connections and transactions
# --------------------------------------------------------------------------- #
def connect(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open a connection in explicit-transaction mode.

    ``isolation_level=None`` disables the sqlite3 module's implicit BEGIN so that
    every multi-statement write is wrapped by :func:`transaction` explicitly.
    That is what guarantees an ``audit_log`` row lands in the same transaction as
    the write it describes.
    """
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE`` ... ``COMMIT``, rolling back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(path: str | os.PathLike[str]) -> None:
    """Create the schema at ``path`` (parent directories are created)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    try:
        conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
def load_base_data(json_path: str | os.PathLike[str] | None = None) -> dict:
    """Load ``base_data.json`` (the source of truth for the golden base data)."""
    src = Path(json_path) if json_path is not None else BASE_DATA_PATH
    with open(src, "r", encoding="utf-8") as fh:
        return json.load(fh)


def seed_base(path: str | os.PathLike[str], json_path: str | os.PathLike[str] | None = None) -> dict[str, int]:
    """Insert the fixed base data from ``base_data.json`` into the DB at ``path``.

    Returns a ``{table: rows_inserted}`` summary. Runs inside one transaction so a
    partial seed can never be observed.
    """
    data = load_base_data(json_path)
    conn = connect(path)
    counts: dict[str, int] = {}
    try:
        with transaction(conn):
            conn.executemany(
                "INSERT INTO users (id, username, password_hash, display_name) VALUES (:id, :username, :password_hash, :display_name)",
                data["users"],
            )
            counts["users"] = len(data["users"])
            conn.executemany(
                "INSERT INTO providers (id, npi, name, specialty) VALUES (:id, :npi, :name, :specialty)",
                data["providers"],
            )
            counts["providers"] = len(data["providers"])
            conn.executemany(
                "INSERT INTO patients (id, portal_patient_id, first_name, last_name, dob, member_id, payer_plan, group_number, created_at) "
                "VALUES (:id, :portal_patient_id, :first_name, :last_name, :dob, :member_id, :payer_plan, :group_number, :created_at)",
                data["patients"],
            )
            counts["patients"] = len(data["patients"])
            conn.executemany(
                "INSERT INTO claims (id, claim_number, patient_id, provider_id, service_date, cpt_code, amount_cents, status, denial_code, denial_reason, submitted_member_id, updated_at) "
                "VALUES (:id, :claim_number, :patient_id, :provider_id, :service_date, :cpt_code, :amount_cents, :status, :denial_code, :denial_reason, :submitted_member_id, :updated_at)",
                data["claims"],
            )
            counts["claims"] = len(data["claims"])
            conn.executemany(
                "INSERT INTO appeals (id, claim_id, reason_code, authorization_number, narrative, attachment_name, attachment_sha256, created_at) "
                "VALUES (:id, :claim_id, :reason_code, :authorization_number, :narrative, :attachment_name, :attachment_sha256, :created_at)",
                data.get("appeals", []),
            )
            counts["appeals"] = len(data.get("appeals", []))
            conn.executemany(
                "INSERT INTO resubmissions (id, claim_id, member_id, note, created_at) VALUES (:id, :claim_id, :member_id, :note, :created_at)",
                data.get("resubmissions", []),
            )
            counts["resubmissions"] = len(data.get("resubmissions", []))
            conn.executemany(
                "INSERT INTO messages (id, subject, body, received_at, is_read) VALUES (:id, :subject, :body, :received_at, :is_read)",
                data["messages"],
            )
            counts["messages"] = len(data["messages"])
    finally:
        conn.close()
    return counts


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="portal.db", description="Portal database utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create the schema")
    p_init.add_argument("--db", required=True, help="sqlite path")

    p_seed = sub.add_parser("seed-base", help="insert the fixed base data from base_data.json")
    p_seed.add_argument("--db", required=True, help="sqlite path")
    p_seed.add_argument("--json", default=None, help="override base_data.json path")

    args = parser.parse_args(argv)
    if args.command == "init":
        init_db(args.db)
        print(f"initialised schema at {args.db}")
        return 0
    if args.command == "seed-base":
        counts = seed_base(args.db, args.json)
        summary = ", ".join(f"{k}={v}" for k, v in counts.items())
        print(f"seeded {args.db}: {summary}")
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
