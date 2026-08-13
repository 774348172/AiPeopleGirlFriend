"""DEITA 式选择器（阶段 3 C-5：先质量阈值 → 再 coverage/diversity）。

- select_winner(candidates, ...)：同 item 的 K 候选 → 质量阈值过滤（judge soft 分
  低于下限淘汰；abstain 标记 human_review）→ 选最高分候选（**非首个合法**）；
  judge 未配置 → 退化"首个合法"（明确记录 reason=first_valid）。
- n_gram_coverage / select_dataset：数据集级 coverage/diversity 组件（character
  n-gram 覆盖贪心，按 task_type 分桶）——阶段 4 切分与消融 D 组复用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

N_GRAM = 3


def n_grams(text: str, n: int = N_GRAM) -> set[str]:
    text = str(text)
    return {text[i : i + n] for i in range(max(0, len(text) - n + 1))}


def n_gram_coverage(texts: list[str]) -> float:
    """已有文本集合的 n-gram 并集大小（diversity 度量）。"""
    covered: set[str] = set()
    for text in texts:
        covered |= n_grams(text)
    return float(len(covered))


@dataclass(frozen=True, slots=True)
class Selection:
    candidate: dict[str, Any]
    reason: str  # judge_selected / first_valid / human_review
    scores: dict[str, float] = field(default_factory=dict)
    abstain_reason: str = ""


class DatasetSelector:
    """先质量阈值 → 再分数/覆盖选择（DEITA 式；judge 未配置退化首个合法）。"""

    def __init__(self, judge: Any | None = None) -> None:
        self._judge = judge

    def select_winner(
        self,
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
        thresholds: dict[str, Any] | None = None,
    ) -> Selection | None:
        """同 item 候选 → 1 个 winner（None = 全部不达标）。"""
        thresholds = thresholds or {}
        soft_min = thresholds.get("soft_minimums") or {}
        if self._judge is None:
            # 无 judge：退化"首个合法"（gate 已过滤；明确记录）
            return (
                Selection(candidate=candidates[0], reason="first_valid")
                if candidates
                else None
            )
        best: Selection | None = None
        for candidate in candidates:
            result = self._judge.score(candidate, context)
            candidate["quality"] = dict(result.scores)  # 写回候选（record 落盘）
            if result.abstain:
                # abstain 候选标记待人工，不参与自动选择
                continue
            if any(
                result.scores.get(axis, 0.0) < min_value
                for axis, min_value in soft_min.items()
            ):
                continue  # 低于质量阈值淘汰
            total = sum(result.scores.values())
            if best is None or total > sum(best.scores.values()):
                best = Selection(
                    candidate=candidate,
                    reason="judge_selected",
                    scores=dict(result.scores),
                )
        return best

    def select_dataset(
        self,
        items: list[tuple[str, list[dict[str, Any]]]],
        context: dict[str, Any],
        thresholds: dict[str, Any] | None = None,
        *,
        per_item: int = 1,
    ) -> list[Selection]:
        """数据集级选择：每 item 选 per_item 个（质量阈值后按 task_type 桶做
        n-gram 覆盖贪心，桶内覆盖新增 n-gram 最多的优先）。

        items = [(task_type, [候选...]), ...]
        """
        selected: list[Selection] = []
        by_bucket: dict[str, list[Selection]] = {}
        for task_type, candidates in items:
            winner = self.select_winner(candidates, context, thresholds)
            if winner is not None:
                by_bucket.setdefault(task_type, []).append(winner)
        for task_type, winners in by_bucket.items():
            picked: list[Selection] = []
            covered: set[str] = set()
            remaining = list(winners)
            while remaining and len(picked) < max(1, per_item * len(winners)):
                # 贪心：选覆盖新增 n-gram 最多的
                best_idx = max(
                    range(len(remaining)),
                    key=lambda i: len(
                        n_grams(_text_of(remaining[i])) - covered
                    ),
                )
                picked.append(remaining.pop(best_idx))
                covered |= n_grams(_text_of(picked[-1]))
            selected.extend(picked)
        return selected


def _text_of(selection: Selection) -> str:
    messages = (selection.candidate.get("target") or {}).get("messages", [])
    return " ".join(str(m.get("content", "")) for m in messages)
