"""Offline, processor-only training smoke. Uses cached assets, no model weights or GPU.

Example: python -m train.check_inputs --data data/sft_f3_25_v3.jsonl --indices 0,1,44,89 --out smoke.json
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

from .train_lora import SFTExamples, load_records, make_collate


def check_inputs(data: str, *, model: str, indices: list[int], out: str,
                 system_prompt_file: str | None = None, instruction_note: str | None = None,
                 max_image_side: int = 1280, max_seq_len: int = 16384, history_k: int = 8,
                 nav_macro: bool = True) -> dict:
    import torch
    from transformers import AutoProcessor
    records=load_records(data)
    selected=[records[i] for i in indices]
    template=Path(system_prompt_file).read_text() if system_prompt_file else None
    dataset=SFTExamples(selected,max_image_side=max_image_side,style='fara',history_k=history_k,
                        coord_space='norm1000',system_prompt_template=template,
                        instruction_note=instruction_note,nav_macro=nav_macro)
    processor=AutoProcessor.from_pretrained(model,local_files_only=True)
    collate=make_collate(processor)
    samples=[]
    for start in range(0,len(dataset),2):
        examples=[dataset[i] for i in range(start,min(start+2,len(dataset)))]
        batch=collate(examples)
        assert batch['image_grid_thw'].shape[0]==sum(len(e['images']) for e in examples)
        for b,example in enumerate(examples):
            i=start+b; record=selected[i]; stats=collate.stats_rows[-len(examples)+b]
            prompt=processor.apply_chat_template(example['prompt_messages'],tokenize=False,add_generation_prompt=True)
            prefix=processor(text=[prompt],images=example['images'],return_tensors='pt')['input_ids'][0]
            assert torch.equal(batch['input_ids'][b,:len(prefix)],prefix)
            assert (batch['labels'][b,:len(prefix)]==-100).all()
            assert (batch['labels'][b,stats['seq_len']:]==-100).all()
            assert stats['seq_len']<=max_seq_len, (indices[i],stats['seq_len'],max_seq_len)
            labels=batch['labels'][b][batch['labels'][b]!=-100]
            decoded=processor.tokenizer.decode(labels.tolist())
            full=processor.apply_chat_template(example['full_messages'],tokenize=False,add_generation_prompt=False)
            assert decoded==full[len(prompt):]
            samples.append({'record_index':indices[i], 'episode_id':record['episode_id'],'step':record['step'],
                            'images':len(example['images']),'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                            'target_label_text':decoded,**stats})
    result={'passed':True,'data':str(Path(data).resolve()),'data_sha256':hashlib.sha256(Path(data).read_bytes()).hexdigest(),
            'model':model,'processor_class':type(processor).__name__,'samples':samples,
            'configuration':dict(system_prompt_file=system_prompt_file,instruction_note=instruction_note,
                                 max_image_side=max_image_side,history_k=history_k,nav_macro=nav_macro,max_seq_len=max_seq_len),
            'versions':{p:importlib.metadata.version(p) for p in ['torch','transformers','pillow']},
            'model_execution_verified':False,'backward_verified':False,'gpu_execution_verified':False}
    Path(out).parent.mkdir(parents=True,exist_ok=True)
    Path(out).write_text(json.dumps(result,indent=2))
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',required=True); p.add_argument('--model',default='microsoft/Fara1.5-4B')
    p.add_argument('--indices',default='0,1,2,3'); p.add_argument('--out',required=True)
    p.add_argument('--system-prompt-file'); p.add_argument('--instruction-note')
    p.add_argument('--max-image-side',type=int,default=1280); p.add_argument('--max-seq-len',type=int,default=16384)
    p.add_argument('--history-k',type=int,default=8); p.add_argument('--nav-macro',action=argparse.BooleanOptionalAction,default=True)
    args=vars(p.parse_args(argv)); args['indices']=[int(i) for i in args['indices'].split(',')]
    result=check_inputs(**args)
    print(json.dumps({'passed':result['passed'],'examples':len(result['samples']),
                      'seq_len_max':max(s['seq_len'] for s in result['samples']),'gpu_execution_verified':False}))
    return 0


if __name__=='__main__': raise SystemExit(main())
