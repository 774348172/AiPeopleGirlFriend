from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from eval.chat01.blind import (
    BlindCandidate,
    create_blind_assignment,
    reveal_ballot,
    verify_blind_assignment,
)


ROOT = Path(__file__).resolve().parents[2]
CHAT01 = ROOT / "eval" / "chat01"
SUITES = CHAT01 / "suites"
HUMAN_SUITE = SUITES / "chat01_human_blind_v1.jsonl"
EXAMPLE_BALLOT = CHAT01 / "examples" / "human_ballot.valid.json"
DIMENSIONS = {
    "relevance",
    "correctness",
    "persona_fit",
    "relationship_naturalness",
    "emotion_fit",
    "naturalness",
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        _load_json(CHAT01 / "schema" / schema_name),
        format_checker=FormatChecker(),
    )


def test_quality_rubric_has_exactly_six_unique_fully_anchored_dimensions() -> None:
    rubric = _load_json(CHAT01 / "rubrics" / "quality_rubric_v1.json")
    _validator("quality_rubric.schema.json").validate(rubric)
    assert {item["dimension_id"] for item in rubric["dimensions"]} == DIMENSIONS
    assert all(set(item["anchors"]) == {"1", "2", "3", "4", "5"} for item in rubric["dimensions"])
    assert rubric["policy"]["blocker_precedence"] is True
    assert rubric["policy"]["keyword_persona_pass_forbidden"] is True


def test_manifest_rubric_assets_exist() -> None:
    manifest = _load_json(SUITES / "chat01_suite_manifest_v3.json")
    assert "eval/chat01/rubrics/quality_rubric_v1.json" in manifest["rubrics"]
    assert "eval/chat01/rubrics/human_blind_rubric_v1.md" in manifest["rubrics"]
    for relative_path in manifest["rubrics"]:
        assert (ROOT / relative_path).is_file()


def test_human_suite_validates_and_meets_pair_and_long_session_counts() -> None:
    records = _load_jsonl(HUMAN_SUITE)
    validator = _validator("case.schema.json")
    for record in records:
        validator.validate(record)
    counts = Counter(record["case_type"] for record in records)
    assert counts == {"human_pair": 60, "human_long_session": 8}
    assert len(records) == 68


def test_human_pair_mix_meets_low_drama_and_high_risk_contract() -> None:
    pairs = [
        record for record in _load_jsonl(HUMAN_SUITE) if record["case_type"] == "human_pair"
    ]
    classes = Counter(record["human_metadata"]["scenario_class"] for record in pairs)
    assert classes["ordinary"] >= 15
    assert classes["serious"] + classes["adversarial"] >= 12
    assert classes == {
        "ordinary": 20,
        "adversarial": 14,
        "serious": 8,
        "neutral": 18,
    }


def test_human_units_have_no_oracle_or_prewritten_assistant_answers() -> None:
    records = _load_jsonl(HUMAN_SUITE)
    for record in records:
        assert "oracle" not in record
        assert len(record["checks"]) == 1
        assert record["checks"][0]["type"] == "human_review"
        assert set(record["checks"][0]["dimensions"]) == DIMENSIONS
        if record["case_type"] == "human_pair":
            assert {message["role"] for message in record["messages"]} == {"user"}
        else:
            assert 30 <= record["human_brief"]["target_minutes"] <= 60
            assert "messages" not in record


def test_human_prompts_are_unique_and_not_exact_copies_of_other_splits() -> None:
    records = _load_jsonl(HUMAN_SUITE)
    pair_prompts = [record["messages"][0]["content"] for record in records if record["case_type"] == "human_pair"]
    assert len(pair_prompts) == len(set(pair_prompts))
    other_prompts: set[str] = set()
    for filename in (
        "chat01_dev_v1.jsonl",
        "chat01_frozen_single_v1.jsonl",
    ):
        for record in _load_jsonl(SUITES / filename):
            other_prompts.add(record["messages"][0]["content"])
    assert set(pair_prompts).isdisjoint(other_prompts)


def test_ballot_requires_all_six_candidate_scores_and_separate_failure_reasons() -> None:
    ballot = _load_json(EXAMPLE_BALLOT)
    validator = _validator("human_ballot.schema.json")
    validator.validate(ballot)
    assert set(ballot["dimension_scores"]) == DIMENSIONS
    assert set(ballot["failure_reasons"]) == {"candidate_a", "candidate_b"}

    missing_dimension = copy.deepcopy(ballot)
    del missing_dimension["dimension_scores"]["emotion_fit"]
    with pytest.raises(ValidationError):
        validator.validate(missing_dimension)


def test_both_unacceptable_requires_failure_reason_for_each_candidate() -> None:
    ballot = _load_json(EXAMPLE_BALLOT)
    ballot["verdict"] = "both_unacceptable"
    ballot["both_unacceptable"] = True
    ballot["failure_reasons"] = {
        "candidate_a": ["off_topic"],
        "candidate_b": ["assistant_tone"],
    }
    _validator("human_ballot.schema.json").validate(ballot)
    ballot["failure_reasons"]["candidate_a"] = []
    with pytest.raises(ValidationError):
        _validator("human_ballot.schema.json").validate(ballot)


def test_assignment_is_deterministic_blind_committed_and_revealable() -> None:
    candidate_1 = BlindCandidate("model-base", "opaque-output-001")
    candidate_2 = BlindCandidate("model-lora", "opaque-output-002")
    public, private = create_blind_assignment(
        unit_id="human.pair.ordinary_low_drama.01",
        first=candidate_1,
        second=candidate_2,
        seed=42,
    )
    repeated_public, repeated_private = create_blind_assignment(
        unit_id="human.pair.ordinary_low_drama.01",
        first=candidate_2,
        second=candidate_1,
        seed=42,
    )
    assert public == repeated_public
    assert private == repeated_private
    assert "model" not in json.dumps(public)
    assert verify_blind_assignment(public, private)

    tampered = copy.deepcopy(private)
    tampered["candidate_a_model_id"] = "model-tampered"
    assert not verify_blind_assignment(public, tampered)

    ballot = _load_json(EXAMPLE_BALLOT)
    ballot["unit_id"] = "human.pair.ordinary_low_drama.01"
    ballot.update(public)
    revealed = reveal_ballot(
        ballot, private, revealed_at="2026-08-05T19:30:00+08:00"
    )
    _validator("human_ballot.schema.json").validate(revealed)
    assert revealed["revealed"] is True
    assert revealed["reveal"]["candidate_a_model_id"] == private["candidate_a_model_id"]

    with pytest.raises(ValueError, match="earlier"):
        reveal_ballot(
            ballot, private, revealed_at="2026-08-05T18:59:59+08:00"
        )


def test_candidate_order_is_not_fixed_to_a_model_across_seeds() -> None:
    first = BlindCandidate("model-base", "opaque-output-001")
    second = BlindCandidate("model-lora", "opaque-output-002")
    model_a = {
        private["candidate_a_model_id"]
        for seed in range(32)
        for _public, private in [
            create_blind_assignment(
                unit_id=f"human.pair.test.{seed:02d}",
                first=first,
                second=second,
                seed=seed,
            )
        ]
    }
    assert model_a == {"model-base", "model-lora"}
