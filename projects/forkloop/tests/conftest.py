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
