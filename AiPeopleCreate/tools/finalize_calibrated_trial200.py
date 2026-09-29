"""Combine old adjudicated candidates and 100 prospective candidates, no release."""
import copy,hashlib,json,sys
from pathlib import Path
from collections import Counter
from build_pending_human_review import build
from summarize_curated_reply_usage import summarize

ROOT=Path(__file__).resolve().parents[1]
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,data):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def finish(human_path):
    oldroot=ROOT/'训练数据/codex_authored100_20260916'
    newroot=ROOT/'训练数据/calibrated_add100_20260916'
    out=ROOT/'训练数据/calibrated_trial200_20260916'
    if out.exists():raise ValueError('Use fresh finalization output')
    labels=read(human_path);old=read(oldroot/'gemma31b_qat_reaudit/results.json')
    machine=read(newroot/'formal_run/results.json')
    if len(old)!=100 or len(machine)!=100:raise ValueError('Both 100-row batches must complete')
    if any(not (r.get('target') or {}).get('reply','').strip() for r in old+machine):
        raise ValueError('Trial requires 200 nonempty reply candidates; preserve failures separately')
    pair_keys=[json.dumps([r['generation_messages'],r['target']['reply'].strip()],ensure_ascii=False,sort_keys=True) for r in old+machine]
    if len(set(pair_keys))!=200:raise ValueError('Duplicate complete training pair across trial batches')
    if {r['sample_id'] for r in labels}!=set(read(oldroot/'human_review24_20260916/queue_manifest.json')['selected_ids']):raise ValueError('Human manifest mismatch')
    judgments={};decisions=newroot/'reply_review_notes.txt'
    for line in decisions.read_text(encoding='utf-8').splitlines():
        n,decision,note=line.split('|');sid=f'calibrated100.20260916.{int(n):03d}'
        if decision not in ('approve','reject','human_review') or sid in judgments:raise ValueError('Bad semantic ledger')
        judgments[sid]=(decision,note)
    if set(judgments)!={r['sample_id'] for r in machine}:raise ValueError('Require explicit review of all 100 replies')
    semantic=[]
    for r in machine:
        d,note=judgments[r['sample_id']];s=copy.deepcopy(r)
        s.update(semantic_approved=d=='approve',borderline=d=='human_review',review_note=note,
            method='codex_file_review',report={'approved':d=='approve','reasons':[] if d=='approve' else [note]},
            reviewed_reply_sha256=hashlib.sha256(r['target']['reply'].encode()).hexdigest())
        semantic.append(s)
    write(newroot/'semantic_results.json',semantic)
    write(newroot/'semantic_provenance.json',{'notes_sha256':sha(decisions),'machine_results_sha256':sha(newroot/'formal_run/results.json'),
        'method':'codex_file_review','count':100,'non_blind':True,'independent_api_calls':0,'codex_tokens':None})
    out.mkdir()
    write(out/'human_labels24.json',labels)
    labelmap={r['sample_id']:r for r in labels}
    oldsemantic={r['sample_id']:r for r in read(oldroot/'gpt6_astra_reaudit/results.json')}
    combined=[];combined_semantic=[]
    for r in old:
        sid=r['sample_id'];s=copy.deepcopy(r);t=copy.deepcopy(oldsemantic[sid])
        s['original_machine_report']=copy.deepcopy(r['report'])
        if sid in labelmap:
            label=labelmap[sid];approved=label['verdict']=='good'
            for z in (s,t):
                z.update(report={'approved':approved,'reasons':[] if approved else ['human_rejected']},
                    status='approved' if approved else 'content_rejected',quarantined=not approved,
                    human_review=label,review_note='用户已裁决，无需重复审核。',semantic_approved=approved,borderline=False)
        s['origin']='prior100';combined.append(s);combined_semantic.append(t)
    for r,s in zip(machine,semantic):
        r=copy.deepcopy(r);r['origin']='prospective100';combined.append(r);combined_semantic.append(s)
    assert len({r['sample_id'] for r in combined})==200
    write(out/'results.json',combined)
    write(out/'semantic_results.json',combined_semantic)
    # Known human rejections remain rejected but are not sent back to the user.
    unresolved=[r for r in combined if r['sample_id'] not in labelmap]
    unresolved_s=[r for r in combined_semantic if r['sample_id'] not in labelmap]
    write(out/'unresolved_results.json',unresolved);write(out/'unresolved_semantic.json',unresolved_s)
    queue=build(out/'unresolved_results.json',out/'human_review_pending',out/'unresolved_semantic.json',
        title='200条试产验收 · 仅审核疑点（已裁决24条不重复送审）')
    summarize(newroot/'formal_run')
    report={'count':200,'old_reused':100,'new_generated':100,'human_good':17,'human_bad':7,
        'new_machine_pass':sum(r['report']['approved'] for r in machine),
        'new_semantic_pass':sum(r['semantic_approved'] for r in semantic),
        'new_semantic_decisions':dict(Counter(d for d,n in judgments.values())),
        'valid_replies':200,'unique_complete_training_pairs':len(set(pair_keys)),
        'new_generation_report':read(newroot/'formal_run/report.json'),
        'pending_human':queue['pending_count'],'status':'awaiting_human_acceptance',
        'ready_for_1000':False,'released_training_rows':0,
        'scenes':dict(Counter(r['scenario']['component_source']['component_ids']['scene_id'] for r in combined)),
        'human_source_sha256':sha(human_path),'old_results_sha256':sha(oldroot/'gemma31b_qat_reaudit/results.json'),
        'new_results_sha256':sha(newroot/'formal_run/results.json'),
        'machine_review_scope':'原100保留历史Gemma结果与24条人工覆盖；新增100使用校准Gemma，另重审人工24作校准验证，并非全200统一重审',
        'calibration_recheck':read(ROOT/'训练数据/human24_calibration_recheck_20260916/report.json')}
    write(out/'report.json',report)
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':finish(Path(sys.argv[1]))
