"""计划状态不得混入 MEMORY_PROPOSE（§10.3）：schema 层 + 行为层双重验证。"""
from __future__ import annotations

import json
from pathlib import Path

from data_gen_v4.adapters.modes.structured_protocol import StructuredProtocolModeAdapter
from data_gen_v4.adapters.models.pool import StrictTestModelAdapter
from tests.adapters.test_protocol_adapter import _FakeCallExecutor

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "data_gen_v4" / "schemas" / "mode_v4"
ALLOWED_CLAIM_TYPES = {"fact", "preference", "relationship", "episode", "correction"}


def _target_schema(mode: str) -> dict:
    return json.loads(
        (SCHEMAS_DIR / f"{mode}_target.schema.json").read_text(encoding="utf-8")
    )


def _fixture() -> dict:
    return {
        "model_input": {
            "mode_system_input": "mode=MEMORY_PROPOSE",
            "committed_events": [{"event_id": "ev-1", "actor": "user", "text": "明晚一起看电影吧。"}],
            "current_projection": {},
        },
        "oracle_state": {},
    }


def _run_with_target(target: dict) -> dict:
    adapter = StructuredProtocolModeAdapter()
    model = StrictTestModelAdapter([{"content": json.dumps(target, ensure_ascii=False)}])
    executor = _FakeCallExecutor(model)
    plan_item = {
        "plan_id": "plan:p1", "family_id": "fam:p1", "mode": "MEMORY_PROPOSE",
        "task_type": "sealed", "attempt_no": 1, "seed": 1, "input": {},
        "source_event_ids": [], "support_spans": [], "fixture": _fixture(),
    }
    job = adapter.prepare(plan_item, {"fixture": _fixture()})
    return adapter.generate(job, executor)


def test_schema_claim_types_are_role_data_only():
    """claim_type 枚举只含事实/偏好/关系/情节/修正，不含任何计划类（§10.3）。"""
    schema = _target_schema("memory_propose")
    claim_types = set(
        schema["properties"]["candidates"]["items"]["properties"]["claim_type"]["enum"]
    )
    assert claim_types == ALLOWED_CLAIM_TYPES
    assert not (claim_types & {"plan", "promise", "plan_state", "reminder"})


def test_schema_has_no_plan_fields():
    """target 与 input schema 均无 plan 建立/取消/完成/错过字段。"""
    target = _target_schema("memory_propose")
    target_text = json.dumps(target, ensure_ascii=False)
    for field in ("plan_id", "plan_state", "due_at", "cancel", "complete_plan"):
        assert field not in target_text
    inp = json.loads(
        (SCHEMAS_DIR / "memory_propose_input.schema.json").read_text(encoding="utf-8")
    )
    input_text = json.dumps(inp, ensure_ascii=False)
    for field in ("plan_id", "plan_state"):
        assert field not in input_text


def test_plan_smuggling_candidate_is_rejected_by_schema():
    """模型试图把计划转换偷渡进 MEMORY_PROPOSE → schema_error（行为层验证）。"""
    smuggled = {
        "action": "propose",
        "candidates": [
            {
                "claim_type": "plan",
                "subject": "user",
                "predicate": "plans_to",
                "object": "去公园",
                "status": "proposed",
                "evidence": [],
            }
        ],
    }
    payload = _run_with_target(smuggled)
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "schema_error"
    assert "not one of" in payload["reason"]
    assert "plan" in payload["reason"]


def test_promise_like_candidate_is_rejected():
    smuggled = {
        "action": "propose",
        "candidates": [
            {
                "claim_type": "promise",
                "subject": "user",
                "predicate": "promises",
                "object": "明天还书",
                "status": "proposed",
                "evidence": [],
            }
        ],
    }
    payload = _run_with_target(smuggled)
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "schema_error"


def test_legitimate_episode_candidate_still_passes():
    """合法候选（episode 类）不受影响——防误伤。"""
    legitimate = {
        "action": "propose",
        "candidates": [
            {
                "claim_type": "episode",
                "subject": "user",
                "predicate": "suggests_outing",
                "object": "公园",
                "status": "proposed",
                "evidence": [{"event_id": "ev-1", "role": "support", "excerpt_start": 0, "excerpt_end": 4}],
            }
        ],
    }
    payload = _run_with_target(legitimate)
    assert "mode_failure" not in payload
    assert payload["target"]["candidates"][0]["claim_type"] == "episode"
