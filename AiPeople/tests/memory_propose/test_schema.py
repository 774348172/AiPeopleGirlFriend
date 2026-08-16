from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from runtime._memory_propose import memory_propose_request_from_dict

from ._helpers import ROOT, request_dict, valid_cases


SCHEMA_DIR = ROOT / "runtime" / "schemas"


def _schema(name: str):
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_request_and_result_schemas_are_valid_draft_202012() -> None:
    for name in (
        "memory_propose_request_v1.schema.json",
        "memory_propose_result_v1.schema.json",
    ):
        Draft202012Validator.check_schema(_schema(name))


def test_valid_fixtures_pass_both_schemas() -> None:
    request_validator = Draft202012Validator(
        _schema("memory_propose_request_v1.schema.json"), format_checker=FormatChecker()
    )
    result_validator = Draft202012Validator(
        _schema("memory_propose_result_v1.schema.json"), format_checker=FormatChecker()
    )
    for case in valid_cases():
        request_validator.validate(case["request"])
        result_validator.validate(case["output"])
        memory_propose_request_from_dict(case["request"])


def test_schema_rejects_runtime_owned_fields_and_visible_text() -> None:
    result = valid_cases()[0]["output"]
    result["proposals"][0]["memory_id"] = "model-owned-id"
    errors = list(
        Draft202012Validator(_schema("memory_propose_result_v1.schema.json")).iter_errors(
            result
        )
    )
    assert errors

    result = {"schema_version": 1, "mode": "MEMORY_PROPOSE", "proposal_run_id": "x", "proposals": [], "reply": "我会记住"}
    errors = list(
        Draft202012Validator(_schema("memory_propose_result_v1.schema.json")).iter_errors(
            result
        )
    )
    assert errors


def test_request_schema_and_python_reject_empty_event_window() -> None:
    source = request_dict()
    source["events"] = []
    validator = Draft202012Validator(_schema("memory_propose_request_v1.schema.json"))
    assert list(validator.iter_errors(source))
