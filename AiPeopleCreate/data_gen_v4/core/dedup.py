"""三级去重（阶段 4 D-1，设计 §15.1）。

1. normalized exact hash：NFKC + lower + 只保留 alnum（与 AiPeople leakage_guard /
   tools/build_chat02_blocklist 同式）→ sha256；
2. n-gram MinHash：character n-gram（curation_policy.ngram_range）+ MinHash
   （num_perm，阈值 policy 驱动）→ 候选对 Jaccard 相似度 → 并查集聚类；
3. embedding 近邻：接口预留——生产 policy allowed_dedup_modes=[exact, minhash]
   本就不含 embedding；embedding_model_ref 配置时阻断并提示通道未实现。

产出 dedup cluster report：{clusters: [{cluster_id, members: [{sample_id, text_hash}],
size, max_similarity, mode}], summary: {total, clusters, exact, minhash, embedding}}。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

_ALNUM_KEEP = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]")


def normalize_for_hash(text: str) -> str:
    """NFKC + lower + 仅保留字母数字与 CJK（exact/normalized 级统一口径）。"""
    return _ALNUM_KEEP.sub("", unicodedata.normalize("NFKC", text).lower())


def exact_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()


def n_grams(text: str, n: int = 3) -> set[str]:
    text = str(text)
    return {text[i : i + n] for i in range(max(0, len(text) - n + 1))}


def _perm_hash(token: str, perm: int) -> int:
    """MinHash 置换哈希：token 与置换号的确定性混合（zlib.crc32 双混合）。"""
    import zlib

    return zlib.crc32(f"{perm}:{token}".encode("utf-8")) & 0xFFFFFFFF


def minhash_signature(text: str, *, num_perm: int = 128, n: int = 3) -> list[int]:
    grams = n_grams(text, n)
    if not grams:
        return [0] * num_perm
    sig: list[int] = []
    for perm in range(num_perm):
        sig.append(min(_perm_hash(g, perm) for g in grams))
    return sig


def minhash_jaccard(a: list[int], b: list[int]) -> float:
    if not a or not b:
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


@dataclass(frozen=True, slots=True)
class DedupConfig:
    ngram_range: tuple[int, int] = (3, 5)
    num_perm: int = 128
    minhash_threshold: float = 0.9
    allowed_modes: tuple[str, ...] = ("exact", "minhash")
    embedding_model_ref: str | None = None


def dedup_report(
    samples: list[tuple[str, str]],
    config: DedupConfig | None = None,
) -> dict[str, Any]:
    """对 (sample_id, text) 列表做三级去重，产出 cluster report。

    embedding 模式：allowed_modes 含 embedding 或 embedding_model_ref 配置时
    阻断（通道未实现，诚实失败）；缺省 [exact, minhash] 正常。
    """
    config = config or DedupConfig()
    if config.embedding_model_ref or "embedding" in config.allowed_modes:
        raise NotImplementedError(
            "embedding 级去重通道未实现（BGE 资产未落地）；"
            "生产 policy allowed_dedup_modes=[exact, minhash] 不启用 embedding"
        )
    n = config.ngram_range[0] if config.ngram_range else 3
    hashes: dict[str, list[int]] = {}  # exact_hash -> sample indexes
    sigs: list[list[int]] = []
    exact_pairs: list[tuple[int, int]] = []
    for i, (sample_id, text) in enumerate(samples):
        h = exact_hash(text)
        hashes.setdefault(h, []).append(i)
        sigs.append(minhash_signature(text, num_perm=config.num_perm, n=n))
    for bucket in hashes.values():
        for j in range(1, len(bucket)):
            exact_pairs.append((bucket[0], bucket[j]))

    uf = _UnionFind(len(samples))
    mode: dict[tuple[int, int], str] = {}
    for a, b in exact_pairs:
        uf.union(a, b)
        mode[(a, b)] = "exact"
    # MinHash 候选：仅对尚未同簇的样本对计算（O(n²) 上限，样本量大时由
    # 阶段 4 的 MinHash LSH 优化——本轮数据集级报告可接受）
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            if uf.find(i) == uf.find(j):
                continue
            sim = minhash_jaccard(sigs[i], sigs[j])
            if sim >= config.minhash_threshold:
                uf.union(i, j)
                mode[(i, j)] = "minhash"

    clusters: dict[int, list[int]] = {}
    for i in range(len(samples)):
        clusters.setdefault(uf.find(i), []).append(i)
    report_clusters = []
    for root, members in clusters.items():
        if len(members) < 2:
            continue
        member_rows = []
        for idx in members:
            member_rows.append(
                {
                    "sample_id": samples[idx][0],
                    "text_hash": exact_hash(samples[idx][1]),
                }
            )
        report_clusters.append(
            {
                "cluster_id": f"dup:{root}",
                "members": member_rows,
                "size": len(members),
                "mode": next((m for (a, b), m in mode.items() if a in members and b in members), "exact"),
            }
        )
    return {
        "clusters": report_clusters,
        "summary": {
            "total": len(samples),
            "clusters": len(report_clusters),
            "duplicated_samples": sum(c["size"] for c in report_clusters),
            "modes": sorted({c["mode"] for c in report_clusters}),
        },
    }
