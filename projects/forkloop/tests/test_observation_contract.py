"""Full observation parity and boundaries, independent of target answers."""
import asyncio
import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from forkloop.exporters.observations import image_fields
from forkloop.policies.observation import OBSERVATION_SCHEMA
from forkloop.policies.student import StudentPolicy
from forkloop.types import Observation
from train.train_lora import SFTExamples, build_messages


def png(color, size=(1280, 720)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='PNG')
    return buf.getvalue()


def canonical_http(messages):
    """Convert API transport blocks to HF blocks without dropping any text/order."""
    out = []
    for m in messages:
        c = m['content']
        if isinstance(c, str):
            c = [{'type': 'text', 'text': c}]
        else:
            c = [{'type': 'image'} if p['type'] == 'image_url' else p for p in c]
        out.append({'role': m['role'], 'content': c})
    return out


@pytest.mark.parametrize('style,space', [('fara','norm1000'),('compact','image'),('json','screen'),('fara','norm999')])
@pytest.mark.parametrize('history_k', [0, 1, 8])
@pytest.mark.parametrize('step', [0, 7])
async def test_complete_training_inference_messages_and_images(tmp_path, style, space, history_k, step):
    previous, current = png('red'), png('blue')
    paths = []
    for i, data in enumerate(([previous, current] if step else [current])):
        p = tmp_path / f'{i}.png'; p.write_bytes(data); paths.append(str(p))
    history = ['click(640, 360)', 'drag(128, 72, 1024, 576)', 'wait(1)', 'click(640, 360)', 'click(640, 360)', 'click(640, 360)', 'key("Tab")'] if step else []
    rec = dict(images=paths, instruction='Read the document and enter the value.', history=history, step=step,
               screen_size=[1280,720], target='type("TARGET-ONLY-SECRET")', reasoning='TARGET REASONING NEVER INPUT')
    template = '{fara_identity}\n{fara_tools}\nDisplay {w} {h}.' if style=='fara' else None
    ds = SFTExamples([rec],max_image_side=640,style=style,history_k=history_k,coord_space=space,
                     system_prompt_template=template,instruction_note='Synthetic convention.',nav_macro=True)
    ex = ds[0]
    pol = StudentPolicy('http://local.test/v1','test',image_max_side=640,prompt_style=style,coord_space=space,
                        history_k=history_k,prev_screenshot=True,system_prompt=template,
                        instruction_note='Synthetic convention.',nav_macro=True)
    try:
        obs=Observation(current,rec['instruction'],step,history,1280,720,previous if step else b'')
        messages,_=pol.build_messages(obs)
        assert canonical_http(messages)==ex['prompt_messages']
        assert 'TARGET-ONLY-SECRET' not in json.dumps(messages)
        assert 'TARGET REASONING' not in json.dumps(messages)
        assert pol.build_messages(obs)[0]==messages  # rendering is pure
        decoded=[Image.open(io.BytesIO(base64.b64decode(p['image_url']['url'].split(',')[1])))
                 for p in messages[1]['content'] if p['type']=='image_url']
        assert len(decoded)==len(ex['images'])
        assert [im.tobytes() for im in decoded]==[im.tobytes() for im in ex['images']]
    finally:
        await pol.aclose()


async def test_macro_previous_is_immediately_preceding_env_step():
    requests=[]
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'<tool_call>{"name":"computer_use","arguments":{"action":"visit_url","url":"http://example.test"}}</tool_call>'}}],'usage':{'prompt_tokens':10,'completion_tokens':4}})
    pol=StudentPolicy('http://local.test/v1','test',prompt_style='fara',nav_macro=True,prev_screenshot=True,
                      transport=httpx.MockTransport(respond))
    history=[]
    try:
        for i,color in enumerate(['red','green','blue','yellow','purple']):
            obs=Observation(png(color), 'Synthetic navigation', i, list(history),1280,720)
            action,_=await pol.act(obs); history.append(action.to_compact())
        assert len(requests)==2
        parts=[p for p in requests[-1]['messages'][1]['content'] if p['type']=='image_url']
        prev=Image.open(io.BytesIO(base64.b64decode(parts[0]['image_url']['url'].split(',')[1])))
        assert prev.getpixel((0,0))==(255,255,0)  # preceding macro step, not initial red screen
        pol.reset()
        messages,_=pol.build_messages(Observation(png('white'),'New episode',0,[],1280,720))
        assert len([p for p in messages[1]['content'] if p['type']=='image_url'])==1
        assert not pol._queue and not pol._notes
    finally: await pol.aclose()


