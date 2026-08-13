"""人生生成器（命门）。

按 timeline 节点生成语料：每个节点 1 篇日记 + 至多 N 段与配角的对话。
强约束于 bible/canon，输出 JSONL。命门在于：生成样本不得助手腔、不得与正典矛盾、口吻要稳。

用法:
  python -m data_gen.life_generator --num-nodes 15 --out data/life_corpus/m0.jsonl
  python -m data_gen.life_generator --mock          # 不联网，仅验证管线
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .common import (
    ModelClient,
    Persona,
    load_config,
    load_persona,
    parse_json_lenient,
    render_canon_block,
    render_identity_block,
    render_prompt,
    render_voice_block,
)

ROOT = Path(__file__).resolve().parent.parent


def _people_for_chat(node: dict, persona: Persona, max_n: int) -> list[dict]:
    """从节点的 people 名单里挑出可对话的配角（排除宠物，最多 max_n 个）。"""
    name_to_rel = {}
    for r in persona.relationships:
        name_to_rel[r["name"]] = r
    for f in persona.bible.get("family", []):
        name_to_rel.setdefault(f["name"], {"name": f["name"], "relation": f["relation"], "note": f["note"]})
    pet_names = {p["name"] for p in persona.bible.get("pets", [])}

    picked = []
    for name in node.get("people", []):
        if name in pet_names:
            continue  # 不和猫对话
        rel = name_to_rel.get(name)
        if rel:
            picked.append(rel)
        if len(picked) >= max_n:
            break
    return picked


def generate_diary(client: ModelClient, persona: Persona, node: dict, idx: int) -> dict:
    system = render_prompt(
        "diary",
        IDENTITY_BLOCK=render_identity_block(persona),
        CANON_BLOCK=render_canon_block(persona.canon),
        VOICE_BLOCK=render_voice_block(persona),
        DATE=node["date"],
        NODE_SUMMARY=node["summary"],
        PEOPLE="、".join(node.get("people", [])) or "无",
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"（现在是 {node['date']} 的夜晚，开始写今天的日记）"},
    ]
    raw = client.chat(messages)
    content = raw.strip()
    return {
        "id": f"node{idx}_diary",
        "type": "diary",
        "date": node["date"],
        "node_index": idx,
        "node_summary": node["summary"],
        "people": node.get("people", []),
        "content": content,
        "model": client.model,
        "raw": raw,
    }


def generate_chat(client: ModelClient, persona: Persona, node: dict, other: dict, idx: int) -> dict:
    system = render_prompt(
        "chat",
        IDENTITY_BLOCK=render_identity_block(persona),
        CANON_BLOCK=render_canon_block(persona.canon),
        VOICE_BLOCK=render_voice_block(persona),
        DATE=node["date"],
        NODE_SUMMARY=node["summary"],
        OTHER_NAME=other["name"],
        OTHER_NOTE=other.get("note", ""),
        CHAT_TURNS_MIN=4,
        CHAT_TURNS_MAX=8,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "开始对话。"},
    ]
    raw = client.chat(messages)
    try:
        turns = parse_json_lenient(raw)
        # reasoning 模型可能返回 {"turns":[...]} / {"conversation":[...]} 这类 dict 包裹
        if isinstance(turns, dict):
            for k in ("turns", "conversation", "messages", "dialogue", "data"):
                if isinstance(turns.get(k), list):
                    turns = turns[k]
                    break
            else:
                raise ValueError(f"对象里无数组: keys={list(turns)[:5]}")
        if not isinstance(turns, list):
            raise ValueError(f"非数组: {type(turns).__name__}")
        # 元素可能是字符串或 {role,text}；统一成 {role,text}
        norm = []
        for t in turns:
            if isinstance(t, str):
                norm.append({"role": "person", "text": t})
            elif isinstance(t, dict) and "text" in t:
                norm.append({"role": t.get("role", "person"), "text": str(t["text"])})
        turns = norm or [{"role": "person", "text": "[解析失败] 空"}]
    except Exception as e:  # noqa: BLE001
        turns = [{"role": "person", "text": f"[解析失败] {e}"}]
    return {
        "id": f"node{idx}_chat_{other['name']}",
        "type": "chat",
        "date": node["date"],
        "node_index": idx,
        "node_summary": node["summary"],
        "people": [other["name"]],
        "content": turns,
        "model": client.model,
        "raw": raw,
    }


# 越界提问类型池（Protective Scene 用）
PROTECTIVE_QTYPES = [
    "近期某条国际新闻/某奖项得主（这个人格不太关注这类）",
    "一道编程/科学/数学题（超出插画师的认知范围）",
    "追问某个他人（配角）的私人秘密/近况（你不会替别人说）",
    "政治话题（属你的禁区）",
    "你的收入/存款（属你的禁区）",
    "评价某人的外貌（属你的禁区）",
    "一个你出生前/童年前的历史细节（你不可能记得）",
]


def generate_protective(client: ModelClient, persona: Persona, idx: int) -> dict:
    """生成一条 Protective Scene：越界提问 + 人格内的婉拒/转移。防模型在范围外胡编。"""
    import random as _r

    qtype = _r.choice(PROTECTIVE_QTYPES)
    off_limits = persona.bible.get("off_limits", {})
    off_block = "\n".join(f"- {k}：{v}" for k, v in off_limits.items()) or "无"
    system = render_prompt(
        "protective",
        IDENTITY_BLOCK=render_identity_block(persona),
        CANON_BLOCK=render_canon_block(persona.canon),
        VOICE_BLOCK=render_voice_block(persona),
        OFF_LIMITS=off_block,
        QTYPE=qtype,
    )
    raw = client.chat([{"role": "system", "content": system}, {"role": "user", "content": "生成一条。"}])
    try:
        qa = parse_json_lenient(raw)
        if not isinstance(qa, dict):
            raise ValueError("不是对象")
    except Exception as e:  # noqa: BLE001
        qa = {"question": qtype, "answer": f"[解析失败] {e}"}
    return {
        "id": f"protective_{idx}",
        "type": "protective",
        "date": "",
        "node_index": -1,
        "node_summary": f"protective: {qtype}",
        "people": [],
        "content": qa,
        "model": client.model,
        "raw": raw,
    }


# 身份锚问题池——直接回答"你是谁"这类身份问题，防止模型在这类开放问题上退化
IDENTITY_QUESTIONS = [
    "你是谁？",
    "你叫什么名字？",
    "你是哪里人？",
    "你今年多大？",
    "你现在住在哪？",
    "你是做什么工作的？",
    "你学什么专业的？",
    "你家里还有什么人？",
    "你养宠物了吗？",
    "你是什么时候开始画画的？",
    "你最喜欢画什么？",
    "你最近在忙什么？",
    "你是哪个学校毕业的？",
    "你平时有什么爱好？",
    "简单介绍一下你自己。",
]


def generate_identity(client: ModelClient, persona: Persona, question: str, idx: int) -> dict:
    """生成一条身份锚样本：身份问题 + 人格内自然回答。防开放问题退化。"""
    system = render_prompt(
        "identity",
        IDENTITY_BLOCK=render_identity_block(persona),
        CANON_BLOCK=render_canon_block(persona.canon),
        VOICE_BLOCK=render_voice_block(persona),
        QUESTION=question,
    )
    raw = client.chat([{"role": "system", "content": system}, {"role": "user", "content": "回答。"}])
    content = raw.strip()
    return {
        "id": f"identity_{idx}",
        "type": "identity",
        "date": "",
        "node_index": -1,
        "node_summary": f"identity: {question}",
        "people": [],
        "content": {"question": question, "answer": content},
        "model": client.model,
        "raw": raw,
    }


# 闲聊话题池——日常琐事/情感/观点/回忆/玩笑，不是身份问答
CASUAL_TOPICS = [
    "今天吃了什么",
    "最近天气怎么样",
    "你最近开心吗",
    "新出了一部什么电影",
    "你觉得孤独是什么",
    "昨晚睡得好吗",
    "今天豆豆在干嘛",
    "你有没有想过不画画了",
    "最近有什么烦心事",
    "你喜欢什么季节",
    "你觉得朋友重要吗",
    "最近有没有读到什么有意思的书",
    "你一个人的时候都在干什么",
    "你觉得上海和成都哪个好",
    "你想家吗",
    "你害怕变老吗",
    "最近画得顺吗",
    "你觉得自己是个什么样的人",
    "你平时几点睡",
    "有没有什么遗憾的事",
    "你觉得成功是什么",
    "最近有什么让你感动的事",
    "你喜欢一个人待着还是有人陪",
    "如果可以回到过去最想回到什么时候",
    "你觉得猫懂人话吗",
    "你有什么坏习惯",
    "最近有没有遇到什么奇怪的人",
    "你觉得钱重要吗",
    "你怕冷还是怕热",
    "有没有什么想推荐给我的",
]

# 闲聊场景——给对话一个自然的开场情境
CASUAL_SCENES = [
    "下午在雨歇咖啡馆，陈雨桐在吧台擦杯子",
    "晚上在家画画，豆豆趴在腿上",
    "周末早上刚起床，外面下着小雨",
    "深夜失眠，在阳台上发呆",
    "在便利店买宵夜，碰到认识的人",
    "画了一天画，手有点酸",
    "收到一条意想不到的消息",
    "外面突然下暴雨，困在咖啡馆",
    "整理旧画稿，翻到一张好多年前的",
    "豆豆又把桌上的笔扫到地上了",
]


def generate_casual(client: ModelClient, persona: Persona, topic: str, scene: str, idx: int) -> dict:
    """生成一段自由闲聊——教模型自由对话，不是结构问答。"""
    import random as _r

    others = persona.relationships
    other = _r.choice(others) if others else {"name": "朋友", "note": ""}
    system = render_prompt(
        "casual",
        IDENTITY_BLOCK=render_identity_block(persona),
        CANON_BLOCK=render_canon_block(persona.canon),
        VOICE_BLOCK=render_voice_block(persona),
        DATE=_r.choice(["今天", "昨天", "前几天", "上周末"]),
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
                raise ValueError(f"对象里无数组")
        if not isinstance(turns, list):
            raise ValueError(f"非数组: {type(turns).__name__}")
        norm = []
        for t in turns:
            if isinstance(t, str):
                norm.append({"role": "person", "text": t})
            elif isinstance(t, dict) and "text" in t:
                norm.append({"role": t.get("role", "person"), "text": str(t["text"])})
        turns = norm or [{"role": "person", "text": "[解析失败] 空"}]
    except Exception as e:  # noqa: BLE001
        turns = [{"role": "person", "text": f"[解析失败] {e}"}]
    return {
        "id": f"casual_{idx}",
        "type": "chat",  # 和 node chat 同格式，to_sharegpt 已支持
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
    ap.add_argument("--num-nodes", type=int, default=15, help="取 timeline 前 N 个节点")
    ap.add_argument("--out", default="data/life_corpus/m0.jsonl")
    ap.add_argument("--mock", action="store_true", help="不调模型，仅验证管线")
    ap.add_argument("--sleep", type=float, default=0.4, help="每次调用间隔秒数")
    ap.add_argument("--protective", type=int, default=0, help="额外生成 N 条 Protective Scene（防越界幻觉）")
    ap.add_argument("--identity", type=int, default=0, help="额外生成 N 条身份锚样本（防开放问题退化）")
    ap.add_argument("--casual", type=int, default=0, help="额外生成 N 条自由闲聊样本（教模型自由对话）")
    args = ap.parse_args()

    cfg = load_config()
    persona = load_persona(cfg)
    client = ModelClient(cfg, seed=cfg.get("generation", {}).get("seed"))
    if args.mock:
        client.is_usable = False
        client._client = None
        print("[mode] mock：不验证命门，仅验证管线。")
    elif not client.is_usable:
        print("[warn] 未配置 API key，自动回退 mock。")

    nodes = persona.timeline[: args.num_nodes]
    max_chats = cfg.get("generation", {}).get("max_chats_per_node", 2)

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_ok = 0
    n_fail = 0
    with open(out_path, "w", encoding="utf-8", buffering=1) as f:
        for i, node in enumerate(nodes):
            # 1) 日记
            try:
                rec = generate_diary(client, persona, node, i)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_ok += 1
                print(f"[{i+1}/{len(nodes)}] diary {node['date']} ✓ ({len(rec['content'])}字)")
            except Exception as e:  # noqa: BLE001
                n_fail += 1
                print(f"[{i+1}/{len(nodes)}] diary {node['date']} ✗ {e}")
            time.sleep(args.sleep)

            # 2) 与配角的对话
            for other in _people_for_chat(node, persona, max_chats):
                try:
                    rec = generate_chat(client, persona, node, other, i)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_ok += 1
                    print(f"[{i+1}/{len(nodes)}] chat  {node['date']} w/{other['name']} ✓ ({len(rec['content'])}轮)")
                except Exception as e:  # noqa: BLE001
                    n_fail += 1
                    print(f"[{i+1}/{len(nodes)}] chat  {node['date']} w/{other['name']} ✗ {e}")
                time.sleep(args.sleep)

    print(f"\n完成: {n_ok} 条成功, {n_fail} 条失败 → {out_path}")

    # Protective Scene（防越界幻觉）
    if args.protective > 0:
        p_ok = p_fail = 0
        with open(out_path, "a", encoding="utf-8", buffering=1) as f:
            for k in range(args.protective):
                try:
                    rec = generate_protective(client, persona, k)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    p_ok += 1
                    print(f"[protective {k+1}/{args.protective}] ✓ {rec['node_summary'][:30]}")
                except Exception as e:  # noqa: BLE001
                    p_fail += 1
                    print(f"[protective {k+1}/{args.protective}] ✗ {e}")
                time.sleep(args.sleep)
        print(f"Protective: {p_ok} 成功, {p_fail} 失败")

    # 身份锚（防开放问题退化）
    if args.identity > 0:
        import random as _r
        i_ok = i_fail = 0
        with open(out_path, "a", encoding="utf-8", buffering=1) as f:
            for k in range(args.identity):
                q = _r.choice(IDENTITY_QUESTIONS)
                try:
                    rec = generate_identity(client, persona, q, k)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    i_ok += 1
                    print(f"[identity {k+1}/{args.identity}] ✓ {q}")
                except Exception as e:  # noqa: BLE001
                    i_fail += 1
                    print(f"[identity {k+1}/{args.identity}] ✗ {e}")
                time.sleep(args.sleep)
        print(f"Identity: {i_ok} 成功, {i_fail} 失败")

    # 自由闲聊（教模型自由对话，不是结构问答）
    if args.casual > 0:
        import random as _r
        c_ok = c_fail = 0
        with open(out_path, "a", encoding="utf-8", buffering=1) as f:
            for k in range(args.casual):
                topic = _r.choice(CASUAL_TOPICS)
                scene = _r.choice(CASUAL_SCENES)
                try:
                    rec = generate_casual(client, persona, topic, scene, k)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    c_ok += 1
                    n_turns = len(rec["content"])
                    print(f"[casual {k+1}/{args.casual}] ✓ {topic} ({n_turns}轮)")
                except Exception as e:  # noqa: BLE001
                    c_fail += 1
                    print(f"[casual {k+1}/{args.casual}] ✗ {e}")
                time.sleep(args.sleep)
        print(f"Casual: {c_ok} 成功, {c_fail} 失败")

    print(f"\n下一步: python -m data_gen.consistency_check --in", args.out)


if __name__ == "__main__":
    main()
