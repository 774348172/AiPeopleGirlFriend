"""秦未晞数据修复：对解析失败(1轮)的 chat 样本重新生成。
问题根因：reasoning 模型思考吃满 max_tokens=2048，正文 JSON 被截断。
修复：max_tokens=4096 + user 消息强制直接输出 JSON。
用法: python repair_qwx.py
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gen_qwx import (  # noqa: E402
    CASUAL_TOPICS, EMOTION_TOPICS, ROMANCE_SCENES, SCENES,
    _render_blocks, load_qwx,
)
from data_gen.common import ModelClient, load_config, render_prompt  # noqa: E402

NO_THINK = "【硬性要求】直接输出结果 JSON，不要输出任何思考过程、分析、说明文字。"


def _parse_turns(raw):
    from data_gen.common import parse_json_lenient
    try:
        turns = parse_json_lenient(raw)
        if isinstance(turns, dict):
            for k in ("turns", "conversation", "messages", "dialogue", "data"):
                if isinstance(turns.get(k), list):
                    turns = turns[k]
                    break
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
        return norm
    except Exception as e:  # noqa: BLE001
        return [{"role": "person", "text": f"[解析失败] {e}"}]


def rebuild_prompt(kind, summary, ib, cb, vb):
    """从 node_summary 还原生成参数，重建 prompt。scene 随机重选。"""
    scene = random.choice(SCENES)
    if kind == "casual":
        topic = summary[len("casual:"):]
        return render_prompt("qwx_casual", IDENTITY_BLOCK=ib, CANON_BLOCK=cb,
                             VOICE_BLOCK=vb, SCENE=scene, TOPIC=topic)
    if kind == "romance":
        stype = summary[len("romance:"):]
        return render_prompt("qwx_romance", IDENTITY_BLOCK=ib, CANON_BLOCK=cb,
                             VOICE_BLOCK=vb, SCENE=scene, SCENE_TYPE=stype)
    # emotion
    topic = summary[len("emotion:"):]
    desc = next((d for t, d in EMOTION_TOPICS if t == topic), "")
    return render_prompt("qwx_emotion", IDENTITY_BLOCK=ib, CANON_BLOCK=cb,
                         VOICE_BLOCK=vb, SCENE=f"{scene}（{desc}）", TOPIC=topic)


def main():
    cfg = load_config()
    persona = load_qwx()
    ib, cb, vb = _render_blocks(persona)
    client = ModelClient(cfg)
    if not client.is_usable:
        print("[error] 未配 API key")
        return

    in_path = Path(__file__).parent / "data" / "life_corpus" / "qwx_all.jsonl"
    rows = [json.loads(l) for l in open(in_path, encoding="utf-8")]

    # 失败判定：chat 类且解析后只有 1 轮（含解析失败标记）
    bad = [r for r in rows
           if r["type"] == "chat"
           and len(r["content"]) == 1
           and "解析失败" in r["content"][0].get("text", "")]
    print(f"待修复: {len(bad)} 条")

    fixed = 0
    for i, r in enumerate(bad):
        summary = r["node_summary"]
        kind = summary.split(":", 1)[0]
        prompt = rebuild_prompt(kind, summary, ib, cb, vb)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "开始对话。" + NO_THINK},
        ]
        raw = client.chat(messages, max_tokens=4096)
        turns = _parse_turns(raw)
        ok = len(turns) >= 2
        if ok:
            r["content"] = turns
            r["raw"] = raw
            r["repaired"] = True
            fixed += 1
        print(f"  [{kind} {i+1}/{len(bad)}] {'✓ 修复' if ok else '✗ 仍失败'} "
              f"({summary.split(':',1)[-1][:20]} {len(turns)}轮)")
        time.sleep(0.2)

    with open(in_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n=== 修复完成: {fixed}/{len(bad)} → {in_path} ===")


if __name__ == "__main__":
    main()
