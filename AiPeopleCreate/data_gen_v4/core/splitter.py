"""整组切分（阶段 4 D-2，设计 §15.2）。

- 输入：样本 [{sample_id, split_anchor_ids, text, task_type, ...}] + split_policy；
- 连通分量：同锚样本同分量（锚图并查集）→ 整组分配 train/dev/test；
- max_component_ratio 超限 → 阻断（SplitBlockedError）；
- 跨 split 近重复检查：character_trigram_jaccard ≥ threshold（freeze02
  split_contract 口径 0.9，blocking）→ G4：跨 split 近重复 = 0，违规即阻断。

分配确定性：分量按 (size desc, 最小 sample_id) 稳定排序，贪心放入当前
ratio 缺口最大的 split——同 seed/同输入 partition 恒定（消融复用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .dedup import n_grams


class SplitBlockedError(Exception):
    """切分被阻断：最大分量超限或跨 split 近重复。"""


@dataclass(frozen=True, slots=True)
class SplitPolicy:
    initial_ratios: tuple[float, float, float] = (0.8, 0.1, 0.1)
    max_component_ratio: float = 0.3
    cross_split_threshold: float = 0.9
    blocking: bool = True


@dataclass(frozen=True, slots=True)
class SplitResult:
    assignments: dict[str, str]  # sample_id -> split
    components: dict[str, list[str]]  # component_id -> sample_ids
    max_component_ratio: float
    split_counts: dict[str, int]
    cross_split_near_duplicates: list[tuple[str, str, float]] = field(default_factory=list)


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


def split_dataset(
    samples: list[dict[str, Any]],
    policy: SplitPolicy | None = None,
    *,
    seed: int = 42,
) -> SplitResult:
    """按锚连通分量整组切分（train/dev/test）。"""
    policy = policy or SplitPolicy()
    if not samples:
        return SplitResult(
            assignments={}, components={}, max_component_ratio=0.0,
            split_counts={"train": 0, "dev": 0, "test": 0},
        )
    # 1) 锚图并查集：同锚样本同分量
    anchor_to_index: dict[str, int] = {}
    uf = _UnionFind(len(samples))
    for i, sample in enumerate(samples):
        for anchor in sample.get("split_anchor_ids") or [f"family:{sample.get('family_id')}"]:
            if anchor in anchor_to_index:
                uf.union(i, anchor_to_index[anchor])
            else:
                anchor_to_index[anchor] = i
    # 2) 分量
    components: dict[int, list[int]] = {}
    for i in range(len(samples)):
        components.setdefault(uf.find(i), []).append(i)
    # 3) 分量排序（size desc, 最小 sample_id）→ 贪心分配
    component_rows = []
    for root, indexes in components.items():
        member_ids = sorted(samples[i]["sample_id"] for i in indexes)
        component_rows.append((root, indexes, member_ids))
    component_rows.sort(key=lambda row: (-len(row[1]), row[2][0]))
    ratios = policy.initial_ratios
    total = len(samples)
    counts = {"train": 0, "dev": 0, "test": 0}
    assignments: dict[str, str] = {}
    for root, indexes, member_ids in component_rows:
        size = len(indexes)
        if size / total > policy.max_component_ratio:
            raise SplitBlockedError(
                f"最大分量 {size}/{total} 超限 {policy.max_component_ratio}"
            )
        # 放入 ratio 缺口最大的 split（确定性：同值取固定顺序）
        target = min(
            ("train", "dev", "test"),
            key=lambda s: (counts[s] / total - ratios[("train", "dev", "test").index(s)],
                           ("train", "dev", "test").index(s)),
        )
        counts[target] += size
        for i in indexes:
            assignments[samples[i]["sample_id"]] = target
    # 4) 跨 split 近重复检查（trigram_jaccard ≥ threshold，blocking）
    near_dups: list[tuple[str, str, float]] = []
    grams = {s["sample_id"]: n_grams(s.get("text", "")) for s in samples}
    ids = list(assignments)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if assignments[a] == assignments[b]:
                continue
            union = grams[a] | grams[b]
            if not union:
                continue
            sim = len(grams[a] & grams[b]) / len(union)
            if sim >= policy.cross_split_threshold:
                near_dups.append((a, b, round(sim, 4)))
    if near_dups and policy.blocking:
        raise SplitBlockedError(f"跨 split 近重复 {len(near_dups)} 对（blocking）: {near_dups[:3]}")
    return SplitResult(
        assignments=assignments,
        components={f"comp:{root}": member_ids for root, _, member_ids in component_rows},
        max_component_ratio=max(len(v) for v in components.values()) / total,
        split_counts=counts,
        cross_split_near_duplicates=near_dups,
    )
