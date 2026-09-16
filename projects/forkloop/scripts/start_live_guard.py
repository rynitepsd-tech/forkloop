"""Explicit future session ledger + detached watchdog; never creates a VM."""
import argparse,datetime,hashlib,json,os,subprocess,sys,time
from pathlib import Path
from forkloop.spending import SessionLedger

def main(a):
    prior=[{'path':str(Path(p).resolve()),'services':SessionLedger(p).summary()['services']} for p in a.prior_ledger]
    used=sum(p['services'].get('solari',{}).get('accounted_upper_usd',0) for p in prior)
    cap=min(a.new_ceiling,a.cumulative_ceiling-used)
    if cap<=0:raise ValueError('no authorized headroom')
    out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=False)
    ledger=SessionLedger.create(out/'session-ledger.sqlite',limits={'solari':{'ceiling':cap,'stop':cap},'openai':{'ceiling':0,'stop':0},'gpu':{'ceiling':0,'stop':0}})
    deadline=time.time()+a.minutes*60
    env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
    with (out/'watchdog.log').open('x') as f:
        p=subprocess.Popen([sys.executable,'-m','scripts.evaluation_watchdog','--ledger',str(ledger.path),'--out',str(out),'--deadline',str(deadline)],env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
    for _ in range(100):
        if (out/'watchdog-heartbeat.json').exists():break
        if p.poll() is not None:raise RuntimeError('watchdog failed; no VM may be created')
        time.sleep(.1)
    else:raise RuntimeError('watchdog did not start')
    row={'ledger':str(ledger.path),'watchdog_pid':p.pid,'deadline':datetime.datetime.fromtimestamp(deadline,datetime.timezone.utc).isoformat(),'prior':prior,'prior_accounted_upper_usd':used,'new_ceiling_usd':cap}
    (out/'guard.json').write_text(json.dumps(row,indent=2));print(json.dumps(row,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--minutes',type=float,required=True);p.add_argument('--new-ceiling',type=float,required=True);p.add_argument('--cumulative-ceiling',type=float,required=True);p.add_argument('--prior-ledger',action='append',required=True);main(p.parse_args())