def test_image_export_fails_closed_on_gaps_missing_and_cross_episode(tmp_path):
    root=tmp_path/'ep'; root.mkdir()
    for i in range(3): (root/f'{i}.png').write_bytes(png('white'))
    steps=[{'i':i,'shot_before':f'{i}.png'} for i in range(3)]
    fields=image_fields(root,steps,2)
    assert fields['schema_version']==OBSERVATION_SCHEMA
    assert fields['image_steps']==[1,2]
    assert fields['image_roles']==['previous','current']
    assert [Path(x).name for x in fields['images']]==['1.png','2.png']
    with pytest.raises(ValueError): image_fields(root,[steps[0],steps[2]],1)
    (root/'1.png').unlink()
    with pytest.raises(FileNotFoundError): image_fields(root,steps,2)
    with pytest.raises(ValueError): image_fields(root,[{'i':0,'shot_before':'../outside.png'}],0)


async def test_empty_choices_are_still_charged_and_state_clone_does_not_copy_client():
    pol=StudentPolicy('http://local.test/v1','test',transport=httpx.MockTransport(lambda req:
         httpx.Response(200,json={'choices':[],'usage':{'prompt_tokens':30,'completion_tokens':7}})))
    try:
        action,meta=await pol.act(Observation(png('white'),'x',0,[],1280,720))
        assert action is None and meta['tokens']=={'in':30,'out':7}
        pol._notes[0]='Observed note'; pol._queue=[({'type':'key','keys':['Tab']},'queued')]
        state=pol.snapshot_state(); clone=pol.clone_for_branch(state)
        assert clone._client is pol._client and clone.usage is pol.usage
        clone._notes[0]='Branch only'; clone._queue.clear()
        assert pol._notes[0]=='Observed note' and pol._queue
        clone.restore_state(state)
        assert clone._notes==pol._notes and clone._queue==pol._queue
        await clone.aclose()
        assert not pol._client.is_closed
    finally: await pol.aclose()


@pytest.mark.parametrize('style', ['fara', 'compact'])
async def test_v4_notes_render_identically_in_training_and_serving(tmp_path, style):
    """Recipe v4-notes: the notes a record carries are the notes serving shows, and serving
    derives them from Fara replies with the same function the dataset uses."""
    from forkloop.policies.student import note_from_reply

    previous, current = png('red'), png('blue')
    paths = []
    for i, data in enumerate([previous, current]):
        p = tmp_path / f'{i}.png'; p.write_bytes(data); paths.append(str(p))
    history = ['click(640, 360)', 'type("admin")', 'click(100, 200)']
    teacher_raw = ['Open the documents tab.\nclick(640, 360)', 'Log in.\ntype("admin")',
                   'The authorization number is AUTH-12A34567.\nclick(100, 200)']
    notes = [note_from_reply(r) for r in teacher_raw]
    rec = dict(images=paths, instruction='Enter the value.', history=history, step=3, notes=notes,
               screen_size=[1280, 720], target='type("AUTH-12A34567")')
    ds = SFTExamples([rec], max_image_side=640, style=style, history_k=8, coord_space='auto')
    pol = StudentPolicy('http://local.test/v1', 'test', image_max_side=640, prompt_style=style,
                        history_k=8, prev_screenshot=True, history_notes=True)
    try:
        # What the student itself replied on those steps, in Fara's tool-call format.
        fara_replies = ['\n</think>\n\n' + r.rsplit('\n', 1)[0] + '\n<tool_call>\n{"name": "computer_use", '
                        '"arguments": {"action": "wait", "time": 1}}\n</tool_call>' for r in teacher_raw]
        pol._notes = {i: note_from_reply(r) for i, r in enumerate(fara_replies)}
        obs = Observation(current, rec['instruction'], 3, history, 1280, 720, previous)
        messages, _ = pol.build_messages(obs)
        assert canonical_http(messages) == ds[0]['prompt_messages']
        assert 'AUTH-12A34567' in json.dumps(messages)  # carried by the note, not the target
    finally:
        await pol.aclose()
