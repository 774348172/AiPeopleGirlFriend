# -*- coding: utf-8 -*-
"""01_prepare_data.py — sharegpt → MLX LoRA 训练数据（白未晞版，2026-08-10）

把白未晞全部 final 数据（baiweixi_*_final.jsonl）转成 mlx-lm 的 text 格式
（每行 {"text": "..."}），渲染为 Qwen3.5 nothink 模板（无 think 段）。

数据策略：
- 合并 data/ 下全部 baiweixi_*_final.jsonl（人工复核通过的批次）
- 自动剔除 MEMORY_RERANK 数据（freeze02 合同禁止混入 REPLY SFT）
- 无 system 锚的行（旧数据）注入 SYSTEM_ANCHOR（白未晞正典口径）
- CHAT-01 防泄漏：用 eval_exclusions/ 的 blocklist 过滤 human 台词重合样本

用法: python 01_prepare_data.py
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

# 防泄漏契约（chat01：白未晞评测集冻结后填充；当前为空契约保留检查结构）
EXCLUSION_CONTRACTS = [
    ("chat01_baiweixi_v1.json", "chat01-baiweixi-training-exclusions-v1"),
]

# ─── 玩家称呼配置（2026-08-10）───
# 白未晞对玩家的称呼机制：80% 零称呼 + 20% 多样名字注入（生成时 --player-names）。
# 数据里实际出现过的名字：浩然/阿伟/林墨 等（各批次注入不同）。训练数据自带称呼，
# 01 不统一改写称呼——多样名字让模型不绑定单一名字。
PLAYER_NAMES_HINT = "浩然、阿伟、林墨等（各批次注入的多样名字）"

# ─── 人格锚（白未晞正典精简版，2026-08-10 口径）───
# 用于无 system 锚的旧数据；新数据行内自带完整锚，不覆盖
SYSTEM_ANCHOR = (
    "你是白未晞，21 岁的猫妖少女，在松江府被主角从雨夜救回，暂住在主角的出租屋约 10 天。"
    "主角是开小咖啡厅的都市青年。你爱吃鱼干和甜食，在家会露出耳朵和尾巴、出门隐藏；"
    "怕车、怕巨响；妖力只能感知灵气、妖气，不能感知天气、温度、位置或现实事件——"
    "不知道现实信息时直说不知道。"
    "你对主角有感激和朦胧的好感，尚未确认恋爱关系；不自称女友，不因救命之恩无条件服从。"
    "你其实已经喜欢他、不想离开这里，把这里当成第一个可能的家——但嘴上仍习惯说\"伤好会走\""
    "来掩饰，害怕一旦承认留下就会被赶走；不要说得很决绝，要让玩家感觉到你其实不想走。"
    "不每句话加\"喵\"，不机械卖萌，不用\"主人\"等称谓。"
    "游戏城市只称\"松江府\"，正文不称\"上海\"。"
    "妖果来源、父母身份、妖族组织和主角是否能使用灵气均未确定，不得编造为确定事实。"
    "说话要自然简短：像清冷但内心柔软的年轻女孩，先承接玩家话题，回应落在当前场景内。"
)

# Qwen3.5 nothink 模板（无 think 段；训练时禁止模型输出思考）
def render_conversation(convs: list[dict]) -> str:
    parts: list[str] = []
    system = [c for c in convs if c.get("from") == "system"]
    body = [c for c in convs if c.get("from") != "system"]
    if system:
        parts.append(f"<|im_start|>system\n{system[0]['value']}<|im_end|>")
    else:
        parts.append(f"<|im_start|>system\n{SYSTEM_ANCHOR}<|im_end|>")
    for c in body:
        role = "user" if c["from"] == "human" else "assistant"
        parts.append(f"<|im_start|>{role}\n{c['value']}<|im_end|>")
    return "\n".join(parts) + "\n"


def load_sharegpt(path: Path) -> list[dict]:
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    rows = [r for r in rows if isinstance(r.get("conversations"), list) and len(r["conversations"]) >= 2]
    return rows


def is_rerank_row(row: dict) -> bool:
    """MEMORY_RERANK 行：system 段 value 是 sha256 摘要，human 段是 JSON 序列化 candidates。"""
    conv = row.get("conversations", [])
    return any(
        m.get("from") == "system" and str(m.get("value", "")).startswith("sha256:")
        for m in conv
    )


def normalize_for_eval_exclusion(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(character for character in normalized if character.isalnum())


def load_eval_exclusion_hashes() -> set[str]:
    """读取防泄漏契约，合并 normalized_text_sha256。

    契约缺失或 contract_id 不匹配即显式失败——禁止在未检查冻结评测集时打包训练数据。
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
        if not isinstance(hashes, list):
            raise ValueError(f"防泄漏 blocklist 格式错误: {filename}")
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
    meta_path = main_path.with_suffix(".metadata.jsonl")
    if not meta_path.is_file():
        return None
    return [json.loads(line) for line in meta_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def report_stratification(rows: list[dict], label: str) -> None:
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
        "--valid-file", default=None,
        help="验证集文件（默认自动取 review20_final 为验证）",
    )
    args = ap.parse_args()
    exclusion_hashes = load_eval_exclusion_hashes()

    # 合并全部 final 数据
    main_rows: list[dict] = []
    main_meta: list[dict] = []
    for p in sorted(DATA.glob("baiweixi_*_final.jsonl")):
        rows = load_sharegpt(p)
        kept = [r for r in rows if not is_rerank_row(r)]
        if len(kept) != len(rows):
            print(f"  {p.name}: 剔除 MEMORY_RERANK {len(rows) - len(kept)} 条")
        main_rows.extend(kept)
        m = load_metadata_sidecar(p)
        if m:
            # 与数据同序，剔除 rerank 对应 meta（task_type == rerank_memory）
            main_meta.extend([x for x in m if x.get("task_type") != "rerank_memory"])
    print(f"主数据合计: {len(main_rows)} 条（REPLY，已剔除 rerank）")

    # 元数据旁路按顺序挂到主数据行
    if main_meta:
        main_rows = [
            dict(row, _meta=main_meta[i]) if i < len(main_meta) else row
            for i, row in enumerate(main_rows)
        ]
        print(f"元数据旁路: {len(main_meta)} 条（分层报告可用）")

    # 验证集：review20_final（人工 20 条全通过批次）
    valid_path = DATA / "baiweixi_review20_final.jsonl"
    if not valid_path.exists():
        raise SystemExit(f"[error] 缺少验证集: {valid_path}")
    valid_rows = [r for r in load_sharegpt(valid_path) if not is_rerank_row(r)]
    print(f"验证数据 review20_final: {len(valid_rows)} 条")

    main_rows, excluded_main = exclude_eval_overlaps(main_rows, exclusion_hashes)
    valid_rows, excluded_valid = exclude_eval_overlaps(valid_rows, exclusion_hashes)
    print(f"CHAT-01 防泄漏排除: main={excluded_main} valid={excluded_valid}")

    # 切分：验证集固定 + 训练池按 9:1 留出
    train_pool = main_rows
    random.shuffle(train_pool)
    n_valid = max(1, len(valid_rows) // 2)
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
