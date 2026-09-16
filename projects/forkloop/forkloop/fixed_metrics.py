"""Controller-only frozen observation metrics; never imported into prompt construction.

Reading requires a single strict structured computer_use action. Only type.text
(or its runtime value alias) and pause_and_memorize_fact.fact are recognized.
Prose outside those fields is never evidence. Multiple calls, conflicting fields,
or multiple distinct AUTH tokens are ambiguous and do not earn exact credit.
"""
import difflib
import json
import math
import re
from .policies.action_parse import FARA_ACTIONS, FARA_UNSUPPORTED, FARA_NAV_ACTIONS

RULES_VERSION='forkloop.fixed-metrics.v1'
TOKEN=re.compile(r'(?<![A-Za-z0-9_-])AUTH-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?![A-Za-z0-9_-])')

def structured_actions(raw):
    if not isinstance(raw,str): return [],'non_text_output'
    try:
        if '<tool_call>' in raw or '</tool_call>' in raw:
            blocks=re.findall(r'<tool_call>\s*(.*?)\s*</tool_call>',raw,re.S)
            if len(blocks)!=raw.count('<tool_call>') or len(blocks)!=raw.count('</tool_call>'):
                return [],'incomplete_tool_call'
            objects=[json.loads(b) for b in blocks]
        else:
            obj=json.loads(raw.strip());objects=obj if isinstance(obj,list) else [obj]
        actions=[]
        for obj in objects:
            if not isinstance(obj,dict):raise ValueError('non_object_call')
            fn=obj.get('function',obj)
            if fn.get('name')!='computer_use':raise ValueError('unrecognized_tool')
            args=fn.get('arguments')
            if isinstance(args,str):args=json.loads(args)
            if not isinstance(args,dict) or not isinstance(args.get('action'),str):raise ValueError('invalid_arguments')
            actions.append(args)
        return actions,None
    except (ValueError,TypeError,AttributeError) as e:return [],f'{type(e).__name__}: {e}'

def character_error(expected,got):
    previous=list(range(len(got)+1))
    for i,a in enumerate(expected,1):
        current=[i]
        for j,b in enumerate(got,1): current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(a!=b)))
        previous=current
    return {'value':got,'edit_distance':previous[-1],'expected_length':len(expected),'actual_length':len(got),
            'edits':[{'operation':op,'expected_range':[i,j],'actual_range':[k,l],'expected':expected[i:j],'actual':got[k:l]}
                     for op,i,j,k,l in difflib.SequenceMatcher(a=expected,b=got,autojunk=False).get_opcodes() if op!='equal']}

def action_agreement(got,expected):
    if not got or got.get('type')!=expected.get('type'):return False
    if expected['type']=='click':
        return got.get('button','left')==expected.get('button','left') and math.hypot(got['x']-expected['x'],got['y']-expected['y'])<=20
    return all(got.get(k)==v for k,v in expected.items())

def score(raw,action,meta,label):
    calls,error=structured_actions(raw);target=label.get('expected_authorization');fields=[];ambiguous=len(calls)>1
    for c in calls:
        field='fact' if c['action']=='pause_and_memorize_fact' else 'text' if c['action']=='type' else None
        if field:
            value=c.get(field,c.get('value') if c['action']=='type' else None)
            if c['action']=='type' and 'text' in c and 'value' in c and c['text']!=c['value']:ambiguous=True
            if isinstance(value,str):fields.append({'action':c['action'],'field':field,'value':value,'tokens':TOKEN.findall(value)})
    tokens=sorted({v for f in fields for v in f['tokens']});ambiguous=ambiguous or len(tokens)>1
    exact=bool(target and not error and not ambiguous and len(calls)==1 and target in tokens)
    typed=bool(target and not error and not ambiguous and len(calls)==1 and calls[0]['action']=='type' and fields and fields[0]['value']==target)
    supported=all(c['action'] in FARA_ACTIONS and c['action'] not in (FARA_UNSUPPORTED-FARA_NAV_ACTIONS) for c in calls)
    wrong=[character_error(target,v) for v in tokens if target and v!=target]
    # Non-AUTH wrong type text is still a wrong entry, even when it cannot be called a read token.
    if target and action and action.get('type')=='type' and action.get('text')!=target and action.get('text') not in tokens:
        wrong.append(character_error(target,action.get('text','')))
    agreement=action_agreement(action,label['expected_action'])
    return {'rules_version':RULES_VERSION,'exact_authorization_emitted':exact if target else None,
            'exact_authorization_type_selected':typed if target else None,
            'exact_authorization_runtime_type':bool(target and action and action.get('type')=='type' and action.get('text')==target) if target else None,
            'authorization_fields':fields,'authorization_tokens':tokens,'incorrect_authorizations':wrong,
            'ambiguous':ambiguous,'structured_parse_error':error,'runtime_parse_error':action is None and not meta.get('error'),
            'unsupported_action':bool(calls and not supported),'truncated':meta.get('finish_reason')=='length',
            'transport_error':bool(meta.get('error')),'teacher_action_agreement':agreement,
            'navigation_agreement':agreement if label['kind']=='navigation' else None,
            'navigation_disagreement_interpretation':'unadjudicated alternative; not necessarily incorrect' if label['kind']=='navigation' and not agreement else None}
