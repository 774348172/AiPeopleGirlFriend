"""秦未晞数据二次修复：处理剩余 13 条顽固失败。
策略：
1. 先从 raw 里正则恢复被截断的 JSON 对话轮次（零成本）
2. 恢复不了的重新生成（max_tokens=6144 + 更强制禁止思考）
用法: python repair_qwx2.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from repair_qwx import NO_THINK, _parse_turns, rebuild_prompt  # noqa: E402
from data_gen.common import ModelClient, load_config  # noqa: E402

STRICTER = ("【硬性要求】禁止任何思考/分析/前言。第一个字符必须是 [，"
            "直接输出完整 JSON 数组，输出结尾必须是 ]。")

TURN_RE = re.compile(
    r'\{\s*"role"\s*:\s*"(other|person|user|assistant)"\s*,\s*"text"\s*:\s*"(.*?)"\s*\}',
    re.S,
)


def recover_from_raw(raw):
    """从被截断的 raw 中正则提取对话轮次（容错，不要求完整 JSON）。"""
    turns = []
    for m in TURN_RE.finditer(raw):
        role = "person" if m.group(1) in ("person", "assistant") else "other"
        text = m.group(2)
        # 反转义常见 JSON 转义
        text = (text.replace(r"\"", '"').replace(r"\\", "\\")
                .replace(r"\n", "\n").replace(r"\u201c", "“").replace(r"\u201d", "”"))
        turns.append({"role": role, "text": text})
    return turns


def main():
    cfg = load_config()
    from gen_qwx import _render_blocks, load_qwx
    persona = load_qwx()
    ib, cb, vb = _render_blocks(persona)
    client = ModelClient(cfg)

    in_path = Path(__file__).parent / "data" / "life_corpus" / "qwx_all.jsonl"
    rows = [json.loads(l) for l in open(in_path, encoding="utf-8")]
    bad = [r for r in rows
           if r["type"] == "chat"
           and len(r["content"]) == 1
           and "解析失败" in r["content"][0].get("text", "")]
    print(f"待处理: {len(bad)} 条")

    fixed, regenerated = 0, 0
    for i, r in enumerate(bad):
        # 先尝试从 raw 恢复
        turns = recover_from_raw(r["raw"])
        if len(turns) >= 2:
            r["content"] = turns
            r["recovered_from_raw"] = True
            fixed += 1
            print(f"  [{i+1}/{len(bad)}] ✓ 从raw恢复 ({len(turns)}轮) {r['id']}")
            continue
        # 恢复失败 → 重新生成
        summary = r["node_summary"]
        kind = summary.split(":", 1)[0]
        prompt = rebuild_prompt(kind, summary, ib, cb, vb)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "开始对话。" + NO_THINK + STRICTER},
        ]
        raw = client.chat(messages, max_tokens=6144)
        turns = _parse_turns(raw)
        ok = len(turns) >= 2
        if ok:
            r["content"] = turns
            r["raw"] = raw
            r["repaired"] = True
            fixed += 1
            regenerated += 1
        print(f"  [{i+1}/{len(bad)}] {'✓ 重生成' if ok else '✗ 仍失败'} "
              f"({len(turns)}轮) {r['id']} {summary.split(':',1)[-1][:16]}")
        time.sleep(0.2)

    with open(in_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n=== 完成: 修复 {fixed}/{len(bad)} (raw恢复 {fixed-regenerated} + 重生成 {regenerated}) ===")


if __name__ == "__main__":
    main()
