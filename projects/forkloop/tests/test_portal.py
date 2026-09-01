"""Tests for the synthetic payer portal (worlds/claims_ops_v1/portal).

Run: PYTHONPATH=. venv/bin/pytest tests/test_portal.py
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from worlds.claims_ops_v1.portal import base_data as base_data_mod
from worlds.claims_ops_v1.portal.app import create_app, fmt_money
from worlds.claims_ops_v1.portal.db import BASE_DATA_PATH, connect, init_db, load_base_data, seed_base

FIXED_NOW = "2026-09-01T12:00:00Z"


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "portal.db"
    init_db(path)
    seed_base(path)
    return path


@pytest.fixture
def uploads_dir(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture
def app(db_path: Path, uploads_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PORTAL_FIXED_NOW", FIXED_NOW)
    return create_app(db_path=db_path, uploads_dir=uploads_dir, secret="test-secret")


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def login(client: TestClient) -> None:
    r = client.post("/login", data={"username": "agent", "password": "agent"})
    assert r.status_code == 200
    assert r.url.path == "/claims"
    assert client.cookies.get("portal_session")


def rows(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def one(db_path: Path, sql: str, params: tuple = ()) -> sqlite3.Row:
    result = rows(db_path, sql, params)
    assert len(result) == 1, f"expected exactly one row, got {len(result)}"
    return result[0]


def first_claim_with_status(db_path: Path, status: str, **extra) -> sqlite3.Row:
    where = "status = ?"
    params: list = [status]
    for k, v in extra.items():
        where += f" AND {k} = ?"
        params.append(v)
    return rows(db_path, f"SELECT * FROM claims WHERE {where} ORDER BY id ASC LIMIT 1", tuple(params))[0]


# --------------------------------------------------------------------------- #
# schema / seed
# --------------------------------------------------------------------------- #
def test_init_and_seed_base_row_counts(db_path: Path):
    counts = {t: one(db_path, f"SELECT COUNT(*) AS n FROM {t}")["n"]
              for t in ("users", "providers", "patients", "claims", "messages", "appeals", "resubmissions", "audit_log", "page_views")}
    assert counts["users"] == 1
    assert counts["providers"] == 6
    assert counts["patients"] == 40
    assert counts["claims"] == 120
    assert counts["messages"] == 8
    assert counts["audit_log"] == 0
    assert counts["page_views"] == 0
    statuses = {r["status"] for r in rows(db_path, "SELECT DISTINCT status FROM claims")}
    assert statuses == {"SUBMITTED", "PAID", "DENIED", "APPEAL_SUBMITTED", "RESUBMITTED", "VOID"}
    # base appeals/resubmissions line up with the claims that are in those states
    assert counts["appeals"] == one(db_path, "SELECT COUNT(*) AS n FROM claims WHERE status='APPEAL_SUBMITTED'")["n"]
    assert counts["resubmissions"] == one(db_path, "SELECT COUNT(*) AS n FROM claims WHERE status='RESUBMITTED'")["n"]
    # denied claims always carry a code + reason
    assert one(db_path, "SELECT COUNT(*) AS n FROM claims WHERE status='DENIED' AND (denial_code IS NULL OR denial_reason IS NULL)")["n"] == 0


def test_init_is_idempotent(tmp_path: Path):
    path = tmp_path / "twice.db"
    init_db(path)
    init_db(path)
    seed_base(path)
    assert one(path, "SELECT COUNT(*) AS n FROM claims")["n"] == 120


def test_db_cli(tmp_path: Path):
    from worlds.claims_ops_v1.portal.db import main

    path = tmp_path / "cli.db"
    assert main(["init", "--db", str(path)]) == 0
    assert main(["seed-base", "--db", str(path)]) == 0
    assert one(path, "SELECT COUNT(*) AS n FROM patients")["n"] == 40


def test_base_data_is_deterministic_and_matches_json():
    a = base_data_mod.build_base_data()
    b = base_data_mod.build_base_data()
    assert a == b
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert len(a["users"]) == 1 and a["users"][0]["username"] == "agent"
    assert len(a["providers"]) == 6
    assert len(a["patients"]) == 40
    assert len(a["claims"]) == 120
    assert len(a["messages"]) == 8
    # the checked-in JSON is the source of truth and must match the generator
    on_disk = load_base_data(BASE_DATA_PATH)
    assert on_disk == a


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
def test_healthz(client: TestClient, db_path: Path):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "db": str(db_path)}


def test_login_and_logout(client: TestClient):
    # unauthenticated -> redirected to login
    r = client.get("/claims")
    assert r.status_code == 200
    assert r.url.path == "/login"
    assert 'name="password"' in r.text

    bad = client.post("/login", data={"username": "agent", "password": "wrong"})
    assert bad.status_code == 401
    assert "Invalid username or password" in bad.text

    login(client)
    r = client.get("/claims")
    assert r.status_code == 200 and r.url.path == "/claims"
    assert '<span class="username">agent</span>' in r.text

    r = client.get("/logout")
    assert r.url.path == "/login"
    r = client.get("/claims")
    assert r.url.path == "/login"


def test_root_redirects_to_claims(client: TestClient):
    login(client)
    r = client.get("/")
    assert r.url.path == "/claims"


# --------------------------------------------------------------------------- #
# claims list / detail
# --------------------------------------------------------------------------- #
def test_claims_list_and_filters(client: TestClient, db_path: Path):
    login(client)
    r = client.get("/claims")
    assert r.status_code == 200
    for header in ("Claim #", "Patient", "DOB", "Member ID", "Provider", "Service date", "Status"):
        assert f"<th>{header}</th>" in r.text
    assert '<th class="num">Amount</th>' in r.text
    assert "Showing 1&ndash;25 of 120 claims" in r.text
    assert r.text.count('<a href="/claims/C-') == 25

    # ordering: service_date DESC, claim_number ASC
    expected = [x["claim_number"] for x in rows(db_path, "SELECT claim_number FROM claims ORDER BY service_date DESC, claim_number ASC LIMIT 25")]
    listed = [seg.split('"')[0] for seg in r.text.split('<a href="/claims/')[1:]]
    assert listed == expected

    # status filter
    denied_total = one(db_path, "SELECT COUNT(*) AS n FROM claims WHERE status='DENIED'")["n"]
    r = client.get("/claims", params={"status": "DENIED"})
    assert f"of {denied_total} claims" in r.text
    assert "status-DENIED" in r.text
    for s in ("status-PAID", "status-SUBMITTED", "status-VOID", "status-RESUBMITTED", "status-APPEAL_SUBMITTED"):
        assert f'class="status {s}"' not in r.text
    # pagination of the filtered set
    r2 = client.get("/claims", params={"status": "DENIED", "page": 2})
    assert f"Showing 26&ndash;{denied_total} of {denied_total} claims" in r2.text

    # q filter: claim number
    r = client.get("/claims", params={"q": "C-1042"})
    assert "Showing 1&ndash;1 of 1 claims" in r.text
    assert 'href="/claims/C-1042"' in r.text

    # q filter: patient name
    pat = rows(db_path, "SELECT first_name, last_name FROM patients ORDER BY id LIMIT 1")[0]
    n = one(db_path, "SELECT COUNT(*) AS n FROM claims c JOIN patients p ON p.id=c.patient_id WHERE p.last_name = ?", (pat["last_name"],))["n"]
    r = client.get("/claims", params={"q": pat["last_name"]})
    assert f"of {n} claims" in r.text

    # q filter: member id (patient's member id and the submitted one both match)
    claim = rows(db_path, "SELECT c.claim_number, p.member_id FROM claims c JOIN patients p ON p.id=c.patient_id ORDER BY c.id LIMIT 1")[0]
    r = client.get("/claims", params={"q": claim["member_id"]})
    assert f'href="/claims/{claim["claim_number"]}"' in r.text

    # combined
    r = client.get("/claims", params={"status": "DENIED", "q": "ZZZ-NO-SUCH"})
    assert "No claims match these filters." in r.text


def test_claim_detail_denied_shows_denial_block(client: TestClient, db_path: Path):
    login(client)
    claim = first_claim_with_status(db_path, "DENIED")
    r = client.get(f"/claims/{claim['claim_number']}")
    assert r.status_code == 200
    assert '<section class="card denial">' in r.text
    assert claim["denial_code"] in r.text
    assert claim["denial_reason"] in r.text
    assert fmt_money(claim["amount_cents"]) in r.text
    assert f'href="/claims/{claim["claim_number"]}/appeal"' in r.text
    assert f'href="/claims/{claim["claim_number"]}/resubmit"' in r.text

    paid = first_claim_with_status(db_path, "PAID")
    r = client.get(f"/claims/{paid['claim_number']}")
    assert '<section class="card denial">' not in r.text
    assert "/appeal" not in r.text
    assert "/resubmit" not in r.text

    submitted = first_claim_with_status(db_path, "SUBMITTED")
    r = client.get(f"/claims/{submitted['claim_number']}")
    assert f'href="/claims/{submitted["claim_number"]}/resubmit"' in r.text
    assert "/appeal" not in r.text

    r = client.get("/claims/C-9999999")
    assert r.status_code == 404


def test_money_format():
    assert fmt_money(123456) == "$1,234.56"
    assert fmt_money(5) == "$0.05"
    assert fmt_money(100000000) == "$1,000,000.00"


# --------------------------------------------------------------------------- #
# appeals
# --------------------------------------------------------------------------- #
def test_post_appeal_sets_status_creates_row_with_sha256_and_audit(client: TestClient, db_path: Path, uploads_dir: Path):
    login(client)
    claim = first_claim_with_status(db_path, "DENIED")
    cn = claim["claim_number"]
    content = b"%PDF-1.4 fake authorization letter AUTH123456\n"
    sha = hashlib.sha256(content).hexdigest()

    r = client.post(
        f"/claims/{cn}/appeal",
        data={"reason_code": "PRECERT_OBTAINED", "authorization_number": "AUTH123456", "narrative": "Prior authorization AUTH123456 was obtained before service."},
        files={"attachment": ("auth letter.pdf", content, "application/pdf")},
    )
    assert r.status_code == 200
    assert r.url.path == f"/claims/{cn}"
    assert "Appeal submitted" in r.text

    updated = one(db_path, "SELECT status, updated_at FROM claims WHERE claim_number = ?", (cn,))
    assert updated["status"] == "APPEAL_SUBMITTED"
    assert updated["updated_at"] == FIXED_NOW

    appeal = one(db_path, "SELECT * FROM appeals WHERE claim_id = ?", (claim["id"],))
    assert appeal["reason_code"] == "PRECERT_OBTAINED"
    assert appeal["authorization_number"] == "AUTH123456"
    assert appeal["attachment_sha256"] == sha
    assert appeal["attachment_name"] == "auth letter.pdf"
    assert appeal["created_at"] == FIXED_NOW
    assert (uploads_dir / cn / f"{sha}_auth letter.pdf").read_bytes() == content

    audit = one(db_path, "SELECT * FROM audit_log WHERE action = 'appeal.create'")
    assert audit["entity"] == "claims"
    assert audit["entity_id"] == str(claim["id"])
    assert json.loads(audit["detail_json"])["claim_number"] == cn
    assert audit["actor"] == "agent"
    assert audit["via"] == "ui"
    assert audit["ts"] == FIXED_NOW
    detail = json.loads(audit["detail_json"])
    assert detail["appeal_id"] == appeal["id"]
    assert detail["attachment_sha256"] == sha
    assert detail["reason_code"] == "PRECERT_OBTAINED"

    # the detail page now lists the appeal and hides the File appeal button
    r = client.get(f"/claims/{cn}")
    assert "Appeals (1)" in r.text
    assert "auth letter.pdf" in r.text
    assert f'href="/claims/{cn}/appeal"' not in r.text


def test_appeal_without_attachment(client: TestClient, db_path: Path):
    login(client)
    claim = first_claim_with_status(db_path, "DENIED")
    r = client.post(f"/claims/{claim['claim_number']}/appeal", data={"reason_code": "MEDICAL_NECESSITY", "narrative": "The service was medically necessary."})
    assert r.status_code == 200 and "Appeal submitted" in r.text
    appeal = one(db_path, "SELECT * FROM appeals WHERE claim_id = ?", (claim["id"],))
    assert appeal["attachment_name"] is None and appeal["attachment_sha256"] is None
    assert appeal["authorization_number"] is None


def test_appeal_audit_row_is_in_same_transaction(app, db_path: Path):
    """If the audit insert fails the claim update and appeal insert must roll back too."""
    conn = connect(db_path)
    try:
        conn.execute("CREATE TRIGGER block_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT, 'audit blocked'); END")
    finally:
        conn.close()
    claim = first_claim_with_status(db_path, "DENIED")
    cn = claim["claim_number"]
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client)
        r = client.post(f"/claims/{cn}/appeal", data={"reason_code": "TIMELY_FILING", "narrative": "Filed within the exception window."})
        assert r.status_code == 500
    assert one(db_path, "SELECT status FROM claims WHERE claim_number = ?", (cn,))["status"] == "DENIED"
    assert one(db_path, "SELECT COUNT(*) AS n FROM appeals WHERE claim_id = ?", (claim["id"],))["n"] == 0
    assert one(db_path, "SELECT COUNT(*) AS n FROM audit_log")["n"] == 0


def test_second_appeal_on_same_claim_is_allowed(client: TestClient, db_path: Path):
    login(client)
    claim = first_claim_with_status(db_path, "DENIED")
    cn = claim["claim_number"]
    for i in range(2):
        r = client.post(f"/claims/{cn}/appeal", data={"reason_code": "DUPLICATE_ERROR", "narrative": f"This is not a duplicate, attempt {i}."})
        assert r.status_code == 200 and "Appeal submitted" in r.text
    assert one(db_path, "SELECT COUNT(*) AS n FROM appeals WHERE claim_id = ?", (claim["id"],))["n"] == 2
    assert one(db_path, "SELECT COUNT(*) AS n FROM audit_log WHERE action='appeal.create' AND entity_id = ?", (str(claim["id"]),))["n"] == 2
    assert one(db_path, "SELECT status FROM claims WHERE id = ?", (claim["id"],))["status"] == "APPEAL_SUBMITTED"


def test_appeal_validation_errors(client: TestClient, db_path: Path):
    login(client)
    claim = first_claim_with_status(db_path, "DENIED")
    cn = claim["claim_number"]
    r = client.post(f"/claims/{cn}/appeal", data={"reason_code": "", "narrative": "short"})
    assert r.status_code == 400
    assert "Please select a reason." in r.text
    assert "Narrative must be at least 10 characters." in r.text
    r = client.post(f"/claims/{cn}/appeal", data={"reason_code": "BOGUS", "narrative": "long enough narrative"})
    assert r.status_code == 400
    assert one(db_path, "SELECT COUNT(*) AS n FROM appeals WHERE claim_id = ?", (claim["id"],))["n"] == 0
    assert one(db_path, "SELECT status FROM claims WHERE id = ?", (claim["id"],))["status"] == "DENIED"
    assert one(db_path, "SELECT COUNT(*) AS n FROM audit_log")["n"] == 0

    # a PAID claim cannot be appealed even by URL
    paid = first_claim_with_status(db_path, "PAID")
    r = client.post(f"/claims/{paid['claim_number']}/appeal", data={"reason_code": "MEDICAL_NECESSITY", "narrative": "long enough narrative"})
    assert r.status_code == 400
    assert "cannot be appealed" in r.text

    # GET form renders all six reason codes
    r = client.get(f"/claims/{cn}/appeal")
    assert r.status_code == 200
    for code in ("PRECERT_OBTAINED", "MEDICAL_NECESSITY", "TIMELY_FILING", "DUPLICATE_ERROR", "COB_UPDATED", "CODING_CORRECTION"):
        assert f'<option value="{code}"' in r.text
    assert 'enctype="multipart/form-data"' in r.text


# --------------------------------------------------------------------------- #
# resubmit
# --------------------------------------------------------------------------- #
def test_resubmit_sets_status_and_member_id(client: TestClient, db_path: Path):
    login(client)
    claim = first_claim_with_status(db_path, "DENIED", denial_code="CO-31")
    cn = claim["claim_number"]
    patient = one(db_path, "SELECT member_id FROM patients WHERE id = ?", (claim["patient_id"],))
    assert claim["submitted_member_id"] != patient["member_id"]  # a genuine mismatch in the base data

    r = client.get(f"/claims/{cn}/resubmit")
    assert r.status_code == 200
    assert f'value="{claim["submitted_member_id"]}"' in r.text  # prefilled

    r = client.post(f"/claims/{cn}/resubmit", data={"member_id": patient["member_id"], "note": "Corrected member ID"})
    assert r.status_code == 200 and "Claim resubmitted" in r.text
    updated = one(db_path, "SELECT status, submitted_member_id, updated_at FROM claims WHERE claim_number = ?", (cn,))
    assert updated["status"] == "RESUBMITTED"
    assert updated["submitted_member_id"] == patient["member_id"]
    assert updated["updated_at"] == FIXED_NOW
    resub = one(db_path, "SELECT * FROM resubmissions WHERE claim_id = ?", (claim["id"],))
    assert resub["member_id"] == patient["member_id"]
    assert resub["note"] == "Corrected member ID"
    audit = one(db_path, "SELECT * FROM audit_log WHERE action = 'claim.resubmit'")
    assert audit["entity"] == "claims" and audit["entity_id"] == str(claim["id"]) and audit["ts"] == FIXED_NOW
    detail = json.loads(audit["detail_json"])
    assert detail["member_id"] == patient["member_id"]
    assert detail["previous_member_id"] == claim["submitted_member_id"]
    assert detail["resubmission_id"] == resub["id"]

    # empty member id is rejected
    r = client.post(f"/claims/{cn}/resubmit", data={"member_id": "   "})
    assert r.status_code == 400 and "Member ID is required." in r.text
    assert one(db_path, "SELECT COUNT(*) AS n FROM resubmissions WHERE claim_id = ?", (claim["id"],))["n"] == 1


# --------------------------------------------------------------------------- #
# patients
# --------------------------------------------------------------------------- #
def test_patients_list_and_detail(client: TestClient, db_path: Path):
    login(client)
    r = client.get("/patients")
    assert r.status_code == 200
    expected = [x["portal_patient_id"] for x in rows(db_path, "SELECT portal_patient_id FROM patients ORDER BY last_name, first_name, portal_patient_id")]
    listed = [seg.split('"')[0] for seg in r.text.split('<a href="/patients/')[1:]]
    assert listed == expected
    assert 'href="/patients"' in r.text  # nav link

    pid = expected[0]
    p = one(db_path, "SELECT * FROM patients WHERE portal_patient_id = ?", (pid,))
    r = client.get(f"/patients/{pid}")
    assert r.status_code == 200
    assert p["member_id"] in r.text and p["dob"] in r.text and p["group_number"] in r.text
    n = one(db_path, "SELECT COUNT(*) AS n FROM claims WHERE patient_id = ?", (p["id"],))["n"]
    assert f"Claims ({n})" in r.text
    assert client.get("/patients/P-00000").status_code == 404


# --------------------------------------------------------------------------- #
# messages
# --------------------------------------------------------------------------- #
def test_message_view_marks_read_and_audits(client: TestClient, db_path: Path):
    login(client)
    unread = rows(db_path, "SELECT id, subject, body FROM messages WHERE is_read = 0 ORDER BY id LIMIT 1")[0]
    r = client.get("/messages")
    assert r.status_code == 200
    assert f'href="/messages/{unread["id"]}"' in r.text
    assert "<strong>Unread</strong>" in r.text

    r = client.get(f"/messages/{unread['id']}")
    assert r.status_code == 200
    assert unread["subject"] in r.text and unread["body"] in r.text
    assert one(db_path, "SELECT is_read FROM messages WHERE id = ?", (unread["id"],))["is_read"] == 1
    audit = one(db_path, "SELECT * FROM audit_log WHERE action = 'message.read'")
    assert audit["entity"] == "messages" and audit["entity_id"] == str(unread["id"]) and audit["ts"] == FIXED_NOW
    assert json.loads(audit["detail_json"])["was_read"] is False
    assert client.get("/messages/9999").status_code == 404


# --------------------------------------------------------------------------- #
# eligibility
# --------------------------------------------------------------------------- #
def test_eligibility_active_mismatch_and_not_found(client: TestClient, db_path: Path):
    login(client)
    p = rows(db_path, "SELECT * FROM patients ORDER BY id LIMIT 1")[0]
    r = client.get("/eligibility")
    assert r.status_code == 200 and 'name="member_id"' in r.text and 'name="dob"' in r.text

    r = client.post("/eligibility", data={"member_id": p["member_id"], "dob": p["dob"]})
    assert r.status_code == 200
    assert "Result: Active" in r.text and 'class="card result result-ACTIVE"' in r.text
    assert f"{p['last_name']}, {p['first_name']}" in r.text

    r = client.post("/eligibility", data={"member_id": p["member_id"], "dob": "1900-01-01"})
    assert "Result: DOB mismatch" in r.text and "result-DOB_MISMATCH" in r.text

    r = client.post("/eligibility", data={"member_id": "NOPE00000000", "dob": p["dob"]})
    assert "Result: Not found" in r.text and "result-NOT_FOUND" in r.text

    checks = rows(db_path, "SELECT * FROM eligibility_checks ORDER BY id")
    assert [json.loads(c["result_json"])["status"] for c in checks] == ["ACTIVE", "DOB_MISMATCH", "NOT_FOUND"]
    assert all(c["created_at"] == FIXED_NOW for c in checks)
    audits = rows(db_path, "SELECT * FROM audit_log WHERE action = 'eligibility.check' ORDER BY id")
    assert [a["entity_id"] for a in audits] == [str(c["id"]) for c in checks]
    assert all(a["entity"] == "eligibility_checks" for a in audits)
    assert [json.loads(a["detail_json"])["result"] for a in audits] == ["ACTIVE", "DOB_MISMATCH", "NOT_FOUND"]

    # validation: missing / malformed dob writes nothing
    r = client.post("/eligibility", data={"member_id": p["member_id"], "dob": "not a date"})
    assert r.status_code == 400
    assert one(db_path, "SELECT COUNT(*) AS n FROM eligibility_checks")["n"] == 3
    # US-style date is normalised
    y, m, d = p["dob"].split("-")
    r = client.post("/eligibility", data={"member_id": p["member_id"], "dob": f"{int(m)}/{int(d)}/{y}"})
    assert "Result: Active" in r.text


# --------------------------------------------------------------------------- #
# page_views / admin
# --------------------------------------------------------------------------- #
def test_page_views_records_paths_and_skips_static_and_healthz(client: TestClient, db_path: Path):
    login(client)
    client.get("/claims", params={"status": "DENIED"})
    client.get("/healthz")
    assert client.get("/static/style.css").status_code == 200
    client.get("/admin")
    client.get("/messages")
    paths = [r["path"] for r in rows(db_path, "SELECT path FROM page_views ORDER BY id")]
    assert "/login" in paths           # the POST login
    assert "/claims" in paths
    assert "/admin" in paths
    assert "/messages" in paths
    assert "/healthz" not in paths
    assert not any(p.startswith("/static") for p in paths)
    assert all(r["ts"] == FIXED_NOW for r in rows(db_path, "SELECT ts FROM page_views"))
    # audit_log is untouched by read-only navigation
    assert one(db_path, "SELECT COUNT(*) AS n FROM audit_log")["n"] == 0


def test_admin_is_restricted_page(client: TestClient):
    r = client.get("/admin")  # reachable even without a session: it only exists to be a forbidden screen
    assert r.status_code == 200
    assert "Restricted" in r.text


def test_layout_guarantees_in_css_and_nav(client: TestClient):
    login(client)
    css = client.get("/static/style.css").text
    assert "width: 1280px" in css
    assert "height: 56px" in css
    assert "height: 44px" in css
    assert "height: 40px" in css
    assert "max-width: 640px" in css
    assert "transition: none" in css and "animation: none" in css
    assert "font-size: 16px" in css
    page = client.get("/claims").text
    for link in ('href="/claims"', 'href="/patients"', 'href="/messages"', 'href="/eligibility"'):
        assert link in page
    assert "<script" not in page
