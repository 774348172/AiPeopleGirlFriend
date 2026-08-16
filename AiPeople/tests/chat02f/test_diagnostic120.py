from __future__ import annotations

import json

from eval.diagnostic120.build_cases import EXCLUDED_CASE_IDS, QUOTAS, SOURCE_PATH, select_cases
from eval.diagnostic120.runner import MODEL_ORDER, _blind_pairs


def _source_cases() -> list[dict]:
    return [json.loads(line) for line in SOURCE_PATH.read_text(encoding="utf-8").splitlines() if line]


def test_selection_is_stratified_unique_and_excludes_contaminated_case() -> None:
    selected = select_cases(_source_cases())

    assert len(selected) == 120
    assert len({case["case_id"] for case in selected}) == 120
    assert not ({case["case_id"] for case in selected} & EXCLUDED_CASE_IDS)
    assert {
        category: sum(case["category"] == category for case in selected)
        for category in QUOTAS
    } == QUOTAS
    assert all(case["generation"]["seed_set"] == [42] for case in selected)


def test_blind_pairs_are_complete_and_balanced() -> None:
    cases = select_cases(_source_cases())
    outputs = {
        model_id: [
            {"case_id": case["case_id"], "output": f"{model_id}:{case['case_id']}"}
            for case in cases
        ]
        for model_id in MODEL_ORDER
    }

    public, private = _blind_pairs(cases, outputs)

    assert len(public) == len(private) == 120
    assert all("model" not in json.dumps(item, ensure_ascii=False).lower() for item in public)
    a_counts = {
        model_id: sum(item["candidate_a_model"] == model_id for item in private)
        for model_id in MODEL_ORDER
    }
    assert abs(a_counts[MODEL_ORDER[0]] - a_counts[MODEL_ORDER[1]]) <= 12
