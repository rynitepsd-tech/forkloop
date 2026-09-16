"""Build a fresh local inference-only transfer directory; no network or models."""
import argparse,json,shutil,tarfile
from pathlib import Path
from scripts.evaluation_contract import ROOT,BASE,REVISION,ADAPTER_SHA,sha,verify_dataset

def stage(a):
    source=Path(a.package).resolve();verify_dataset(source)
    adapter=ROOT/'runs/lambda-v3-20260906/final-artifacts/project/checkpoints/v3-main25/final'
    if sha(adapter/'adapter_model.safetensors')!=ADAPTER_SHA:raise ValueError('frozen adapter hash mismatch')
    out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=False)
    # Explicit source directories/extensions; no env files, ledgers, datasets or run trees.
    for folder in ['forkloop','train','worlds']:
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and p.suffix in ['.py','.md','.yaml','.yml','.json','.sql','.html','.css','.sh','.js'] and '__pycache__' not in p.parts:
                target=out/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    scripts=['lambda_serve','lambda_fixed_probe','evaluation_contract','saved_fixed_eval','compare_saved_evaluation','run_inference_only','lambda_development_eval','lambda_score_development','evaluation_watchdog','start_live_guard','compare_live_evaluation']
    (out/'scripts').mkdir()
    for name in scripts:shutil.copyfile(ROOT/f'scripts/{name}.py',out/f'scripts/{name}.py')
    for name in ['pyproject.toml','README.md']:shutil.copyfile(ROOT/name,out/name)
    (out/'tests/fixtures').mkdir(parents=True)
    for name in ['test_fixed_metrics.py','test_saved_evaluation.py','test_solari_readiness_deadline.py','test_lambda_serving_serialization.py','test_evaluation_live_guards.py']:
        shutil.copyfile(ROOT/'tests'/name,out/'tests'/name)
    shutil.copyfile(ROOT/'tests/fixtures/v3-historical-actions.json',out/'tests/fixtures/v3-historical-actions.json')
    shutil.copytree(adapter,out/'adapter');shutil.copytree(source,out/'evaluation')
    archive=ROOT/'runs/lambda-v3-20260906/final-artifacts'
    shutil.copyfile(archive/'project/runs/lambda-v3/environment.json',out/'environment-reference.json')
    (out/'environment-evidence').mkdir()
    for name in ['dependencies.log','torch-install.log','package-freeze.txt']:shutil.copyfile(archive/name,out/'environment-evidence'/name)
    # Reconstruct the package pins from recorded installation receipts; pip freeze itself failed.
    import re
    pins={}
    for name in ['dependencies.log','torch-install.log']:
        for line in (archive/name).read_text().splitlines():
            m=re.match(r' \+ ([\w-]+)==([^\s]+)$',line)
            if m:pins[m[1]]=m[2]
    (out/'requirements-recorded.txt').write_text('\n'.join(f'{k}=={v}' for k,v in sorted(pins.items()) if k!='forkloop')+'\n')
    manifest={'schema':'forkloop.inference-payload.v1','base':BASE,'revision':REVISION,'adapter_sha256':ADAPTER_SHA,'source_origin':str(ROOT),'evaluation_manifest_sha256':sha(source/'manifest.json'),'files':{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}}
    (out/'payload-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'payload':str(out),'files':len(manifest['files']),'manifest_sha256':sha(out/'payload-manifest.json'),'bytes':sum(p.stat().st_size for p in out.rglob('*') if p.is_file())},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package',required=True);p.add_argument('--out',required=True);stage(p.parse_args())
