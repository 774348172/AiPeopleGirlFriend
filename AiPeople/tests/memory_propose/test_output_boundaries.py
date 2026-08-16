from __future__ import annotations

import json

import pytest

from runtime._memory_propose import (
    MEMORY_PROPOSE_LIMITS,
    MemoryProposeError,
    canonical_memory_propose_result_json,
    parse_memory_propose_output,
)

from ._helpers import EVAL_DIR, load_json, valid_cases
from runtime._memory_propose import memory_propose_request_from_dict


def test_valid_outputs_parse_and_empty_result_is_first_class_success() -> None:
    parsed = []
    for case in valid_cases():
        request = memory_propose_request_from_dict(case["request"])
        raw = json.dumps(case["output"], ensure_ascii=False)
        result = parse_memory_propose_output(raw, request)
        parsed.append(result)
        assert json.loads(canonical_memory_propose_result_json(result)) == case["output"]
    assert any(result.proposals == () for result in parsed)


def test_invalid_fixtures_fail_with_exact_codes() -> None:
    cases = load_json(EVAL_DIR / "fixtures" / "invalid" / "mode_cases_v1.json")
    for case in cases:
        request = memory_propose_request_from_dict(case["request"])
        with pytest.raises(MemoryProposeError) as caught:
            parse_memory_propose_output(case["raw_output"], request)
        assert caught.value.code == case["expected_error"], case["case_id"]
        assert case["raw_output"] not in str(caught.value)


def test_output_byte_limit_is_checked_before_json_parsing() -> None:
    request = memory_propose_request_from_dict(valid_cases()[0]["request"])
    raw = b"x" * (MEMORY_PROPOSE_LIMITS["output_bytes_max"] + 1)
    with pytest.raises(MemoryProposeError) as caught:
        parse_memory_propose_output(raw, request)
    assert caught.value.code == "memory_propose_output_too_large"

    with pytest.raises(MemoryProposeError) as caught:
        parse_memory_propose_output("\ud800", request)
    assert caught.value.code == "memory_propose_invalid_json"


def test_duplicate_proposals_are_rejected_as_one_failed_batch() -> None:
    case = valid_cases()[0]
    output = case["output"]
    output["proposals"].append(output["proposals"][0])
    request = memory_propose_request_from_dict(case["request"])
    with pytest.raises(MemoryProposeError) as caught:
        parse_memory_propose_output(json.dumps(output, ensure_ascii=False), request)
    assert caught.value.code == "memory_propose_contract_invalid"
