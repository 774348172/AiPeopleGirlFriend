"""record 判别联合：序列化/反序列化往返与 schema 校验。"""
from __future__ import annotations

import pytest

from data_gen_v4.core.errors import SchemaValidationError
from data_gen_v4.core.records import (
    CallRecord,
    CandidateRecordV4,
    FailureRecord,
    GateDecisionRecord,
    LineageHeaderV4,
    RunEvent,
    record_from_dict,
)


def _header(record_type: str = "run") -> LineageHeaderV4:
    return LineageHeaderV4(
        record_type=record_type,
        record_id="rec-1",
        run_id="run-1",
        plan_id="plan-1:0000",
        package_lock_hash="sha256:" + "a" * 64,
        profile_id="fixture.alpha",
        profile_snapshot_id="sha256:" + "b" * 64,
        protocol_bundle_id="protocol:fixture:rel",
        recipe_id="recipe:fixture:basic",
        mode="REPLY",
        task_type="casual",
        family_id="fam:1",
        created_at="2026-08-04T00:00:00Z",
    )


def _candidate() -> CandidateRecordV4:
    return CandidateRecordV4(
        header=_header("candidate"),
        sample_id="plan-1:0000:a1:c1",
        family_id="fam:1",
        question_family_id="qf:casual",
        scene_family_id=None,
        mode="REPLY",
        task_type="casual",
        profile_id="fixture.alpha",
        profile_snapshot_id="sha256:" + "b" * 64,
        protocol_bundle_id="protocol:fixture:rel",
        recipe_id="recipe:fixture:basic",
        attempt_no=1,
        candidate_no=1,
        parent_sample_id=None,
        fixture_id=None,
        fixture_hash=None,
        source_refs=[],
        source_event_ids=[],
        support_spans=[],
        knowledge_scope=["general_knowledge"],
        visibility_scope=["profile_public"],
        evidence_state="not_required",
        desired_policy="answer",
        required_behaviors=[],
        forbidden_behaviors=[],
        expected_outcomes=[],
        input={"scene": "日常"},
        target={"messages": [{"role": "human", "content": "你好。"}]},
        model={"name": "fixture-model"},
        prompt_hash="sha256:" + "c" * 64,
        prompt_template_version="reply-fixture-v1",
        config_hash="config:fixture:1",
        render_profile_id="reply-runtime-v1",
        representation_ids=[],
        seed=7,
    )


@pytest.mark.parametrize(
    "record",
    [
        RunEvent(header=_header("run"), event_name="run_started"),
        CallRecord(
            header=_header("call"), attempt_no=1, candidate_no=1, stage="semantic",
            model={"name": "m"}, prompt_hash="ph", input_hash="ih",
            output_hash="oh", finish_reason="stop", seed=1,
        ),
        _candidate(),
        FailureRecord(
            header=_header("failure"), attempt_no=1, candidate_no=None,
            stage="generate", error_code="timeout", retryable=True, reason="超时",
        ),
        GateDecisionRecord(
            header=_header("gate_decision"),
            subject_sample_id="s1", subject_candidate_record_id="rec-1",
            gate_id="G2", decision="approved", reason_codes=[],
            validator_id="validator:fixture", reviewer="human-1",
            reviewed_at="2026-08-04T00:00:00Z",
        ),
    ],
)
def test_record_roundtrip(record):
    restored = record_from_dict(record.to_dict())
    assert restored == record
    assert restored.to_dict() == record.to_dict()


def test_candidate_review_status_is_always_pending():
    candidate = _candidate()
    assert candidate.review_status == "pending"
    assert candidate.to_dict()["review_status"] == "pending"


def test_candidate_review_status_cannot_be_approved():
    data = _candidate().to_dict()
    data["review_status"] = "approved"
    with pytest.raises(SchemaValidationError):
        record_from_dict(data)


def test_candidate_missing_visibility_scope_is_rejected():
    data = _candidate().to_dict()
    del data["visibility_scope"]
    with pytest.raises(SchemaValidationError):
        record_from_dict(data)


def test_failure_error_code_must_be_registered():
    data = FailureRecord(
        header=_header("failure"), attempt_no=1, candidate_no=None,
        stage="generate", error_code="unknown_error", retryable=False, reason="x",
    ).to_dict()
    with pytest.raises(SchemaValidationError):
        record_from_dict(data)


def test_gate_decision_decision_enum():
    data = GateDecisionRecord(
        header=_header("gate_decision"),
        subject_sample_id="s1", subject_candidate_record_id="rec-1",
        gate_id="G7", decision="maybe", reason_codes=[], validator_id="v",
        reviewer=None, reviewed_at="2026-08-04T00:00:00Z",
    ).to_dict()
    with pytest.raises(SchemaValidationError):
        record_from_dict(data)


def test_run_event_name_enum():
    with pytest.raises(SchemaValidationError):
        record_from_dict(
            RunEvent(header=_header("run"), event_name="random_event").to_dict()
        )


def test_unknown_record_type_rejected():
    data = _candidate().to_dict()
    data["record_type"] = "unknown"
    with pytest.raises(SchemaValidationError):
        record_from_dict(data)


def test_candidate_requires_full_lineage():
    data = _candidate().to_dict()
    del data["profile_snapshot_id"]
    with pytest.raises(SchemaValidationError):
        record_from_dict(data)


def test_calls_must_be_list_of_ids():
    data = _candidate().to_dict()
    data["provenance"] = {"calls": "not-a-list"}
    with pytest.raises(SchemaValidationError):
        record_from_dict(data)
