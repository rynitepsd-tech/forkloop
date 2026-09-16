"""Recovery may kill this session's resources, never another session by default."""
from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest

from forkloop.cli import _reap
from forkloop.spending import SessionLedger


async def test_reap_selects_ledger_operations_and_preserves_other_sessions(tmp_path, monkeypatch):
    ledger = SessionLedger.create(tmp_path / "ledger.sqlite")
    operation = ledger.reserve("solari", 0.7, label="machine_create")
    # Creation timed out before a machine ID was saved; provider metadata still
    # identifies the exact ledger operation, even after moving the ledger file.
    ledger.reconcile(operation, None, status="create_uncertain")
    machines = [SimpleNamespace(id="ours", state="running", metadata={"spend_operation": operation}),
                SimpleNamespace(id="another-session", state="running", metadata={"spend_operation": "not-ours"})]
    killed = []

    class Backend:
        def __init__(self, **kwargs):
            self.resources = {}

        async def list_machines(self, **kwargs):
            return [machine for machine in machines if machine.id not in killed]

        async def kill_machine(self, machine_id):
            killed.append(machine_id)

        async def close(self):
            pass

    monkeypatch.setattr("forkloop.backends.solari.SolariBackend", Backend)
    args = Namespace(ledger=str(ledger.path), all_sessions=False, dry_run=True)
    assert await _reap(args) == 0
    assert killed == []
    args.dry_run = False
    assert await _reap(args) == 0
    assert killed == ["ours"]
    service = ledger.summary()["services"]["solari"]
    assert service["actual_usd"] == 0
    assert service["pending_upper_usd"] == 0.7
    args.all_sessions = True
    assert await _reap(args) == 0
    assert killed == ["ours", "another-session"]


async def test_reap_without_scope_refuses_before_connecting(monkeypatch):
    monkeypatch.delenv("FORKLOOP_SESSION_LEDGER", raising=False)
    monkeypatch.delenv("SOLARI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="reap requires"):
        await _reap(Namespace(ledger=None, all_sessions=False, dry_run=False))
