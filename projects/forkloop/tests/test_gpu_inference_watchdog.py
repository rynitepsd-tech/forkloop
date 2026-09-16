import pytest
from types import SimpleNamespace
from scripts.gpu_inference_watchdog import upper_bound,utc,main,OLD_INSTANCE

def test_reserve_includes_entire_accrued_time_tax_and_cleanup():
    assert upper_bound(3.29,0,5400)==pytest.approx(6.58)
    assert upper_bound(3.29,0,10800)==pytest.approx(12.502)
    with pytest.raises(ValueError):utc('2026-09-06T20:00:00')

def test_old_instance_is_refused_before_network_or_outputs():
    with pytest.raises(ValueError,match='former instance'):main(SimpleNamespace(instance_id=OLD_INSTANCE))
