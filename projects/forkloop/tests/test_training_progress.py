"""Regression for tail accumulation and honest interrupted-epoch reporting."""
import json
from types import SimpleNamespace
import pytest
torch=pytest.importorskip('torch')
from train import train_lora as t

@pytest.mark.parametrize('max_steps,steps,examples,epochs,fraction',[(None,6,20,2,2.0),(4,4,14,1,1.4)])
def test_epoch_tail_is_a_separate_accumulation_group(tmp_path,monkeypatch,max_steps,steps,examples,epochs,fraction):
 class Model(torch.nn.Module):
  def __init__(self):super().__init__();self.weight=torch.nn.Parameter(torch.tensor(0.0));self.config=SimpleNamespace(use_cache=True)
  def forward(self,**kw):return SimpleNamespace(loss=(self.weight-1).square())
  def save_pretrained(self,path):path.mkdir(parents=True,exist_ok=True)
 class Processor:
  def save_pretrained(self,path):pass
 monkeypatch.setattr(t,'load_model_and_processor',lambda *a,**kw:(Model(),Processor()))
 monkeypatch.setattr(t,'apply_lora',lambda m,**kw:m)
 monkeypatch.setattr(t,'load_records',lambda *a,**kw:[{} for _ in range(10)])
 monkeypatch.setattr(t,'SFTExamples',lambda records,**kw:records)
 monkeypatch.setattr(t,'make_collate',lambda p:lambda batch:{'input_ids':torch.ones((len(batch),1),dtype=torch.long)})
 monkeypatch.setattr(torch.cuda,'is_available',lambda:False)
 args=t.build_parser().parse_args(['--model','synthetic','--data','unused','--output-dir',str(tmp_path),'--epochs','2','--grad-accum','4','--dtype','fp32','--no-gradient-checkpointing','--save-steps','0'])
 args.max_steps=max_steps
 summary=t.train(args)
 assert summary['steps']==steps
 assert summary['examples_seen']==examples
 assert summary['epochs_completed']==epochs
 assert summary['epoch_fraction']==fraction
 assert summary['planned_steps']==steps
