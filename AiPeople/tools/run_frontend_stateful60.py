"""Compare production/candidate on the established 60-turn simulated trajectory."""
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime

from run_frontend_regression12 import ROOT, sha
from run_v5_small_three_way import _load_arm, _release
from build_v5_pilot_dataset import _load_baiweixi_production_contract
from run_gemma4_stateful_grounding60 import TURNS
from run_gemma4_stateful_grounding16 import generate
from runtime.world_mind.game_reply_context import render_game_reply_context_v1
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template
import torch


def context(turn, anchor):
    return anchor + "\n\n" + render_game_reply_context_v1(
        character_display_name="白未晞", game_time="第18天 20:10", location="出租屋客厅",
        scene_text="男主与白未晞在客厅交谈。", protagonist_location="出租屋客厅",
        protagonist_activity="交谈", protagonist_body="无额外身体状态",
        heroine={"form":"人形", "body":"正常", "emotion":"平静", "attention":"男主本轮的话", "current_activity":"交谈", "immediate_intent":"回应男主"},
        relationship={"stage":"close_companions", "trust":"high", "unresolved_tension":""},
        authoritative_facts=() if turn["player_fact_update"] else (turn["prompt_world"] + "（来源：测试脚本当前状态）",),
        memories=tuple(m + "（来源：测试脚本召回记忆）" for m in turn["memories"]),
    )


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--review",type=Path,required=True)
    parser.add_argument("--source-report",type=Path,required=True)
    parser.add_argument("--candidate",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    review=json.loads(args.review.read_text(encoding="utf-8"))
    original=json.loads(args.source_report.read_text(encoding="utf-8"))
    if len(review["cases"])!=12 or review["generated_at"]!=original["generated_at"]:
        raise ValueError("Review identity mismatch")
    for actual, expected in zip(review["cases"],original["cases"],strict=True):
        if actual["sample_id"]!=expected["sample_id"] or actual["arms"]!=expected["arms"] or actual["messages"]!=expected["messages"]:
            raise ValueError("Reviewed results differ")
        if actual["human_review"]["new_adapter"]!="好":
            raise ValueError("Candidate regression gate not passed")
    candidate=args.candidate.resolve()
    if sha(candidate/"adapter_model.safetensors")!=original["arms"]["new_adapter"]["sha256"]:
        raise ValueError("Candidate changed")
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/"regression12_human_review.json").write_bytes(args.review.read_bytes())
    arms={k:original["arms"][k] for k in ("old_adapter","new_adapter")}
    report={"schema_version":"aip.frontend_stateful60_review.v1","status":"running",
        "generated_at":datetime.now().astimezone().isoformat(),"source_sha256":sha(args.review),
        "automatic_quality_judgment":False,"arms":arms,"cases":[],
        "controls":{"context_renderer":"GAME_REPLY_CONTEXT_V1","rows":60,"history_limit_messages":6,
        "history_policy":"each arm's own generated responses; not teacher-forced",
        "same_external_state_and_player_inputs":True,"identical_history_across_arms":False,
        "max_new_tokens":96,"thinking":False,"greedy_decoding":True,"production_promoted":False,
        "scope":"scripted state and memory injection, not full production event/memory-writeback end-to-end",
        "generalization":"historical suite, not newly sealed unseen concepts",
        "script_sha256":sha(Path(__file__).with_name("run_baiweixi_60turn_player_simulation.py"))}}
    anchor,_=_load_baiweixi_production_contract()
    if hashlib.sha256(anchor.encode("utf-8")).hexdigest()!=original["controls"]["system_anchor_sha256"]:
        raise ValueError("Current production role anchor differs from regression gate")
    for i,turn in enumerate(TURNS,1):
        report["cases"].append({"ordinal":i,"sample_id":f"frontend.stateful60.{i:02d}","question":turn["player"],
            "task_type":turn["category"],"review_expectation":turn["focus"],"script_world":turn["world"],
            "messages":[],"arms":{},"human_review":{k:"" for k in arms}})
    def save():
        temp=args.output/"report.tmp"
        temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        temp.replace(args.output/"report.json")
    save()
    for arm in arms:
        print("LOADING "+arm,flush=True)
        if arm=="old_adapter":
            model,tokenizer,asset=_load_arm(arm,Path(arms[arm]["manifest"]))
        else:
            model,tokenizer=FastModel.from_pretrained(model_name=str(candidate),max_seq_length=4096,dtype=torch.bfloat16,
                load_in_4bit=True,text_only=True,trust_remote_code=True,use_exact_model_name=True,attn_implementation="eager")
            if not getattr(model,"peft_config",None):raise ValueError("Missing candidate LoRA")
            tokenizer=get_chat_template(tokenizer,chat_template="gemma-4")
            FastModel.for_inference(model)
        history=[]
        try:
            for i,turn in enumerate(TURNS):
                messages=[{"role":"system","content":context(turn,anchor)},*history[-6:],{"role":"user","content":turn["player"]}]
                result=generate(model,tokenizer,messages,2026092201+i)
                result["messages"]=messages
                report["cases"][i]["arms"][arm]=result
                report["cases"][i]["messages"]=[messages[0],messages[-1]]
                history.extend([messages[-1],{"role":"assistant","content":result["response"]}])
                save()
                print(json.dumps({"arm":arm,"turn":i+1,"reply":result["response"]},ensure_ascii=False),flush=True)
        finally:
            _release(model,tokenizer)
    report["status"]="awaiting_human_review"
    save()
    template=Path(__file__).with_name("frontend_regression12_review.html").read_text(encoding="utf-8")
    template=template.replace("新训练三方审核","连续60轮双模型审核").replace("frontend-regression12:","frontend-stateful60:")
    template=template.replace("saved.cases.length===12","saved.cases.length===original.cases.length").replace("+' / 36'","+' / '+(report.cases.length*arms.length)").replace("n===36","n===report.cases.length*arms.length")
    template=template.replace("frontend_full_regression12_user_review.json","frontend_full_stateful60_user_review.json")
    template=template.replace("box.append(title,text,time,label)","const detail=document.createElement('details');const summary=document.createElement('summary');summary.textContent='本模型实际输入与历史';const pre=document.createElement('pre');pre.textContent=c.arms[arm].messages.map(m=>m.role+'\\n'+m.content).join('\\n\\n');detail.append(summary,pre);box.append(title,text,time,detail,label)")
    template=template.replace("repeat(3,minmax(0,1fr))","repeat(2,minmax(0,1fr))")
    (args.output/"review.html").write_text(template.replace("__REPORT__",json.dumps(report,ensure_ascii=False).replace("<","\\u003c")),encoding="utf-8")
    print("COMPLETED 120/120",flush=True)

if __name__=="__main__":main()
