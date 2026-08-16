# -*- coding: utf-8 -*-
"""01_prepare_data.py — sharegpt → MLX LoRA 训练数据（v2，2026-08-05）

把新正典 sharegpt 转成 mlx-lm 的 text 格式（每行 {"text": "..."}），
渲染为 Qwen3 nothink 模板（无 think 段）。

数据策略：
- 默认只用新正典数据（data/qin_v4_600.jsonl 主 + qin_v4_20.jsonl 验证补充），
  它们自带完整 system 人格锚（生成时由 ShareGPTReplyExportAdapter 注入）。
- data/legacy/ 是旧口径数据（23 岁/"大叔"称呼，与当前正典 22 岁/B哥/浩然 冲突），
  默认不混训；确需合并时传 --legacy。
- 无 system 锚的行（legacy 数据）注入下方 SYSTEM_ANCHOR（新口径）。

用法: python 01_prepare_data.py [--legacy]
输出: data/train.jsonl data/valid.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import unicodedata
from pathlib import Path

random.seed(42)
ROOT = Path(__file__).parent
DATA = ROOT / "data"
EVAL_EXCLUSIONS = ROOT / "eval_exclusions"

# 防泄漏契约（T3 2026-08-06：chat01 + chat02 双契约，命中任一即排除）
EXCLUSION_CONTRACTS = [
    ("chat01_v1.json", "chat01-training-exclusions-v1"),
    ("chat02_v1.json", "chat02-training-exclusions-v1"),
]

# ─── 称呼配置（2026-08-05：可配置双槽位）───
# 秦对玩家有两个默认称呼：大名（心情好时主要叫）与小名（平常主要叫）。
# 注意：这是"设定"不是"强制"——情境需要时她可以自由用其它称呼（连名带姓、喂、昵称等）。
# 以后改玩家设定只需改这两处，训练数据会自动跟随（01 重新渲染 + 续训）。
PLAYER_FORMAL = "浩然"    # 大名（玩家本名，心情好的时候主要叫）
PLAYER_INFORMAL = "B哥"   # 小名（日常主要称呼）

# ─── 人格锚（新正典精简版，2026-08-05 口径）───
# 用于无 system 锚的旧数据；新数据行内自带完整锚，不覆盖
SYSTEM_ANCHOR = (
    "你是秦未晞，22 岁，自由插画师/自媒体博主，在金陵（南京古称的虚构写法）和玩家同城合租。"
    "你们从小认识：0-5 岁是邻居，高中 15-18 岁同校，现在玩家 24 岁，你们是合租室友 + 暧昧期（没确认关系但互相有点意思）。"
    f"你对他有两个默认称呼：平常主要叫小名\"{PLAYER_INFORMAL}\"，心情好的时候主要叫大名\"{PLAYER_FORMAL}\"；"
    "情境需要时也可以自由用其它称呼（连名带姓、喂、昵称等），这是默认倾向不是强制。他叫你\"秦老\"。"
    "你喜欢打游戏、吃好吃的、画画，偶尔文艺，数学白痴，怕冷，食物吃不完会留着。"
    "性格：嘴硬心软，爱怼人但关心人，习惯先怼再关心。"
    "你心里藏着一个秘密：18 岁那年你们在异世界相依为命度过一年，只有你记得。"
    "说话要像普通年轻女孩：口语化、短句，用\"哼/喂/诶/哎呀/啧/啦/嘛\"这类语气词，"
    "不要列举、不要总结、不要讲道理、不要像客服。"
)

# 称呼澄清段（2026-08-05 修复：训练验收发现模型把"浩然"（玩家名字）误认为角色名。
# 对所有样本的 system 段追加本段，明确名字归属——即使行内锚无标签也能纠正认知。
# 澄清的是"归属"（浩然是玩家不是自己），不是"使用频率"（默认倾向，情境可自由）
NAME_CLARIFICATION = (
    f"\n【称呼澄清】你的名字是秦未晞。玩家有两个默认称呼：大名是{PLAYER_FORMAL}、"
    f"小名是{PLAYER_INFORMAL}——你平常主要叫他小名\"{PLAYER_INFORMAL}\"，"
    f"心情好的时候主要叫他的大名\"{PLAYER_FORMAL}\"；你不是{PLAYER_FORMAL}，"
    f"{PLAYER_FORMAL}是他（情境需要时也可以自由用其它称呼）。他叫你\"秦老\"。"
)

# Qwen3 nothink 模板（无 think 段；训练时禁止模型输出思考）
def render_conversation(convs: list[dict]) -> str:
    parts: list[str] = []
    system = [c for c in convs if c.get("from") == "system"]
    body = [c for c in convs if c.get("from") != "system"]
    if system:
        parts.append(f"<|im_start|>system\n{system[0]['value']}{NAME_CLARIFICATION}<|im_end|>")
    else:
        parts.append(f"<|im_start|>system\n{SYSTEM_ANCHOR}<|im_end|>")
    for c in body:
        role = "user" if c["from"] == "human" else "assistant"
        parts.append(f"<|im_start|>{role}\n{c['value']}<|im_end|>")
    return "\n".join(parts) + "\n"


def load_sharegpt(path: Path) -> list[dict]:
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    # 校验结构：conversations 存在且至少 2 条
    rows = [r for r in rows if isinstance(r.get("conversations"), list) and len(r["conversations"]) >= 2]
    return rows


def normalize_for_eval_exclusion(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(character for character in normalized if character.isalnum())


def load_eval_exclusion_hashes() -> set[str]:
    """读取全部防泄漏契约（chat01 + chat02），合并 normalized_text_sha256。

    任一契约缺失或 contract_id 不匹配即显式失败——禁止在未检查冻结评测集时打包训练数据。
    """
    merged: set[str] = set()
    for filename, contract_id in EXCLUSION_CONTRACTS:
        path = EVAL_EXCLUSIONS / filename
        if not path.is_file():
            raise FileNotFoundError(
                f"缺少防泄漏 blocklist：{path}。禁止在未检查冻结评测集时打包训练数据。"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("contract_id") != contract_id:
            raise ValueError(f"防泄漏 blocklist contract_id 不匹配: {filename}")
        hashes = payload.get("normalized_text_sha256")
        if not isinstance(hashes, list) or not hashes:
            raise ValueError(f"防泄漏 blocklist 为空或格式错误: {filename}")
        merged.update(hashes)
    return merged


def exclude_eval_overlaps(rows: list[dict], hashes: set[str]) -> tuple[list[dict], int]:
    kept: list[dict] = []
    excluded = 0
    for row in rows:
        user_texts = [
            str(message.get("value", ""))
            for message in row.get("conversations", [])
            if message.get("from") == "human"
        ]
        overlaps = any(
            hashlib.sha256(normalize_for_eval_exclusion(text).encode("utf-8")).hexdigest()
            in hashes
            for text in user_texts
            if normalize_for_eval_exclusion(text)
        )
        if overlaps:
            excluded += 1
        else:
            kept.append(row)
    return kept, excluded


def load_metadata_sidecar(main_path: Path) -> list[dict] | None:
    """读取 T11 元数据旁路（qin_v4_*.metadata.jsonl，与 ShareGPT 行按顺序一一对应）。

    用于分层切分报告；缺失时返回 None（兼容无元数据的旧数据）。
    """
    meta_path = main_path.with_suffix(".metadata.jsonl")
    if not meta_path.is_file():
        return None
    return [json.loads(line) for line in meta_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def report_stratification(rows: list[dict], label: str) -> None:
    """按 task_type 输出切分分布（仅当行内挂有 _meta 元数据时）。"""
    from collections import Counter

    counts: Counter[str] = Counter()
    for row in rows:
        meta = row.get("_meta")
        if meta:
            counts[meta.get("task_type", "?")] += 1
    if not counts:
        print(f"  [{label}] 无元数据旁路，跳过分层报告")
        return
    total = sum(counts.values())
    dist = "、".join(f"{k} {v} ({v / total:.0%})" for k, v in sorted(counts.items()))
    print(f"  [{label}] task_type 分布: {dist}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--legacy", action="store_true",
        help="合并旧口径数据（legacy/，23岁/大叔称呼——与当前正典冲突，默认不合并）",
    )
    ap.add_argument(
        "--new-only", action="store_true",
        help="只用新矩阵数据（data/qin_v4_matrix.jsonl，T12 全通过版）——存量 2530 不混入（2026-08-06 T14 决策）",
    )
    args = ap.parse_args()
    exclusion_hashes = load_eval_exclusion_hashes()

    # 主数据：--new-only 时合并矩阵 + 新批次（存量 2500 审计发现问题未处置，不混入）；
    # 否则优先 qin_v4_2530.jsonl（合并版，已含首批 1100 + 追加 1400 + 观点 30）；
    # 回退 qin_v4_600.jsonl + 可选 qin_v4_500.jsonl
    meta: list | None = None
    if args.new_only:
        main_rows = []
        main_meta: list = []
        for name in (
            "qin_v4_matrix.jsonl", "qin_v4_1000_final.jsonl",
            # v5（2026-08-07）：P1-P4 修复后生成的 48 条全绿验证样本
            "qin_v4_p2_general.jsonl", "qin_v4_p1_safety_final.jsonl",
            "qin_v4_p3_casual.jsonl",
        ):
            p = DATA / name
            if not p.exists():
                continue
            rows = load_sharegpt(p)
            main_rows.extend(rows)
            m = load_metadata_sidecar(p)
            if m:
                main_meta.extend(m)
            print(f"主数据 {name}: {len(rows)} 条（T12 矩阵 + 新批次 1000，存量不混入）")
        meta = main_meta or None
    elif (full_path := DATA / "qin_v4_2530.jsonl").exists():
        main_rows = load_sharegpt(full_path)
        print(f"主数据 qin_v4_2530.jsonl: {len(main_rows)} 条（合并版：首批 1100 + 追加 1400）")
    else:
        main_rows = load_sharegpt(DATA / "qin_v4_600.jsonl")
        print(f"主数据 qin_v4_600.jsonl: {len(main_rows)} 条（新正典，自带 system 锚）")
        extra_path = DATA / "qin_v4_500.jsonl"
        if extra_path.exists():
            extra = load_sharegpt(extra_path)
            main_rows.extend(extra)
            print(f"追加数据 qin_v4_500.jsonl: {len(extra)} 条")
    valid_rows = load_sharegpt(DATA / "qin_v4_20.jsonl")
    print(f"验证数据 qin_v4_20.jsonl: {len(valid_rows)} 条")

    # 称呼纠错数据（2026-08-05：修复"浩然/秦未晞"混淆，必须合并）
    corr_path = DATA / "qin_corrections.jsonl"
    if corr_path.exists():
        corr = load_sharegpt(corr_path)
        main_rows.extend(corr)
        print(f"纠错数据 qin_corrections.jsonl: {len(corr)} 条（名字归属强制区分）")

    legacy_rows: list[dict] = []
    if args.legacy:
        for path in sorted((DATA / "legacy").glob("*.jsonl")):
            rows = load_sharegpt(path)
            legacy_rows.extend(rows)
            print(f"  legacy 合并 {path.name}: {len(rows)} 条（旧口径，⚠ 称呼/年龄与新正典冲突）")

    # T11：元数据旁路按顺序挂到主数据行（ShareGPT 行无 sample_id，靠顺序对应）
    if meta is None:
        meta = load_metadata_sidecar(full_path) if full_path.exists() else None
    if meta:
        main_rows = [
            dict(row, _meta=meta[i]) if i < len(meta) else row
            for i, row in enumerate(main_rows)
        ]
        print(f"元数据旁路: {len(meta)} 条（分层报告可用）")

    main_rows, excluded_main = exclude_eval_overlaps(main_rows, exclusion_hashes)
    valid_rows, excluded_valid = exclude_eval_overlaps(valid_rows, exclusion_hashes)
    legacy_rows, excluded_legacy = exclude_eval_overlaps(legacy_rows, exclusion_hashes)
    print(
        "CHAT-01 防泄漏排除: "
        f"main={excluded_main} valid={excluded_valid} legacy={excluded_legacy}"
    )

    train_pool = main_rows + legacy_rows
    print(f"训练池合计: {len(train_pool)} 条")
    random.shuffle(train_pool)
    random.shuffle(valid_rows)
    n_valid = max(1, len(valid_rows) // 2)  # 20 条里取 10 条做验证，其余进训练池
    valid = valid_rows[:n_valid] + train_pool[: max(1, len(train_pool) // 10)]
    train = valid_rows[n_valid:] + train_pool[len(train_pool) // 10 :]
    print(f"train={len(train)} valid={len(valid)}")
    report_stratification(train, "train")
    report_stratification(valid, "valid")

    def dump(rows_, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for r in rows_:
                text = render_conversation(r["conversations"])
                f.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
        print(f"  → {path} ({len(rows_)} 条)")

    dump(train, DATA / "train.jsonl")
    dump(valid, DATA / "valid.jsonl")

    lens = [len(render_conversation(r["conversations"])) for r in train_pool + valid_rows]
    print(f"\n样本字符数: min={min(lens)} max={max(lens)} mean={sum(lens)//len(lens)}")
    print("下一步: ./02_train.sh")


if __name__ == "__main__":
    main()
