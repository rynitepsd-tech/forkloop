import copy,hashlib,io,json
from pathlib import Path
from types import SimpleNamespace
import httpx,pytest
from PIL import Image
from scripts import saved_fixed_eval as runner
from scripts.evaluation_contract import sha,verify_dataset,validate_identity,BASE,REVISION,ADAPTER_SHA,AuditedPolicy
from scripts.compare_saved_evaluation import compare

@pytest.fixture
def package(tmp_path):
    p=tmp_path/'package';p.mkdir();(p/'images').mkdir();buf=io.BytesIO();Image.new('RGB',(1280,720),'white').save(buf,format='PNG');(p/'images/shot.png').write_bytes(buf.getvalue())
    cases=[{'case_id':str(i),'schema_version':'forkloop.observation.v3','instruction':'Navigate to OpenEMR','history':[],'step':i,'screen_size':[1280,720],'images':['images/shot.png']*2,'image_roles':['previous','current'],'image_steps':[i-1,i],'history_coordinate_space':'screen'} for i in [4,8]]
    labels=[{'case_id':str(i),'kind':'navigation','seed':100+i,'expected_action':{'type':'click','x':640,'y':90}} for i in [4,8]]
    for name,rows in [('cases.jsonl',cases),('labels.jsonl',labels)]: (p/name).write_text(''.join(json.dumps(r)+'\n' for r in rows))
    (p/'review.json').write_text(json.dumps({'approved':True,'cases_sha256':sha(p/'cases.jsonl'),'labels_sha256':sha(p/'labels.jsonl')}))
    m={'cases':2,'files':{n:sha(p/n) for n in ['cases.jsonl','labels.jsonl','review.json']},'assets':{'images/shot.png':{'sha256':sha(p/'images/shot.png')}}};(p/'manifest.json').write_text(json.dumps(m));return p

def identity(label):return {'base_model':BASE,'base_revision':REVISION,'adapter_sha256':ADAPTER_SHA if label=='trained' else None,'base_files_sha256':'same-base','serving_source_sha256':'same-source'}

async def test_fresh_policy_each_case_and_matched_aggregation(package,tmp_path,monkeypatch):
    calls=[];active_label='base';original=httpx.AsyncClient
    def handler(request):
        ident=identity(active_label)
        if request.url.path=='/health':return httpx.Response(200,json={'ready':True,'model_identity':ident})
        body=json.loads(request.content);calls.append(body);h=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
        raw='<tool_call>'+json.dumps({'name':'computer_use','arguments':{'action':'visit_url','url':'http://localhost/openemr/'}})+'</tool_call>'
        return httpx.Response(200,json={'model_identity':ident,'choices':[{'message':{'content':raw},'finish_reason':'stop'}],
            'evaluation_trace':{'request_sha256':h,'prompt_sha256':'same-prompt','image_grid_thw':[[1,10,10]],'input_tokens':100}})
    transport=httpx.MockTransport(handler)
    monkeypatch.setattr(httpx,'AsyncClient',lambda *a,**kw:original(*a,**{**kw,'transport':transport}))
    reports=[]
    for label in ['base','trained']:
        active_label=label
        a=SimpleNamespace(package=str(package),out=str(tmp_path/label),label=label,base_url='http://127.0.0.1:8011/v1',max_seconds=300)
        reports.append(await runner.run(a))
        assert all(r['calls']==1 for r in reports[-1]['results'])
        assert all('1/4' in r['meta']['macro'] for r in reports[-1]['results'])
        with pytest.raises(FileExistsError): await runner.run(a)
    assert len(calls)==4
    result=compare(package,*reports);assert result['matched_cases']==2
    changed=copy.deepcopy(reports[1]);changed['results'][0]['model_identity']['base_files_sha256']='wrong'
    assert compare(package,reports[0],changed)['unmatched_cases']==1
    missing=copy.deepcopy(reports[1]);missing['results'][0]={'case_id':'4','status':'missing','error':'deadline'}
    assert compare(package,reports[0],missing)['unmatched_cases']==1
    bad=copy.deepcopy(reports[1]);bad['results'][0]['meta']['finish_reason']='length';bad['results'][0]['metrics']['truncated']=True
    assert compare(package,reports[0],bad)['diagnostic_counts']['trained']['truncated']==1
    # Labels never enter the network request.
    assert all('expected_action' not in json.dumps(c) and 'case_id' not in json.dumps(c) for c in calls)

def test_integrity_and_identity_fail_closed(package):
    verify_dataset(package)
    with pytest.raises(ValueError):validate_identity(identity('base'),'trained')
    (package/'images/shot.png').write_bytes(b'changed')
    with pytest.raises(ValueError,match='image integrity'):verify_dataset(package)

async def test_missing_identity_stops_without_policy_calls(package,tmp_path,monkeypatch):
    original=httpx.AsyncClient;requests=[]
    def handler(request):requests.append(request);return httpx.Response(200,json={'ready':True})
    monkeypatch.setattr(httpx,'AsyncClient',lambda *a,**kw:original(*a,**{**kw,'transport':httpx.MockTransport(handler)}))
    report=await runner.run(SimpleNamespace(package=str(package),out=str(tmp_path/'missing'),label='base',base_url='http://127.0.0.1:8011/v1',max_seconds=300))
    assert len(requests)==1 and all(r['status']=='missing' for r in report['results'])
