"""Future GPU execution only: same server, frozen base then frozen adapter.

Does not create/rent hardware, train, contact Solari, or use hosted inference.
The caller must separately authorize and bound the fresh GPU's provider lifetime.
"""
import argparse,datetime,importlib.metadata,json,os,socket,subprocess,sys,time
from pathlib import Path
import httpx
from scripts.evaluation_contract import BASE,REVISION,ADAPTER_SHA,sha,verify_dataset,validate_identity

ROOT=Path(__file__).resolve().parents[1]
def verify_payload(root):
    root=Path(root).resolve();m=json.loads((root/'payload-manifest.json').read_text())
    for rel,h in m['files'].items():
        p=(root/rel).resolve()
        if not p.is_relative_to(root) or sha(p)!=h:raise ValueError(f'payload hash mismatch: {rel}')
    if sha(root/'adapter/adapter_model.safetensors')!=ADAPTER_SHA:raise ValueError('adapter identity mismatch')
    verify_dataset(root/'evaluation')
    return m

def main(a):
    verify_payload(ROOT)
    out=Path(a.out).resolve()
    if out.is_relative_to(ROOT/'adapter') or out.is_relative_to(ROOT/'evaluation'):raise ValueError('output must not touch frozen assets')
    out.mkdir(parents=True,exist_ok=False)
    versions={k:importlib.metadata.version(k) for k in ['torch','torchvision','transformers','peft','accelerate','pillow','huggingface-hub','fastapi','uvicorn']}
    expected=json.loads((ROOT/'environment-reference.json').read_text())['versions']
    if versions!=expected:raise ValueError(f'environment mismatch; expected {expected}, got {versions}')
    deadline=time.monotonic()+a.max_minutes*60
    (out/'execution-plan.json').write_text(json.dumps({'order':['base','trained'],'max_minutes':a.max_minutes,'versions':versions,'base':BASE,'revision':REVISION,'adapter_sha256':ADAPTER_SHA,'payload_manifest_sha256':sha(ROOT/'payload-manifest.json'),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat()},indent=2))
    for label in ['base','trained']:
        # Refuse an occupied port before starting our process; never stop another server.
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',8011))==0:raise RuntimeError('port 8011 already occupied')
        if deadline-time.monotonic()<300:raise RuntimeError('insufficient remaining deadline for next variant')
        command=[sys.executable,'-u','-m','scripts.lambda_serve','--model',BASE,'--revision',REVISION,'--log',str(out/f'{label}-requests.jsonl')]
        if label=='trained':command+=['--adapter',str(ROOT/'adapter'),'--expected-adapter-sha256',ADAPTER_SHA]
        with (out/f'{label}-server.log').open('x') as log:
            server=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            try:
                startup_deadline=min(deadline-150,time.monotonic()+600)
                while True:
                    if server.poll() is not None:raise RuntimeError(f'{label} server exited; see log')
                    if time.monotonic()>=startup_deadline:raise TimeoutError('server readiness deadline')
                    try:
                        health=httpx.get('http://127.0.0.1:8011/health',timeout=2).json()
                        validate_identity(health.get('model_identity'),label);break
                    except httpx.HTTPError:time.sleep(1)
                budget=deadline-time.monotonic()-60
                if budget<125:raise TimeoutError('no fixed-probe budget')
                subprocess.run([sys.executable,'-u','-m','scripts.saved_fixed_eval','--package',str(ROOT/'evaluation'),'--out',str(out/label),'--label',label,'--max-seconds',str(budget)],cwd=ROOT,check=True,timeout=budget+5)
            finally:
                server.terminate()
                try:server.wait(timeout=30)
                except subprocess.TimeoutExpired:server.kill();server.wait(timeout=10)
                (out/f'{label}-server-cleanup.json').write_text(json.dumps({'pid':server.pid,'returncode':server.returncode,'terminated':True},indent=2))
    from scripts.compare_saved_evaluation import compare
    report=compare(ROOT/'evaluation',json.loads((out/'base/results.json').read_text()),json.loads((out/'trained/results.json').read_text()))
    with (out/'comparison.json').open('x') as f:json.dump(report,f,indent=2)
    print(json.dumps(report['metrics'],indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--max-minutes',type=float,required=True);a=p.parse_args()
    if a.max_minutes<=0:p.error('max-minutes must be positive')
    main(a)
