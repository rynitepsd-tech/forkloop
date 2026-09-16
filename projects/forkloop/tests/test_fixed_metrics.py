import json
from pathlib import Path
from forkloop.fixed_metrics import score
ROOT=Path(__file__).resolve().parents[1]
def call(action,**kw):return '<tool_call>'+json.dumps({'name':'computer_use','arguments':{'action':action,**kw}})+'</tool_call>'
def label(target='AUTH-61H18482'):return {'kind':'authorization','expected_action':{'type':'type','text':target},'expected_authorization':target}
def test_actual_seed140_memory_and_seed141_typo():
    fixtures=json.loads((ROOT/'tests/fixtures/v3-historical-actions.json').read_text())
    r=fixtures[0]['result']
    s=score(r['meta']['raw_action'],r['action'],r['meta'],label())
    assert s['exact_authorization_emitted'] and not s['exact_authorization_type_selected']
    assert r['action']['type']=='wait'
    r=fixtures[1]['result']
    s=score(r['meta']['raw_action'],r['action'],r['meta'],label('AUTH-57A40046'))
    assert not s['exact_authorization_emitted']
    assert s['incorrect_authorizations'][0]['edit_distance']==1
    assert s['incorrect_authorizations'][0]['edits'][0]['expected']=='0'
def test_prose_wrong_field_and_ambiguous_never_read_credit():
    for raw in ['The answer is AUTH-61H18482',call('wait',fact='AUTH-61H18482'),call('pause_and_memorize_fact',fact='AUTH-61H18482 or AUTH-61H18483'),call('type',text='AUTH-61H18482')*2]:
        assert not score(raw,None,{},label())['exact_authorization_emitted']
def test_malformed_unsupported_and_truncated():
    s=score('<tool_call>{',None,{'finish_reason':'length'},label());assert s['structured_parse_error'] and s['truncated']
    assert score(call('web_search',query='x'),None,{},label())['unsupported_action']
def test_alternative_navigation_is_not_task_failure():
    s=score(call('left_click',coordinate=[100,100]),{'type':'click','x':100,'y':100},{},{'kind':'navigation','expected_action':{'type':'click','x':900,'y':400}})
    assert not s['navigation_agreement'] and s['navigation_disagreement_interpretation'].startswith('unadjudicated')
