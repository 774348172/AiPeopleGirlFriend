"""StructuredProtocolModeAdapter：RECALL_PLAN / MEMORY_PROPOSE（《数据生成器v4设计》§10.2/10.3）。

按 mode_v4 冻结的 input/target schema 渲染 mode prompt → 模型输出 JSON →
jsonschema 校验 → 显式失败。输入为双视图 fixture：model_input 进 prompt，
oracle_state 永不进 prompt（§10.4）。只生成受约束输出，不支持 plan 转换偷渡。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from data_gen_v4.core.schemas import SCHEMAS_DIR

from .training import TrainingRecordV4, estimate_token_count

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

_MODE_FAILURE = "mode_failure"

_MODE_PROMPTS = {
    "RECALL_PLAN": "recall_plan",
    "MEMORY_PROPOSE": "memory_propose",
}
_MODE_TARGET_SCHEMAS = {
    "RECALL_PLAN": "recall_plan_target.schema.json",
    "MEMORY_PROPOSE": "memory_propose_target.schema.json",
}
_MODE_RENDERERS = {
    "RECALL_PLAN": "recall-plan-v1",
    "MEMORY_PROPOSE": "memory-propose-v1",
}


class StructuredProtocolModeAdapter:
    """单一 adapter 服务两种协议 mode；mode 由 plan item 决定。"""

    def __init__(
        self,
        *,
        prompts_dir: Path = PROMPTS_DIR,
        protocol_snapshot_id: str = "protocol-v1",
    ) -> None:
        self._prompts_dir = Path(prompts_dir)
        self._protocol_snapshot_id = protocol_snapshot_id
        self._validators: dict[str, Draft7Validator] = {}

    def prepare(self, plan_item: dict[str, Any], package_set: dict[str, Any]) -> dict[str, Any]:
        return {"plan_item": plan_item, "package_set": package_set}

    def generate(self, mode_job: dict[str, Any], call_executor: Any) -> dict[str, Any]:
        item = mode_job["plan_item"]
        package_set = mode_job["package_set"]
        mode = item.get("mode")
        if mode not in _MODE_PROMPTS:
            return {
                _MODE_FAILURE: True,
                "error_code": "mode_not_registered",
                "retryable": False,
                "reason": f"StructuredProtocolModeAdapter 不支持 mode: {mode}",
            }
        fixture = item.get("fixture") or package_set.get("fixture") or {}
        model_input = fixture.get("model_input", {})
        oracle = fixture.get("oracle_state", {})
        if not model_input:
            return {
                _MODE_FAILURE: True,
                "error_code": "precondition_failed",
                "retryable": False,
                "reason": "缺少双视图 fixture 的 model_input",
            }

        prompt = self._render_prompt(mode, model_input)
        result = call_executor.call(
            {
                "model": {"name": "protocol-teacher", "revision": "v1"},
                "prompt_hash": _hash_of(prompt),
                "role": mode,
                "seed": f"{item.get('seed', 0)}:{mode}",
                "messages": [{"role": "system", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 2048,  # reasoning 模型思考留足空间
            },
            stage=mode,
            attempt_no=item.get("attempt_no", 1),
            # 大块 B（P0-4）：candidate_no 由 engine 注入（K 候选幂等键隔离）
            candidate_no=item.get("candidate_no"),
        )
        try:
            target = self._parse_and_validate(mode, result["content"])
        except _ModeFailureSignal as failure:
            return failure.payload
        return {
            "input": model_input,
            "target": target,
            "model": {"name": "protocol-teacher", "revision": "v1"},
            "prompt_hash": _hash_of(prompt),
            "prompt_template_version": f"{mode.lower()}-v1",
            "config_hash": "config:protocol:v1",
            "seed": f"{item.get('seed', 0)}:{mode}",
            "source_event_ids": item.get("source_event_ids", []),
            "support_spans": item.get("support_spans", []),
            # 大块 B（阶段 2 P0-3）：真实调用链回链（CallRecord.record_id 列表）
            "calls": call_executor.record_ids(),
            "_oracle_never_in_prompt": oracle is not None,
        }

    def render_training(self, candidate: dict[str, Any], package_set: dict[str, Any]) -> TrainingRecordV4:
        mode = candidate["mode"]
        messages = [
            {"role": "human", "content": _compact_input(candidate.get("input", {}))},
            {"role": "assistant", "content": json.dumps(candidate["target"], ensure_ascii=False)},
        ]
        return TrainingRecordV4(
            sample_id=candidate["sample_id"],
            mode=mode,
            render_profile_id=_MODE_RENDERERS.get(mode, f"{mode.lower()}-v1"),
            messages=messages,
            supervised_message_indexes=[1],
            supervised_token_count=estimate_token_count(messages, [1]),
            protocol_snapshot_id=self._protocol_snapshot_id,
            system_anchor=candidate.get("prompt_hash", ""),
        )

    # ───────────────────────── 内部 ─────────────────────────

    def _render_prompt(self, mode: str, model_input: dict[str, Any]) -> str:
        template = (self._prompts_dir / f"{_MODE_PROMPTS[mode]}.txt").read_text(encoding="utf-8")
        if mode == "RECALL_PLAN":
            cues = "\n".join(
                f"- [{c.get('cue_type')}] {c.get('text')}"
                for c in model_input.get("visible_cues", [])
            ) or "（无）"
            tokens = {
                "VISIBLE_CUES": cues,
                "CURRENT_INPUT": model_input.get("current_input", ""),
            }
        else:
            events = "\n".join(
                f"- [{e.get('event_id')}] {e.get('actor')}: {e.get('text')}"
                for e in model_input.get("committed_events", [])
            ) or "（无）"
            tokens = {
                "COMMITTED_EVENTS": events,
                "CURRENT_PROJECTION": json.dumps(
                    model_input.get("current_projection", {}), ensure_ascii=False
                ),
            }
        for key, value in tokens.items():
            template = template.replace("{{" + key + "}}", str(value))
        return template

    def _parse_and_validate(self, mode: str, raw: str) -> dict[str, Any]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        try:
            target = json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise _ModeFailureSignal(
                {
                    _MODE_FAILURE: True,
                    "error_code": "parse_error",
                    "retryable": True,
                    "reason": f"{mode} JSON 解析失败: {error}",
                }
            ) from error
        if not isinstance(target, dict):
            raise _ModeFailureSignal(
                {
                    _MODE_FAILURE: True,
                    "error_code": "schema_error",
                    "retryable": True,
                    "reason": f"{mode} 输出不是对象",
                }
            )
        validator = self._validator_for(mode)
        errors = sorted(validator.iter_errors(target), key=lambda e: list(e.path))
        if errors:
            raise _ModeFailureSignal(
                {
                    _MODE_FAILURE: True,
                    "error_code": "schema_error",
                    "retryable": True,
                    "reason": f"{mode} 不满足冻结 schema: {errors[0].message}",
                }
            )
        return target

    def _validator_for(self, mode: str) -> Draft7Validator:
        validator = self._validators.get(mode)
        if validator is None:
            schema = json.loads(
                (SCHEMAS_DIR / "mode_v4" / _MODE_TARGET_SCHEMAS[mode]).read_text(encoding="utf-8")
            )
            validator = Draft7Validator(schema)
            self._validators[mode] = validator
        return validator


class _ModeFailureSignal(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("reason", "")))
        self.payload = payload


def _compact_input(model_input: dict[str, Any]) -> str:
    return json.dumps(model_input, ensure_ascii=False, sort_keys=True, default=str)


def _hash_of(text: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
