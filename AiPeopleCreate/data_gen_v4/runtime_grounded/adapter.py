"""V5 single-reply teacher plus independent semantic-audit mode adapter."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from data_gen_v4.adapters.modes.renderers import ProductionRenderers
from data_gen_v4.adapters.modes.training import TrainingRecordV4

from .contracts import (
    RuntimeGroundedContractError,
    validate_semantic_audit,
    validate_teacher_target,
)
from .gates import evaluate_runtime_grounded_candidate
from .renderer import render_runtime_grounded_training_record
from .scenario import ScenarioContractError, validate_scenario

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
MODE = "RUNTIME_GROUNDED_REPLY"


class RuntimeGroundedReplyAdapter:
    """Reuse V4 execution/ledger while keeping V5 facts fixture-driven."""

    def __init__(
        self,
        *,
        prompts_dir: Path = PROMPTS_DIR,
        teacher_adapter_id: str = "runtime-grounded-teacher",
        audit_adapter_id: str = "runtime-grounded-audit",
        teacher_model: str | dict[str, Any] = "runtime-grounded-teacher",
        audit_model: str | dict[str, Any] = "runtime-grounded-audit",
        teacher_max_tokens: int = 800,
        audit_max_tokens: int = 1200,
        teacher_temperature: float = 0.7,
        teacher_retry_temperature: float | None = None,
    ) -> None:
        self._prompts_dir = Path(prompts_dir)
        self._teacher_adapter_id = teacher_adapter_id
        self._audit_adapter_id = audit_adapter_id
        self._teacher_model = teacher_model
        self._audit_model = audit_model
        self._teacher_max_tokens = teacher_max_tokens
        self._audit_max_tokens = audit_max_tokens
        self._teacher_temperature = teacher_temperature
        self._teacher_retry_temperature = teacher_retry_temperature

    def prepare(self, plan_item: dict[str, Any], package_set: dict[str, Any]) -> dict[str, Any]:
        scenario = _resolve_scenario(plan_item.get("fixture_id"), package_set)
        return {"plan_item": plan_item, "package_set": package_set, "scenario": scenario}

    def generate(self, mode_job: dict[str, Any], call_executor: Any) -> dict[str, Any]:
        item = mode_job["plan_item"]
        package_set = mode_job["package_set"]
        scenario = mode_job.get("scenario")
        generation_budget = int(item.get("candidate_count", 1)) * int(
            item.get("max_attempts", 1)
        )
        if generation_budget > 2:
            return _failure(
                "precondition_failed",
                False,
                "one ScenarioSpec may request at most two teacher generations",
            )
        if not scenario:
            return _failure("precondition_failed", False, "runtime-grounded fixture not found")
        try:
            validate_scenario(scenario)
        except ScenarioContractError as error:
            return _failure("schema_error", False, str(error))

        system_anchor = _system_anchor(package_set)
        teacher_prompt = self._render_prompt(
            "runtime_grounded_reply",
            PROFILE_STYLE=system_anchor,
            MODEL_VIEW=_json_text(scenario["model_view"]),
            ORACLE_VIEW=_json_text(scenario["oracle_view"]),
            SURFACE_OBLIGATIONS=_surface_obligations(scenario),
        )
        last_failure = item.get("_last_failure")
        if last_failure:
            teacher_prompt += (
                "\n\n上次候选未通过同一场景的硬门。只重写当前回复，不得修改场景、"
                "历史或事实。失败原因：" + str(last_failure.get("reason", ""))[:400]
            )
        teacher_result = call_executor.call(
            self._call_spec(
                adapter_id=self._teacher_adapter_id,
                model=self._teacher_model,
                prompt=teacher_prompt,
                seed=item.get("seed", 0),
                temperature=(
                    self._teacher_retry_temperature
                    if last_failure and self._teacher_retry_temperature is not None
                    else self._teacher_temperature
                ),
                max_tokens=self._teacher_max_tokens,
            ),
            stage="runtime_grounded_teacher",
            attempt_no=item.get("attempt_no", 1),
            candidate_no=item.get("candidate_no"),
        )
        try:
            teacher_target = _parse_object(teacher_result.get("content", ""), "teacher")
            validate_teacher_target(teacher_target)
        except (ValueError, RuntimeGroundedContractError) as error:
            return _failure("schema_error", True, str(error))

        audit_prompt = self._render_prompt(
            "runtime_grounded_audit",
            MODEL_VIEW=_json_text(scenario["model_view"]),
            ORACLE_VIEW=_json_text(scenario["oracle_view"]),
            REPLY=teacher_target["reply"],
            SURFACE_OBLIGATIONS=_surface_obligations(scenario),
        )
        audit_result = call_executor.call(
            self._call_spec(
                adapter_id=self._audit_adapter_id,
                model=self._audit_model,
                prompt=audit_prompt,
                seed=item.get("seed", 0),
                temperature=0.0,
                max_tokens=self._audit_max_tokens,
            ),
            stage="runtime_grounded_audit",
            attempt_no=item.get("attempt_no", 1),
            candidate_no=item.get("candidate_no"),
        )
        try:
            semantic_audit = _parse_object(audit_result.get("content", ""), "audit")
            validate_semantic_audit(semantic_audit)
            report = evaluate_runtime_grounded_candidate(
                scenario, teacher_target, semantic_audit
            )
        except (ValueError, RuntimeGroundedContractError) as error:
            return _failure("schema_error", True, str(error))
        if report.decision == "reject":
            failed = [gate for gate, ok in report.verdicts.items() if not ok]
            return _failure(
                "unsupported_claim",
                True,
                f"runtime-grounded hard gates rejected: {failed}; {report.reasons}",
            )

        model_view_text = _json_text(scenario["model_view"])
        return {
            "input": {
                "scenario_id": scenario["scenario_id"],
                "model_view": scenario["model_view"],
            },
            "target": {
                "messages": [
                    {"role": "human", "content": model_view_text},
                    {"role": "assistant", "content": teacher_target["reply"]},
                ],
                "teacher_target": teacher_target,
                "semantic_audit": semantic_audit,
                "runtime_grounded_gate_report": report.to_dict(),
                "eligible_for_training": report.approved,
            },
            "model": {
                "teacher": self._teacher_model,
                "audit": self._audit_model,
            },
            "prompt_hash": _hash_text(teacher_prompt + "\n---AUDIT---\n" + audit_prompt),
            "prompt_template_version": "runtime-grounded-reply-audit-v1",
            "config_hash": "config:runtime-grounded:v1",
            "seed": item.get("seed", "unsupported"),
            "source_event_ids": list(
                scenario["model_view"]["evidence_contract"]["allowed_evidence_refs"]
            ),
            "calls": call_executor.record_ids(),
        }

    def render_training(
        self, candidate: dict[str, Any], package_set: dict[str, Any]
    ) -> TrainingRecordV4:
        target = candidate.get("target") or {}
        report = target.get("runtime_grounded_gate_report") or {}
        if report.get("decision") != "approve" or not target.get("eligible_for_training"):
            raise ValueError("runtime-grounded candidate is not approved for training")
        scenario = _resolve_scenario(candidate.get("fixture_id"), package_set)
        if not scenario:
            scenario_id = (candidate.get("input") or {}).get("scenario_id")
            scenario = _resolve_scenario(scenario_id, package_set)
        if not scenario:
            raise ValueError("runtime-grounded fixture is unavailable during rendering")
        return render_runtime_grounded_training_record(
            scenario,
            target["teacher_target"],
            system_anchor=_system_anchor(package_set),
            sample_id=candidate.get("sample_id") or scenario["scenario_id"],
        )

    def _render_prompt(self, name: str, **tokens: str) -> str:
        text = (self._prompts_dir / f"{name}.txt").read_text(encoding="utf-8")
        for key, value in tokens.items():
            text = text.replace("{{" + key + "}}", value)
        if re.search(r"\{\{[A-Z][A-Z0-9_]*\}\}", text):
            raise ValueError(f"unresolved prompt token in {name}")
        return text

    @staticmethod
    def _call_spec(
        *,
        adapter_id: str,
        model: str | dict[str, Any],
        prompt: str,
        seed: int | str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        return {
            "adapter_id": adapter_id,
            "model": model,
            "prompt_hash": _hash_text(prompt),
            "seed": seed,
            "messages": [{"role": "system", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }


def _resolve_scenario(
    fixture_id: Any, package_set: dict[str, Any]
) -> dict[str, Any] | None:
    if not isinstance(fixture_id, str) or not fixture_id:
        return None
    values = package_set.get("runtime_grounded_scenarios") or {}
    if isinstance(values, dict):
        value = values.get(fixture_id)
        return value if isinstance(value, dict) else None
    if isinstance(values, list):
        for value in values:
            if isinstance(value, dict) and value.get("scenario_id") == fixture_id:
                return value
    return None


def _system_anchor(package_set: dict[str, Any]) -> str:
    profile = package_set.get("profile") or {}
    protocol = package_set.get("protocol") or {}
    return ProductionRenderers.reply_system_anchor(
        profile,
        anchor_facts=package_set.get("anchor_facts") or {},
        beliefs=str(package_set.get("beliefs", "")),
        rules=protocol.get("reply_runtime_rules") or [],
    )


def _surface_obligations(scenario: dict[str, Any]) -> str:
    lines: list[str] = []
    oracle = scenario["oracle_view"]
    contract = oracle.get("response_contract") or {}
    for assertion in oracle["required_assertions"]:
        if assertion["predicate"] == "location" and assertion["object"]:
            lines.append(
                f"- 地点必须写成“在/放在/挂在/位于{assertion['object']}”一类方位句，"
                f"不能写成“物品是{assertion['object']}”。"
            )
    if scenario["task_type"] == "reply_subject_attribution":
        speakers = [
            line.split("：", 1)[0].strip()
            for line in scenario["model_view"].get("recent_dialogue", [])
            if "：" in line
        ]
        speaker = next(
            (value for value in speakers if value not in {"男主", "白未晞"}),
            None,
        )
        if speaker:
            statement = next(
                line.split("：", 1)[1].strip().rstrip("。！？")
                for line in scenario["model_view"]["recent_dialogue"]
                if line.startswith(speaker + "：")
            )
            lines.append(
                f"- 回复正文必须逐字说出具体说话者“{speaker}”，并明确复述“{statement}”；"
                "不能只说朋友/他/她，也不能只回答人名后省略所述事实。"
            )
            if contract.get("subject_proposition_binding", "required") == "required":
                lines.append(f"- 联合目标不可拆分：同一句回答中必须同时出现“{speaker}”和事实“{statement}”。")
    if scenario["task_type"] == "reply_insufficient_information":
        lines.append(
            "- 只可说明没有可靠记录、无法确定，并请求线索；不得补写对象曾经出问题，"
            "也不得评价它现在正常、还好、没事或其他当前状态。"
        )
        if contract.get("unknown_acknowledgement", "required") == "required":
            lines.append("- 必须先明确说不知道、不清楚、不确定或没有可靠记录；只复述问题或反问玩家直接拒绝。")
    if contract.get("unsupported_expansion_policy", "reject") == "reject":
        lines.append(
            "- 禁止把单次事实扩展为每天、每次、一直、永远、都会或都有；"
            "禁止新增已经通知、已经安排、已经联系、我查过或我确认过等无来源动作。"
        )
    if "我的" in scenario["model_view"]["current_protagonist_utterance"]:
        lines.append("- 玩家说的“我的”属于男主；女主回复必须改成“你的”或省略代词，不得说成女主的“我的”。")
    return "\n".join(lines) if lines else "- 无额外表面义务，仍须完整满足 oracle。"


def _parse_object(content: Any, label: str) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"{label} returned empty content")
    try:
        value = json.loads(content.strip())
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} must return one JSON object: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} output is not an object")
    return value


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _failure(error_code: str, retryable: bool, reason: str) -> dict[str, Any]:
    return {
        "mode_failure": True,
        "error_code": error_code,
        "retryable": retryable,
        "reason": reason,
    }
