"""Tests for forkloop.bench.cost_model against the verified Sept-2026 price list."""

from __future__ import annotations

import pytest

from forkloop.bench import cost_model as cm


def test_starter_medium_with_screen_is_0_134_per_hour():
    assert cm.vm_hour_cost("starter", 2, 4, screen=True) == pytest.approx(0.134)
    assert cm.vm_hour_cost("starter", 2, 4, screen=False) == pytest.approx(0.114)


def test_all_published_prices_present():
    assert cm.vm_hour_cost("pro", 1, 2, screen=False) == pytest.approx(0.040)
    assert cm.vm_hour_cost("free", 4, 8, screen=False) == pytest.approx(0.342)
    with pytest.raises(ValueError):
        cm.vm_hour_cost("starter", 8, 16)
    with pytest.raises(ValueError):
        cm.vm_hour_cost("enterprise", 2, 4)


def test_149_vm_hours_per_20_dollars_on_starter():
    assert round(cm.vm_hours_per_credit("starter", 2, 4)) == 149
    assert round(cm.vm_hours_per_credit("starter", 2, 4, credits_usd=20.0)) == 149


def test_pro_credits_not_published():
    with pytest.raises(ValueError):
        cm.vm_hours_per_credit("pro")


def test_cost_per_1k_resets_monotone_in_seconds():
    xs = [cm.cost_per_1k_resets("revert", "starter", s) for s in (0, 1, 2, 5, 10, 30, 60, 300)]
    assert all(a <= b for a, b in zip(xs, xs[1:]))
    assert xs[0] == 0.0
    # 10 s per reset × 1000 = 10,000 s = 2.777 h × $0.134
    assert cm.cost_per_1k_resets("revert", "starter", 10) == pytest.approx(10_000 / 3600 * 0.134)


def test_cost_per_1k_resets_overhead_and_methods():
    base = cm.cost_per_1k_resets("revert", "starter", 10)
    assert cm.cost_per_1k_resets("revert", "starter", 10, overhead_seconds=5) > base
    assert cm.cost_per_1k_resets("from_snapshot", "starter", 10) == pytest.approx(base)
    assert cm.cost_per_1k_resets("local", "starter", 10) == 0.0
    with pytest.raises(ValueError):
        cm.cost_per_1k_resets("teleport", "starter", 10)


def test_episode_cost_splits_env_and_teacher():
    c = cm.episode_cost("starter", "medium", 3600, 1_000_000, 0, 3.0, 15.0)
    assert c["env"] == pytest.approx(0.134)
    assert c["teacher"] == pytest.approx(3.0)
    assert c["total"] == pytest.approx(3.134)


def test_budget_table_rows_and_totals():
    rows = cm.budget_table()
    items = [r["item"] for r in rows]
    for expected in ("environment (Solari)", "teacher API", "GPU rental", "local box", "snapshot storage", "total"):
        assert expected in items
    total = rows[-1]
    assert total["item"] == "total"
    assert total["low_usd"] == pytest.approx(sum(r["low_usd"] for r in rows[:-1]))
    assert total["high_usd"] == pytest.approx(sum(r["high_usd"] for r in rows[:-1]))
    assert total["low_usd"] == pytest.approx(60.0)   # promo month: Solari $0
    assert total["high_usd"] == pytest.approx(350.0)
    assert cm.budget_table(solari_promo_month=False)[-1]["low_usd"] == pytest.approx(80.0)


def test_snapshot_storage_defaults_to_zero():
    assert cm.snapshot_storage_cost(10.0) == 0.0
    assert cm.snapshot_storage_cost(10.0, usd_per_gb_month=0.05) == pytest.approx(0.5)


def test_main_prints_markdown(capsys):
    cm.main()
    out = capsys.readouterr().out
    assert "| shape | starter | pro | free |" in out
    assert "0.134" in out
