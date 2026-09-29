"""Frozen old failures plus positive controls; never generates or releases training rows."""
import argparse
import copy
import json
from pathlib import Path

from audit_generated_history import audit_history
from build_v5_pilot_dataset import _load_baiweixi_render_context
from pilot_pipeline import persist


def run(source, out, thinking=False, limit=None, model='gemma4:12b', sample_ids=None):
    out.mkdir(parents=True, exist_ok=False)
    anchor = _load_baiweixi_render_context()[2]
    if thinking:
        import httpx
        import audit_generated_history
        def reasoned_audit(url, model, messages, schema, tokens):
            response=httpx.post(url.removesuffix('/v1')+'/api/chat',json={
                'model':model,'messages':messages,'stream':False,'think':True,'format':schema,
                'keep_alive':'10m','options':{'num_ctx':16384,'num_predict':7000,'temperature':0}},timeout=180)
            response.raise_for_status()
            return response.json()
        audit_generated_history.call_auditor=reasoned_audit
    cases=[]
    for path in sorted((source/'checkpoints').glob('*.json')):
        rec=json.loads(path.read_text(encoding='utf-8'))
        if sample_ids and rec['sample_id'] not in sample_ids: continue
        cases.append((rec['sample_id'],rec['scenario']['component_source'],False))
    if limit is not None: cases=cases[:limit]
    base=copy.deepcopy(cases[0][1]); base['historical_events']=[]
    controls=[
        ('subjective', ['假如有两盆花，一盆白的一盆黑的，你想选哪盆？','我想选黑的，看着少见。',
                        '只因为少见吗？','也不全是，我想看看它配白花盆是什么样。'],
                       ['你前面说想用白花盆，为什么？','颜色不一样，花会更显眼。',
                        '那换成灰色的花盆呢？','也可以，应该会柔和一点。'], '那你更喜欢显眼的，还是柔和的？'),
        ('canonical', ['你小时候在哪里生活？','我从小以小猫的样子在深山老林里流浪。',
                       '那现在想象一天不用忙，会想做什么？','我想安安静静看一会儿图画。'],
                      ['要是只能选看图和猜谜，你选哪个？','猜谜吧，可以一起想。',
                       '猜不出来会不会觉得无聊？','可以给一点提示，别一下把答案说了。'], '那提示是给画面好，还是给一句话好？'),
    ]
    for name,old,recent,current in controls:
        row=copy.deepcopy(base)
        for kind,lines in [('memory_turns',old),('recent_turns',recent)]:
            for turn,line in zip(row[kind],lines): turn['text']=line
        row['current_user']=current
        cases.append(('positive_'+name,row,True))
    results=[]
    for name,row,expected in cases:
        path=out/(name+'.json')
        persist(out/(name+'.input.json'),row)
        try:
            verdict=audit_history(row,'http://127.0.0.1:11434/v1',model,path,anchor)
            accepted=True; error=None
        except Exception as exc:
            accepted=False; error=str(exc).split('\n')[0]
        raw=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        surface_path=path.with_name(path.stem+'.surface.json')
        surface=json.loads(surface_path.read_text(encoding='utf-8')) if surface_path.exists() else {}
        surface_rejected=bool(surface.get('review',{}).get('issues'))
        # Transport/schema failures are not evidence of semantic detection.
        review=raw.get('review')
        completed=isinstance(review,dict) or surface_rejected
        model_pass=False if surface_rejected else review.get('pass') if isinstance(review,dict) else None
        results.append({'id':name,'expected_pass':expected,'gate_pass':accepted,
                        'model_pass':model_pass,'review_completed':completed,'error':error,
                        'matched':completed and accepted==expected})
        persist(out/'results.json',results)
        print(name+': expected='+str(expected)+' gate='+str(accepted)+' model='+str(model_pass),flush=True)
    persist(out/'report.json',{'cases':len(results),'matched':sum(r['matched'] for r in results),
        'reviews_completed':sum(r['review_completed'] for r in results),
        'old_bad_gate_rejected':sum(not r['gate_pass'] and r['review_completed'] for r in results if not r['expected_pass']),
        'old_bad_model_rejected':sum(r['model_pass'] is False for r in results if not r['expected_pass']),
        'positive_pass':sum(r['gate_pass'] for r in results if r['expected_pass']),
        'model':model,'thinking':thinking,'training_rows':0})


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output-root',type=Path,required=True)
    p.add_argument('--thinking',action='store_true'); p.add_argument('--limit',type=int)
    p.add_argument('--model',default='gemma4:12b')
    p.add_argument('--sample-ids',nargs='+')
    args=p.parse_args()
    run(args.source,args.output_root,args.thinking,args.limit,args.model,args.sample_ids)
