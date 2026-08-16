# -*- coding: utf-8 -*-
"""01_prepare_data.py — H200 版数据准备（LLaMA-Factory sharegpt 格式）

把 2500 条新正典 + 41 条称呼纠错合并，注入称呼澄清段（修复"浩然/秦未晞"混淆），
并做 CHAT-01 防泄漏过滤，输出 LF 可直接注册的 qin_v4_ready.jsonl。

用法: python 01_prepare_data.py
输出: data/qin_v4_ready.jsonl（2500 + 41 - 泄漏条）
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
EVAL_EXCLUSIONS = ROOT / "eval_exclusions"
OUT = DATA / "qin_v4_ready.jsonl"

# 防泄漏契约（T3 2026-08-06：chat01 + chat02 双契约，命中任一即排除）
EXCLUSION_CONTRACTS = [
    ("chat01_v1.json", "chat01-training-exclusions-v1"),
    ("chat02_v1.json", "chat02-training-exclusions-v1"),
]

# ─── 称呼配置（2026-08-05：可配置双槽位）───
# 秦对玩家有两个默认称呼：大名（心情好时主要叫）与小名（平常主要叫）。
# 这是"设定"不是"强制"——情境需要时她可以自由用其它称呼（连名带姓、喂、昵称等）。
# 以后改玩家设定只需改这两处。
PLAYER_FORMAL = "浩然"    # 大名（玩家本名，心情好的时候主要叫）
PLAYER_INFORMAL = "B哥"   # 小名（日常主要称呼）

# 称呼澄清段（2026-08-05 修复：训练验收发现模型把"浩然"（玩家名字）误认为角色名。
# 对所有样本的 system 段追加本段，明确名字归属——即使行内锚无标签也能纠正认知）
NAME_CLARIFICATION = (
    f"\n【称呼澄清】你的名字是秦未晞。玩家有两个默认称呼：大名是{PLAYER_FORMAL}、"
    f"小名是{PLAYER_INFORMAL}——你平常主要叫他小名\"{PLAYER_INFORMAL}\"，"
    f"心情好的时候主要叫他的大名\"{PLAYER_FORMAL}\"；你不是{PLAYER_FORMAL}，"
    f"{PLAYER_FORMAL}是他（情境需要时也可以自由用其它称呼）。他叫你\"秦老\"。"
)


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


def load_sharegpt(path: Path) -> list[dict]:
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    return [r for r in rows if isinstance(r.get("conversations"), list) and len(r["conversations"]) >= 2]


def main() -> None:
    exclusion_hashes = load_eval_exclusion_hashes()

    rows = load_sharegpt(DATA / "qin_v4_2530.jsonl")
    print(f"主数据 qin_v4_2530.jsonl: {len(rows)} 条")
    corr = load_sharegpt(DATA / "qin_corrections.jsonl")
    print(f"纠错数据 qin_corrections.jsonl: {len(corr)} 条")
    rows.extend(corr)

    # 称呼澄清段注入（每条 system 段后追加）
    for row in rows:
        for message in row["conversations"]:
            if message.get("from") == "system":
                message["value"] = message["value"] + NAME_CLARIFICATION
                break

    rows, excluded = exclude_eval_overlaps(rows, exclusion_hashes)
    print(f"CHAT-01 防泄漏排除: {excluded} 条")

    with OUT.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"→ {OUT} ({len(rows)} 条，LF 可直接注册)")


if __name__ == "__main__":
    main()
