from __future__ import annotations

from datetime import datetime, timezone

from eval.chat02.runner import ProviderResult, _aggregate, _evaluate_result


def test_human_pair_output_is_transport_valid_but_not_human_scored() -> None:
    case = {
        "case_id": "human.pair.test",
        "case_type": "human_pair",
        "category": "style",
        "scenario_family": "test",
        "messages": [{"role": "user", "content": "今天挺累。"}],
        "checks": [{"type": "human_review", "dimensions": ["relevance", "naturalness"]}],
        "generation": {"lane": "experience", "max_new_tokens": 32, "seed_set": [11]},
        "risk": "normal",
    }
    item, reviews = _evaluate_result(
        run_id="chat02-test", suite_id="chat01-qinweixi-v2", model_id="model-test",
        case=case, attempt_id="human.pair.test.s11", output="那就先歇会儿。",
        result=ProviderResult("那就先歇会儿。", 10.0, 20.0, 30, 8), seed=11,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    assert item["status"] == "pass"
    assert reviews == []
    assert any(check["kind"] == "human_review" and check["status"] == "not_run" for check in item["checks"])


def test_aggregate_preserves_attempt_and_item_denominators() -> None:
    cases = [{"case_id": "case.one", "category": "style", "scenario_family": "family"}]
    base = {
        "case_id": "case.one", "status": "pass", "error_code": None,
        "blocker_flags": [],
        "metrics": {"first_token_ms": 10.0, "total_ms": 20.0, "output_tokens": 4},
    }
    aggregate = _aggregate("run.test", "chat01-qinweixi-v2", cases, [base, {**base, "status": "fail"}])
    assert aggregate["item_denominator"] == 1
    assert aggregate["attempt_denominator"] == 2
    assert aggregate["item_counts"]["fail"] == 1
    assert aggregate["attempt_counts"] == {
        "pass": 1, "fail": 1, "blocker": 0, "invalid": 0, "error": 0, "skipped": 0
    }
