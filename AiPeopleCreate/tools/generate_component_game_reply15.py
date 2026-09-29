import json, httpx, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_gen_v4.runtime_grounded.renderer import render_game_reply_context_from_model_view, build_game_reply_chat_messages, parse_game_reply_dialogue_lines
ROOT=Path(__file__).resolve().parents[1]; src=ROOT/'训练数据/component_game_reply15_20260913/game_reply_scenarios.jsonl'; out=ROOT/'训练数据/component_game_reply15_20260913/generated.jsonl'
anchor='你是白未晞。请用自然、简短的中文回答。没有权威状态的具体事实要说不知道，不得补全或虚构动作。'
rows=[]
for line in src.read_text(encoding='utf-8').splitlines():
 s=json.loads(line); v=s['model_view']; full=render_game_reply_context_from_model_view(v); keep=[]
 for section in ('[当前权威事实]','[明确未知]','[当前世界]','[你此刻的状态]','[相关记忆]'):
  a=full.find(section); b=full.find('\n[',a+2) if a>=0 else -1; keep.append(full[a:b if b>=0 else len(full)] if a>=0 else '')
 context='\n'.join(keep)+'\n男主基础设定：四川人，四川口味，能吃辣，喜欢油腻。'
 dialogue=build_game_reply_chat_messages(parse_game_reply_dialogue_lines(tuple(v['recent_dialogue'])),character_display_name='白未晞',current_protagonist_utterance=v['current_protagonist_utterance']); messages=[{'role':'system','content':anchor+'\n'+context},*({'role':r,'content':c} for r,c in dialogue)]
 resp=httpx.post('http://127.0.0.1:11434/api/chat',json={'model':'qwen2.5:14b-instruct-q4_K_M','messages':messages,'stream':False,'format':'json','think':False,'options':{'num_ctx':8192,'num_predict':180,'temperature':0.55}},timeout=90).json(); raw=(resp.get('message') or {}).get('content','');
 try:
  target=json.loads(raw[raw.find('{'):raw.rfind('}')+1]); reply=str(target.get('reply') or target.get('说话内容') or target.get('content') or target.get('text') or '').strip()
 except Exception: target={'reply':''}; reply=''
 rows.append({'sample_id':s['scenario_id'],'scenario':s,'generation_messages':messages,'raw_output':raw,'reply':reply,'valid':bool(reply)})
out.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8'); print(json.dumps({'total':len(rows),'valid':sum(x['valid'] for x in rows),'empty':sum(not x['valid'] for x in rows),'model':'qwen2.5:14b-instruct-q4_K_M','output':str(out)},ensure_ascii=False))

