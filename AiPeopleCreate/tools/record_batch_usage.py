"""Read-only session accounting; write only this batch's usage ledger.

Cumulative deltas avoid double-counting repeated token_count events and
compaction markers. A local watcher closes the ledger after this turn ends.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone

from pilot_pipeline import persist


def session_usage(log, turn_id):
    previous=None; baseline=None; latest=None; started=None; ended=None
    process=subprocess.Popen(['rg','"type":"token_count"|"type":"task_started"|"type":"task_complete"',str(log)],
        stdout=subprocess.PIPE,encoding='utf-8')
    for line in process.stdout:
        record=json.loads(line); payload=record['payload']; kind=payload['type']
        if kind=='task_started' and payload.get('turn_id')==turn_id:
            started=record['timestamp']; baseline=previous
        elif kind=='token_count' and payload.get('info'):
            previous={'ordinal':record['ordinal'],'timestamp':record['timestamp'],
                      'usage':payload['info']['total_token_usage']}
            if baseline is not None: latest=previous
        elif kind=='task_complete' and payload.get('turn_id')==turn_id:
            ended=record['timestamp']; break
    process.stdout.close(); process.wait()
    if baseline is None: raise ValueError('turn baseline not found')
    latest=latest or baseline
    delta={k:latest['usage'].get(k,0)-baseline['usage'].get(k,0) for k in baseline['usage']}
    if any(v<0 for v in delta.values()): raise ValueError('usage counter reset; cannot report a simple delta')
    delta['uncached_input_tokens']=delta['input_tokens']-delta['cached_input_tokens']
    delta['uncached_input_plus_output']=delta['uncached_input_tokens']+delta['output_tokens']
    return {'turn_id':turn_id,'started_at':started,'completed_at':ended,
        'status':'final' if ended else 'in_progress','baseline':baseline,'latest':latest,'delta':delta,
        'source':str(log),'scope':'本轮修复、输入编排、工具往返、上下文压缩、审核及交付；非15或100条答案本身的价格',
        'notes':['cached_input_tokens 是 input_tokens 的子集，不重复相加。',
                 'reasoning_output_tokens 是 output_tokens 的子集。',
                 'token 数不是 ChatGPT 套餐扣额或费用；本地 Ollama 另计。']}


def snapshot(log, turn_id, out):
    cloud=session_usage(log,turn_id); local={}
    for path in (out/'requests').glob('*.json'):
        try: request=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError): continue
        model=request['model']; total=local.setdefault(model,{'attempts':0,'responses':0,'input_tokens':0,'output_tokens':0})
        for attempt in request.get('attempts',[]):
            total['attempts']+=1; response=attempt.get('response')
            if response:
                total['responses']+=1
                total['input_tokens']+=response.get('prompt_eval_count',0)
                total['output_tokens']+=response.get('eval_count',0)
    out.mkdir(parents=True,exist_ok=True)
    result={'updated_at':datetime.now(timezone.utc).isoformat(),'codex':cloud,'local_ollama':local}
    persist(out/'token_usage.json',result)
    print(json.dumps({'status':cloud['status'],'codex_delta':cloud['delta'],'local_ollama':local},ensure_ascii=False),flush=True)
    return cloud['status']=='final'


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--log',type=Path,required=True)
    p.add_argument('--turn-id',required=True); p.add_argument('--output-root',type=Path,required=True)
    p.add_argument('--watch',action='store_true'); args=p.parse_args()
    for _ in range(240 if args.watch else 1):
        if snapshot(args.log,args.turn_id,args.output_root): break
        if not args.watch: break
        time.sleep(30)
