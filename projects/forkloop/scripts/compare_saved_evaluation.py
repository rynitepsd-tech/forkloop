"""Match inputs and verified identities before aggregating; retain missing/failures."""
import argparse,json
from pathlib import Path
from scripts.evaluation_contract import verify_dataset,validate_identity,sha

METRICS=['exact_authorization_emitted','exact_authorization_type_selected','exact_authorization_runtime_type','navigation_agreement']
def compare(package,base,trained):
    manifest,cases,labels=verify_dataset(package)
    for report,label in [(base,'base'),(trained,'trained')]:
        if report.get('label')!=label or report['dataset_manifest_sha256']!=sha(Path(package)/'manifest.json'):raise ValueError('run label or dataset manifest mismatch')
        if report.get('decoding')!={'temperature':0,'max_tokens':512,'best_of':1}:raise ValueError('decoding mismatch')
        if report['planned_case_ids']!=[c['case_id'] for c in cases]:raise ValueError('planned case mismatch')
        ids=[r['case_id'] for r in report['results']]
        if len(ids)!=len(set(ids)) or set(ids)-set(report['planned_case_ids']):raise ValueError('duplicate or extra results')
    maps=[{r['case_id']:r for r in report['results']} for report in [base,trained]]
    pairs=[];unmatched=[]
    for c in cases:
        cid=c['case_id'];a,b=[m.get(cid) for m in maps];reasons=[]
        if not a or not b:reasons.append('missing_result')
        elif a.get('status')=='missing' or b.get('status')=='missing':reasons.append('not_executed')
        else:
            try:
                validate_identity(a.get('model_identity'),'base');validate_identity(b.get('model_identity'),'trained')
                for key in ['base_files_sha256','serving_source_sha256']:
                    if a['model_identity'][key]!=b['model_identity'][key]:reasons.append(key+'_mismatch')
            except ValueError as e:reasons.append(str(e))
            for key in ['request_sha256','image_sha256']:
                if not a.get(key) or a.get(key)!=b.get(key):reasons.append(key+'_mismatch')
            ta=(a.get('raw_response') or {}).get('evaluation_trace',{});tb=(b.get('raw_response') or {}).get('evaluation_trace',{})
            for key in ['request_sha256','prompt_sha256','image_grid_thw','input_tokens']:
                if key not in ta or ta.get(key)!=tb.get(key):reasons.append('server_'+key+'_missing_or_mismatch')
            if ta.get('request_sha256')!=a.get('request_sha256'):reasons.append('server_client_request_mismatch')
        if reasons:unmatched.append({'case_id':cid,'reasons':reasons,'base':a,'trained':b})
        else:pairs.append({'case_id':cid,'kind':labels[cid]['kind'],'seed':labels[cid]['seed'],'base':a,'trained':b})
    summary={}
    for metric in METRICS:
        eligible=[r for r in pairs if r['base'].get('metrics',{}).get(metric) is not None and r['trained'].get('metrics',{}).get(metric) is not None]
        counts={label:sum(bool(r[label]['metrics'][metric]) for r in eligible) for label in ['base','trained']}
        wins=sum(not r['base']['metrics'][metric] and bool(r['trained']['metrics'][metric]) for r in eligible)
        losses=sum(bool(r['base']['metrics'][metric]) and not r['trained']['metrics'][metric] for r in eligible)
        import math
        n=wins+losses;p=min(1,2*sum(math.comb(n,i) for i in range(min(wins,losses)+1))/2**n) if n else 1
        summary[metric]={'matched_denominator':len(eligible),'planned_denominator':sum(labels[c['case_id']]['kind']==('navigation' if metric=='navigation_agreement' else 'authorization') for c in cases),**counts,'adapter_only':wins,'base_only':losses,'paired_exact_two_sided_p':p,'p_scope':'exploratory development; no multiplicity correction'}
    diagnostic_counts={label:{key:sum(bool(r.get('metrics',{}).get(key)) for r in report['results']) for key in ['ambiguous','structured_parse_error','runtime_parse_error','unsupported_action','truncated','transport_error']} for label,report in [('base',base),('trained',trained)]}
    return {'planned_cases':len(cases),'matched_cases':len(pairs),'unmatched_cases':len(unmatched),'metrics':summary,'diagnostic_counts':diagnostic_counts,'pairs':pairs,'unmatched':unmatched,'interpretation':'Teacher-reached saved states; navigation agreement is not complete-task correctness. Missing and transport failures are retained and never treated as model task failures.'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package',required=True);p.add_argument('--base',required=True);p.add_argument('--trained',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    result=compare(a.package,json.loads(Path(a.base).read_text()),json.loads(Path(a.trained).read_text()))
    with Path(a.out).open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ['pairs','unmatched']},indent=2))
