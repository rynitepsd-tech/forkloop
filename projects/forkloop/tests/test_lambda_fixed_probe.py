"""Fixed observations must not consume a preceding case's navigation queue."""
import json
from types import SimpleNamespace
import httpx
from scripts import lambda_fixed_probe as probe
from forkloop.policies.student import StudentPolicy
from tests.test_observation_contract import png

async def test_independent_fixed_observations_each_request_model(tmp_path,monkeypatch):
 shot=tmp_path/'shot.png';shot.write_bytes(png('white'))
 rows=[dict(images=[str(shot),str(shot)],instruction='Navigate to OpenEMR.',step=i,history=[],screen_size=[1280,720],target='click(640, 90)',seed=20+i) for i in [4,8]]
 data=tmp_path/'data.jsonl';data.write_text(''.join(json.dumps(r)+'\n' for r in rows))
 calls=[]
 def handler(request):
  calls.append(json.loads(request.content))
  content='<tool_call>\n'+json.dumps({'name':'computer_use','arguments':{'action':'visit_url','url':'http://localhost/openemr/'}})+'\n</tool_call>'
  return httpx.Response(200,json={'choices':[{'message':{'content':content}}]})
 def policy(*args,**kwargs):return StudentPolicy(*args,**kwargs,transport=httpx.MockTransport(handler))
 monkeypatch.setattr(probe,'StudentPolicy',policy)
 out=tmp_path/'out.json'
 await probe.run(SimpleNamespace(data=str(data),indices='0,1',out=str(out),base_url='http://local.test/v1',label='test'))
 report=json.loads(out.read_text())
 assert len(calls)==2
 assert report['results'][0]['action']==report['results'][1]['action']
 assert all('1/4' in r['meta']['macro'] for r in report['results'])
