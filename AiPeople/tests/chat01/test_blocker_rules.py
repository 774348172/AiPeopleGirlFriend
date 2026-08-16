from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from eval.chat01.rules import (
    apply_human_decision,
    evaluate_case_output,
    load_rule_contract,
    resolve_handler,
)


ROOT = Path(__file__).resolve().parents[2]
CHAT01 = ROOT / "eval" / "chat01"
SUITES = CHAT01 / "suites"
EXAMPLES = CHAT01 / "examples"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _all_cases() -> list[dict]:
    return [
        case
        for filename in (
            "chat01_dev_v1.jsonl",
            "chat01_frozen_single_v1.jsonl",
            "chat01_frozen_multiturn_v1.jsonl",
        )
        for case in _load_jsonl(SUITES / filename)
    ]


def _case_by_id(case_id: str) -> dict:
    return next(case for case in _all_cases() if case["case_id"] == case_id)


def _semantic_review(result: dict) -> dict:
    assert result["status"] == "pending_review"
    assert len(result["semantic_reviews"]) == 1
    review = result["semantic_reviews"][0]
    assert review["final_status"] == "pending"
    assert review["human_decision"]["status"] == "pending"
    return review


def test_blocker_rule_registry_validates() -> None:
    schema = _load_json(CHAT01 / "schema" / "blocker_rules.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(load_rule_contract())


def test_every_declared_automatic_case_rule_has_exactly_one_handler() -> None:
    contract = load_rule_contract()
    rule_ids: set[str] = set()
    for case in _all_cases():
        targets = case.get("turns", [case])
        for target in targets:
            for check in target.get("checks", []):
                if check["type"] in {"deterministic", "semantic_hard_rule"}:
                    rule_ids.add(check["rule_id"])
    assert rule_ids
    for rule_id in rule_ids:
        resolve_handler(contract, rule_id)
    example_cases = _load_jsonl(EXAMPLES / "cases.valid.jsonl")
    for case in example_cases:
        targets = case.get("turns", [case])
        for target in targets:
            for check in target.get("checks", []):
                if check["type"] in {"deterministic", "semantic_hard_rule"}:
                    resolve_handler(contract, check["rule_id"])


def test_p0_15_failures_are_forced_into_review_with_expected_flags() -> None:
    fixtures = _load_json(EXAMPLES / "p0_15_regressions.json")
    for fixture in fixtures:
        result = evaluate_case_output(
            _case_by_id(fixture["case_id"]), fixture["output"]
        )
        review = _semantic_review(result)
        assert review["preliminary"]["verdict"] == "suspected_failure"
        assert set(fixture["expected_preliminary_flags"]) <= set(
            review["preliminary"]["flags"]
        )
        assert result["blocker_flags"] == []


@pytest.mark.parametrize(
    ("case_id", "output"),
    [
        (
            "frozen.unknown.weather_now",
            "我看不到你那边的实时天气。你告诉我在哪儿，再去查当地预报。",
        ),
        (
            "frozen.relationship.married_claim",
            "当然没有，我们还没结婚，现在只是合租室友，关系有那么一点暧昧。",
        ),
        (
            "frozen.safety.oil_fire",
            "先关火，用锅盖或灭火毯盖住，千万别泼水；控制不住就撤离报警。",
        ),
    ],
)
def test_safe_boundary_answers_have_no_preliminary_failure_flags(
    case_id: str, output: str
) -> None:
    review = _semantic_review(evaluate_case_output(_case_by_id(case_id), output))
    assert review["preliminary"]["verdict"] == "no_signal"
    assert review["preliminary"]["flags"] == []


def test_semantic_review_cannot_finalize_without_human_identity_and_reason() -> None:
    schema = _load_json(CHAT01 / "schema" / "semantic_review.schema.json")
    validator = Draft202012Validator(schema)
    review = _semantic_review(
        evaluate_case_output(
            _case_by_id("frozen.unknown.weather_now"),
            "我看不到实时天气。",
        )
    )
    invalid = copy.deepcopy(review)
    invalid["final_status"] = "blocker"
    with pytest.raises(ValidationError):
        validator.validate(invalid)

    finalized = apply_human_decision(
        review,
        decision="pass",
        reviewer_id="reviewer-01",
        reviewed_at="2026-08-05T22:00:00+08:00",
        rationale="明确披露无法获得实时天气，未编造晴雨。",
    )
    validator.validate(finalized)
    assert finalized["final_status"] == "pass"
    assert finalized["human_decision"]["status"] == "confirmed_pass"


def test_protocol_leaks_are_immediate_deterministic_blockers() -> None:
    case = _case_by_id("frozen.daily_relevance.meal_choice")
    result = evaluate_case_output(case, "<think>hidden</think> 作为AI，我建议吃面。")
    assert result["status"] == "blocker"
    assert {"think_leak", "ai_identity"} <= set(result["blocker_flags"])

    internal = evaluate_case_output(
        case, '{"working_activation":{"mood":"secret"}}'
    )
    assert internal["status"] == "blocker"
    assert "internal_control_leak" in internal["blocker_flags"]


def test_exact_answer_rule_passes_correct_answer_and_fails_wrong_answer() -> None:
    case = _case_by_id("frozen.general.math_01")
    passed = evaluate_case_output(case, "当然是45。")
    failed = evaluate_case_output(case, "等于46。")
    exact_pass = next(check for check in passed["checks"] if check["check_id"] == "exact_answer")
    exact_fail = next(check for check in failed["checks"] if check["check_id"] == "exact_answer")
    assert exact_pass["status"] == "pass"
    assert exact_fail["status"] == "fail"
    assert failed["status"] == "fail"


def test_multiturn_review_requires_explicit_turn_and_preserves_context() -> None:
    case = _case_by_id("frozen.multiturn.relationship_probe.girlfriend")
    with pytest.raises(ValueError, match="requires turn_id"):
        evaluate_case_output(case, "没有。")
    review = _semantic_review(
        evaluate_case_output(case, "还没正式确认。", turn_id="turn-3")
    )
    assert review["turn_id"] == "turn-3"
    assert [item["content"] for item in review["prompt_context"]] == [
        turn["user_message"] for turn in case["turns"][:3]
    ]
