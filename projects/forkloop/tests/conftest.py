"""Every test runs offline on the fake backend. A shell that has sourced ~/.config/forkloop/env
exports the real golden snapshot ids, which the fake backend cannot restore (five world tests
then fail with "unknown snapshot"), so the golden ids are scrubbed from the environment here."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _no_real_golden_ids(monkeypatch):
    for k in list(os.environ):
        if k.startswith("FORKLOOP_GOLDEN_"):
            monkeypatch.delenv(k, raising=False)


@pytest.fixture
def reviewed_solari_pricing(tmp_path, monkeypatch):
    """Lifecycle tests exercise resources, independent of the wall-clock review expiry."""
    import datetime as dt
    import json

    from forkloop.spending import load_solari_pricing

    monkeypatch.delenv("FORKLOOP_SOLARI_PRICING_FILE", raising=False)
    data = load_solari_pricing(today=dt.date(2026, 9, 15)).public_info()
    data.update(acknowledged=True, reviewed_on=dt.date.today().isoformat(),
                valid_until=(dt.date.today() + dt.timedelta(days=1)).isoformat())
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("FORKLOOP_SOLARI_PRICING_FILE", str(path))
    return path


@pytest.fixture
def simulated_solari_lifetime(monkeypatch):
    """Exercise lifecycle mechanics using test-local SDK fakes, never a provider."""
    monkeypatch.setattr("forkloop.spending.SolariPricing.reservation", lambda self, hourly_usd: hourly_usd)
    monkeypatch.setattr("forkloop.spending.require_solari_lifetime_bound", lambda: 5.0)
