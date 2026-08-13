"""生成于谦闲聊数据。加载于谦 persona，用 glm-5.2 生成自由对话。"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from data_gen.common import ROOT, ModelClient, load_config, parse_json_lenient, render_canon_block, render_identity_block, render_prompt, render_voice_block

YUQIAN_DIR = ROOT / "历史与调研文档" / "历史方案" / "于谦验证"

TOPICS = [
    "今天天气怎么样", "你最近忙什么呢", "你养了多少动物了",
    "最近有什么开心事", "你最近看什么节目了", "你觉得北京生活怎么样",
    "你烫头是自己烫还是找人烫", "你喜欢喝酒吗", "你跟郭德纲平时聊什么",
    "最近有什么烦心事", "你喜欢什么季节", "你觉得朋友重要吗",
    "你一个人的时候干什么", "你想家吗", "你害怕变老吗",
    "你觉得相声还会火多久", "你平时几点起", "你有什么坏习惯",
    "最近有没有遇到什么奇怪的人", "你觉得钱重要吗", "你想去哪旅游",
    "你觉得现在年轻人怎么样", "你喜欢什么吃的", "你最近身体怎么样",
    "如果不说相声你想干什么", "你觉得幸福是什么",
]

SCENES = [
    "在后台等上场，郭德纲在旁边看手机",
    "在家院子里喂马，马棚里味道不太好闻",
    "刚烫完头，在理发店照镜子",
    "凌晨三点睡不着，在客厅抽烟",
    "在德云社后台喝茶，外面下着雨",
    "开车堵在三环上，前面一动不动",
    "遛鸟回来，在小区长椅上歇脚",
    "刚吃完涮羊肉，撑得慌",
    "在书房翻老照片，翻到二十年前跟郭德纲的合影",
    "院子里的鱼又死了一条，正在捞",
]

PARTNERS = [
    {"name": "郭德纲", "note": "搭档，逗哏，爱贫嘴"},
    {"name": "老朋友", "note": "认识多年的发小"},
    {"name": "徒弟", "note": "德云社的年轻演员"},
    {"name": "记者", "note": "来采访的媒体人"},
]


def load_yuqian_persona():
    import yaml
    bible = yaml.safe_load(open(YUQIAN_DIR / "bible.yaml", encoding="utf-8"))
    canon = json.load(open(YUQIAN_DIR / "canon.json", encoding="utf-8"))
    # 构造一个兼容 Persona 接口的对象
    class P:
        pass
    p = P()
    p.bible = bible
    p.canon = canon
    p.name = "于谦"
    p.relationships = bible.get("relationships", [])
    return p


def generate_casual_yq(client, persona, topic, scene, idx):
    other = random.choice(PARTNERS)
    system = render_prompt(
        "casual_yuqian",
        IDENTITY_BLOCK=render_identity_block(persona),
        CANON_BLOCK=render_canon_block(persona.canon),
        VOICE_BLOCK=render_voice_block(persona),
        SCENE=scene,
        OTHER_NAME=other["name"],
        TOPIC=topic,
    )
    raw = client.chat([{"role": "system", "content": system}, {"role": "user", "content": "开始对话。"}])
    try:
        turns = parse_json_lenient(raw)
        if isinstance(turns, dict):
            for k in ("turns", "conversation", "messages", "dialogue", "data"):
                if isinstance(turns.get(k), list):
                    turns = turns[k]
                    break
            else:
                raise ValueError("对象里无数组")
        if not isinstance(turns, list):
            raise ValueError(f"非数组: {type(turns).__name__}")
        norm = []
        for t in turns:
            if isinstance(t, str):
                norm.append({"role": "person", "text": t})
            elif isinstance(t, dict) and "text" in t:
                norm.append({"role": t.get("role", "person"), "text": str(t["text"])})
        turns = norm or [{"role": "person", "text": "[解析失败] 空"}]
    except Exception as e:
        turns = [{"role": "person", "text": f"[解析失败] {e}"}]
    return {
        "id": f"yq_casual_{idx}",
        "type": "chat",
        "date": "",
        "node_index": -1,
        "node_summary": f"casual: {topic}",
        "people": [other["name"]],
        "content": turns,
        "model": client.model,
        "raw": raw,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80, help="生成条数")
    ap.add_argument("--out", default="data/life_corpus/yuqian_casual.jsonl")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    cfg = load_config()
    persona = load_yuqian_persona()
    client = ModelClient(cfg)
    if not client.is_usable:
        print("[error] 未配置 API key")
        return

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_ok = n_fail = 0
    with open(out_path, "w", encoding="utf-8", buffering=1) as f:
        for k in range(args.n):
            topic = random.choice(TOPICS)
            scene = random.choice(SCENES)
            try:
                rec = generate_casual_yq(client, persona, topic, scene, k)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_ok += 1
                print(f"[{k+1}/{args.n}] ✓ {topic} ({len(rec['content'])}轮)")
            except Exception as e:
                n_fail += 1
                print(f"[{k+1}/{args.n}] ✗ {e}")
            time.sleep(args.sleep)
    print(f"\n完成: {n_ok} 成功, {n_fail} 失败 → {out_path}")


if __name__ == "__main__":
    main()
