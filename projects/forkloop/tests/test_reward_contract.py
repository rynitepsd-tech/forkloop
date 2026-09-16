"""Instruction/oracle controls. Portal routes are real; OpenEMR uses its SQLite shim and synthetic audit rows."""
import pytest

from forkloop.env import Env
from forkloop.world import load_world
from forkloop.backends.fake import FakeBackend
from forkloop.oracle import OracleSpec
from tests.test_claims_ops_world import portal_client, openemr_ui_update_insurance
from worlds.claims_ops_v1.openemr import openemr_sql as osql


@pytest.fixture
def backend(tmp_path):
    b=FakeBackend(base_dir=tmp_path/'fake')
    yield b
    b.cleanup()


@pytest.mark.parametrize('mutation,failed', [
    ('none',None),('wrong_plan','openemr_plan'),('wrong_member','openemr_policy'),
    ('subscriber','insurance_fields_preserved'),('claim_amount','claim_fields_preserved'),
    ('other_claim','no_collateral'),('other_resubmission','other_resubmissions_preserved'),
    ('resubmission_member','resubmission_member'),('duplicate','single_resubmission')])
async def test_insurance_material_requirements(backend,mutation,failed):
    w=load_world('claims-ops-v1'); e=Env(w,backend,family='update_insurance_reconcile',settle_s=0)
    seed=next(s for s in range(30) if w.generate('update_insurance_reconcile',s,'train').difficulty['both_systems'])
    try:
        await e.reset(seed); ex=e.ep.task.expected
        assert OracleSpec.from_dict(e.ep.task.oracle.to_dict()).to_dict()==e.ep.task.oracle.to_dict()
        await openemr_ui_update_insurance(e,ex['patient_pid'],ex['new_member'],ex['new_plan'],990001)
        with portal_client(e) as c:
            c.post(f"/claims/{ex['claim_number']}/resubmit",data={'member_id':ex['new_member'],'note':'corrected'})
        db=e.ep.dbs
        if mutation=='wrong_plan': await db['openemr'].execute_script(osql.update_row('insurance_data',{'plan_name':'Wrong Plan'},{'pid':ex['patient_pid']}))
        if mutation=='wrong_member': await db['openemr'].execute_script(osql.update_row('insurance_data',{'policy_number':'Wrong Member'},{'pid':ex['patient_pid']}))
        if mutation=='subscriber': await db['openemr'].execute_script(osql.update_row('insurance_data',{'subscriber_lname':'Unrelated Edit'},{'pid':ex['patient_pid']}))
        if mutation=='claim_amount': await db['portal'].execute_script(f"UPDATE claims SET amount_cents=amount_cents+1 WHERE id={ex['claim_id']};")
        if mutation=='other_claim': await db['portal'].execute_script(f"UPDATE claims SET status='DENIED' WHERE claim_number='{ex['other_claim']}';")
        if mutation=='other_resubmission': await db['portal'].execute_script("UPDATE resubmissions SET note='unauthorized' WHERE id=(SELECT MIN(id) FROM resubmissions);")
        if mutation=='resubmission_member': await db['portal'].execute_script(f"UPDATE resubmissions SET member_id='wrong' WHERE claim_id={ex['claim_id']};")
        if mutation=='duplicate': await db['portal'].execute_script(f"INSERT INTO resubmissions (claim_id,member_id,note,created_at) VALUES ({ex['claim_id']},'{ex['new_member']}','duplicate','2026-09-08');")
        verdict=await e.verify()
        if failed: assert verdict.reward==0 and failed in verdict.failed,verdict.to_dict()
        else: assert verdict.reward==1,verdict.to_dict()
    finally: await e.close()


async def test_portal_only_variant_preserves_insurance_row(backend):
    w=load_world('claims-ops-v1'); e=Env(w,backend,family='update_insurance_reconcile',settle_s=0)
    seed=next(s for s in range(50) if w.generate('update_insurance_reconcile',s,'train').difficulty['partially_updated'])
    try:
        await e.reset(seed); ex=e.ep.task.expected
        with portal_client(e) as c:
            c.post(f"/claims/{ex['claim_number']}/resubmit",data={'member_id':ex['new_member'],'note':'corrected'})
        assert (await e.verify()).reward==1
        e.ep.verdict=None
        await e.ep.dbs['openemr'].execute_script(osql.update_row('insurance_data',{'subscriber_fname':'unrelated'},{'pid':ex['patient_pid']}))
        v=await e.verify(); assert v.reward==0 and 'insurance_fields_preserved' in v.failed
    finally: await e.close()


@pytest.mark.parametrize('mutation,failed',[
    ({},None),({'pc_catid':1},'visit_type_unchanged'),({'pc_title':'New visit'},'event_fields_preserved'),
    ({'pc_pid':'100001'},'event_fields_preserved'),({'pc_aid':'0'},'provider_unchanged'),
    ({'pc_duration':1800},'event_fields_preserved'),({'pc_hometext':'Unrelated note'},'event_fields_preserved'),
    ({'pc_endDate':'2020-01-01'},'event_end_date'),({'pc_eventDate':'2020-01-01'},'event_date'),
    ({'pc_startTime':'23:59:00'},'event_time_window'),({'pc_endTime':'00:00:00'},'event_duration_consistent')])
async def test_reschedule_material_requirements(backend,mutation,failed):
    e=Env(load_world('claims-ops-v1'),backend,family='reschedule_constrained',settle_s=0)
    try:
        await e.reset(3); ex=e.ep.task.expected
        fields={'pc_eventDate':ex['target_date'],'pc_endDate':ex['target_date'],'pc_startTime':ex['window'][0],'pc_duration':900,**mutation}
        await e.ep.dbs['openemr'].execute_script(osql.update_appointment(pc_eid=ex['event_id'],**fields)+'\n'+
            osql.insert_log(id=990002,event='scheduling-update',category='Scheduling',user='admin',patient_id=ex['patient_pid'],comments='moved',date='2026-09-08 10:00:00'))
        v=await e.verify()
        if failed: assert v.reward==0 and failed in v.failed,v.to_dict()
        else: assert v.reward==1,v.to_dict()
    finally: await e.close()
