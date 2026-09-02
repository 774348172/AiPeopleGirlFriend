from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

from data_gen_v4.adapters.models.pool import ModelPool, StrictTestModelAdapter
from data_gen_v4.core.engine import GenerationEngineV4
from data_gen_v4.core.plan import GenerationPlanV4, PlanItemV4
from data_gen_v4.core.sink import MemorySink
from data_gen_v4.runtime_grounded import RuntimeGroundedReplyAdapter

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "runtime_grounded_golden_v1.yaml"


class ScriptedCallExecutor:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[dict, dict]] = []

    def call(self, spec: dict, **metadata) -> dict:
        self.calls.append((spec, metadata))
        return self.responses[len(self.calls) - 1]

    def record_ids(self) -> list[str]:
        return [f"call-{index + 1}" for index in range(len(self.calls))]


def _scenario() -> dict:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["scenarios"][0]


def _teacher_and_audit() -> tuple[dict, dict]:
    current_claim = {
        "subject": "protagonist",
        "predicate": "dentist_appointment",
        "object": "星期六上午十点",
        "polarity": "positive",
        "temporal_status": "current",
        "source_fact_ids": ["fact.dentist.sat"],
    }
    stale_denial = {
        "subject": "protagonist",
        "predicate": "dentist_appointment",
        "object": "星期五下午",
        "polarity": "negative",
        "temporal_status": "current",
        "source_fact_ids": ["fact.dentist.fri"],
    }
    teacher = {
        "reply": "知道了，已经改到星期六上午十点，不是星期五下午。",
        "declared_claims": [current_claim, stale_denial],
    }
    audit = {
        "extracted_claims": copy.deepcopy(teacher["declared_claims"]),
        "verdicts": {gate: True for gate in ("RG5", "RG6", "RG7", "RG8", "RG9", "RG10")},
        "decision": "approve",
        "reasons": ["通过"],
    }
    return teacher, audit


def _package(scenario: dict) -> dict:
    return {
        "profile": {"profile_id": "baiweixi", "display_name": "白未晞"},
        "protocol": {"package_version": "v5", "reply_runtime_rules": ["只说出口的话。"]},
        "runtime_grounded_scenarios": {scenario["scenario_id"]: scenario},
    }


def _item(scenario: dict) -> dict:
    return {
        "fixture_id": scenario["scenario_id"],
        "attempt_no": 1,
        "candidate_no": 1,
        "seed": 42,
    }


def test_adapter_runs_teacher_then_independent_audit_and_renders_training() -> None:
    scenario = _scenario()
    teacher, audit = _teacher_and_audit()
    executor = ScriptedCallExecutor(
        [
            {"content": json.dumps(teacher, ensure_ascii=False), "finish_reason": "stop"},
            {"content": json.dumps(audit, ensure_ascii=False), "finish_reason": "stop"},
        ]
    )
    adapter = RuntimeGroundedReplyAdapter()
    package = _package(scenario)
    payload = adapter.generate(adapter.prepare(_item(scenario), package), executor)

    assert not payload.get("mode_failure")
    assert len(executor.calls) == 2
    assert executor.calls[0][1]["stage"] == "runtime_grounded_teacher"
    assert executor.calls[1][1]["stage"] == "runtime_grounded_audit"
    assert executor.calls[0][0]["temperature"] == 0.7
    assert executor.calls[1][0]["temperature"] == 0.0
    assert payload["target"]["eligible_for_training"] is True
    assert payload["target"]["runtime_grounded_gate_report"]["decision"] == "approve"
    assert payload["calls"] == ["call-1", "call-2"]

    training_text = payload["target"]["messages"][0]["content"]
    assert "required_assertions" not in training_text
    assert "oracle_view" not in training_text
    assert "available_actions" not in training_text
    assert "required_assertions" in executor.calls[0][0]["messages"][0]["content"]
    assert "required_assertions" in executor.calls[1][0]["messages"][0]["content"]

    candidate = {
        **payload,
        "fixture_id": scenario["scenario_id"],
        "sample_id": "candidate-v5-1",
    }
    record = adapter.render_training(candidate, package)
    assert record.sample_id == "candidate-v5-1"
    assert record.supervised_message_indexes == [1]
    assert record.messages[-1]["content"] == teacher["reply"]


