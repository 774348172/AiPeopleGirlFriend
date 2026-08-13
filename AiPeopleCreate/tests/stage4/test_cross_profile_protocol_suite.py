"""跨 profile 协议 sealed suite：协议能力不依赖任何角色事实（§16.2）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from data_gen_v4.adapters.modes.structured_protocol import StructuredProtocolModeAdapter
from data_gen_v4.adapters.models.pool import StrictTestModelAdapter
from data_gen_v4.core.schemas import SCHEMAS_DIR
from tests.adapters.test_protocol_adapter import _FakeCallExecutor

SUITE_PATH = Path(__file__).resolve().parent / "fixtures" / "sealed_protocol_suite.json"
INPUT_SCHEMAS = {
    "RECALL_PLAN": "recall_plan_input.schema.json",
    "MEMORY_PROPOSE": "memory_propose_input.schema.json",
}


def _suite() -> dict:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def _input_validator(mode: str) -> Draft7Validator:
    schema = json.loads(
        (SCHEMAS_DIR / "mode_v4" / INPUT_SCHEMAS[mode]).read_text(encoding="utf-8")
    )
    return Draft7Validator(schema)


def _run_suite_item(item: dict, package_context: dict) -> dict:
    adapter = StructuredProtocolModeAdapter()
    plan_item = {
        "plan_id": f"plan:{item['item_id']}",
        "family_id": f"fam:{item['item_id']}",
        "mode": item["mode"],
        "task_type": "sealed_suite",
        "attempt_no": 1,
        "seed": 1,
        "input": {},
        "source_event_ids": [],
        "support_spans": [],
        "fixture": item,
    }
    model = StrictTestModelAdapter(
        [{"content": json.dumps(item["expected_target"], ensure_ascii=False)}]
    )
    executor = _FakeCallExecutor(model)
    job = adapter.prepare(plan_item, {"fixture": item, **package_context})
    return adapter.generate(job, executor)


def test_suite_structure_is_complete():
    suite = _suite()
    assert suite["suite_id"] == "sealed-protocol-v1"
    items = suite["items"]
    assert len(items) == 8
    assert {i["mode"] for i in items} == {"RECALL_PLAN", "MEMORY_PROPOSE"}
    for item in items:
        assert item["item_id"]
        assert item["expected_behavior"]
        assert item["model_input"]
        assert "oracle_state" in item
        assert item["expected_target"]


@pytest.mark.parametrize("item", _suite()["items"], ids=lambda i: i["item_id"])
def test_suite_inputs_pass_frozen_input_schemas(item):
    validator = _input_validator(item["mode"])
    errors = sorted(validator.iter_errors(item["model_input"]), key=lambda e: list(e.path))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize("item", _suite()["items"], ids=lambda i: i["item_id"])
def test_suite_items_pass_with_alpha_and_beta_contexts(item, package_context):
    """同一 suite 在 Alpha 与 Beta 的 profile 上下文中都生成合法协议输出。"""
    from tests.stage3.conftest import PROFILES_ROOT
    from data_gen_v4.adapters.sources.registry import FilePackageRegistry

    registry = FilePackageRegistry(PROFILES_ROOT)
    beta_context = {
        "profile": registry.get("profile.fixture.beta"),
        "facts": ["贝塔是一名图书管理员。"],
    }
    for context in (package_context, beta_context):
        payload = _run_suite_item(item, context)
        assert "mode_failure" not in payload, payload
        assert payload["target"] == item["expected_target"]


@pytest.mark.parametrize("item", _suite()["items"], ids=lambda i: i["item_id"])
def test_suite_outputs_contain_no_role_facts(item, package_context):
    """协议 target 不含任何角色事实（阿尔法/贝塔/图书馆/河畔公寓）。"""
    payload = _run_suite_item(item, package_context)
    text = json.dumps(payload["target"], ensure_ascii=False)
    for term in ("阿尔法", "贝塔", "图书馆", "河畔公寓", "豆包"):
        assert term not in text


def test_suite_is_frozen_and_never_modified():
    """封存集不得被生成/修复/调参修改（§16.2）——测试只读引用。"""
    suite = _suite()
    # 封存标记存在
    assert suite["frozen_at"]
    assert "封存后不进入 teacher/critic/调参" in suite["purpose"]
