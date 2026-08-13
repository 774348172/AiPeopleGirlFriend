"""阶段 3 C-5（DEITA 式选择）验收。

- 同 item 多候选：质量阈值过滤 + 分数选最高（**非首个合法**，G3 验收）；
- abstain 候选不参与自动选择；
- 无 judge → 退化"首个合法"（明确记录）；
- 数据集级 coverage/diversity（n-gram 覆盖贪心）。
"""
from __future__ import annotations

from data_gen_v4.core.judge import JudgeResult, ScriptedJudge
from data_gen_v4.core.selector import DatasetSelector, n_gram_coverage


def _candidate(sample_id: str, text: str, task_type: str = "reply_casual") -> dict:
    return {
        "sample_id": sample_id,
        "task_type": task_type,
        "input": {"scene": "客厅", "topic": "今天吃什么"},
        "target": {
            "messages": [
                {"role": "human", "content": "h"},
                {"role": "assistant", "content": text},
            ]
        },
    }


def _judge_scores(*values: float) -> dict:
    return {axis: values[0] for axis in ("instruction_fulfillment", "persona_naturalness",
                                         "relationship_fit", "conversational_progress",
                                         "style_restraint")}


def test_selector_picks_highest_score_not_first_valid():
    """两个候选都过阈值：选分数高的（非首个合法）。"""
    judge = ScriptedJudge(per_sample={
        "c1": JudgeResult(scores=_judge_scores(0.5)),
        "c2": JudgeResult(scores=_judge_scores(0.95)),
    })
    selector = DatasetSelector(judge)
    thresholds = {"soft_minimums": {"instruction_fulfillment": 0.4}}
    selection = selector.select_winner(
        [_candidate("c1", "一般"), _candidate("c2", "很好")], {}, thresholds
    )
    assert selection is not None
    assert selection.candidate["sample_id"] == "c2"  # 高分胜出，非首个
    assert selection.reason == "judge_selected"
    assert selection.scores["instruction_fulfillment"] == 0.95


def test_selector_filters_below_threshold():
    judge = ScriptedJudge(per_sample={
        "c1": JudgeResult(scores=_judge_scores(0.3)),
        "c2": JudgeResult(scores=_judge_scores(0.9)),
    })
    selector = DatasetSelector(judge)
    thresholds = {"soft_minimums": {"instruction_fulfillment": 0.5}}
    selection = selector.select_winner(
        [_candidate("c1", "差"), _candidate("c2", "好")], {}, thresholds
    )
    assert selection.candidate["sample_id"] == "c2"  # c1 被阈值淘汰


def test_selector_abstain_not_selected():
    judge = ScriptedJudge(per_sample={
        "c1": JudgeResult(abstain=True, abstain_reason="no_info"),
        "c2": JudgeResult(scores=_judge_scores(0.8)),
    })
    selector = DatasetSelector(judge)
    selection = selector.select_winner(
        [_candidate("c1", "?"), _candidate("c2", "好")], {}, {}
    )
    assert selection.candidate["sample_id"] == "c2"


def test_selector_all_abstain_returns_none():
    judge = ScriptedJudge(per_sample={
        "c1": JudgeResult(abstain=True, abstain_reason="x"),
    })
    selector = DatasetSelector(judge)
    assert selector.select_winner([_candidate("c1", "?")], {}, {}) is None


def test_selector_no_judge_falls_back_to_first_valid():
    selector = DatasetSelector()  # 无 judge
    selection = selector.select_winner(
        [_candidate("c1", "a"), _candidate("c2", "b")], {}, {}
    )
    assert selection.candidate["sample_id"] == "c1"
    assert selection.reason == "first_valid"


def test_selector_writes_quality_back_to_candidate():
    judge = ScriptedJudge(scores=_judge_scores(0.7))
    selector = DatasetSelector(judge)
    candidate = _candidate("c1", "好")
    selector.select_winner([candidate], {}, {})
    assert candidate["quality"]["instruction_fulfillment"] == 0.7


def test_n_gram_coverage_and_dataset_selection():
    # 覆盖贪心：文本差异大的并集更大
    assert n_gram_coverage(["aaaaaa", "bbbbbb"]) > n_gram_coverage(["aaaaaa", "aaaaaa"])
    judge = ScriptedJudge(scores=_judge_scores(0.8))
    selector = DatasetSelector(judge)
    items = [
        ("reply_casual", [_candidate("d1", "今天吃什么好呢", "reply_casual"),
                          _candidate("d2", "昨天那家面馆不错", "reply_casual")]),
        ("reply_romance", [_candidate("r1", "你想我了吗", "reply_romance")]),
    ]
    selected = selector.select_dataset(items, {}, per_item=1)
    # 每桶至少 1 个
    assert len(selected) == 2
    assert {s.candidate["task_type"] for s in selected} == {"reply_casual", "reply_romance"}
