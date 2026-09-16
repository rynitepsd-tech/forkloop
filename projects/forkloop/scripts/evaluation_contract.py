"""Shared frozen inference contract and integrity checks. No model execution."""
import hashlib, json
from pathlib import Path
from forkloop.policies.student import StudentPolicy
BASE='microsoft/Fara1.5-4B'
REVISION='776a33ae5b2ad503796a97ae20fdc66f61d2feea'
ADAPTER_SHA='97b1f2d4581332ce679f555834c7ac842c54d585fe22799c702bcb64b3a5018d'
ROOT=Path(__file__).resolve().parents[1]
NOTE='Credentials for OpenEMR: username admin, password pass. Click the Username field, type admin, click the Password field, type pass, click Login.'
OPTIONS={'prompt_style':'fara','coord_space':'norm1000','max_tokens':512,'image_max_side':1280,'history_k':8,'prev_screenshot':True,'nav_macro':True,'timeout_s':120,'instruction_note':NOTE}
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()
def jsonl(p):return [json.loads(l) for l in Path(p).read_text().splitlines()]
def validate_identity(identity,label):
    if not identity or identity.get('base_model')!=BASE or identity.get('base_revision')!=REVISION:
        raise ValueError('missing or mismatched pinned base identity')
    if identity.get('adapter_sha256')!=(ADAPTER_SHA if label=='trained' else None):
        raise ValueError('adapter identity mismatch')
    for key in ['base_files_sha256','serving_source_sha256']:
        if not identity.get(key):raise ValueError(f'missing {key}')
    return identity

def verify_dataset(package):
    package=Path(package).resolve();m=json.loads((package/'manifest.json').read_text())
    for p,h in m['files'].items():
        path=(package/p).resolve()
        if not path.is_relative_to(package) or sha(path)!=h:raise ValueError(f'file integrity: {p}')
    for p,asset in m['assets'].items():
        path=(package/p).resolve()
        if not path.is_relative_to(package) or sha(path)!=asset['sha256']:raise ValueError(f'image integrity: {p}')
    review=json.loads((package/'review.json').read_text())
    if not review.get('approved') or review['cases_sha256']!=sha(package/'cases.jsonl') or review['labels_sha256']!=sha(package/'labels.jsonl'):
        raise ValueError('visual review not bound to these cases/labels')
    cases=jsonl(package/'cases.jsonl');labels=jsonl(package/'labels.jsonl');ids=[r['case_id'] for r in cases]
    if len(set(ids))!=len(ids) or ids!=[r['case_id'] for r in labels] or len(cases)!=m['cases']:raise ValueError('case identity mismatch')
    allowed={'case_id','schema_version','instruction','history','step','screen_size','images','image_roles','image_steps','history_coordinate_space'}
    for c,l in zip(cases,labels):
        if set(c)!=allowed:raise ValueError('unexpected policy input fields')
        if c['schema_version']!='forkloop.observation.v3' or c['image_roles']!=['previous','current'] or c['image_steps']!=[c['step']-1,c['step']] or c['history_coordinate_space']!='screen':raise ValueError('observation contract mismatch')
        if len(c['images'])!=2 or any(p not in m['assets'] for p in c['images']):raise ValueError('unmanifested image')
        if l.get('expected_authorization') and l['expected_authorization'] in json.dumps([c['instruction'],c['history']]):raise ValueError('text answer leakage')
    return m,cases,{r['case_id']:r for r in labels}

class AuditedPolicy(StudentPolicy):
    """Same policy behavior, with response identity retained outside model inputs."""
    def __init__(self,*a,weights_label,**kw):
        super().__init__(*a,**kw);self.weights_label=weights_label;self.last_response=None
    async def _post(self,body):
        data=await super()._post(body);self.last_response=data
        validate_identity(data.get('model_identity'),self.weights_label)
        return data
    async def act(self,obs):
        action,meta=await super().act(obs)
        if self.last_response:meta['model_identity']=self.last_response.get('model_identity')
        return action,meta

def make_policy(base_url,label):
    from urllib.parse import urlsplit
    if urlsplit(base_url).hostname not in ['127.0.0.1','localhost','::1']:raise ValueError('evaluation requires a loopback endpoint / SSH tunnel')
    return AuditedPolicy(base_url,'fara-v3',weights_label=label,system_prompt=(ROOT/'forkloop/policies/prompts/fara_no_user_v1.md').read_text(),**OPTIONS)
