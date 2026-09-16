"""Real cached Fara processor checks. No weights, inference, or GPU are loaded."""
import json
import os
from pathlib import Path
import pytest

torch=pytest.importorskip('torch')
transformers=pytest.importorskip('transformers')
from train.train_lora import SFTExamples,make_collate
from forkloop.policies.student import StudentPolicy
from forkloop.types import Observation
from tests.test_observation_contract import png,canonical_http


@pytest.fixture(scope='module')
def processor():
    try:
        return transformers.AutoProcessor.from_pretrained(os.environ.get('FORKLOOP_PROCESSOR_MODEL','microsoft/Fara1.5-4B'),local_files_only=True)
    except OSError:
        pytest.skip('Fara processor is not cached locally')


async def test_fara_real_processor_all_images_prefix_and_target_mask(tmp_path,processor):
    paths=[]
    for i,color in enumerate(['red','blue']):
        p=tmp_path/f'{i}.png'; p.write_bytes(png(color)); paths.append(str(p))
    base=dict(instruction='Read only the observed synthetic document.',screen_size=[1280,720],
              target='type("TARGETONLY123")',reasoning='Continuation only.')
    records=[dict(base,images=[paths[0]],history=[],step=0),
             dict(base,images=paths,history=['click(640, 360)'],step=1)]
    ds=SFTExamples(records,max_image_side=1280,style='fara',history_k=8)
    exs=[ds[0],ds[1]]; collate=make_collate(processor); batch=collate(exs)
    assert batch['image_grid_thw'].shape[0]==3
    assert batch['pixel_values'].shape[0]==batch['image_grid_thw'].prod(dim=1).sum()
    assert batch['mm_token_type_ids'].shape==batch['input_ids'].shape
    assert collate.stats_rows[1]['image_tokens']==2*collate.stats_rows[0]['image_tokens']
    pol=StudentPolicy('http://local.test/v1','fara',prompt_style='fara',prev_screenshot=True)
    try:
        for i,(rec,ex,stats) in enumerate(zip(records,exs,collate.stats_rows)):
            obs=Observation(Path(rec['images'][-1]).read_bytes(),rec['instruction'],rec['step'],rec['history'],1280,720,
                            Path(paths[0]).read_bytes() if i else b'')
            http,_=pol.build_messages(obs)
            serving=processor.apply_chat_template(canonical_http(http),tokenize=False,add_generation_prompt=True)
            assert serving==processor.apply_chat_template(ex['prompt_messages'],tokenize=False,add_generation_prompt=True)
            encoded=processor(text=[serving],images=ex['images'],return_tensors='pt')
            n=stats['prompt_len']
            assert torch.equal(batch['input_ids'][i,:n],encoded['input_ids'][0])
            assert torch.all(batch['labels'][i,:n]==-100)
            assert torch.all(batch['labels'][i,stats['seq_len']:]==-100)
            assert torch.all(batch['labels'][i,n:stats['seq_len']]!=-100)
            target=processor.tokenizer.decode(batch['labels'][i,n:stats['seq_len']].tolist())
            full=processor.apply_chat_template(ex['full_messages'],tokenize=False,add_generation_prompt=False)
            assert target==full[len(serving):]
            assert 'TARGETONLY123' in target and 'TARGETONLY123' not in serving
            assert 'Continuation only.' in target
    finally: await pol.aclose()
