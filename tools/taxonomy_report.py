# -*- coding: utf-8 -*-
"""错误回流统计（第一批改造，2026-08-07）：taxonomy 飞轮第一步。

输入：sqlite（v4_records 的 failure 记录）+ gate_report.jsonl（导出侧拒绝）
输出：设计文档/错误taxonomy统计/<run>.md —— 错误分布报告

用法: python tools/taxonomy_report.py <sqlite路径> [gate_report路径]
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "设计文档" / "错误taxonomy统计"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 错误类别 → 关键词映射（reason 半结构化标签 → taxonomy 大类）
TAXONOMY_RULES = [
    ("E1 事实编造", ("无正典支撑", "无支撑", "共同经历无支撑", "禁表述", "invent")),
    ("E2 朗读", ("身份声明", "属性朗读", "recite", "朗读")),
    ("E3 人设过拟合", ("数学",)),
    ("E4 安全", ("safety", "安全动作", "attitude")),
    ("E5 视角", ("玩家台词", "秘密词", "人类台词", "视角")),
    ("E6 格式", ("parse", "解析", "结构", "轮数", "未交替", "schema")),
    ("E7 语义保持", ("语义保持", "缺失命题", "semantic")),
]


def classify(reason: str) -> str:
    for label, keywords in TAXONOMY_RULES:
        if any(k in reason for k in keywords):
            return label
    return "E9 其他"


def extract_tag(reason: str) -> str:
    """提取 reason 中的结构化标签（[] 内或冒号后），如 '身份声明[我叫秦未晞]' → '我叫秦未晞'。"""
    m = re.search(r"\[([^\]]+)\]", reason)
    if m:
        return m.group(1)
    m = re.search(r"(?:secret_leak|safety_action_missing|safety_action_forbidden|safety_attitude_forbidden):([^\s\]]+)", reason)
    if m:
        return m.group(1)
    return ""


def analyze_sqlite(db_path: Path) -> dict:
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT payload_json FROM v4_records WHERE record_type='failure'"
    ).fetchall()
    failures = [json.loads(r[0]) for r in rows]
    con.close()
    return analyze_failures(failures)


def analyze_failures(failures: list[dict]) -> dict:
    by_code = Counter(f.get("error_code", "?") for f in failures)
    by_taxonomy = Counter()
    by_tag = Counter()
    samples: dict[str, list[str]] = {}
    for f in failures:
        reason = str(f.get("reason", ""))
        tax = classify(reason)
        by_taxonomy[tax] += 1
        tag = extract_tag(reason)
        if tag:
            by_tag[f"{tax}|{tag}"] += 1
        samples.setdefault(tax, []).append(reason[:120])
    return {
        "total": len(failures),
        "by_code": dict(by_code.most_common()),
        "by_taxonomy": dict(by_taxonomy.most_common()),
        "by_tag": dict(by_tag.most_common(20)),
        "samples": {k: v[:3] for k, v in samples.items()},
    }


def analyze_gate_report(gate_path: Path) -> dict:
    rows = [json.loads(l) for l in open(gate_path, encoding="utf-8")]
    by_gate: Counter = Counter()
    reasons: Counter = Counter()
    for r in rows:
        for d in r.get("decisions", []):
            if d["decision"] != "approved":
                by_gate[d["gate_id"]] += 1
                for code in d.get("reason_codes", []):
                    reasons[code] += 1
    return {
        "total_candidates": len(rows),
        "by_gate": dict(by_gate.most_common()),
        "top_reasons": dict(reasons.most_common(20)),
    }


def render_md(name: str, sqlite_stats: dict, gate_stats: dict | None) -> str:
    lines = [
        f"# 错误 taxonomy 统计：{name}",
        "",
        f"> 生成时间：{datetime.now().isoformat(timespec='minutes')}",
        f"> 失败记录：{sqlite_stats['total']} 条",
        "",
        "## 一、按错误码（error_code）",
        "",
        "| error_code | 次数 |",
        "|---|---|",
    ]
    for code, n in sqlite_stats["by_code"].items():
        lines.append(f"| {code} | {n} |")
    lines += ["", "## 二、按 taxonomy 大类", "", "| 类别 | 次数 |", "|---|---|"]
    for tax, n in sqlite_stats["by_taxonomy"].items():
        lines.append(f"| {tax} | {n} |")
    lines += ["", "## 三、高频具体错误（tag）", "", "| 错误标签 | 次数 |", "|---|---|"]
    for tag, n in sqlite_stats["by_tag"].items():
        lines.append(f"| {tag} | {n} |")
    lines += ["", "## 四、各类别示例", ""]
    for tax, exs in sqlite_stats["samples"].items():
        lines.append(f"### {tax}")
        for e in exs:
            lines.append(f"- {e}")
    if gate_stats:
        lines += ["", "## 五、导出侧门禁拒绝（gate_report）", "",
                  "| gate | 拒绝数 |", "|---|---|"]
        for g, n in gate_stats["by_gate"].items():
            lines.append(f"| {g} | {n} |")
        lines += ["", "| reason_code | 次数 |", "|---|---|"]
        for rc, n in gate_stats["top_reasons"].items():
            lines.append(f"| {rc} | {n} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python tools/taxonomy_report.py <sqlite路径> [gate_report路径]")
        sys.exit(1)
    db = Path(sys.argv[1])
    gate = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    sqlite_stats = analyze_sqlite(db)
    gate_stats = analyze_gate_report(gate) if gate and gate.exists() else None
    name = db.stem
    md = render_md(name, sqlite_stats, gate_stats)
    out = OUT_DIR / f"{name}_taxonomy.md"
    out.write_text(md, encoding="utf-8")
    print(f"→ {out}")
    print(f"失败总数: {sqlite_stats['total']}")
    for tax, n in sqlite_stats["by_taxonomy"].items():
        print(f"  {tax}: {n}")


if __name__ == "__main__":
    main()
