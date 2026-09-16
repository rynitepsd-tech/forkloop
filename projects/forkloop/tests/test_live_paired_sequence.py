import pytest
from scripts.lambda_development_eval import CELL_S, CLEANUP_S, COMPLETED, INCOMPLETE, SETUP_FAILED, parse_plan, sequence

PLAN = parse_plan('trained:200,base:200,base:201,trained:201')


def runner(script, calls):
    """`script` maps (label, seed, attempt) -> status; unlisted cells complete."""
    async def run_cell(label, seed, attempt):
        calls.append((label, seed, attempt))
        return {'status': script.get((label, seed, attempt), COMPLETED)}
    return run_cell


async def test_predeclared_order_completes_pair_before_next_seed():
    calls = []
    attempts, stop = await sequence(PLAN, runner({}, calls), remaining_seconds=lambda: 10 ** 6)
    assert calls == [('trained', 200, 1), ('base', 200, 1), ('base', 201, 1), ('trained', 201, 1)] and stop is None


async def test_setup_failure_gets_one_replacement_then_partner_runs():
    calls = []
    attempts, stop = await sequence(PLAN, runner({('trained', 200, 1): SETUP_FAILED}, calls), remaining_seconds=lambda: 10 ** 6)
    assert calls[:3] == [('trained', 200, 1), ('trained', 200, 2), ('base', 200, 1)]
    assert [a['status'] for a in attempts][:3] == [SETUP_FAILED, COMPLETED, COMPLETED]
    assert attempts[0]['recovery_scheduled'] and stop is None


async def test_second_setup_failure_is_never_retried():
    calls = []
    script = {('trained', 200, 1): SETUP_FAILED, ('base', 201, 1): SETUP_FAILED}
    attempts, stop = await sequence(PLAN, runner(script, calls), remaining_seconds=lambda: 10 ** 6)
    assert calls == [('trained', 200, 1), ('trained', 200, 2), ('base', 200, 1), ('base', 201, 1)]
    assert 'no model evidence' in stop and ('trained', 201, 1) not in calls and 'recovery_scheduled' not in attempts[-1]


async def test_model_episode_failure_is_not_rerun_but_partner_still_runs():
    calls = []
    attempts, stop = await sequence(PLAN, runner({('trained', 200, 1): INCOMPLETE}, calls), remaining_seconds=lambda: 10 ** 6)
    assert calls == [('trained', 200, 1), ('base', 200, 1)]
    assert 'secondary seed 201 not started' in stop


async def test_no_model_evidence_after_recovery_stops_before_partner():
    calls = []
    script = {('trained', 200, 1): SETUP_FAILED, ('trained', 200, 2): SETUP_FAILED}
    attempts, stop = await sequence(PLAN, runner(script, calls), remaining_seconds=lambda: 10 ** 6)
    assert calls == [('trained', 200, 1), ('trained', 200, 2)] and 'no model evidence' in stop


async def test_group_needs_time_for_both_cells_and_cleanup():
    calls = []
    clock = {'left': 2 * CELL_S + CLEANUP_S + 1}
    async def run_cell(label, seed, attempt):
        calls.append((label, seed)); clock['left'] -= CELL_S
        return {'status': COMPLETED}
    attempts, stop = await sequence(PLAN, run_cell, remaining_seconds=lambda: clock['left'])
    assert calls == [('trained', 200), ('base', 200)] and stop.startswith('budget stop before base:201')


@pytest.mark.parametrize('text', ['trained:200,base:201', 'base:200,trained:200,base:201', 'base:203,trained:203', 'base:200,base:200'])
def test_plan_must_pair_authorized_seeds(text):
    with pytest.raises(ValueError):
        parse_plan(text)