def test_adapter_rejects_non_json_teacher_without_calling_audit() -> None:
    scenario = _scenario()
    executor = ScriptedCallExecutor([{"content": "not json", "finish_reason": "stop"}])
    adapter = RuntimeGroundedReplyAdapter()
    payload = adapter.generate(adapter.prepare(_item(scenario), _package(scenario)), executor)
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "schema_error"
    assert len(executor.calls) == 1


def test_adapter_rejects_more_than_two_teacher_generations_per_scenario() -> None:
    scenario = _scenario()
    item = {**_item(scenario), "candidate_count": 2, "max_attempts": 2}
    executor = ScriptedCallExecutor([])
    adapter = RuntimeGroundedReplyAdapter()
    payload = adapter.generate(adapter.prepare(item, _package(scenario)), executor)
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "precondition_failed"
    assert executor.calls == []


def test_adapter_rejects_failed_semantic_hard_gate() -> None:
    scenario = _scenario()
    teacher, audit = _teacher_and_audit()
    audit["extracted_claims"] = []
    audit["verdicts"]["RG5"] = False
    audit["decision"] = "reject"
    executor = ScriptedCallExecutor(
        [
            {"content": json.dumps(teacher, ensure_ascii=False), "finish_reason": "stop"},
            {"content": json.dumps(audit, ensure_ascii=False), "finish_reason": "stop"},
        ]
    )
    adapter = RuntimeGroundedReplyAdapter()
    payload = adapter.generate(adapter.prepare(_item(scenario), _package(scenario)), executor)
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "unsupported_claim"
    assert payload["retryable"] is True


def test_v4_engine_executes_runtime_grounded_mode_without_core_changes() -> None:
    scenario = _scenario()
    teacher, audit = _teacher_and_audit()
    teacher_model = StrictTestModelAdapter(
        [{"content": json.dumps(teacher, ensure_ascii=False), "finish_reason": "stop"}]
    )
    audit_model = StrictTestModelAdapter(
        [{"content": json.dumps(audit, ensure_ascii=False), "finish_reason": "stop"}]
    )
    pool = ModelPool(default_id="runtime-grounded-teacher")
    pool.register("runtime-grounded-teacher", teacher_model)
    pool.register("runtime-grounded-audit", audit_model)
    item = PlanItemV4(
        family_id="family:dentist",
        question_family_id="question:dentist",
        scene_family_id="scene:home",
        mode="RUNTIME_GROUNDED_REPLY",
        task_type=scenario["task_type"],
        family_role="standalone",
        knowledge_scope=["runtime_evidence"],
        visibility_scope=["profile_public"],
        evidence_state="supported",
        desired_policy="answer",
        required_behaviors=[],
        forbidden_behaviors=[],
        expected_outcomes=[],
        refusal_required=False,
        fixture_id=scenario["scenario_id"],
        fixture_hash="sha256:" + "a" * 64,
        representation_ids=[],
        render_profile_id="runtime-grounded-reply-v1",
        prompt_template_version="runtime-grounded-reply-audit-v1",
        config_hash="config:runtime-grounded:v1",
        seed=42,
        candidate_count=1,
        max_attempts=1,
        risk_level="high",
        required_review="auto",
        split_anchor_ids=list(scenario["split_anchors"]),
    )
    lock_hash = "sha256:" + "b" * 64
    plan = GenerationPlanV4.build(
        run_id="run-v5-adapter",
        profile_id="baiweixi",
        profile_snapshot_id="snapshot-profile-v5",
        protocol_bundle_id="protocol-v5",
        recipe_id="recipe-v5",
        package_lock_hash=lock_hash,
        items=[item],
        plan_id="plan-v5-adapter",
    )
    package = _package(scenario)
    engine = GenerationEngineV4(
        {"RUNTIME_GROUNDED_REPLY": RuntimeGroundedReplyAdapter()},
        package_context=package,
    )
    sink = MemorySink()
    result = engine.execute(plan, {"lock_hash": lock_hash}, pool, sink)
    assert result.completed == 1
    progress = sink.read_progress("run-v5-adapter")
    assert len(progress.candidates) == 1
    assert progress.candidates[0].target["eligible_for_training"] is True
    assert len(progress.candidates[0].provenance["calls"]) == 2
