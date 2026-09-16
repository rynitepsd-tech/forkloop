"""Single-request Transformers serving for a matched base/LoRA experiment.

Accepts only inline screenshot data, never remote image URLs. Bind to loopback
and reach it through SSH. No expected answers or dataset metadata are loaded.
"""
from __future__ import annotations
import argparse,base64,io,json,time,uuid,hashlib,threading
from pathlib import Path
import torch
from PIL import Image
from fastapi import FastAPI,HTTPException
from train.train_lora import load_model_and_processor

def main():
 p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--adapter');p.add_argument('--port',type=int,default=8011);p.add_argument('--log',required=True);p.add_argument('--revision');p.add_argument('--expected-adapter-sha256')
 a=p.parse_args();torch.set_num_threads(8)
 source_model=a.model
 identity=None
 if a.revision:
  from scripts.evaluation_contract import BASE,REVISION,ADAPTER_SHA,sha
  if a.model!=BASE or a.revision!=REVISION:raise ValueError('frozen evaluation requires pinned Fara base')
  if a.adapter and (a.expected_adapter_sha256!=ADAPTER_SHA or sha(Path(a.adapter)/'adapter_model.safetensors')!=ADAPTER_SHA):raise ValueError('frozen adapter hash mismatch')
  from huggingface_hub import snapshot_download
  source_model=snapshot_download(repo_id=a.model,revision=a.revision)
  base_files={p.name:sha(p) for p in Path(source_model).iterdir() if p.is_file()}
  source_root=Path(__file__).resolve().parents[1]
  source_files={str(p.relative_to(source_root)):sha(p) for folder in ['forkloop','train','scripts'] for p in (source_root/folder).rglob('*.py')}
  source_files.update({str(p.relative_to(source_root)):sha(p) for p in (source_root/'forkloop/policies/prompts').glob('*.md')})
  identity={'base_model':a.model,'base_revision':a.revision,'base_files_sha256':hashlib.sha256(json.dumps(base_files,sort_keys=True).encode()).hexdigest(),'adapter_sha256':sha(Path(a.adapter)/'adapter_model.safetensors') if a.adapter else None,'serving_source_sha256':hashlib.sha256(json.dumps(source_files,sort_keys=True).encode()).hexdigest()}
  if Path(a.log).exists():raise ValueError('preserve previous server log')

 model,processor=load_model_and_processor(source_model,max_image_side=1280,attn='sdpa',dtype='bf16',device_map={'':0})
 if a.adapter:
  from peft import PeftModel
  model=PeftModel.from_pretrained(model,a.adapter,is_trainable=False)
 model.eval();model.config.use_cache=True
 app=FastAPI();log=Path(a.log);log.parent.mkdir(parents=True,exist_ok=True)
 @app.get('/health')
 def health():return {'ready':True,'base':a.model,'adapter':a.adapter,'dtype':'bf16','attention':'sdpa','model_identity':identity}
 @app.get('/v1/models')
 def models():return {'data':[{'id':'fara-v3','object':'model'}]}
 request_lock=threading.Lock()
 def complete_serially(body:dict):
  started=time.time();messages=[];images=[]
  for m in body['messages']:
   content=m['content']
   if isinstance(content,str): content=[{'type':'text','text':content}]
   parts=[]
   for c in content:
    if c['type']=='text':parts.append(c)
    elif c['type']=='image_url':
     url=c['image_url']['url']
     if not url.startswith('data:image/'):raise HTTPException(400,'inline images only')
     im=Image.open(io.BytesIO(base64.b64decode(url.split(',',1)[1]))).convert('RGB');images.append(im);parts.append({'type':'image'})
    else:raise HTTPException(400,'unsupported content')
   messages.append({'role':m['role'],'content':parts})
  if len(images) not in [1,2]:raise HTTPException(400,'one or two images required')
  if body.get('temperature',0)!=0 or body.get('n',1)!=1:raise HTTPException(400,'matched experiment requires greedy best-of-one')
  cap=min(int(body.get('max_tokens',512)),512)
  prompt=processor.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
  inputs=processor(text=[prompt],images=images,return_tensors='pt').to('cuda')
  n=inputs['input_ids'].shape[1]
  if n+cap>16384:raise HTTPException(400,'context limit')
  with torch.inference_mode():
   ids=model.generate(**inputs,max_new_tokens=cap,do_sample=False,use_cache=True)
  output=processor.tokenizer.decode(ids[0,n:],skip_special_tokens=True)
  row={'utc':time.time(),'model_identity':identity,'adapter':a.adapter,'request_sha256':hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest(),'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'image_count':len(images),'image_grid_thw':inputs['image_grid_thw'].tolist(),'input_tokens':n,'output_tokens':len(ids[0])-n,'elapsed_s':time.time()-started,'response':output,'peak_cuda_allocated_bytes':torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None}
  with log.open('a') as f:f.write(json.dumps(row)+'\n')
  return {'id':'chatcmpl-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),'model':'fara-v3','model_identity':identity,'evaluation_trace':{k:row[k] for k in ['request_sha256','prompt_sha256','image_grid_thw','input_tokens','output_tokens','elapsed_s','peak_cuda_allocated_bytes']},'choices':[{'index':0,'message':{'role':'assistant','content':output},'finish_reason':'length' if len(ids[0])-n==cap else 'stop'}],'usage':{'prompt_tokens':n,'completion_tokens':len(ids[0])-n,'total_tokens':len(ids[0])}}
 @app.post('/v1/chat/completions')
 def completion(body:dict):
  # Client cancellation does not stop an in-flight GPU generation.
  with request_lock:
   return complete_serially(body)
 import uvicorn
 uvicorn.run(app,host='127.0.0.1',port=a.port,workers=1)
if __name__=='__main__':main()
