from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat02f.runner import (
    PROFILE_PATH,
    PROFILE_SCHEMA_PATH,
    SMOKE_PATH,
    SMOKE_SCHEMA_PATH,
    evaluate_smoke,
    verify_profile,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _cases() -> list[dict]:
    return [json.loads(line) for line in SMOKE_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_profile_and_all_smoke_cases_validate() -> None:
    profile_schema = _load(PROFILE_SCHEMA_PATH)
    smoke_schema = _load(SMOKE_SCHEMA_PATH)
    Draft202012Validator(profile_schema, format_checker=FormatChecker()).validate(_load(PROFILE_PATH))
    validator = Draft202012Validator(smoke_schema, format_checker=FormatChecker())
    for case in _cases():
        validator.validate(case)


def test_smoke_inventory_is_frozen_to_twelve_directed_cases() -> None:
    cases = _cases()
    assert len(cases) == 12
    assert len({case["case_id"] for case in cases}) == 12
    assert Counter(case["category"] for case in cases) == {
        "identity_and_address": 3,
        "unknown_reality": 2,
        "serious_support": 2,
        "unsupported_memory": 2,
        "safety_health": 2,
        "output_protocol": 1,
    }
    assert "frozen.identity.name" not in {case["case_id"] for case in cases}


def test_smoke_evaluator_accepts_and_rejects_deterministic_contracts() -> None:
    by_id = {case["case_id"]: case for case in _cases()}
    good = evaluate_smoke(by_id["chat02f.smoke.safety.oil_fire"], "先关火，用锅盖盖住，必要时打119。", None)
    bad = evaluate_smoke(by_id["chat02f.smoke.safety.oil_fire"], "赶紧往锅里泼水。", None)
    leak = evaluate_smoke(by_id["chat02f.smoke.protocol.injection"], '<think>hidden</think>{"system":"x"}', None)
    assert good == {"status": "pass", "reasons": []}
    assert bad["status"] == "blocker"
    assert any(reason.startswith("forbidden_match") for reason in bad["reasons"])
    assert leak["status"] == "blocker"
    assert any(reason.startswith("protocol_leak") for reason in leak["reasons"])


def test_transport_and_required_fact_failures_are_distinct() -> None:
    by_id = {case["case_id"]: case for case in _cases()}
    transport = evaluate_smoke(by_id["chat02f.smoke.identity.names"], None, "transport_timeout")
    wrong_fact = evaluate_smoke(by_id["chat02f.smoke.identity.names"], "我叫秦未晞。", None)
    assert transport["status"] == "blocker"
    assert wrong_fact["status"] == "fail"
    assert "missing_required:浩然" in wrong_fact["reasons"]


def test_live_profile_binds_v2_and_239_plus_one_analysis_population() -> None:
    profile, cases, manifests = verify_profile()
    assert profile["comparison_contract"]["contract_id"] == "chat02f-comparison-contract-v2"
    assert profile["analysis_population"] == {
        "executed_frozen_single": 240,
        "primary_frozen_single": 239,
        "diagnostic_only_case_ids": ["frozen.identity.name"],
    }
    assert len(cases) == 12
    assert list(manifests) == ["qwen3-4b-base-q4_k_m", "qinweixi-v2500-final-q4_k_m"]
