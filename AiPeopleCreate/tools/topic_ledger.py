# -*- coding: utf-8 -*-
"""话题使用记账（2026-08-10 对话重复根因方案 1）。

记录"哪些池话题已生成过"，生成前避开已用话题，从源头防重复。

用法:
  python tools/topic_ledger.py --scan        # 扫描 训练数据/*.jsonl 生成已用话题表
  python tools/topic_ledger.py --unused      # 列出未用话题（供 --range-types 选段）
  python tools/topic_ledger.py --range-check reply_casual=66:80,reply_romance=10:26
                                             # 检查指定段是否含已用话题（冲突提示）

记账文件: 训练数据/_topic_ledger.jsonl（sample_id → topic → task_type）
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "训练数据"
LEDGER = DATA_DIR / "_topic_ledger.jsonl"


def scan() -> dict[str, set[str]]:
    """扫描 训练数据/ 下 baiweixi_*_final.jsonl（含子目录，递归）→ {task_type: set(topic)}。

    只扫白未晞批次（baiweixi_ 前缀）；秦历史批次（qin_v4_*）话题不属于
    白未晞池，混入会污染 ledger（2026-08-10 修复）。
    2026-08-13 目录重整理后主力批次移入 baiweixi_v1_legacy/，改为 rglob 递归。
    """
    used: dict[str, set[str]] = {}
    for jsonl in sorted(str(p) for p in DATA_DIR.rglob("baiweixi_*_final.jsonl")):
        meta_path = jsonl.replace(".jsonl", ".metadata.jsonl")
        if not Path(meta_path).exists():
            continue
        for line in open(meta_path, encoding="utf-8"):
            m = json.loads(line)
            task = m.get("task_type", "?")
            topic = m.get("topic", "")
            if topic:
                used.setdefault(task, set()).add(topic)
    return used


def write_ledger(used: dict[str, set[str]]) -> None:
    lines = [
        json.dumps({"task_type": task, "topic": topic}, ensure_ascii=False)
        for task, topics in used.items()
        for topic in sorted(topics)
    ]
    LEDGER.write_text("\n".join(lines) + "\n", encoding="utf-8")
    total = sum(len(v) for v in used.values())
    print(f"已用话题 {total} 个（{len(used)} 类）→ {LEDGER.name}")


def unused(used: dict[str, set[str]]) -> None:
    import yaml

    pools = yaml.safe_load(
        (ROOT / "profiles" / "baiweixi" / "pools.yaml").read_text(encoding="utf-8")
    )["pools"]
    print("=== 未用话题（按类型，供 --range-types 选段）===")
    for task, entries in pools.items():
        if task == "rerank_memory":
            continue
        all_topics = [e["topic"] for e in entries if "topic" in e]
        used_set = used.get(task, set())
        unused_set = [t for t in all_topics if t not in used_set]
        print(f"\n{task}: {len(all_topics)} 条池，已用 {len(used_set)}，未用 {len(unused_set)}")
        print("  " + " / ".join(unused_set[:30]) + ("…" if len(unused_set) > 30 else ""))


def range_check(used: dict[str, set[str]], ranges: str) -> int:
    import yaml

    pools = yaml.safe_load(
        (ROOT / "profiles" / "baiweixi" / "pools.yaml").read_text(encoding="utf-8")
    )["pools"]
    conflicts = 0
    for pair in ranges.split(","):
        task, span = pair.split("=")
        lo, hi = (int(x) for x in span.split(":"))
        entries = pools.get(task, [])
        used_set = used.get(task, set())
        for idx in range(lo, min(hi, len(entries))):
            topic = entries[idx].get("topic", "")
            if topic in used_set:
                conflicts += 1
                print(f"  [冲突] {task}[{idx}] {topic}（已生成过）")
    if conflicts == 0:
        print("✅ 指定段无话题冲突")
    else:
        print(f"⚠️ {conflicts} 个话题与已生成重复")
    return conflicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="扫描已生成话题并写 ledger")
    ap.add_argument("--unused", action="store_true", help="列出未用话题")
    ap.add_argument("--range-check", help="检查 --range-types 段是否含已用话题")
    args = ap.parse_args()

    used = scan()
    if args.scan:
        write_ledger(used)
        return 0
    if args.unused:
        unused(used)
        return 0
    if args.range_check:
        return 1 if range_check(used, args.range_check) else 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
