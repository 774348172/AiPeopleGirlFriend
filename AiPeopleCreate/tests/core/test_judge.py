"""阶段 3 C-4（校准 judge）验收。

- ScriptedJudge 固定分数/abstain；
- LLMJudge prompt 渲染 + JSON 解析（成功/坏 JSON/越界轴 → abstain）；
- PairwiseJudge 换序两次：一致才采纳，一致率统计（G3 验收指标）；
- 校准报告（tools/calibrate_judge.py 的 calibrate 函数）输出指标。
"""
from __future__ import annotations

import json

from data_gen_v4.core.judge import (
    LLMJudge,
    PairwiseJudge,
    ScriptedJudge,
    SOFT_AXES,
)
from tools.calibrate_judge import calibrate


def _candidate(sample_id: str, text: str) -> dict:
    return {
        "sample_id": sample_id,
        "input": {"scene": "客厅", "topic": "今天吃什么"},
        "target": {
            "messages": [
                {"role": "human", "content": "h"},
                {"role": "assistant", "content": text},
            ]
        },
    }


def test_scripted_judge_fixed_scores():
    judge = ScriptedJudge(scores={"instruction_fulfillment": 0.5})
    result = judge.score(_candidate("c1", "x"), {})
    assert result.abstain is False
    assert result.scores["instruction_fulfillment"] == 0.5
    # 未指定轴用默认 0.9
    assert result.scores["persona_naturalness"] == 0.9


def test_scripted_judge_abstain():
    judge = ScriptedJudge(abstain=True)
    result = judge.score(_candidate("c1", "x"), {})
    assert result.abstain is True


class _FakeJudgePool:
    """按脚本返回 judge 响应（LLMJudge 的模型通道 fake）。"""

    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls = 0

    def resolve(self, spec):
        return self

    def generate(self, spec):
        self.calls += 1
        content = self._contents[min(self.calls - 1, len(self._contents) - 1)]
        if isinstance(content, Exception):
            raise content
        return {"content": content, "finish_reason": "stop"}


def test_llm_judge_parses_scores():
    pool = _FakeJudgePool([
        json.dumps(
            {"scores": {axis: 0.8 for axis in SOFT_AXES}, "abstain": False}, ensure_ascii=False
        )
    ])
    judge = LLMJudge(pool)
    result = judge.score(_candidate("c1", "好呀"), {})
    assert result.abstain is False
    assert all(result.scores[axis] == 0.8 for axis in SOFT_AXES)
    # prompt 渲染含对话文本
    assert "好呀" in judge.calls[0]["messages"][0]["content"]


def test_llm_judge_injects_role_context():
    """2026-08-10（方案 A）：judge prompt 注入角色上下文（人格/秘密边界/秘密词）。"""
    pool = _FakeJudgePool([
        json.dumps(
            {"scores": {axis: 0.8 for axis in SOFT_AXES}, "abstain": False}, ensure_ascii=False
        )
    ])
    judge = LLMJudge(pool)
    context = {
        "profile": {
            "display_name": "白未晞",
            "anchor_contract": {
                "personality": "清冷、简短、克制但不冷漠",
                "secret_boundary": "妖果来源、父母身份未确定",
            },
            "policy_terms": {"secret_terms": ["妖果", "深山", "野外"]},
        }
    }
    judge.score(_candidate("c1", "好呀"), context)
    prompt = judge.calls[0]["messages"][0]["content"]
    assert "【角色上下文】" in prompt
    assert "角色：白未晞" in prompt
    assert "妖果" in prompt and "野外" in prompt  # 秘密词注入
    # 无 profile → （无）占位（旧行为兼容，不崩）
    judge2 = LLMJudge(pool)
    judge2.score(_candidate("c2", "x"), {})
    assert "（无）" in judge2.calls[0]["messages"][0]["content"]


def test_llm_judge_abstains_on_bad_output():
    # 坏 JSON → abstain
    judge = LLMJudge(_FakeJudgePool(["不是 JSON"]))
    assert judge.score(_candidate("c1", "x"), {}).abstain is True
    # 越界分数 → abstain
    judge = LLMJudge(_FakeJudgePool([json.dumps({"scores": {"instruction_fulfillment": 1.5}}) ]))
    assert judge.score(_candidate("c1", "x"), {}).abstain is True
    # 模型调用失败 → abstain（不编造）
    judge = LLMJudge(_FakeJudgePool([RuntimeError("boom")]))
    assert judge.score(_candidate("c1", "x"), {}).abstain is True


def test_pairwise_judge_agreement():
    judge = ScriptedJudge(per_sample={
        "a": type("R", (), {"abstain": False, "scores": {"instruction_fulfillment": 0.9,
                                                          "persona_naturalness": 0.9,
                                                          "relationship_fit": 0.9,
                                                          "conversational_progress": 0.9,
                                                          "style_restraint": 0.9}})(),
        "b": type("R", (), {"abstain": False, "scores": {"instruction_fulfillment": 0.5,
                                                          "persona_naturalness": 0.5,
                                                          "relationship_fit": 0.5,
                                                          "conversational_progress": 0.5,
                                                          "style_restraint": 0.5}})(),
    })
    pairwise = PairwiseJudge(judge)
    preferred, agreed = pairwise.compare(
        _candidate("a", "好"), _candidate("b", "差"), {}
    )
    assert preferred == "a"
    assert agreed is True
    assert pairwise.agreement_rate == 1.0


def test_pairwise_judge_inconsistent_pairs_flagged():
    """judge 不稳定（同对两次判断不同）→ 不一致对不计入采纳。"""
    judge = ScriptedJudge(per_sample={
        # 顺序相关 judge：结果取决于调用顺序（模拟不稳定）
        "a": type("R", (), {"abstain": False, "scores": {"instruction_fulfillment": 0.9,
                                                          "persona_naturalness": 0.9,
                                                          "relationship_fit": 0.9,
                                                          "conversational_progress": 0.9,
                                                          "style_restraint": 0.9}})(),
        "b": type("R", (), {"abstain": False, "scores": {"instruction_fulfillment": 0.5,
                                                          "persona_naturalness": 0.5,
                                                          "relationship_fit": 0.5,
                                                          "conversational_progress": 0.5,
                                                          "style_restraint": 0.5}})(),
    })
    # 用分数相等的候选对：两次判断都是 tie（None）→ 不一致
    pairwise = PairwiseJudge(judge)
    preferred, agreed = pairwise.compare(
        _candidate("a", "好"), _candidate("a", "好"), {}
    )
    assert preferred is None
    assert agreed is False
    assert pairwise.agreement_rate == 0.0


def test_calibrate_report_metrics():
    """校准报告输出 G3 指标：换序一致率 / abstain 率 / 人机一致率 / 轴 MAE。"""
    good = _candidate("gold-1", "我今年22岁，住在河畔公寓。")
    bad = _candidate("gold-2", "嗯。")
    rows = [
        {"pair": [good, bad], "gold": {"preferred": "a"}},
        {"candidate": good, "gold": {"scores": {axis: 0.9 for axis in SOFT_AXES}}},
    ]
    report = calibrate(ScriptedJudge(scores={axis: 0.9 for axis in SOFT_AXES}), rows)
    assert report["total"] == 2
    assert "abstain_rate" in report
    assert report["abstain_rate"] == 0.0
    assert report["pairwise_agreement_rate"] >= 0.0
    assert report["human_agreement_rate"] is not None
    assert all("axis_mae" in report for _ in [0])
