"""The opt-in Solari lifetime bound that replaced the unconditional hold (2026-09-23)."""
from __future__ import annotations

import pytest

from forkloop.spending import BudgetExceeded, require_solari_lifetime_bound, solari_allocation_status


def test_allocation_refuses_without_both_opt_ins(monkeypatch):
    monkeypatch.delenv("FORKLOOP_SOLARI_MAX_LIFETIME_MIN", raising=False)
    monkeypatch.delenv("FORKLOOP_SOLARI_ACCEPT_BALANCE_BOUND", raising=False)
    with pytest.raises(BudgetExceeded):
        require_solari_lifetime_bound()
    monkeypatch.setenv("FORKLOOP_SOLARI_MAX_LIFETIME_MIN", "90")
    with pytest.raises(BudgetExceeded):  # the balance acknowledgment is still missing
        require_solari_lifetime_bound()
    assert solari_allocation_status().startswith("solari: blocked")


@pytest.mark.parametrize("value", ["4", "301", "nan", "soon"])
def test_lifetime_must_be_a_sane_number_of_minutes(monkeypatch, value):
    monkeypatch.setenv("FORKLOOP_SOLARI_MAX_LIFETIME_MIN", value)
    monkeypatch.setenv("FORKLOOP_SOLARI_ACCEPT_BALANCE_BOUND", "1")
    with pytest.raises(BudgetExceeded):
        require_solari_lifetime_bound()


def test_opt_in_returns_hours_and_sizes_the_reservation(monkeypatch):
    import datetime as dt

    from forkloop.spending import load_solari_pricing

    monkeypatch.setenv("FORKLOOP_SOLARI_MAX_LIFETIME_MIN", "90")
    monkeypatch.setenv("FORKLOOP_SOLARI_ACCEPT_BALANCE_BOUND", "1")
    assert require_solari_lifetime_bound() == 1.5
    pricing = load_solari_pricing(today=dt.date(2026, 9, 20))
    assert pricing.reservation(1.0) == pytest.approx(1.5 + 10 / 60)
    assert "killed after 90 minutes" in solari_allocation_status()


async def test_machines_past_their_deadline_are_killed():
    from forkloop.backends.solari import SolariBackend

    b = SolariBackend.__new__(SolariBackend)
    killed = []

    async def kill_machine(machine_id):
        killed.append(machine_id)
        b.resources[machine_id]["closed"] = True

    b.kill_machine = kill_machine  # type: ignore[method-assign]
    b.resources = {"old": {"closed": False, "deadline": 100.0}, "young": {"closed": False, "deadline": 1e12},
                   "done": {"closed": True, "deadline": 100.0}}
    assert await b.enforce_lifetimes_once(now=200.0) == ["old"]
    assert killed == ["old"] and b.resources["old"]["killed_at_deadline"]
