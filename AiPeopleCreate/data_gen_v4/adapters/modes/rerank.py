"""MemoryRerankAdapter：MEMORY_RERANK（记忆选择器训练数据生产，2026-08-09）。

模式定位（对齐 AI 程序侧《RelationshipRuntime_全局记忆选择器训练与模型决策方案_V2.md》）：
- 教师模型为"当前对话场景"生成 query（最近对话/当前消息/工作状态），
  并对每个候选记忆标注 positive / hard_negative / easy_negative + 相关性分数。
- 候选记忆来自 profile 正典（timeline_event / canon_fact units，从 package_set
  snapshots 蒸馏），不依赖角色硬编码 —— 多角色通用（character_id 由 profile 决定）。
- 输出经 jsonschema 校验 + 确定性检查（labels 必须覆盖全部候选 memory_id，
  不得编造新 ID）。

与 StructuredProtocolModeAdapter 同构：单次模型调用 → JSON target →
校验 → render_training 监督 JSON（协议数据不进 ShareGPT 回复格式）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from data_gen_v4.core.schemas import SCHEMAS_DIR

from .training import TrainingRecordV4, estimate_token_count

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

_MODE = "MEMORY_RERANK"
_MODE_FAILURE = "mode_failure"
_TARGET_SCHEMA = "memory_rerank_target.schema.json"
_RENDER_PROFILE = "memory-rerank-v1"


# 元数据类 canon_fact（不是记忆内容，不作为候选）
_META_FACTS = {
    "facts:character_id", "facts:world_id", "facts:protagonist_id", "facts:product_name",
    "facts:player_age", "facts:current_time",
}


def distill_candidates(
    package_set: dict[str, Any],
    memory_pool: list[str] | None = None,
    *,
    pool_size: int = 8,
    seed: int = 0,
) -> list[dict]:
    """从 package_set snapshots 蒸馏候选记忆（timeline_event + canon_fact units）。

    selector_text 按运行时记忆视图的 statement/temporal 顺序渲染为可读文本
    （与 runtime/_memory_vectors.build_selector_views 的精确逐字对齐留 P1b BGE 接入）。

    memory_pool 为空/未提供时取全部 timeline_event units；提供时按 source_id 过滤
    （可同时含 canon_fact source_id，如 facts:xxx）。

    2026-08-11（SELECT-01 泛化修复）：候选数不足 pool_size 时，从全库其余单元
    **确定性均衡补足**——训练时每条样本的候选集合更接近运行时 Top32 重排场景
    （混杂 positive / hard_negative / easy_negative），避免候选永远来自同一小批
    timeline 事件导致泛化差。补足顺序由 seed 决定（同 seed 可复现）。
    """
    snapshots = package_set.get("snapshots") or {}
    all_candidates: list[dict] = []
    seen: set[str] = set()
    for snapshot in snapshots.values():
        for unit in snapshot.get("units", []):
            kind = unit.get("source_kind")
            if kind not in ("timeline_event", "canon_fact"):
                continue
            source_id = unit.get("source_id") or ""
            if not source_id or source_id in seen:
                continue
            if source_id in _META_FACTS:
                continue  # 元数据类不作记忆候选
            if memory_pool and source_id not in memory_pool:
                continue
            value = str(unit.get("value") or "").strip()
            occurred_at = str(unit.get("occurred_at") or "").strip()
            if not value:
                continue
            seen.add(source_id)
            text = f"[{occurred_at}] {value}" if occurred_at else value
            all_candidates.append(
                {
                    "memory_id": source_id,
                    "selector_text": text,
                    "evidence_refs": [f"{kind}:{source_id}"],
                }
            )
    if len(all_candidates) >= pool_size:
        # 候选超过 pool_size：环形轮转取 pool_size 个（seed 决定起点，均衡覆盖）
        if memory_pool:
            # 显式 memory_pool 是运行时给出的严格 allowlist。不能为了凑满
            # pool_size 把未请求的记忆混进来，否则 labels 会覆盖错误候选。
            return all_candidates
        all_candidates = sorted(all_candidates, key=lambda c: c["memory_id"])
        start = seed % len(all_candidates)
        all_candidates = all_candidates[start:] + all_candidates[:start]
        return all_candidates[:pool_size]
    if not memory_pool:
        return all_candidates  # 全库不足 pool_size：全部返回
    # 显式 memory_pool 即使候选不足也不能从全库补足；空结果由调用方报告
    # precondition_failed。否则 reranker 会训练出“候选池”之外的标签。
    if memory_pool:
        return all_candidates

    # 无显式 pool 时，从全库确定性取满 pool_size
    full: list[dict] = []
    full_seen: set[str] = set()
    for snapshot in snapshots.values():
        for unit in snapshot.get("units", []):
            kind = unit.get("source_kind")
            if kind not in ("timeline_event", "canon_fact"):
                continue
            source_id = unit.get("source_id") or ""
            if not source_id or source_id in full_seen:
                continue
            if source_id in _META_FACTS:
                continue  # 元数据类不作记忆候选
            value = str(unit.get("value") or "").strip()
            if not value:
                continue
            full_seen.add(source_id)
            occurred_at = str(unit.get("occurred_at") or "").strip()
            text = f"[{occurred_at}] {value}" if occurred_at else value
            full.append(
                {
                    "memory_id": source_id,
                    "selector_text": text,
                    "evidence_refs": [f"{kind}:{source_id}"],
                }
            )
    anchored = {c["memory_id"] for c in all_candidates}
    extras = [c for c in full if c["memory_id"] not in anchored]
    # 环形轮转补足：seed 只决定起点，所有候选单元均等机会被补足
    # （比 hash 排序更均衡——避免某些单元永远排前面被优先选中）
    extras = sorted(extras, key=lambda c: c["memory_id"])
    if extras:
        start = seed % len(extras)
        extras = extras[start:] + extras[:start]
    return all_candidates + extras[: max(0, pool_size - len(all_candidates))]


class MemoryRerankAdapter:
    """MEMORY_RERANK 模式：query + 候选记忆激活标注（单次模型调用）。"""

    def __init__(
        self,
        *,
        prompts_dir: Path = PROMPTS_DIR,
        protocol_snapshot_id: str = "protocol-v1",
        max_tokens: int = 8192,
        pool_size: int = 8,
    ) -> None:
        self._prompts_dir = Path(prompts_dir)
        self._protocol_snapshot_id = protocol_snapshot_id
        self._max_tokens = max_tokens
        self._pool_size = pool_size
        self._validator: Draft7Validator | None = None

    # ───────────────────────── ModeAdapter 接口 ─────────────────────────

    def prepare(self, plan_item: dict[str, Any], package_set: dict[str, Any]) -> dict[str, Any]:
        return {"plan_item": plan_item, "package_set": package_set}

    def generate(self, mode_job: dict[str, Any], call_executor: Any) -> dict[str, Any]:
        item = mode_job["plan_item"]
        package_set = mode_job["package_set"]
        item_input = item.get("input") or {}
        scene = item_input.get("scene") or "日常"
        working_state = item_input.get("working_state") or "（无特别状态）"
        memory_pool = item_input.get("memory_pool") or []
        real_conv = item_input.get("real_conversation")  # 真实对话注入模式

        candidates = distill_candidates(
            package_set,
            memory_pool or None,
            pool_size=self._pool_size,
            seed=item.get("seed", 0),
        )
        if not candidates:
            return {
                _MODE_FAILURE: True,
                "error_code": "precondition_failed",
                "retryable": False,
                "reason": "候选记忆为空（timeline/canon units 缺失或 memory_pool 无命中）",
            }

        # real 模式：query 从真实对话提取，教师只标 labels（不生成 query）
        if real_conv and isinstance(real_conv, dict):
            query, prompt, model_input = self._build_real_mode(
                real_conv, scene, working_state, candidates
            )
        else:
            query = None
            model_input = {
                "scenario": {"scene": scene, "working_state": working_state},
                "candidates": candidates,
            }
            prompt = self._render_prompt(model_input)

        result = call_executor.call(
            {
                "model": {"name": "protocol-teacher", "revision": "v1"},
                "prompt_hash": _hash_of(prompt),
                "role": _MODE,
                "seed": f"{item.get('seed', 0)}:{_MODE}",
                "messages": [{"role": "system", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": self._max_tokens,
            },
            stage=_MODE,
            attempt_no=item.get("attempt_no", 1),
            candidate_no=item.get("candidate_no"),
        )
        try:
            target = self._parse_and_validate(result["content"], candidates)
        except _ModeFailureSignal as failure:
            return failure.payload
        # real 模式：强制 query 为注入值（教师不得修改）
        if query is not None:
            target = {**target, "query": query}
        return {
            "input": model_input,
            "target": target,
            "model": {"name": "protocol-teacher", "revision": "v1"},
            "prompt_hash": _hash_of(prompt),
            "prompt_template_version": "memory-rerank-real-v1" if query is not None else "memory-rerank-v1",
            "config_hash": "config:memory-rerank-real:v1" if query is not None else "config:memory-rerank:v1",
            "seed": f"{item.get('seed', 0)}:{_MODE}",
            "source_event_ids": [c["memory_id"] for c in candidates],
            "support_spans": item.get("support_spans", []),
            "calls": call_executor.record_ids(),
        }

    def _build_real_mode(
        self,
        real_conv: dict[str, Any],
        scene: str,
        working_state: str,
        candidates: list[dict],
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        """从真实对话构造 query（不调用模型生成），返回 (query, prompt, model_input)。

        real_conv 字段：
          - messages: [{role, content}, ...]（ShareGPT 格式，含 system/human/gpt）
          - scene: 可选，覆盖外层 scene
        query 提取规则：
          - current_user_message = 最后一条 human 消息
          - recent_dialogue = 之前的 human/gpt 对话（最多 4 条，按顺序）
          - working_state = 外层 working_state（场景描述）
        """
        messages = real_conv.get("messages") or []
        humans = [m for m in messages if m.get("role") == "human"]
        if not humans:
            raise _ModeFailureSignal({
                _MODE_FAILURE: True,
                "error_code": "precondition_failed",
                "retryable": False,
                "reason": "real_conversation 无 human 消息",
            })
        current_msg = humans[-1].get("content", "")
        # recent_dialogue：最后一条 human 之前的对话（human/gpt 交替，取最近 4 条）
        prior = []
        for m in messages:
            if m is humans[-1]:
                break
            if m.get("role") in ("human", "gpt", "assistant"):
                prior.append(m.get("content", ""))
        recent = prior[-4:] if prior else []

        query = {
            "recent_dialogue": recent,
            "current_user_message": current_msg,
            "working_state": working_state,
        }
        # 渲染 real 模式 prompt（query 给定，教师只标 labels）
        query_text = (
            f"场景：{scene}\n"
            f"工作状态：{working_state}\n"
            f"最近对话：{recent}\n"
            f"当前消息：{current_msg}"
        )
        candidates_text = "\n".join(
            f"- memory_id={c['memory_id']} | {c['selector_text']}"
            for c in candidates
        )
        template = (self._prompts_dir / "memory_rerank_real.txt").read_text(encoding="utf-8")
        prompt = template.replace("{{QUERY}}", query_text).replace("{{CANDIDATES}}", candidates_text)
        model_input = {
            "scenario": {"scene": scene, "working_state": working_state},
            "candidates": candidates,
            "real_conversation": True,
        }
        return query, prompt, model_input

    def render_training(self, candidate: dict[str, Any], package_set: dict[str, Any]) -> TrainingRecordV4:
        messages = [
            {"role": "human", "content": json.dumps(candidate.get("input", {}), ensure_ascii=False, sort_keys=True, default=str)},
            {"role": "assistant", "content": json.dumps(candidate["target"], ensure_ascii=False)},
        ]
        return TrainingRecordV4(
            sample_id=candidate.get("sample_id", ""),
            mode=_MODE,
            render_profile_id=_RENDER_PROFILE,
            messages=messages,
            supervised_message_indexes=[1],
            supervised_token_count=estimate_token_count(messages, [1]),
            protocol_snapshot_id=self._protocol_snapshot_id,
            system_anchor=candidate.get("prompt_hash", ""),
        )

    # ───────────────────────── 内部 ─────────────────────────

    def _render_prompt(self, model_input: dict[str, Any]) -> str:
        template = (self._prompts_dir / "memory_rerank.txt").read_text(encoding="utf-8")
        scenario = model_input["scenario"]
        scenario_text = (
            f"场景：{scenario.get('scene', '')}\n"
            f"工作状态：{scenario.get('working_state', '')}"
        )
        candidates = "\n".join(
            f"- memory_id={c['memory_id']} | {c['selector_text']}"
            for c in model_input["candidates"]
        )
        return template.replace("{{SCENARIO}}", scenario_text).replace("{{CANDIDATES}}", candidates)

    def _parse_and_validate(self, raw: str, candidates: list[dict]) -> dict[str, Any]:
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
                    "reason": f"{_MODE} JSON 解析失败: {error}",
                }
            ) from error
        if not isinstance(target, dict):
            raise _ModeFailureSignal(
                {
                    _MODE_FAILURE: True,
                    "error_code": "schema_error",
                    "retryable": True,
                    "reason": f"{_MODE} 输出不是对象",
                }
            )
        validator = self._validator_for()
        errors = sorted(validator.iter_errors(target), key=lambda e: list(e.path))
        if errors:
            raise _ModeFailureSignal(
                {
                    _MODE_FAILURE: True,
                    "error_code": "schema_error",
                    "retryable": True,
                    "reason": f"{_MODE} 不满足冻结 schema: {errors[0].message}",
                }
            )
        # 确定性检查：labels 必须覆盖全部候选 memory_id（无遗漏、无编造）
        candidate_ids = {c["memory_id"] for c in candidates}
        labelled_ids = {str(l.get("memory_id")) for l in target.get("labels", [])}
        missing = candidate_ids - labelled_ids
        extra = labelled_ids - candidate_ids
        if missing or extra:
            detail = []
            if missing:
                detail.append(f"未标注: {sorted(missing)}")
            if extra:
                detail.append(f"编造 ID: {sorted(extra)}")
            raise _ModeFailureSignal(
                {
                    _MODE_FAILURE: True,
                    "error_code": "schema_error",
                    "retryable": True,
                    "reason": f"{_MODE} labels 覆盖不完整（{'；'.join(detail)}）",
                }
            )
        return target

    def _validator_for(self) -> Draft7Validator:
        if self._validator is None:
            schema = json.loads(
                (SCHEMAS_DIR / "mode_v4" / _TARGET_SCHEMA).read_text(encoding="utf-8")
            )
            self._validator = Draft7Validator(schema)
        return self._validator


class _ModeFailureSignal(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("reason", "")))
        self.payload = payload


def _hash_of(text: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
