"""V4 生成计划数据结构（《数据生成器v4设计》§5、§9）。

RunSpec / PackageSetV4 是编译输入；GenerationPlanV4 / PlanItemV4 是
不可变编译产物，to_dict/from_dict 以阶段 0 冻结的 plan_v4.schema.json 为校验依据。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from .schemas import validate_plan, validate_plan_item


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _plan_id() -> str:
    return f"plan-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True, slots=True)
class RunSpec:
    """一次生成运行的调用方输入。"""

    run_id: str
    seed: int = 42
    created_at: str = field(default_factory=_utc_now)
    # 大块 A（2026-08-07）：真实模型/导出器 pin 进 lock（发布真实性，lock 全覆盖）
    model_pin: dict[str, Any] | None = None
    exporter_pin: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PackageSetV4:
    """本次运行绑定的四类 package 引用（§5）。

    package_lock_hash 不是调用方输入：lock 由 GenerationPlanCompiler 在编译时
    产出（§5.5、§7.1），调用方在 execute/resume 时回传同一 lock 即可。
    """

    profile_package_ref: str
    protocol_bundle_ref: str
    dataset_recipe_ref: str
    release_policy_ref: str


@dataclass(frozen=True, slots=True)
class PlanItemV4:
    family_id: str
    question_family_id: str | None
    scene_family_id: str | None
    mode: str
    task_type: str
    family_role: str
    knowledge_scope: list[str]
    visibility_scope: list[str]
    evidence_state: str
    desired_policy: str
    required_behaviors: list[str]
    forbidden_behaviors: list[str]
    expected_outcomes: list[dict[str, Any]]
    refusal_required: bool
    fixture_id: str | None
    fixture_hash: str | None
    representation_ids: list[str]
    render_profile_id: str
    prompt_template_version: str
    config_hash: str
    seed: int | str
    candidate_count: int
    max_attempts: int
    risk_level: str
    required_review: str
    split_anchor_ids: list[str]
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    support_spans: list[dict[str, Any]] = field(default_factory=list)
    input: dict[str, Any] = field(default_factory=dict)
    plan_id: str | None = None
    profile_id: str | None = None
    profile_snapshot_id: str | None = None
    protocol_bundle_id: str | None = None
    recipe_id: str | None = None
    # 记忆类型轴（2026-08-14 §24）：persona/item/general/special；None=未标注
    memory_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "profile_id": self.profile_id,
            "profile_snapshot_id": self.profile_snapshot_id,
            "protocol_bundle_id": self.protocol_bundle_id,
            "recipe_id": self.recipe_id,
            "family_id": self.family_id,
            "question_family_id": self.question_family_id,
            "scene_family_id": self.scene_family_id,
            "mode": self.mode,
            "task_type": self.task_type,
            "family_role": self.family_role,
            "source_refs": self.source_refs,
            "support_spans": self.support_spans,
            "knowledge_scope": self.knowledge_scope,
            "visibility_scope": self.visibility_scope,
            "memory_type": self.memory_type,
            "evidence_state": self.evidence_state,
            "desired_policy": self.desired_policy,
            "required_behaviors": self.required_behaviors,
            "forbidden_behaviors": self.forbidden_behaviors,
            "expected_outcomes": self.expected_outcomes,
            "refusal_required": self.refusal_required,
            "fixture_id": self.fixture_id,
            "fixture_hash": self.fixture_hash,
            "representation_ids": self.representation_ids,
            "render_profile_id": self.render_profile_id,
            "prompt_template_version": self.prompt_template_version,
            "config_hash": self.config_hash,
            "seed": self.seed,
            "candidate_count": self.candidate_count,
            "max_attempts": self.max_attempts,
            "risk_level": self.risk_level,
            "required_review": self.required_review,
            "split_anchor_ids": self.split_anchor_ids,
            "input": self.input,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanItemV4:
        validate_plan_item(data)
        return cls(
            family_id=data["family_id"],
            question_family_id=data.get("question_family_id"),
            scene_family_id=data.get("scene_family_id"),
            mode=data["mode"],
            task_type=data["task_type"],
            family_role=data["family_role"],
            knowledge_scope=data["knowledge_scope"],
            visibility_scope=data["visibility_scope"],
            memory_type=data.get("memory_type"),
            evidence_state=data["evidence_state"],
            desired_policy=data["desired_policy"],
            required_behaviors=data["required_behaviors"],
            forbidden_behaviors=data["forbidden_behaviors"],
            expected_outcomes=data["expected_outcomes"],
            refusal_required=data["refusal_required"],
            fixture_id=data.get("fixture_id"),
            fixture_hash=data.get("fixture_hash"),
            representation_ids=data["representation_ids"],
            render_profile_id=data["render_profile_id"],
            prompt_template_version=data["prompt_template_version"],
            config_hash=data["config_hash"],
            seed=data["seed"],
            candidate_count=data["candidate_count"],
            max_attempts=data["max_attempts"],
            risk_level=data["risk_level"],
            required_review=data["required_review"],
            split_anchor_ids=data["split_anchor_ids"],
            source_refs=data.get("source_refs", []),
            support_spans=data.get("support_spans", []),
            input=data.get("input", {}),
            plan_id=data.get("plan_id"),
            profile_id=data.get("profile_id"),
            profile_snapshot_id=data.get("profile_snapshot_id"),
            protocol_bundle_id=data.get("protocol_bundle_id"),
            recipe_id=data.get("recipe_id"),
        )


@dataclass(frozen=True, slots=True)
class GenerationPlanV4:
    plan_id: str
    run_id: str
    profile_id: str
    profile_snapshot_id: str
    protocol_bundle_id: str
    recipe_id: str
    package_lock_hash: str
    created_at: str
    items: list[PlanItemV4]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "profile_id": self.profile_id,
            "profile_snapshot_id": self.profile_snapshot_id,
            "protocol_bundle_id": self.protocol_bundle_id,
            "recipe_id": self.recipe_id,
            "package_lock_hash": self.package_lock_hash,
            "created_at": self.created_at,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationPlanV4:
        plan = cls(
            plan_id=data["plan_id"],
            run_id=data["run_id"],
            profile_id=data["profile_id"],
            profile_snapshot_id=data["profile_snapshot_id"],
            protocol_bundle_id=data["protocol_bundle_id"],
            recipe_id=data["recipe_id"],
            package_lock_hash=data["package_lock_hash"],
            created_at=data["created_at"],
            items=[PlanItemV4.from_dict(item) for item in data["items"]],
        )
        validate_plan(data)
        return plan

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        profile_id: str,
        profile_snapshot_id: str,
        protocol_bundle_id: str,
        recipe_id: str,
        package_lock_hash: str,
        items: list[PlanItemV4],
        plan_id: str | None = None,
    ) -> GenerationPlanV4:
        plan_id = plan_id or _plan_id()
        # plan_id 是 item 级标识（幂等键与进度跟踪以 item.plan_id 为单位），
        # 未显式赋值的 item 由 build 分配确定性编号；plan 级 lineage 字段同步填充
        # 到 item（编译产物必须完整，plan schema 不允许 null）。
        numbered: list[PlanItemV4] = []
        for index, item in enumerate(items):
            if item.plan_id is None:
                item = replace(item, plan_id=f"{plan_id}:{index:04d}")
            if item.profile_id is None:
                item = replace(item, profile_id=profile_id)
            if item.profile_snapshot_id is None:
                item = replace(item, profile_snapshot_id=profile_snapshot_id)
            if item.protocol_bundle_id is None:
                item = replace(item, protocol_bundle_id=protocol_bundle_id)
            if item.recipe_id is None:
                item = replace(item, recipe_id=recipe_id)
            numbered.append(item)
        plan = cls(
            plan_id=plan_id,
            run_id=run_id,
            profile_id=profile_id,
            profile_snapshot_id=profile_snapshot_id,
            protocol_bundle_id=protocol_bundle_id,
            recipe_id=recipe_id,
            package_lock_hash=package_lock_hash,
            created_at=_utc_now(),
            items=numbered,
        )
        validate_plan(plan.to_dict())
        return plan
