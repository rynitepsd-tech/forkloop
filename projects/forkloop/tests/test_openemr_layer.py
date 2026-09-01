"""Tests for worlds/claims_ops_v1/openemr (shim schema, base data, SQL helpers).

Run from projects/forkloop:  PYTHONPATH=. pytest tests/test_openemr_layer.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess

import pytest

from worlds.claims_ops_v1.openemr import base_data, docs_paths, openemr_sql as osql

HERE = pathlib.Path(__file__).resolve().parent
OPENEMR_DIR = HERE.parent / "worlds" / "claims_ops_v1" / "openemr"
SHIM_SQL = (OPENEMR_DIR / "shim_schema.sql").read_text(encoding="utf-8")

FORBIDDEN = ("NOW(", "`", "ON DUPLICATE", "LAST_INSERT_ID")
CONTRACT_TABLES = {
    "users": ["id", "username", "password", "authorized", "fname", "lname", "facility_id", "calendar", "active"],
    "patient_data": ["id", "pid", "pubpid", "fname", "lname", "DOB", "sex", "street", "city", "state", "postal_code", "phone_home", "providerID"],
    "insurance_companies": ["id", "name"],
    "insurance_data": ["id", "type", "provider", "plan_name", "policy_number", "group_number", "subscriber_fname", "subscriber_lname", "subscriber_DOB", "subscriber_relationship", "pid", "date"],
    "openemr_postcalendar_categories": ["pc_catid", "pc_catname", "pc_catcolor", "pc_duration"],
    "openemr_postcalendar_events": ["pc_eid", "pc_catid", "pc_aid", "pc_pid", "pc_title", "pc_eventDate", "pc_endDate", "pc_startTime", "pc_endTime", "pc_duration", "pc_apptstatus", "pc_facility", "pc_hometext"],
    "documents": ["id", "type", "url", "mimetype", "docdate", "foreign_id", "name", "hash", "size"],
    "categories": ["id", "name", "parent"],
    "categories_to_documents": ["category_id", "document_id"],
    "log": ["id", "date", "event", "category", "user", "patient_id", "comments", "success"],
}


def assert_no_forbidden(sql: str) -> None:
    upper = sql.upper()
    for tok in FORBIDDEN:
        assert tok.upper() not in upper, f"forbidden construct {tok!r} in SQL"


@pytest.fixture()
def shim() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SHIM_SQL)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn: sqlite3.Connection, sql: str, *params):
    return conn.execute(sql, params).fetchone()[0]


# --------------------------------------------------------------------------
# shim schema
# --------------------------------------------------------------------------

def test_shim_schema_loads_and_has_contract_columns(shim):
    tables = {r[0] for r in shim.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, cols in CONTRACT_TABLES.items():
        assert table in tables, table
        actual = [r["name"] for r in shim.execute(f"PRAGMA table_info({table})")]
        for c in cols:
            assert c in actual, f"{table}.{c} missing from shim (case-sensitive)"


def test_shim_ships_openemr_reference_rows(shim):
    assert scalar(shim, "SELECT pc_catname FROM openemr_postcalendar_categories WHERE pc_catid=5") == "Office Visit"
    assert scalar(shim, "SELECT pc_duration FROM openemr_postcalendar_categories WHERE pc_catid=5") == 900
    assert scalar(shim, "SELECT pc_catname FROM openemr_postcalendar_categories WHERE pc_catid=10") == "New Patient"
    assert scalar(shim, "SELECT COUNT(*) FROM openemr_postcalendar_categories") == 15
    assert scalar(shim, "SELECT name FROM categories WHERE id=1") == "Categories"
    assert scalar(shim, "SELECT parent FROM categories WHERE id=1") == 0
    for cid, name in [(2, "Lab Report"), (3, "Medical Record"), (4, "Patient Information"), (6, "Advance Directive")]:
        assert scalar(shim, "SELECT name FROM categories WHERE id=?", cid) == name
        assert scalar(shim, "SELECT parent FROM categories WHERE id=?", cid) == 1
    assert scalar(shim, "SELECT COUNT(*) FROM categories") == 34
    # no seeded business rows in the bare shim
    for t in ("users", "patient_data", "insurance_data", "openemr_postcalendar_events", "documents", "log"):
        assert scalar(shim, f"SELECT COUNT(*) FROM {t}") == 0


# --------------------------------------------------------------------------
# quote()
# --------------------------------------------------------------------------

def test_quote_literals():
    q = osql.quote
    assert q(None) == "NULL"
    assert q(5) == "5"
    assert q(-12) == "-12"
    assert q(True) == "1" and q(False) == "0"
    assert q(1.5) == "1.5"
    assert q("abc") == "'abc'"
    assert q("O'Brien") == "'O''Brien'"
    assert q("it''s") == "'it''''s'"
    assert q("") == "''"
    assert q(dt.date(2026, 9, 7)) == "'2026-09-07'"
    assert q(dt.datetime(2026, 9, 7, 9, 30)) == "'2026-09-07 09:30:00'"
    assert q(dt.time(9, 30)) == "'09:30:00'"


def test_quote_rejects_non_portable_values():
    with pytest.raises(osql.NonPortableSQL):
        osql.quote("back\\slash")
    with pytest.raises(osql.NonPortableSQL):
        osql.quote("nul\x00byte")
    with pytest.raises(osql.NonPortableSQL):
        osql.quote(float("nan"))
    with pytest.raises(TypeError):
        osql.quote(object())


def test_quoted_string_round_trips_through_sqlite(shim):
    weird = "Ann-Marie O'Neil \"quoted\" 100% ; -- not a comment"
    shim.executescript(osql.insert_row("insurance_companies", {"id": 100099, "name": weird}))
    assert scalar(shim, "SELECT name FROM insurance_companies WHERE id=100099") == weird


# --------------------------------------------------------------------------
# base data
# --------------------------------------------------------------------------

def test_base_data_is_deterministic_and_matches_committed_json():
    a = base_data.generate()
    b = base_data.generate()
    assert a == b
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    on_disk = json.loads((OPENEMR_DIR / "base_data.json").read_text(encoding="utf-8"))
    assert on_disk == a, "base_data.json is stale: python -m worlds.claims_ops_v1.openemr.base_data"
    providers_on_disk = json.loads((OPENEMR_DIR / "providers.json").read_text(encoding="utf-8"))
    assert providers_on_disk == base_data.provider_records()


def test_providers_constant_shape():
    assert len(base_data.PROVIDERS) == 6
    names = [p[0] for p in base_data.PROVIDERS]
    npis = [p[1] for p in base_data.PROVIDERS]
    assert len(set(names)) == 6 and len(set(npis)) == 6
    for name, npi, specialty in base_data.PROVIDERS:
        assert " " in name and specialty
        assert base_data.npi_is_valid(npi), npi
    assert not base_data.npi_is_valid("1234567890")
    recs = base_data.provider_records()
    assert [r["openemr_user_id"] for r in recs] == list(range(100001, 100007))


def test_base_data_shape():
    data = base_data.generate()
    t = data["tables"]
    assert data["anchor_date"] == "2026-09-07"
    assert dt.date.fromisoformat(data["anchor_date"]).weekday() == 0  # Monday
    assert [u["id"] for u in t["users"]] == list(range(100001, 100007))
    assert all(u["authorized"] == 1 and u["calendar"] == 1 and u["active"] == 1 for u in t["users"])
    assert [c["id"] for c in t["insurance_companies"]] == list(range(100001, 100005))
    assert [p["pid"] for p in t["patient_data"]] == list(range(100001, 100041))
    assert all(p["id"] == p["pid"] for p in t["patient_data"])
    assert len({(p["fname"], p["lname"]) for p in t["patient_data"]}) == 40
    assert all(p["providerID"] in range(100001, 100007) for p in t["patient_data"])
    ins = t["insurance_data"]
    assert [i["id"] for i in ins] == list(range(100001, 100041))
    assert sorted(i["pid"] for i in ins) == list(range(100001, 100041))
    assert all(i["type"] == "primary" and i["provider"] in {"100001", "100002", "100003", "100004"} for i in ins)
    ev = t["openemr_postcalendar_events"]
    assert 60 <= len(ev) <= 120
    anchor = dt.date(2026, 9, 7)
    seen_provider_slots: set = set()
    for e in ev:
        d = dt.date.fromisoformat(e["pc_eventDate"])
        assert anchor <= d < anchor + dt.timedelta(weeks=6)
        assert d.weekday() < 5
        assert e["pc_catid"] in (5, 9, 10)
        assert e["pc_duration"] in (900, 1800)
        assert e["pc_endTime"] == osql.end_time(e["pc_startTime"], e["pc_duration"])
        assert "08:00:00" <= e["pc_startTime"] and e["pc_endTime"] <= "17:00:00"
        assert e["pc_eventstatus"] == 1 and e["pc_sharing"] == 1 and e["pc_multiple"] == 0
        key = (e["pc_aid"], e["pc_eventDate"], e["pc_startTime"])
        assert key not in seen_provider_slots, f"double booking {key}"
        seen_provider_slots.add(key)
    assert [e["pc_eid"] for e in ev] == list(range(100001, 100001 + len(ev)))
    logs = t["log"]
    assert len(logs) == 40
    assert sorted(l["patient_id"] for l in logs) == list(range(100001, 100041))
    assert all(l["event"] == "patient-record-insert" and l["category"] == "Patient Demographics" for l in logs)


def test_render_base_sql_executes_on_shim(shim):
    data = base_data.generate()
    sql = base_data.render_base_sql(data)
    assert_no_forbidden(sql)
    shim.executescript(sql)
    assert scalar(shim, "SELECT COUNT(*) FROM users") == 6
    assert scalar(shim, "SELECT COUNT(*) FROM users WHERE authorized=1 AND calendar=1") == 6
    assert scalar(shim, "SELECT COUNT(*) FROM insurance_companies") == 4
    assert scalar(shim, "SELECT COUNT(*) FROM patient_data") == 40
    assert scalar(shim, "SELECT COUNT(*) FROM insurance_data") == 40
    assert scalar(shim, "SELECT COUNT(*) FROM insurance_data WHERE type='primary'") == 40
    assert scalar(shim, "SELECT COUNT(*) FROM openemr_postcalendar_events") >= 60
    assert scalar(shim, "SELECT COUNT(*) FROM log") == 40
    assert scalar(shim, "SELECT MIN(id) FROM patient_data") >= 100000
    assert scalar(shim, "SELECT MIN(pc_eid) FROM openemr_postcalendar_events") >= 100000
    # every insurance row and appointment references a seeded patient / provider
    assert scalar(shim, "SELECT COUNT(*) FROM insurance_data i LEFT JOIN patient_data p ON p.pid=i.pid WHERE p.pid IS NULL") == 0
    assert scalar(shim, """SELECT COUNT(*) FROM openemr_postcalendar_events e
                           LEFT JOIN users u ON CAST(u.id AS TEXT)=e.pc_aid WHERE u.id IS NULL""") == 0
    assert scalar(shim, """SELECT COUNT(*) FROM openemr_postcalendar_events e
                           LEFT JOIN patient_data p ON CAST(p.pid AS TEXT)=e.pc_pid WHERE p.pid IS NULL""") == 0
    # re-running the same SQL must fail on explicit ids (no silent upsert)
    with pytest.raises(sqlite3.IntegrityError):
        shim.executescript(sql)


def test_base_data_cli_sql_matches_render(tmp_path):
    out = subprocess.run(
        ["python3", "-m", "worlds.claims_ops_v1.openemr.base_data", "--sql"],
        cwd=HERE.parent, capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONPATH": str(HERE.parent)},
    ).stdout
    assert out == base_data.render_base_sql(base_data.generate())
    # flat invocation (how install.sh renders it inside the VM)
    flat = subprocess.run(["python3", "base_data.py", "--sql"], cwd=OPENEMR_DIR,
                          capture_output=True, text=True, check=True).stdout
    assert flat == out


# --------------------------------------------------------------------------
# per-episode helpers
# --------------------------------------------------------------------------

def test_helpers_execute_on_shim(shim):
    stmts = [
        osql.insert_user(id=100101, username="tlee", fname="Tara", lname="Lee", npi="1234567893", specialty="Family Medicine"),
        osql.insert_insurance_company(id=100101, name="Harbor Health"),
        osql.insert_patient(pid=100501, fname="Rosa", lname="O'Connor", dob="1981-04-12", sex="Female",
                            street="12 Elm St", city="Austin", state="TX", postal_code="78701",
                            phone_home="512-555-0100", provider_id=100101),
        osql.insert_insurance(id=100501, pid=100501, company_id=100101, plan_name="Harbor PPO",
                              policy_number="HH123456789", group_number="55501", subscriber_fname="Rosa",
                              subscriber_lname="O'Connor", subscriber_dob="1981-04-12", date="2025-02-01"),
        osql.insert_appointment(pc_eid=100501, pid=100501, provider_id=100101, event_date="2026-09-08",
                                start_time="09:30", duration_sec=900, hometext="follow-up"),
        osql.insert_document(doc_id=100501, pid=100501, name="auth_letter.pdf", size=1234,
                             content_hash="ab" * 32, docdate="2026-08-30"),
        osql.insert_log(id=100501, event="patient-record-insert", category="Patient Demographics",
                        user="admin", patient_id=100501, comments="seed", date="2026-08-30 10:00:00"),
    ]
    for s in stmts:
        assert_no_forbidden(s)
        assert s.rstrip().endswith(";")
        shim.executescript(s)

    assert scalar(shim, "SELECT lname FROM patient_data WHERE pid=100501") == "O'Connor"
    assert scalar(shim, "SELECT pubpid FROM patient_data WHERE pid=100501") == "100501"
    assert scalar(shim, "SELECT providerID FROM patient_data WHERE pid=100501") == 100101
    assert scalar(shim, "SELECT provider FROM insurance_data WHERE id=100501") == "100101"
    row = shim.execute("SELECT * FROM openemr_postcalendar_events WHERE pc_eid=100501").fetchone()
    assert row["pc_aid"] == "100101" and row["pc_pid"] == "100501"
    assert row["pc_startTime"] == "09:30:00" and row["pc_endTime"] == "09:45:00"
    assert row["pc_endDate"] == "2026-09-08" and row["pc_catid"] == 5 and row["pc_facility"] == 3
    assert row["pc_multiple"] == 0 and row["pc_eventstatus"] == 1 and row["pc_sharing"] == 1
    doc = shim.execute("SELECT * FROM documents WHERE id=100501").fetchone()
    assert doc["url"] == "file:///var/www/openemr/sites/default/documents/100501/auth_letter.pdf"
    assert doc["type"] == "file_url" and doc["foreign_id"] == 100501 and doc["mimetype"] == "application/pdf"
    assert doc["revision"] == "2026-08-30 00:00:00"
    assert scalar(shim, "SELECT category_id FROM categories_to_documents WHERE document_id=100501") == 3
    assert scalar(shim, "SELECT name FROM categories WHERE id=3") == "Medical Record"
    assert scalar(shim, "SELECT patient_id FROM log WHERE id=100501") == 100501

    upd = osql.update_insurance_policy(pid=100501, policy_number="NEW987654321", plan_name="Harbor HMO")
    assert_no_forbidden(upd)
    assert upd.startswith("UPDATE insurance_data SET ") and "WHERE pid = 100501 AND type = 'primary'" in upd
    shim.executescript(upd)
    assert scalar(shim, "SELECT policy_number FROM insurance_data WHERE pid=100501") == "NEW987654321"
    assert scalar(shim, "SELECT plan_name FROM insurance_data WHERE pid=100501") == "Harbor HMO"
    assert scalar(shim, "SELECT group_number FROM insurance_data WHERE pid=100501") == "55501"  # untouched

    mv = osql.update_appointment(pc_eid=100501, pc_eventDate="2026-09-10", pc_startTime="14:00", pc_duration=1800)
    assert_no_forbidden(mv)
    shim.executescript(mv)
    row = shim.execute("SELECT * FROM openemr_postcalendar_events WHERE pc_eid=100501").fetchone()
    assert (row["pc_eventDate"], row["pc_startTime"], row["pc_endTime"]) == ("2026-09-10", "14:00:00", "14:30:00")

    shim.executescript(osql.delete_rows("log", {"id": 100501}))
    assert scalar(shim, "SELECT COUNT(*) FROM log") == 0


def test_helpers_validate_inputs():
    with pytest.raises(ValueError):
        osql.insert_insurance(id=1, pid=1, company_id=1, plan_name="x", policy_number="y", group_number="z",
                              subscriber_fname="a", subscriber_lname="b", subscriber_dob="1990-01-01", type="quaternary")
    with pytest.raises(ValueError):
        osql.update_row("log", {"event": "x"}, {})
    with pytest.raises(osql.NonPortableSQL):
        osql.insert_row("patient_data", {"bad col": 1})
    with pytest.raises(ValueError):
        osql.insert_patient(pid=1, fname="a", lname="b", dob="not-a-date", sex="Male")


def test_forbidden_constructs_absent_everywhere():
    sql = base_data.render_base_sql(base_data.generate())
    sql += osql.insert_document(doc_id=1, pid=1, name="a.pdf", size=1, content_hash="h", docdate="2026-01-01")
    sql += osql.update_insurance_policy(pid=1, policy_number="p", company_id=100001)
    assert_no_forbidden(sql)
    assert_no_forbidden(SHIM_SQL)
    with pytest.raises(osql.NonPortableSQL):
        osql.assert_portable("INSERT INTO log (date) VALUES (NOW());")
    with pytest.raises(osql.NonPortableSQL):
        osql.assert_portable("INSERT INTO `log` (id) VALUES (1);")
    with pytest.raises(osql.NonPortableSQL):
        osql.assert_portable("INSERT INTO log (id) VALUES (1) ON DUPLICATE KEY UPDATE id=1;")
    with pytest.raises(osql.NonPortableSQL):
        osql.assert_portable("SELECT LAST_INSERT_ID();")


# --------------------------------------------------------------------------
# docs_paths
# --------------------------------------------------------------------------

def test_docs_paths():
    assert docs_paths.document_fs_path(100007, "auth_A1.pdf") == "/var/www/openemr/sites/default/documents/100007/auth_A1.pdf"
    assert docs_paths.document_url(100007, "auth_A1.pdf") == "file:///var/www/openemr/sites/default/documents/100007/auth_A1.pdf"
    assert docs_paths.document_dir(5) == "/var/www/openemr/sites/default/documents/5"
    for bad in ("../x.pdf", "a/b.pdf", ".hidden", "", "x\\y.pdf", "a;b.pdf"):
        with pytest.raises(ValueError):
            docs_paths.document_fs_path(1, bad)


# --------------------------------------------------------------------------
# install.sh (static checks only; it needs a VM to run)
# --------------------------------------------------------------------------

def test_install_sh_static():
    path = OPENEMR_DIR / "install.sh"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    assert os.access(path, os.X_OK)
    assert "openemr-${OPENEMR_VERSION}.tar.gz" in text
    assert "releases/download/${OPENEMR_TAG}/" in text
    assert re.search(r'OPENEMR_SHA256="\$\{OPENEMR_SHA256:-[0-9a-f]{64}\}"', text)
    assert "--skip-verify" in text and "--with-demo-data" in text
    assert "OPENEMR_ENABLE_INSTALLER_AUTO=1" in text
    assert "contrib/util/installScripts/InstallerAuto.php" in text
    for arg in ("login=${DB_USER}", "dbname=${DB_NAME}", "iuser=${ADMIN_USER}", "iuserpass=${ADMIN_PASS}", "site=${SITE}", "rootpass=${DB_ROOT_PASS}"):
        assert arg in text, arg
    assert "/etc/forkloop/openemr.pw" in text.replace('${FORKLOOP_ETC}', '/etc/forkloop')
    assert "chmod 600" in text
    assert "interface/login/login.php?site=" in text
    if shutil.which("bash"):
        subprocess.run(["bash", "-n", str(path)], check=True)
