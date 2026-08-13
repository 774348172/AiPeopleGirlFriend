"""StructuredProtocolModeAdapter：schema 校验、NO_OP、oracle 隔离。"""
from __future__ import annotations

import pytest

from data_gen_v4.adapters.modes.structured_protocol import StructuredProtocolModeAdapter
from data_gen_v4.adapters.models.pool import StrictTestModelAdapter
from tests.adapters.conftest import (
    MEMORY_NOOP_SCRIPT,
    MEMORY_PROPOSE_SCRIPT,
    RECALL_NOOP_SCRIPT,
    RECALL_SEARCH_SCRIPT,
)


class _FakeCallExecutor:
    def __init__(self, adapter: StrictTestModelAdapter) -> None:
        self._adapter = adapter
        self.specs: list[dict] = []

    def call(self, spec: dict, *, stage: str, attempt_no: int, candidate_no: int | None = None) -> dict:
        self.specs.append(spec)
        return self._adapter.generate(spec)

    def record_ids(self) -> list[str]:
        return [f"fake-call-{i}" for i in range(len(self.specs))]


def _run(script, mode, item=None, package_set=None):
    adapter = StructuredProtocolModeAdapter()
    model = StrictTestModelAdapter(script)
    executor = _FakeCallExecutor(model)
    fixture = package_set.get("fixture") if package_set else None
    item = item or {
        "plan_id": "plan-1:0000",
        "family_id": "fam:1",
        "mode": mode,
        "task_type": "ambiguous_recall",
        "attempt_no": 1,
        "seed": 7,
        "input": {},
        "source_event_ids": [],
        "support_spans": [],
        "fixture": fixture,
    }
    job = adapter.prepare(item, package_set or {})
    return adapter.generate(job, executor), executor


def _recall_fixture():
    return {
        "model_input": {
            "mode_system_input": "mode=RECALL_PLAN",
            "visible_cues": [{"cue_type": "topic", "text": "上次那家餐厅"}],
            "current_input": "你还记得上次我们说的那家餐厅吗？",
        },
        "oracle_state": {"events": [{"event_id": "ev-1", "text": "秘密事件不该进 prompt"}]},
    }


def test_recall_plan_noop_passes_schema():
    payload, _ = _run(RECALL_NOOP_SCRIPT, "RECALL_PLAN", package_set={"fixture": _recall_fixture()})
    assert "mode_failure" not in payload
    assert payload["target"] == {"action": "NO_OP", "reason": "current_context_is_sufficient"}


def test_recall_plan_search_passes_schema():
    payload, _ = _run(RECALL_SEARCH_SCRIPT, "RECALL_PLAN", package_set={"fixture": _recall_fixture()})
    assert payload["target"]["action"] == "search"
    assert payload["target"]["queries"][0]["terms"] == ["餐厅"]


def test_oracle_never_enters_prompt():
    _, executor = _run(RECALL_NOOP_SCRIPT, "RECALL_PLAN", package_set={"fixture": _recall_fixture()})
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "秘密事件不该进 prompt" not in prompt
    assert "ev-1" not in prompt
    assert "上次那家餐厅" in prompt


def test_memory_propose_passes_schema():
    fixture = {
        "model_input": {
            "mode_system_input": "mode=MEMORY_PROPOSE",
            "committed_events": [{"event_id": "ev-1", "actor": "user", "text": "我喜欢喝无糖豆浆。"}],
            "current_projection": {},
        },
        "oracle_state": {},
    }
    payload, _ = _run(MEMORY_PROPOSE_SCRIPT, "MEMORY_PROPOSE", package_set={"fixture": fixture})
    assert "mode_failure" not in payload
    candidate = payload["target"]["candidates"][0]
    assert candidate["status"] == "proposed"
    assert candidate["evidence"][0]["event_id"] == "ev-1"


def test_memory_noop_allowed():
    fixture = {
        "model_input": {
            "mode_system_input": "mode=MEMORY_PROPOSE",
            "committed_events": [],
            "current_projection": {},
        },
        "oracle_state": {},
    }
    payload, _ = _run(MEMORY_NOOP_SCRIPT, "MEMORY_PROPOSE", package_set={"fixture": fixture})
    assert payload["target"]["action"] == "NO_OP"


def test_invalid_action_rejected_by_schema():
    script = [{"content": '{"action": "delete", "candidates": []}'}]
    payload, _ = _run(script, "MEMORY_PROPOSE", package_set={"fixture": {"model_input": {
        "mode_system_input": "m", "committed_events": [], "current_projection": {}}}})
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "schema_error"


def test_parse_failure_returns_parse_error():
    script = [{"content": "这不是 JSON"}]
    payload, _ = _run(script, "RECALL_PLAN", package_set={"fixture": _recall_fixture()})
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "parse_error"


def test_missing_fixture_is_precondition_failure():
    payload, _ = _run(RECALL_NOOP_SCRIPT, "RECALL_PLAN", package_set={})
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "precondition_failed"


def test_unsupported_mode_is_not_registered():
    item = {
        "plan_id": "p1", "family_id": "f1", "mode": "PLAN_PROPOSE",
        "task_type": "x", "attempt_no": 1, "seed": 1, "input": {},
        "source_event_ids": [], "support_spans": [],
    }
    payload, _ = _run(RECALL_NOOP_SCRIPT, "PLAN_PROPOSE", item=item)
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "mode_not_registered"


def test_render_training_supervises_only_json_target():
    fixture = _recall_fixture()
    payload, _ = _run(RECALL_NOOP_SCRIPT, "RECALL_PLAN", package_set={"fixture": fixture})
    adapter = StructuredProtocolModeAdapter()
    candidate = dict(payload, sample_id="s1", mode="RECALL_PLAN")
    record = adapter.render_training(candidate, {})
    assert record.supervised_message_indexes == [1]
    assert record.messages[0]["role"] == "human"
    assert record.messages[1]["role"] == "assistant"
    assert '"action": "NO_OP"' in record.messages[1]["content"]
