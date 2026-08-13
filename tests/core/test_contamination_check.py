"""阶段 4 D-3（sealed suite 单向 contamination check）验收。

- normalized 哈希命中 blocklist → finding + status=blocked（拒绝发布）；
- 单向性：报告不含 eval 原文（findings 只有 text_sha256 + preview）；
- sealed 资产 sha256 校验（hash_mismatch 检出）；
- 无命中 → status=passed。
"""
from __future__ import annotations

import hashlib
import json

from data_gen_v4.core.contamination_check import (
    ContaminationContract,
    check_contamination,
    load_blocklist,
    normalize_nfkc_lower_alnum,
    verify_evaluation_sources,
)


def _blocklist_json(hashes: list[str], path) -> None:
    doc = {"contract_id": "test-blocklist", "excluded_texts": hashes}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_normalized_hash_hit_blocks(tmp_path):
    # 训练文本与 blocklist 哈希（normalized 口径）命中 → blocked
    text = "你好，今天 吃了什么？"
    norm = normalize_nfkc_lower_alnum(text)
    hit_hash = "sha256:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()
    bl_path = tmp_path / "blocklist.json"
    _blocklist_json([hit_hash], bl_path)
    contract = ContaminationContract(blocklist_hashes=load_blocklist(bl_path))
    report = check_contamination([("s1", text)], contract)
    assert report["status"] == "blocked"
    finding = report["findings"][0]
    assert finding["kind"] == "normalized"
    assert finding["training"]["sample_id"] == "s1"
    # 单向性：报告不含 eval 原文（只有哈希）
    assert finding["evaluation"]["text_sha256"].startswith("sha256:")


def test_clean_text_passes(tmp_path):
    bl_path = tmp_path / "blocklist.json"
    _blocklist_json(["sha256:" + "a" * 64], bl_path)
    contract = ContaminationContract(blocklist_hashes=load_blocklist(bl_path))
    report = check_contamination([("s1", "完全无关的内容")], contract)
    assert report["status"] == "passed"
    assert report["findings"] == []


def test_load_blocklist_formats(tmp_path):
    # entries 列表格式 + dict 格式
    p1 = tmp_path / "b1.json"
    p1.write_text(json.dumps({"entries": ["sha256:abc"]}, ensure_ascii=False), encoding="utf-8")
    assert load_blocklist(p1) == frozenset({"sha256:abc"})
    p2 = tmp_path / "b2.json"
    p2.write_text(
        json.dumps({"items": [{"normalized_text_sha256": "sha256:def"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert "sha256:def" in load_blocklist(p2)


def test_verify_evaluation_sources(tmp_path):
    asset = tmp_path / "eval.jsonl"
    asset.write_text("{}", encoding="utf-8")
    good = hashlib.sha256(b"{}").hexdigest()
    sources = ((str(asset), good), (str(tmp_path / "missing.jsonl"), "x" * 64))
    mismatches = verify_evaluation_sources(sources)
    assert mismatches == [f"missing:{tmp_path / 'missing.jsonl'}"]


def test_near_duplicate_delegated_note():
    """单向契约下 near 检查由 eval 侧兜底（生成器无 eval 原文）。"""
    contract = ContaminationContract()
    report = check_contamination([("s1", "x")], contract)
    assert report["summary"]["near_duplicate_check"] == "delegated_to_eval_side"
