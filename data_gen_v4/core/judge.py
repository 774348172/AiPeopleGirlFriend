"""Judge 框架（阶段 3 C-4：校准 judge，v2 §7.3）。

质量向量五轴（soft，只用于排序与选择，不证明事实/安全正确）：
instruction_fulfillment / persona_naturalness / relationship_fit /
conversational_progress / style_restraint。硬约束（G0-G8）不可被软分补偿。

- LLMJudge：经 ModelPool（adapter_id="judge"）调用，prompt 输出 JSON 分数；
  解析失败/无 key → abstain（不编造）。
- ScriptedJudge：测试用（固定分数/可配 abstain）。
- PairwiseJudge：对候选对换序两次打分，一致才采纳（judge 换序一致性门，
  G3 验收：换序一致率 ≥ 阈值）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

SOFT_AXES = (
    "instruction_fulfillment",
    "persona_naturalness",
    "relationship_fit",
    "conversational_progress",
    "style_restraint",
)

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


def _build_role_context(context: dict[str, Any]) -> str:
    """从 context 组装 judge 用的角色上下文（2026-08-10 方案 A）。

    此前 judge 只有 SCENE/TOPIC/MESSAGES，无角色设定知识 → 越界/秘密泄漏
    打不出低分。现从 context["profile"] 组装：
    - 角色名 + 人格（anchor_contract.personality）
    - 秘密边界（anchor_contract.secret_boundary）
    - 秘密词表（policy_terms.secret_terms）—— 命中的台词应压低
      style_restraint / relationship_fit
    无 profile 时返回空（旧行为兼容，judge 靠常识打分）。
    """
    profile = context.get("profile") or {}
    if not profile:
        return "（无）"
    parts: list[str] = []
    display = profile.get("display_name") or profile.get("profile_id") or "角色"
    parts.append(f"角色：{display}")
    contract = profile.get("anchor_contract") or {}
    if contract.get("personality"):
        parts.append(f"人格：{contract['personality']}")
    if contract.get("secret_boundary"):
        parts.append(f"秘密边界：{contract['secret_boundary']}")
    # 2026-08-10：显式关系边界——personality 是描述性的，judge 可能给"接近愿望"
    # 打高分；从 prompt_policy_block 提取硬边界（未确认恋爱/不越级/不无条件服从），
    # 注入后 relationship_fit 轴能识别"越界直球"类错误（如"不想离你太远"）。
    policy_block = profile.get("prompt_policy_block") or ""
    boundary_lines = [
        line.strip().lstrip("- ").strip()
        for line in policy_block.splitlines()
        if any(k in line for k in ("恋爱", "确认", "越级", "服从", "女友"))
    ]
    if boundary_lines:
        parts.append(f"关系边界（对话不得越过）：{'；'.join(boundary_lines)}")
    terms = profile.get("policy_terms") or {}
    secret_terms = terms.get("secret_terms") or []
    if secret_terms:
        parts.append(f"秘密词（台词中出现应视为泄漏，压低 style_restraint/relationship_fit）：{'、'.join(secret_terms)}")
    return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class JudgeResult:
    scores: dict[str, float] = field(default_factory=dict)
    abstain: bool = False
    abstain_reason: str = ""


class Judge(Protocol):
    def score(self, candidate: dict[str, Any], context: dict[str, Any]) -> JudgeResult:
        """单候选五轴打分。"""
        ...


class ScriptedJudge:
    """测试用 judge：固定分数或按候选 sample_id 定制（可配 abstain）。"""

    def __init__(
        self,
        scores: dict[str, float] | None = None,
        *,
        abstain: bool = False,
        per_sample: dict[str, JudgeResult] | None = None,
    ) -> None:
        defaults = {axis: 0.9 for axis in SOFT_AXES}
        defaults.update(scores or {})
        self._scores = defaults
        self._abstain = abstain
        self._per_sample = per_sample or {}
        self.calls: list[str] = []

    def score(self, candidate: dict[str, Any], context: dict[str, Any]) -> JudgeResult:
        sample_id = str(candidate.get("sample_id", ""))
        self.calls.append(sample_id)
        if sample_id in self._per_sample:
            return self._per_sample[sample_id]
        if self._abstain:
            return JudgeResult(abstain=True, abstain_reason="scripted-abstain")
        return JudgeResult(scores=dict(self._scores))


class LLMJudge:
    """真实 judge：经 ModelPool（adapter_id="judge"）五轴打分（temperature=0）。

    模型引用：构造时传入 model_ref（dict 或 str），默认 {"name": "judge"}（由
    adapter 的 default_model 解析）；无 API key/解析失败 → abstain（不编造）。
    """

    def __init__(
        self,
        model_pool: Any,
        *,
        adapter_id: str = "judge",
        model_ref: str | dict[str, Any] | None = None,
        prompt_path: str | None = None,
    ) -> None:
        self._model_pool = model_pool
        self._adapter_id = adapter_id
        self._model_ref = model_ref or {"name": "judge", "revision": "v1"}
        self._prompt_path = Path(prompt_path) if prompt_path else _PROMPTS_DIR / "judge_soft.txt"
        self.calls: list[dict[str, Any]] = []

    def score(self, candidate: dict[str, Any], context: dict[str, Any]) -> JudgeResult:
        prompt = self._render_prompt(candidate, context)
        spec = {
            "adapter_id": self._adapter_id,
            "model": self._model_ref,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 512,
            # 2026-08-10：judge 禁思考（T12 同参数）——reasoning 模型（deepseek-v4-pro）
            # 思考会吃预算/污染 JSON；禁思考后直接输出分数 JSON，judge 才稳定
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        self.calls.append(spec)
        try:
            adapter = self._model_pool.resolve(spec)
            result = adapter.generate(spec)
        except Exception as error:  # noqa: BLE001
            return JudgeResult(abstain=True, abstain_reason=f"call_failed:{type(error).__name__}")
        return self._parse(result.get("content", ""))

    def _render_prompt(self, candidate: dict[str, Any], context: dict[str, Any]) -> str:
        template = self._prompt_path.read_text(encoding="utf-8")
        item_input = candidate.get("input") or {}
        messages = (candidate.get("target") or {}).get("messages", [])
        dialogue = "\n".join(
            f"{'玩家' if m.get('role') == 'human' else '角色'}: {m.get('content', '')}"
            for m in messages
        )
        # 2026-08-10（方案 A）：judge 注入角色上下文——此前 judge 只有
        # SCENE/TOPIC/MESSAGES，没有正典/锚/秘密词/关系边界，导致"越界""秘密
        # 泄漏"类语义错误打不出低分（同模型自评且无设定知识）。现从 context
        # 组装：锚（身份/关系/秘密边界）+ 秘密词 + 关系边界提示。
        role_context = _build_role_context(context)
        return (
            template.replace("{{SCENE}}", str(item_input.get("scene", "日常")))
            .replace("{{TOPIC}}", str(item_input.get("topic", "闲聊")))
            .replace("{{MESSAGES}}", dialogue)
            .replace("{{ROLE_CONTEXT}}", role_context)
        )

    @staticmethod
    def _parse(content: str) -> JudgeResult:
        text = content.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return JudgeResult(abstain=True, abstain_reason="no_json")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return JudgeResult(abstain=True, abstain_reason="json_parse_error")
        scores = data.get("scores")
        if not isinstance(scores, dict):
            return JudgeResult(abstain=True, abstain_reason="no_scores")
        cleaned = {}
        for axis in SOFT_AXES:
            value = scores.get(axis)
            if isinstance(value, (int, float)) and 0 <= value <= 1:
                cleaned[axis] = float(value)
            else:
                return JudgeResult(abstain=True, abstain_reason=f"bad_axis:{axis}")
        if data.get("abstain"):
            return JudgeResult(abstain=True, abstain_reason=str(data.get("reason", "abstain")))
        return JudgeResult(scores=cleaned)


@dataclass(frozen=True, slots=True)
class PairResult:
    preferred: str | None  # "a" / "b" / None（tie 或 abstain）
    abstain: bool = False


class PairwiseJudge:
    """候选对换序两次打分：一致才采纳（换序一致性门）。

    compare(a, b) 返回 (preferred, agreed)：preferred 为最终偏好（一致时），
    agreed=False 表示两次判断不一致（judge 不可靠，该对不采纳）。
    """

    def __init__(self, judge: Judge) -> None:
        self._judge = judge
        self.agreements: list[bool] = []

    def compare(
        self, a: dict[str, Any], b: dict[str, Any], context: dict[str, Any]
    ) -> tuple[str | None, bool]:
        r1 = self._prefer(a, b, context)
        r2 = self._prefer(b, a, context)
        if r1 is None or r2 is None:
            self.agreements.append(False)
            return None, False
        agreed = r1 != r2  # A>B 且 B<A（换序后偏好翻转）→ 一致
        self.agreements.append(agreed)
        return r1, agreed

    def _prefer(
        self, first: dict[str, Any], second: dict[str, Any], context: dict[str, Any]
    ) -> str | None:
        """比较 first/second：返回偏好方（"a"=first / "b"=second）或 None（abstain/tie）。"""
        total_a = self._judge.score(first, context)
        total_b = self._judge.score(second, context)
        if total_a.abstain or total_b.abstain:
            return None
        score_a = sum(total_a.scores.values())
        score_b = sum(total_b.scores.values())
        if abs(score_a - score_b) < 1e-9:
            return None
        return "a" if score_a > score_b else "b"

    @property
    def agreement_rate(self) -> float:
        if not self.agreements:
            return 1.0
        return sum(self.agreements) / len(self.agreements)
