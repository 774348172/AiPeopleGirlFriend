"""Bind explicit per-item Codex judgments to the inspected compiled draft."""
import argparse,json,hashlib
from pathlib import Path

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    drafts=json.loads((a.root/'draft_inputs.json').read_text(encoding='utf-8'))
    notes={}
    for line in (a.root/'input_review_notes.txt').read_text(encoding='utf-8').splitlines():
        n,decision,continuity,evidence,persona=line.split('|')
        sid=drafts[int(n)-1]['sample_id']
        if sid in notes:raise ValueError('duplicate note')
        notes[sid]=dict(decision=decision,continuity=continuity,evidence=evidence,persona=persona)
    if len(notes)!=len(drafts):raise ValueError('incomplete explicit notes')
    ledger=dict(review_run_id=a.root.name+'.codex_self_review',
        reviewed_draft_sha256=hashlib.sha256((a.root/'draft_inputs.json').read_bytes()).hexdigest(),items=notes)
    path=a.root/'review_ledger.json'
    if path.exists():raise ValueError('Do not overwrite an existing review ledger')
    path.write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
