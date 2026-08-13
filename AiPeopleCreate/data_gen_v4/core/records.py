"""V4 追加式记录判别联合（《数据生成器v4设计》§8）。

V4Record = RunEvent | CallRecord | CandidateRecordV4 |
           FailureRecord | GateDecisionRecord

所有记录共享 LineageHeaderV4；to_dict/from_dict 以阶段 0 冻结的
record_v4.schema.json 为唯一校验依据。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .. import __version__
from .schemas import validate_record

SCHEMA_VERSION = "aip.record.v4"
RecordType = Literal["run", "call", "candidate", "failure", "gate_decision"]
RunEventName = Literal["run_started", "generation_completed", "gates_completed", "release_created"]
GateDecision = Literal["approved", "rejected", "needs_revision"]


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _record_id() -> str:
    return f"rec-{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True, slots=True)
class LineageHeaderV4:
    schema_version: str = SCHEMA_VERSION
    record_type: RecordType = "run"
    record_id: str = ""
    run_id: str = ""
    plan_id: str | None = None
    package_lock_hash: str = ""
    profile_id: str | None = None
    profile_snapshot_id: str | None = None
    protocol_bundle_id: str | None = None
    recipe_id: str | None = None
    mode: str | None = None
    task_type: str | None = None
    family_id: str | None = None
    generator_version: str = __version__
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "record_type": self.record_type,
            "record_id": self.record_id,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "package_lock_hash": self.package_lock_hash,
            "profile_id": self.profile_id,
            "profile_snapshot_id": self.profile_snapshot_id,
            "protocol_bundle_id": self.protocol_bundle_id,
            "recipe_id": self.recipe_id,
            "mode": self.mode,
            "task_type": self.task_type,
            "family_id": self.family_id,
            "generator_version": self.generator_version,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LineageHeaderV4:
        return cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            record_type=data.get("record_type", "run"),
            record_id=data["record_id"],
            run_id=data["run_id"],
            plan_id=data.get("plan_id"),
            package_lock_hash=data.get("package_lock_hash", ""),
            profile_id=data.get("profile_id"),
            profile_snapshot_id=data.get("profile_snapshot_id"),
            protocol_bundle_id=data.get("protocol_bundle_id"),
            recipe_id=data.get("recipe_id"),
            mode=data.get("mode"),
            task_type=data.get("task_type"),
            family_id=data.get("family_id"),
            generator_version=data.get("generator_version", __version__),
            created_at=data.get("created_at", ""),
        )

    def with_record(self, *, record_type: RecordType, record_id: str | None = None) -> LineageHeaderV4:
        return LineageHeaderV4(
            schema_version=self.schema_version,
            record_type=record_type,
            record_id=record_id or _record_id(),
            run_id=self.run_id,
            plan_id=self.plan_id,
            package_lock_hash=self.package_lock_hash,
            profile_id=self.profile_id,
            profile_snapshot_id=self.profile_snapshot_id,
            protocol_bundle_id=self.protocol_bundle_id,
            recipe_id=self.recipe_id,
            mode=self.mode,
            task_type=self.task_type,
            family_id=self.family_id,
            generator_version=self.generator_version,
            created_at=self.created_at or _utc_now(),
        )


@dataclass(frozen=True, slots=True)
class RunEvent:
    header: LineageHeaderV4
    event_name: RunEventName

    def to_dict(self) -> dict[str, Any]:
        data = self.header.to_dict()
        data["event_name"] = self.event_name
        return data


@dataclass(frozen=True, slots=True)
class CallRecord:
    header: LineageHeaderV4
    attempt_no: int
    candidate_no: int | None
    stage: str
    model: dict[str, Any]
    prompt_hash: str
    input_hash: str
    output_hash: str
    finish_reason: str
    seed: int | str
    # 大块 B（2026-08-07，阶段 2 P0-3）：原始外部响应全文（可重放，不重新请求）；
    # 失败调用为 ""（finish_reason 标记错误）
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = self.header.to_dict()
        data.update(
            {
                "attempt_no": self.attempt_no,
                "candidate_no": self.candidate_no,
                "stage": self.stage,
                "model": self.model,
                "prompt_hash": self.prompt_hash,
                "input_hash": self.input_hash,
                "output_hash": self.output_hash,
                "finish_reason": self.finish_reason,
                "seed": self.seed,
                "content": self.content,
            }
        )
        return data


@dataclass(frozen=True, slots=True)
class CandidateRecordV4:
    header: LineageHeaderV4
    sample_id: str
    family_id: str
    question_family_id: str | None
    scene_family_id: str | None
    mode: str
    task_type: str
    profile_id: str
    profile_snapshot_id: str
    protocol_bundle_id: str
    recipe_id: str
    attempt_no: int
    candidate_no: int
    parent_sample_id: str | None
    fixture_id: str | None
    fixture_hash: str | None
    source_refs: list[dict[str, Any]]
    source_event_ids: list[str]
    support_spans: list[dict[str, Any]]
    knowledge_scope: list[str]
    visibility_scope: list[str]
    evidence_state: str
    desired_policy: str
    required_behaviors: list[str]
    forbidden_behaviors: list[str]
    expected_outcomes: list[dict[str, Any]]
    input: dict[str, Any]
    target: dict[str, Any]
    model: dict[str, Any]
    prompt_hash: str
    prompt_template_version: str
    config_hash: str
    render_profile_id: str
    representation_ids: list[str]
    seed: int | str
    review_status: Literal["pending"] = "pending"
    provenance: dict[str, Any] = field(default_factory=lambda: {"calls": []})
    # 阶段 3（C-1）：结构化 claim（教师自报命题注册；空 = 未声明，跳过 claim 级校验）
    claims: list[dict[str, Any]] = field(default_factory=list)
    # 阶段 3（C-3）：质量向量（judge soft 分：instruction_fulfillment/
    # persona_naturalness/relationship_fit/conversational_progress/style_restraint；
    # 空 = 未打分）
    quality: dict[str, Any] = field(default_factory=dict)
    # 阶段 4（D-2）：split 锚（engine 从 plan item 透传；manifest 从 ledger 重建）
    split_anchor_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = self.header.to_dict()
        data.update(
            {
                "sample_id": self.sample_id,
                "family_id": self.family_id,
                "question_family_id": self.question_family_id,
                "scene_family_id": self.scene_family_id,
                "mode": self.mode,
                "task_type": self.task_type,
                "profile_id": self.profile_id,
                "profile_snapshot_id": self.profile_snapshot_id,
                "protocol_bundle_id": self.protocol_bundle_id,
                "recipe_id": self.recipe_id,
                "attempt_no": self.attempt_no,
                "candidate_no": self.candidate_no,
                "parent_sample_id": self.parent_sample_id,
                "fixture_id": self.fixture_id,
                "fixture_hash": self.fixture_hash,
                "source_refs": self.source_refs,
                "source_event_ids": self.source_event_ids,
                "support_spans": self.support_spans,
                "knowledge_scope": self.knowledge_scope,
                "visibility_scope": self.visibility_scope,
                "evidence_state": self.evidence_state,
                "desired_policy": self.desired_policy,
                "required_behaviors": self.required_behaviors,
                "forbidden_behaviors": self.forbidden_behaviors,
                "expected_outcomes": self.expected_outcomes,
                "input": self.input,
                "target": self.target,
                "model": self.model,
                "prompt_hash": self.prompt_hash,
                "prompt_template_version": self.prompt_template_version,
                "config_hash": self.config_hash,
                "render_profile_id": self.render_profile_id,
                "representation_ids": self.representation_ids,
                "seed": self.seed,
                "review_status": self.review_status,
                "provenance": self.provenance,
                "claims": self.claims,
                "quality": self.quality,
                "split_anchor_ids": self.split_anchor_ids,
            }
        )
        return data


@dataclass(frozen=True, slots=True)
class FailureRecord:
    header: LineageHeaderV4
    attempt_no: int
    candidate_no: int | None
    stage: str
    error_code: str
    retryable: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        data = self.header.to_dict()
        data.update(
            {
                "attempt_no": self.attempt_no,
                "candidate_no": self.candidate_no,
                "stage": self.stage,
                "error_code": self.error_code,
                "retryable": self.retryable,
                "reason": self.reason,
            }
        )
        return data


@dataclass(frozen=True, slots=True)
class GateDecisionRecord:
    header: LineageHeaderV4
    subject_sample_id: str
    subject_candidate_record_id: str
    gate_id: str
    decision: GateDecision
    reason_codes: list[str]
    validator_id: str
    reviewer: str | None
    reviewed_at: str

    def to_dict(self) -> dict[str, Any]:
        data = self.header.to_dict()
        data.update(
            {
                "subject_sample_id": self.subject_sample_id,
                "subject_candidate_record_id": self.subject_candidate_record_id,
                "gate_id": self.gate_id,
                "decision": self.decision,
                "reason_codes": self.reason_codes,
                "validator_id": self.validator_id,
                "reviewer": self.reviewer,
                "reviewed_at": self.reviewed_at,
            }
        )
        return data


V4Record = RunEvent | CallRecord | CandidateRecordV4 | FailureRecord | GateDecisionRecord


def record_to_dict(record: V4Record) -> dict[str, Any]:
    return record.to_dict()


def record_from_dict(data: dict[str, Any]) -> V4Record:
    """按 record_type 判别构造记录，并做 schema 校验。"""
    validate_record(data)
    record_type = data["record_type"]
    if record_type == "run":
        return RunEvent(header=LineageHeaderV4.from_dict(data), event_name=data["event_name"])
    if record_type == "call":
        return CallRecord(
            header=LineageHeaderV4.from_dict(data),
            attempt_no=data["attempt_no"],
            candidate_no=data.get("candidate_no"),
            stage=data["stage"],
            model=data["model"],
            prompt_hash=data["prompt_hash"],
            input_hash=data["input_hash"],
            output_hash=data["output_hash"],
            finish_reason=data["finish_reason"],
            seed=data["seed"],
            content=data.get("content", ""),
        )
    if record_type == "candidate":
        return CandidateRecordV4(
            header=LineageHeaderV4.from_dict(data),
            sample_id=data["sample_id"],
            family_id=data["family_id"],
            question_family_id=data.get("question_family_id"),
            scene_family_id=data.get("scene_family_id"),
            mode=data["mode"],
            task_type=data["task_type"],
            profile_id=data["profile_id"],
            profile_snapshot_id=data["profile_snapshot_id"],
            protocol_bundle_id=data["protocol_bundle_id"],
            recipe_id=data["recipe_id"],
            attempt_no=data["attempt_no"],
            candidate_no=data["candidate_no"],
            parent_sample_id=data.get("parent_sample_id"),
            fixture_id=data.get("fixture_id"),
            fixture_hash=data.get("fixture_hash"),
            source_refs=data["source_refs"],
            source_event_ids=data["source_event_ids"],
            support_spans=data["support_spans"],
            knowledge_scope=data["knowledge_scope"],
            visibility_scope=data["visibility_scope"],
            evidence_state=data["evidence_state"],
            desired_policy=data["desired_policy"],
            required_behaviors=data["required_behaviors"],
            forbidden_behaviors=data["forbidden_behaviors"],
            expected_outcomes=data["expected_outcomes"],
            input=data["input"],
            target=data["target"],
            model=data["model"],
            prompt_hash=data["prompt_hash"],
            prompt_template_version=data["prompt_template_version"],
            config_hash=data["config_hash"],
            render_profile_id=data["render_profile_id"],
            representation_ids=data["representation_ids"],
            seed=data["seed"],
            review_status=data["review_status"],
            provenance=data.get("provenance", {"calls": []}),
            claims=data.get("claims", []),
            quality=data.get("quality", {}),
            split_anchor_ids=data.get("split_anchor_ids", []),
        )
    if record_type == "failure":
        return FailureRecord(
            header=LineageHeaderV4.from_dict(data),
            attempt_no=data["attempt_no"],
            candidate_no=data.get("candidate_no"),
            stage=data["stage"],
            error_code=data["error_code"],
            retryable=data["retryable"],
            reason=data["reason"],
        )
    if record_type == "gate_decision":
        return GateDecisionRecord(
            header=LineageHeaderV4.from_dict(data),
            subject_sample_id=data["subject_sample_id"],
            subject_candidate_record_id=data["subject_candidate_record_id"],
            gate_id=data["gate_id"],
            decision=data["decision"],
            reason_codes=data["reason_codes"],
            validator_id=data["validator_id"],
            reviewer=data.get("reviewer"),
            reviewed_at=data["reviewed_at"],
        )
    raise ValueError(f"未知 record_type: {record_type}")
