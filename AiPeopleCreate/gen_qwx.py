"""秦未晞训练数据生成器——一步到位生成闲聊+恋爱+身份+情绪标注四类数据。
用法: python gen_qwx.py
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from data_gen.common import (
    ModelClient, load_config, parse_json_lenient,
    render_canon_block, render_identity_block, render_prompt, render_voice_block,
)

# ─── 秦未晞 persona 加载 ───
QWX_DIR = Path(__file__).parent / "人物设定" / "秦"

def load_qwx():
    import yaml
    bible = yaml.safe_load(open(QWX_DIR / "bible.yaml", encoding="utf-8"))
    canon = json.load(open(QWX_DIR / "canon.json", encoding="utf-8"))
    timeline = yaml.safe_load(open(QWX_DIR / "timeline.yaml", encoding="utf-8"))
    class P: pass
    p = P()
    p.bible = bible
    p.canon = canon
    p.timeline = timeline.get("events", [])
    p.name = "秦未晞"
    p.relationships = [{"name": "大叔", "note": "合租室友/青梅竹马/暧昧对象"}]
    p.birth_date = ""
    return p

# ─── 话题池 ───
CASUAL_TOPICS = [
    "今天吃什么", "你又在打游戏", "该打扫卫生了", "你房间好乱",
    "今天好冷/好热", "你最近工作怎么样", "周末干什么",
    "你偷吃我零食了吧", "帮我拿个快递", "今天画画了吗",
    "你好吵能不能安静点", "明天一起出门吧", "你最近在看什么",
    "你怎么又在熬夜", "你画的东西好奇怪", "帮我算个账",
    "今天超市打折", "你能不能别霸占客厅", "你今天回来好晚",
    "你觉得这周过得快不快", "你昨天说梦话了", "借我充电器",
]
ROMANCE_SCENES = [
    ("暧昧-日常关心", "你加班到很晚，她在客厅等你"),
    ("暧昧-偷穿衣服", "她穿了你卫衣被你发现了"),
    ("暧昧-生病", "你生病了，她的反应"),
    ("暧昧-吃醋", "你提到了一个女同事/女同学"),
    ("暧昧-不经意接触", "你不小心碰到她的手"),
    ("暧昧-深夜真心话", "深夜两人都睡不着，在客厅发呆"),
    ("暧昧-你对她好", "你突然帮她做了件小事她不知道怎么反应"),
    ("暧昧-异世界暗示", "你无意中做了和异世界里一样的动作"),
    ("暧昧-画画秘密", "你偷看到她画的东西和异世界有关"),
    ("暧昧-生日/节日", "某个节日两个人一起过"),
    ("暧昧-吵架后和好", "为了一件小事吵架，然后和好"),
    ("暧昧-你说她好看", "你突然夸她，她不知道怎么接"),
]
IDENTITY_QUESTIONS = [
    "你叫什么？", "你今年多大？", "你是做什么的？",
    "你爸妈是干什么的？", "你和我什么关系？", "你怎么住我这的？",
    "你有什么爱好？", "你数学怎么样？", "你为什么叫我大叔？",
    "你为什么叫秦老？", "你和我从小就认识吗？", "你画画画多久了？",
    "你平时几点睡？", "你喜欢吃什么？", "你有什么缺点？",
    "你最近在忙什么？", "你住我这习惯吗？", "你觉得我怎么样？",
    "你为什么会画画？", "你有什么怕的？",
]
EMOTION_TOPICS = [
    ("你在打游戏输了", "游戏输了"),
    ("你做了好吃的给她", "她吃到好吃的"),
    ("你夸她好看", "突然被夸"),
    ("你提到别的女生", "吃醋"),
    ("你帮她算数学题", "数学白痴被戳到"),
    ("你深夜不睡觉", "关心你身体"),
    ("你偷看她的画", "画被看到"),
    ("你做了和异世界一样的动作", "异世界记忆触发"),
    ("你送她礼物", "收到礼物"),
    ("你突然对她很温柔", "被温柔对待"),
    ("你说了句以前不是这样的", "她说漏嘴的暗示"),
    ("你问她今天怎么了", "她出神后恢复"),
    ("你和她一起看晚霞", "文艺时刻"),
    ("你抢她零食", "零食被抢"),
    ("你半夜敲她房门", "深夜接触"),
]

SCENES = [
    "晚上在客厅，她在打游戏你在加班",
    "早上她还没起，你在厨房做饭",
    "深夜两人都睡不着，在阳台发呆",
    "她画了一天画，你端了杯水过去",
    "周末下午，她霸占沙发你坐地上",
    "下雨天，两人困在家",
    "她生病了，你在照顾她",
    "你刚下班回来，她在厨房偷吃你零食",
    "她在画画不让你看，你偷偷瞄了一眼",
    "大冬天两人缩在客厅，暖气不够",
]

def _render_blocks(persona):
    return (
        render_identity_block(persona),
        render_canon_block(persona.canon),
        render_voice_block(persona),
    )

def _parse_turns(raw):
    try:
        turns = parse_json_lenient(raw)
        if isinstance(turns, dict):
            for k in ("turns", "conversation", "messages", "dialogue", "data"):
                if isinstance(turns.get(k), list):
                    turns = turns[k]; break
            else:
                raise ValueError("无数组")
        if not isinstance(turns, list):
            raise ValueError(f"非数组:{type(turns)}")
        norm = []
        for t in turns:
            if isinstance(t, str):
                norm.append({"role": "person", "text": t})
            elif isinstance(t, dict) and "text" in t:
                norm.append({"role": t.get("role", "person"), "text": str(t["text"])})
        return norm or [{"role": "person", "text": "[解析失败] 空"}]
    except Exception as e:
        return [{"role": "person", "text": f"[解析失败] {e}"}]

# ─── 四类生成器 ───
def gen_casual(client, persona, ib, cb, vb, n):
    out = []
    for i in range(n):
        topic = random.choice(CASUAL_TOPICS)
        scene = random.choice(SCENES)
        prompt = render_prompt("qwx_casual", IDENTITY_BLOCK=ib, CANON_BLOCK=cb, VOICE_BLOCK=vb, SCENE=scene, TOPIC=topic)
        raw = client.chat([{"role": "system", "content": prompt}, {"role": "user", "content": "开始对话。"}])
        turns = _parse_turns(raw)
        out.append({"id": f"casual_{i}", "type": "chat", "date": "", "node_index": -1, "node_summary": f"casual:{topic}", "people": ["大叔"], "content": turns, "model": client.model, "raw": raw})
        print(f"  [casual {i+1}/{n}] ✓ {topic} ({len(turns)}轮)")
        time.sleep(0.2)
    return out

def gen_romance(client, persona, ib, cb, vb, n):
    out = []
    for i in range(n):
        stype, scene = random.choice(ROMANCE_SCENES)
        prompt = render_prompt("qwx_romance", IDENTITY_BLOCK=ib, CANON_BLOCK=cb, VOICE_BLOCK=vb, SCENE=scene, SCENE_TYPE=stype)
        raw = client.chat([{"role": "system", "content": prompt}, {"role": "user", "content": "开始对话。"}])
        turns = _parse_turns(raw)
        out.append({"id": f"romance_{i}", "type": "chat", "date": "", "node_index": -1, "node_summary": f"romance:{stype}", "people": ["大叔"], "content": turns, "model": client.model, "raw": raw})
        print(f"  [romance {i+1}/{n}] ✓ {stype} ({len(turns)}轮)")
        time.sleep(0.2)
    return out

def gen_identity(client, persona, ib, cb, vb, n):
    out = []
    qs = IDENTITY_QUESTIONS[:n] if n <= len(IDENTITY_QUESTIONS) else [random.choice(IDENTITY_QUESTIONS) for _ in range(n)]
    for i, q in enumerate(qs):
        prompt = render_prompt("qwx_identity", IDENTITY_BLOCK=ib, CANON_BLOCK=cb, VOICE_BLOCK=vb, QUESTION=q)
        raw = client.chat([{"role": "system", "content": prompt}, {"role": "user", "content": "回答。"}])
        content = {"question": q, "answer": raw.strip()}
        out.append({"id": f"identity_{i}", "type": "identity", "date": "", "node_index": -1, "node_summary": f"identity:{q}", "people": [], "content": content, "model": client.model, "raw": raw})
        print(f"  [identity {i+1}/{n}] ✓ {q}")
        time.sleep(0.2)
    return out

def gen_emotion(client, persona, ib, cb, vb, n):
    out = []
    for i in range(n):
        topic, desc = random.choice(EMOTION_TOPICS)
        scene = random.choice(SCENES)
        prompt = render_prompt("qwx_emotion", IDENTITY_BLOCK=ib, CANON_BLOCK=cb, VOICE_BLOCK=vb, SCENE=f"{scene}（{desc}）", TOPIC=topic)
        raw = client.chat([{"role": "system", "content": prompt}, {"role": "user", "content": "开始对话。"}])
        turns = _parse_turns(raw)
        out.append({"id": f"emotion_{i}", "type": "chat", "date": "", "node_index": -1, "node_summary": f"emotion:{topic}", "people": ["大叔"], "content": turns, "model": client.model, "raw": raw})
        print(f"  [emotion {i+1}/{n}] ✓ {topic} ({len(turns)}轮)")
        time.sleep(0.2)
    return out

def main():
    cfg = load_config()
    persona = load_qwx()
    ib, cb, vb = _render_blocks(persona)
    client = ModelClient(cfg)
    if not client.is_usable:
        print("[error] 未配 API key"); return

    out_path = Path(__file__).parent / "data" / "life_corpus" / "qwx_all.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("=== 生成闲聊 100 条 ===")
    casual = gen_casual(client, persona, ib, cb, vb, 100)
    print("\n=== 生成恋爱场景 80 条 ===")
    romance = gen_romance(client, persona, ib, cb, vb, 80)
    print("\n=== 生成身份锚 30 条 ===")
    identity = gen_identity(client, persona, ib, cb, vb, 30)
    print("\n=== 生成情绪标注 100 条 ===")
    emotion = gen_emotion(client, persona, ib, cb, vb, 100)

    all_data = casual + romance + identity + emotion
    with open(out_path, "w", encoding="utf-8") as f:
        for r in all_data:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n=== 完成 ===")
    print(f"总计 {len(all_data)} 条 → {out_path}")
    print(f"闲聊 {len(casual)} + 恋爱 {len(romance)} + 身份 {len(identity)} + 情绪 {len(emotion)}")
    print(f"\n下一步: python -m data_gen.consistency_check --in {out_path}")

if __name__ == "__main__":
    main()
