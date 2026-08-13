"""Sealed suite 单向 contamination check（阶段 4 D-3）。

消费 freeze02 leakage_contract 的契约输入（单向）：
- eval_blocklist：normalized_text_sha256 哈希集（**训练侧只有哈希，无 eval 原文**）；
- evaluation_sources：文件路径 + sha256（仅校验 sealed 资产未被改动的合规检查，
  不读原文进训练）。

检查口径（freeze02 leakage_contract）：
- exact：utf8_text_equality（blocking）
- normalized：nfkc_lower_alnum_cjk（blocking）
- near_duplicate：character_trigram_jaccard ≥ 0.82，minimum_length_ratio 0.7（blocking）

failure_policy=reject_whole_sample_and_fail_dataset_freeze → blocked 时拒绝发布。
报告对齐 leakage_report schema：findings 只含 text_sha256 + preview（≤240 字符，
训练侧文本），不出现 eval 原文。
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ALNUM_KEEP = None  # normalized 口径用 unicodedata + alnum 过滤（与 leakage_guard 同式）


def normalize_nfkc_lower_alnum(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).lower() if ch.isalnum())


def text_sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _trigrams(text: str) -> set[str]:
    return {text[i : i + 3] for i in range(max(0, len(text) - 2))}


def trigram_jaccard(a: str, b: str) -> float:
    ga, gb = _trigrams(a), _trigrams(b)
    union = ga | gb
    if not union:
        return 0.0
    return len(ga & gb) / len(union)


@dataclass(frozen=True, slots=True)
class ContaminationContract:
    """freeze02 leakage_contract 的生成器侧消费视图。"""
    blocklist_hashes: frozenset[str] = frozenset()
    near_threshold: float = 0.82
    min_length_ratio: float = 0.7
    evaluation_sources: tuple[tuple[str, str], ...] = ()  # (path, sha256)


def load_blocklist(path: str | Path) -> frozenset[str]:
    """读取 eval_blocklist json（normalized_text_sha256 哈希集，单向）。

    递归收集任意嵌套结构中以 sha256: 开头的字符串（兼容
    excluded_texts/entries/items/每项 dict 等格式）。
    """
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    hashes: set[str] = set()

    def _collect(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith("sha256:"):
                hashes.add(node)
        elif isinstance(node, dict):
            for value in node.values():
                _collect(value)
        elif isinstance(node, list):
            for value in node:
                _collect(value)

    _collect(doc)
    return frozenset(hashes)


def verify_evaluation_sources(sources: tuple[tuple[str, str], ...]) -> list[str]:
    """sealed 资产合规校验：sha256 匹配（不读原文进训练）。"""
    mismatches = []
    for path, expected in sources:
        p = Path(path)
        if not p.exists():
            mismatches.append(f"missing:{path}")
            continue
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual != expected:
            mismatches.append(f"hash_mismatch:{path}")
    return mismatches


def check_contamination(
    training_texts: list[tuple[str, str]],
    contract: ContaminationContract,
    *,
    preview_limit: int = 240,
) -> dict[str, Any]:
    """对 (sample_id, text) 训练样本做单向污染检查。

    返回报告：{findings, summary, status}——status=passed|blocked（blocked =
    命中任一 blocking 算法；按 failure_policy 拒绝发布）。报告不含 eval 原文。
    """
    findings: list[dict[str, Any]] = []
    normalized_index: dict[str, list[int]] = {}
    for i, (sample_id, text) in enumerate(training_texts):
        norm = normalize_nfkc_lower_alnum(text)
        h = "sha256:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()
        if h in contract.blocklist_hashes:
            findings.append(
                {
                    "finding_id": f"f{len(findings)}",
                    "kind": "normalized",
                    "similarity": 1.0,
                    "evaluation": {"text_sha256": h},
                    "training": {"sample_id": sample_id, "text_sha256": text_sha256(text)},
                }
            )
        normalized_index.setdefault(h, []).append(i)
    # near_duplicate：与 blocklist 哈希无法直接比对原文——近重复检查对象是
    # 训练内部 + blocklist 只提供哈希（单向）→ near 检查只针对训练内部去重？
    # 不：leakage 的近重复是训练文本 vs eval 原文，但生成器侧只有 eval 哈希。
    # 单向性下 near 检查无法执行（无 eval 原文）——契约的 near 检查由
    # AiPeople 侧 leakage_guard 在 eval 侧执行（它有双方原文）。
    # 生成器侧单向检查 = exact/normalized 哈希比对 + 报告说明 near 由 eval 侧兜底。
    summary = {
        "training_samples": len(training_texts),
        "blocklist_hashes": len(contract.blocklist_hashes),
        "findings": len(findings),
        "near_duplicate_check": "delegated_to_eval_side",
    }
    status = "blocked" if findings else "passed"
    return {"findings": findings, "summary": summary, "status": status}
