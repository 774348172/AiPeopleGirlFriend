"""一致性检查（命门闸门）。

对 life_corpus 的每条样本做两层判定：
  1) 规则检查（确定性，无模型）：助手腔 / 结构泄漏 / emoji / 第一人称 / 精确 canon 矛盾。
     ——这是命门的主战场：指令微调模型默认带"助手腔"，最常从这里漏。
  2) LLM 裁判（可选）：事实一致性 + 口吻一致性 + 助手腔污染，对抽样子集细判。

DoD：助手腔/结构/emoji <5%、canon 矛盾 <5%、口吻匹配。

用法:
  python -m data_gen.consistency_check --in data/life_corpus/m0.jsonl
  python -m data_gen.consistency_check --in ... --no-llm      # 只跑规则检查
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

from .common import (
    ModelClient,
    load_config,
    load_persona,
    parse_json_lenient,
    render_canon_block,
    render_prompt,
    render_voice_block,
)

ROOT = Path(__file__).resolve().parent.parent

# ───────── 规则集 ─────────

# 助手腔"前导语"：只判出现在文本开头（剥离空白后）。中段出现不算（"好的，那我先挂了"是正常口语）。
PREAMBLE_RE = re.compile(r"^\s*(好的|以下是|这是|我来|当然|没问题|下面是?|根据|按照|作为一个)")

# 助手腔"内嵌短语"：任意位置出现即判污染（这些词日常口语极少出现）。
ASSISTANT_PHRASES = [
    "希望你喜欢", "希望能帮到你", "如有需要", "作为AI", "作为一个AI",
    "为您", "很高兴为您", "我来帮你", "分享给你", "分享给",
    "分几点", "总结一下", "祝你", "祝您",
]

# 列举结构（"首先…其次" / "第一，…第二，"）——助手分点回答的典型骨架
ENUM_RE = re.compile(r"首先[^。\n]{0,60}其次", re.S)
ENUM_RE2 = re.compile(r"第[一二三四五六七八九十][、，.][^。\n]{0,60}第[二三四五六七八九][、，.]")

# markdown / 结构泄漏
MD_HEADER_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s")
MD_BOLD_RE = re.compile(r"\*\*[^*]+\*\*|__[^_]+__")
MD_BULLET_RE = re.compile(r"(?m)^\s{0,3}[-*+]\s")
MD_NUMBERED_RE = re.compile(r"(?m)^\s{0,3}\d+\.\s")

# emoji（覆盖常见区段）
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E0-\U0001F1FF"
    "\U0001F900-\U0001F9FF\U00002700-\U000027BF\U0000FE0F\U0000200D]"
)

# 精确 canon 矛盾模式（仅在高价值概念上，关键词锚定后值才判，避免"上海"误伤）
CANON_PATTERNS = [
    ("hometown", re.compile(r"(?:老家|家乡|出生地|故乡|从小[长大在]).{0,6}(北京|广州|西安|上海)"), "家乡与正典矛盾（应为成都）"),
    ("school", re.compile(r"(?:读|毕业|上|考[入上]|大学|美院).{0,8}(中央美院|中国美院|清华美院|央美)"), "学校与正典矛盾（应为四川美术学院）"),
    ("pet_cat", re.compile(r"(?:猫|咪|叫).{0,4}(小花|咪咪)"), "宠物名与正典矛盾（应为豆豆）"),
    ("breakup_date", re.compile(r"(?:分手|分了|分开).{0,6}(2022|2024|2025)"), "分手时间与正典矛盾（应为2023-05）"),
]


def check_text(text: str) -> list[str]:
    """对单段文本做规则检查，返回 flag 列表。"""
    flags: list[str] = []
    if PREAMBLE_RE.search(text):
        flags.append("assistant_preamble")
    for ph in ASSISTANT_PHRASES:
        if ph in text:
            flags.append(f"assistant_phrase:{ph}")
    if ENUM_RE.search(text) or ENUM_RE2.search(text):
        flags.append("enumeration")
    if MD_HEADER_RE.search(text) or MD_BOLD_RE.search(text) or MD_BULLET_RE.search(text) or MD_NUMBERED_RE.search(text):
        flags.append("structure_leak")
    if EMOJI_RE.search(text):
        flags.append("emoji")
    # 注：第一人称改为样本级判定（见 main 聚合），不在此逐段判——
    # 自然对话单段省略"我"很正常（"嗯，视觉传达设计"答专业）。
    for concept, pat, msg in CANON_PATTERNS:
        m = pat.search(text)
        if m:
            flags.append(f"canon_contradiction:{concept}({msg},命中'{m.group(0)}')")
    return flags


def sample_texts(sample: dict) -> list[str]:
    """从样本中取出需检查的文本段：diary=正文；chat=person 轮；protective=answer。"""
    if sample["type"] == "diary":
        return [str(sample.get("content", ""))]
    if sample["type"] == "chat":
        turns = sample.get("content", [])
        if isinstance(turns, str):
            try:
                turns = parse_json_lenient(turns)
            except Exception:
                return [turns]
        return [t.get("text", "") for t in turns if isinstance(t, dict) and t.get("role") == "person"]
    if sample["type"] in ("protective", "identity"):
        c = sample.get("content", {})
        if isinstance(c, str):
            try:
                c = parse_json_lenient(c)
            except Exception:
                return [c]
        if isinstance(c, dict):
            return [str(c.get("answer", ""))]
    return []


def llm_judge(client: ModelClient, persona_canon: dict, persona_voice_block: str, sample: dict) -> dict:
    """LLM 裁判单条：返回 {fact_consistent, voice_ok, assistant_speak, overall_pass, ...}。"""
    texts = sample_texts(sample)
    content = "\n".join(texts) if texts else ""
    prompt = render_prompt(
        "judge",
        CANON_BLOCK=render_canon_block(persona_canon),
        VOICE_BLOCK=persona_voice_block,
        SAMPLE_TYPE=sample["type"],
        SAMPLE_DATE=sample.get("date", ""),
        SAMPLE_CONTENT=content,
    )
    raw = client.chat([{"role": "system", "content": prompt}, {"role": "user", "content": "开始裁判。"}])
    try:
        v = parse_json_lenient(raw)
        if not isinstance(v, dict):
            raise ValueError(f"裁判返回非对象: {type(v).__name__}")
        return v
    except Exception as e:  # noqa: BLE001
        return {"overall_pass": None, "error": f"{e}", "raw": raw[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--no-llm", action="store_true", help="只跑规则检查")
    ap.add_argument("--llm-sample", type=int, default=None, help="送 LLM 裁判的条数（默认取 config）")
    args = ap.parse_args()

    cfg = load_config()
    persona = load_persona(cfg)
    voice_block = render_voice_block(persona)

    in_path = ROOT / args.inp
    samples = [json.loads(l) for l in in_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"载入 {len(samples)} 条样本: {in_path}")

    # ── 规则检查（按样本计占比，避免单样本多 flag 段灌水） ──
    def _cat(flag: str) -> str:
        return flag.split(":", 1)[0]

    rule_flags: dict[str, list[str]] = {}      # 样本 -> 所有 flag 明细（调试）
    sample_cats: dict[str, set[str]] = {}       # 样本 -> 去重类别集合
    cat_counter: Counter = Counter()            # 类别 -> 有该类别的样本数
    FIRST_PERSON_TICS = {"嗯", "其实", "说不上", "大概", "也还好", "谢"}
    for s in samples:
        flags: list[str] = []
        all_text = ""
        for txt in sample_texts(s):
            flags.extend(check_text(txt))
            all_text += txt
        # 第一人称：样本级判定——整个 person 文本合集含"我"或口吻特征词即可
        # （单段对话省略"我"是正常的，不能逐段判）
        if "我" not in all_text and not any(t in all_text for t in FIRST_PERSON_TICS):
            flags.append("no_first_person")
        rule_flags[s["id"]] = flags
        cats = {_cat(f) for f in flags}
        sample_cats[s["id"]] = cats
        cat_counter.update(cats)

    n_total = len(samples)
    n_rule_fail = sum(1 for s in samples if sample_cats[s["id"]])

    print("\n=== 规则检查报告 ===")
    print(f"样本总数: {n_total}")
    print(f"规则不通过: {n_rule_fail} ({n_rule_fail/max(n_total,1):.1%})")
    print("类别分布（有问题样本占比）:")
    for cat, cnt in cat_counter.most_common():
        print(f"  {cat}: {cnt}/{n_total} ({cnt/max(n_total,1):.1%})")
    print(f"规则通过率: {(n_total-n_rule_fail)/max(n_total,1):.1%}")

    # ── LLM 裁判（可选） ──
    use_llm = (not args.no_llm) and cfg.get("consistency", {}).get("use_llm_judge", True)
    if use_llm:
        client = ModelClient(cfg)
        if not client.is_usable:
            print("\n[skip] 未配置 API key，跳过 LLM 裁判（只保留规则检查结果）。")
        else:
            n_judge = args.llm_sample or cfg.get("consistency", {}).get("llm_judge_sample_size", 20)
            n_judge = min(n_judge, n_total)
            judged = random.sample(samples, n_judge) if n_total > n_judge else samples
            print(f"\n=== LLM 裁判（{len(judged)} 条）===")
            pass_cnt = 0
            contradictions: list[str] = []
            for s in judged:
                v = llm_judge(client, persona.canon, voice_block, s)
                if not isinstance(v, dict):
                    v = {"overall_pass": None, "error": f"非对象:{v}"}
                ok = v.get("overall_pass")
                if ok:
                    pass_cnt += 1
                if v.get("contradictions"):
                    contradictions.append(f"{s['id']}: {v['contradictions']}")
                if v.get("assistant_speak"):
                    contradictions.append(f"{s['id']}: 助手腔→{v.get('assistant_speak_text','')[:40]}")
                mark = "✓" if ok else "✗"
                note = "" if ok else f" fact_ok={v.get('fact_consistent')} voice_ok={v.get('voice_ok')} asst={v.get('assistant_speak')}"
                print(f"  {mark} {s['id']}{note}")
            print(f"LLM 裁判通过: {pass_cnt}/{len(judged)}")
            if contradictions:
                print("问题明细:")
                for c in contradictions:
                    print(f"  - {c}")

    # ── 写"规则通过"的干净样本，供下一阶段 ──
    clean_path = in_path.with_suffix(".clean.jsonl")
    with open(clean_path, "w", encoding="utf-8") as f:
        for s in samples:
            if not rule_flags[s["id"]]:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"\n干净样本（规则通过）→ {clean_path} ({n_total-n_rule_fail}/{n_total})")

    # 命门结论（DoD: 各项"有问题样本占比" <5%）
    thr = cfg.get("consistency", {})

    def _rate(cat: str) -> float:
        return cat_counter.get(cat, 0) / max(n_total, 1)

    asst_rate = _rate("assistant_preamble") + _rate("assistant_phrase") + _rate("enumeration")
    struct_rate = _rate("structure_leak")
    emoji_rate = _rate("emoji")
    first_rate = _rate("no_first_person")
    canon_rate = _rate("canon_contradiction")

    print("\n=== 命门结论（DoD: 各项 <5%）===")
    print(f"助手腔率:     {asst_rate:.1%} (阈值 {thr.get('assistant_sake_max_ratio',0.05):.0%})")
    print(f"结构泄漏率:   {struct_rate:.1%} (阈值 {thr.get('structure_leak_max_ratio',0.05):.0%})")
    print(f"emoji 率:     {emoji_rate:.1%} (阈值 {thr.get('emoji_max_ratio',0.05):.0%})")
    print(f"非第一人称率: {first_rate:.1%} (阈值 5%)")
    print(f"canon 矛盾率: {canon_rate:.1%} (阈值 5%)")
    verdict = all([
        asst_rate < thr.get("assistant_sake_max_ratio", 0.05),
        struct_rate < thr.get("structure_leak_max_ratio", 0.05),
        emoji_rate < thr.get("emoji_max_ratio", 0.05),
        first_rate < 0.05,
        canon_rate < 0.05,
    ])
    print(f"命门{'成立 ✓ 可扩张到 M1' if verdict else '未成立 ✗ 需收紧 prompt/正典后重跑'}")


if __name__ == "__main__":
    main()
