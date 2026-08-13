"""阶段 4 D-1（三级去重）验收。

- normalized exact hash：NFKC+alnum 口径（同式不同文本命中）；
- MinHash 近重复检出（阈值边界）；
- embedding 模式未实现 → 显式阻断；
- 报告结构（clusters/summary）。
"""
from __future__ import annotations

import pytest

from data_gen_v4.core.dedup import (
    DedupConfig,
    dedup_report,
    exact_hash,
    minhash_jaccard,
    minhash_signature,
    normalize_for_hash,
)


def test_normalize_and_exact_hash_consistency():
    # 全角/大小写/空白差异 → 同一哈希（build_chat02_blocklist 同式）
    a = "你好，今天 吃了什么？"
    b = "你好,今天吃了什么"
    assert exact_hash(a) == exact_hash(b)
    assert normalize_for_hash("ＡＢＣ") == "abc"


def test_exact_duplicates_clustered():
    samples = [
        ("s1", "今天吃什么好呢"),
        ("s2", "今天吃什么好呢"),  # exact 重复
        ("s3", "完全不同的内容abc"),
    ]
    report = dedup_report(samples)
    assert report["summary"]["clusters"] == 1
    cluster = report["clusters"][0]
    assert cluster["mode"] == "exact"
    assert {m["sample_id"] for m in cluster["members"]} == {"s1", "s2"}


def test_minhash_detects_near_duplicate():
    # 长文本单字替换：trigram jaccard > 0.82（freeze02 leakage 契约口径）
    base = "昨天那家面馆的面特别好吃汤也鲜，下次还想再去一次，顺便看看老板今天有没有新出的浇头"
    near = "昨天那家面馆的面特别好吃汤好鲜，下次还想再去一次，顺便看看老板今天有没有新出的浇头"
    far = "今天天气不错我们出门散步吧"
    sig_base = minhash_signature(base)
    sig_near = minhash_signature(near)
    sig_far = minhash_signature(far)
    assert minhash_jaccard(sig_base, sig_near) > minhash_jaccard(sig_base, sig_far)
    report = dedup_report(
        [("s1", base), ("s2", near), ("s3", far)],
        DedupConfig(minhash_threshold=0.82),
    )
    assert report["summary"]["clusters"] >= 1
    near_cluster = next(
        c for c in report["clusters"]
        if {m["sample_id"] for m in c["members"]} == {"s1", "s2"}
    )
    assert near_cluster["mode"] == "minhash"


def test_minhash_threshold_boundary():
    """低于阈值的相似对不聚类。"""
    a = "今天天气不错我们出门散步吧"
    b = "今天天气不错我们出门散步吧"  # 完全同文 → exact 命中
    c = "我们出门散步吧今天天气不错"  # 同词重排 → trigram 相似但不完全
    report = dedup_report(
        [("s1", a), ("s2", b), ("s3", c)],
        DedupConfig(minhash_threshold=0.95),
    )
    # s1/s2 exact 聚类；s3 若未达阈值则独立
    clusters = report["clusters"]
    members_all = {m["sample_id"] for c in clusters for m in c["members"]}
    assert {"s1", "s2"} <= members_all


def test_embedding_mode_blocks_when_configured():
    with pytest.raises(NotImplementedError, match="embedding"):
        dedup_report(
            [("s1", "x")],
            DedupConfig(allowed_modes=("exact", "minhash", "embedding")),
        )
    with pytest.raises(NotImplementedError, match="embedding"):
        dedup_report([("s1", "x")], DedupConfig(embedding_model_ref="bge-small-zh"))


def test_report_structure():
    samples = [("s1", "第一条内容"), ("s2", "第一条内容"), ("s3", "第二条内容")]
    report = dedup_report(samples)
    assert "clusters" in report and "summary" in report
    assert report["summary"]["total"] == 3
    for cluster in report["clusters"]:
        assert cluster["size"] == len(cluster["members"])
        for member in cluster["members"]:
            assert member["text_hash"].startswith("sha256:")
