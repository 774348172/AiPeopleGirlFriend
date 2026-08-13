# -*- coding: utf-8 -*-
"""T3：生成 chat02 防泄漏 blocklist（与 chat01_v1.json 同契约格式）。

数据源（2026-08-06 chat02e 盲评包）：
- F:\AiPeople\eval/chat02/human/chat02e-v1/public/ab_packets.jsonl      → 每条 unit 的 prompt（user 台词）
- F:\AiPeople\eval/chat02/human/chat02e-v1/public/long_session_briefs.jsonl → 每条会话的 opening（开场指令）

输出：training_package_m3_v2/eval_exclusions/chat02_v1.json（与 m3_v2/h200 包各一份；目标路径在 AiPeople 侧，若不在当前仓库则跳过）。

用途：01_prepare_data.py 在打包训练数据时同时读取 chat01 + chat02 两个契约，
命中任一即排除（诊断 §10.1：40 条盲评题永久禁止进入训练和开发集）。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# 评测文本在 AI 程序侧（F:\AiPeople），只读引用（2026-08-07 目录整理）
ROOT = Path(__file__).resolve().parents[1]
AI_PEOPLE_ROOT = Path(r"F:\AiPeople")
AB_PACKETS = AI_PEOPLE_ROOT / "eval/chat02/human/chat02e-v1/public/ab_packets.jsonl"
LONG_SESSIONS = AI_PEOPLE_ROOT / "eval/chat02/human/chat02e-v1/public/long_session_briefs.jsonl"
CONTRACT_ID = "chat02-training-exclusions-v1"
TARGETS = [
    AI_PEOPLE_ROOT / "training_package_m3_v2/eval_exclusions/chat02_v1.json",
    AI_PEOPLE_ROOT / "training_package_qinweixi_h200/eval_exclusions/chat02_v1.json",
]


def normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(c for c in normalized if c.isalnum())


def extract_user_texts() -> list[tuple[str, str]]:
    """返回 [(来源标识, 原文)]。"""
    items: list[tuple[str, str]] = []
    for line in AB_PACKETS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        unit = json.loads(line)
        unit_id = unit.get("unit_id", "?")
        for message in unit.get("prompt", []):
            if message.get("role") == "user" and message.get("content"):
                items.append((f"{unit_id}:user", message["content"]))
    for line in LONG_SESSIONS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        session = json.loads(line)
        opening = session.get("opening", "")
        if opening:
            items.append((f"{session.get('session_id', '?')}:opening", opening))
    return items


def build() -> int:
    items = extract_user_texts()
    hashes = sorted({hashlib.sha256(normalize(text).encode("utf-8")).hexdigest() for _, text in items})
    payload = {
        "contract_id": CONTRACT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "chat02e-v1 (ab_packets + long_session_briefs)",
        "normalized_text_sha256": hashes,
    }
    for target in TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"→ {target}（{len(hashes)} 个文本哈希）")

    # 与当前 2500 训练数据做重叠自检（应为 0）
    data = ROOT / "训练数据/qin_v4_2500.jsonl"
    hits = 0
    if data.exists():
        for line in data.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            for message in row.get("conversations", []):
                if message.get("from") == "human":
                    h = hashlib.sha256(normalize(message["value"]).encode("utf-8")).hexdigest()
                    if h in set(hashes):
                        hits += 1
                        print(f"  [overlap] {message['value'][:60]}")
    print(f"与 2500 训练数据重叠: {hits} 条")
    return 0 if hits == 0 else 1


if __name__ == "__main__":
    sys.exit(build())
