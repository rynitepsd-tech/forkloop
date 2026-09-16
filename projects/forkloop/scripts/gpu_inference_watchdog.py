"""Future authorized Lambda instance watchdog, run on controller Mac, not GPU.

Only observes/terminates the explicitly identified instance. No launch, storage,
account cleanup, model or training API. Reservation includes accrued lifetime.
"""
import argparse,datetime,json,os,time
from pathlib import Path
import httpx
from forkloop.spending import SessionLedger
OLD_INSTANCE='5112d6f2f4b74e93b9cac3f8db08e677'

def utc(value):
    d=datetime.datetime.fromisoformat(value)
    if d.tzinfo is None:raise ValueError('timezone-aware UTC time required')
    return d.timestamp()
def upper_bound(rate,start,deadline):
    if not 0<rate or not start<deadline:raise ValueError('invalid rate or lifetime')
    return rate*(deadline-start+600)/3600*1.2

def main(a):
    if a.instance_id==OLD_INSTANCE:raise ValueError('the terminated former instance is never a target')
    start=utc(a.billing_start);deadline=utc(a.deadline)
    if start>time.time() or deadline<=time.time():raise ValueError('billing start must include accrued lifetime and deadline must be future')
    out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=False)
    ledger=SessionLedger.create(out/'session-ledger.sqlite',limits={'gpu':{'ceiling':a.ceiling,'stop':a.ceiling},'openai':{'ceiling':0,'stop':0},'solari':{'ceiling':0,'stop':0}})
    op=ledger.reserve('gpu',upper_bound(a.hourly_usd,start,deadline),label='inference-instance-entire-lifetime',evidence={'instance_id':a.instance_id,'name':a.instance_name,'billing_start':start,'deadline':deadline,'hourly_usd':a.hourly_usd,'tax_allowance':.2,'cleanup_seconds':600})
    key=os.environ['LAMBDA_API_KEY']
    def event(name,**kw):
        with (out/'events.jsonl').open('a') as f:f.write(json.dumps({'utc':time.time(),'event':name,**kw})+'\n')
    with httpx.Client(base_url='https://cloud.lambda.ai/api/v1/',auth=(key,''),timeout=25,headers={'User-Agent':'forkloop-inference-watchdog'}) as client:
        while True:
            try:
                response=client.get('instances/'+a.instance_id);response.raise_for_status();d=response.json()['data']
                if d['id']!=a.instance_id or (d.get('name') or '')!=a.instance_name:raise ValueError('provider identity mismatch; refuse unrelated termination')
                row={'utc':time.time(),'instance_id':a.instance_id,'name':a.instance_name,'status':d['status'],'deadline':deadline,'ledger':str(ledger.path),'operation':op}
                (out/'heartbeat.json').write_text(json.dumps(row,indent=2))
                if d['status']=='terminated':
                    evidence={**row,'estimated_compute_usd':a.hourly_usd*(time.time()-start)/3600,'actual_invoice_usd':None}
                    ledger.reconcile(op,None,status='terminated_invoice_pending',evidence=evidence)
                    (out/'termination-confirmed.json').write_text(json.dumps(evidence,indent=2));(out/'spend.json').write_text(json.dumps(ledger.summary(),indent=2));return
                if (time.time()>=deadline or (out/'terminate-now').exists()) and d['status']!='terminating':
                    r=client.post('instance-operations/terminate',json={'instance_ids':[a.instance_id]});r.raise_for_status();event('termination_requested',instance_id=a.instance_id)
                if time.time()>deadline+600:event('cleanup_overdue',instance_id=a.instance_id)
            except ValueError:raise
            except Exception as e:event('provider_error',error_type=type(e).__name__)
            time.sleep(15)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--instance-id',required=True);p.add_argument('--instance-name',required=True);p.add_argument('--billing-start',required=True);p.add_argument('--deadline',required=True);p.add_argument('--hourly-usd',type=float,required=True);p.add_argument('--ceiling',type=float,required=True);p.add_argument('--out',required=True);main(p.parse_args())
