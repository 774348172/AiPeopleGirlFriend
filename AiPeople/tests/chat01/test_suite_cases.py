from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator

from eval.chat01.build_suites import (
    _build_dev,
    _build_frozen_single,
    _build_human_blind,
    _build_multiturn,
)


ROOT = Path(__file__).resolve().parents[2]
CHAT01 = ROOT / "eval" / "chat01"
SUITES = CHAT01 / "suites"
MANIFEST = SUITES / "chat01_suite_manifest_v3.json"
SUITE_PATHS = {
    "dev": SUITES / "chat01_dev_v1.jsonl",
    "frozen_single": SUITES / "chat01_frozen_single_v1.jsonl",
    "frozen_multiturn": SUITES / "chat01_frozen_multiturn_v1.jsonl",
    "human_blind": SUITES / "chat01_human_blind_v1.jsonl",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        assert line.strip(), f"blank line at {path}:{line_number}"
        records.append(json.loads(line))
    return records


def _manifest_asset(split: str) -> dict:
    manifest = _load_json(MANIFEST)
    return next(asset for asset in manifest["assets"] if asset["split"] == split)


def test_checked_in_suites_match_deterministic_builder() -> None:
    assert _load_jsonl(SUITE_PATHS["dev"]) == _build_dev()
    assert _load_jsonl(SUITE_PATHS["frozen_single"]) == _build_frozen_single()
    assert _load_jsonl(SUITE_PATHS["frozen_multiturn"]) == _build_multiturn()
    assert _load_jsonl(SUITE_PATHS["human_blind"]) == _build_human_blind()


def test_all_chat01c_cases_validate_against_contract() -> None:
    validator = Draft202012Validator(
        _load_json(CHAT01 / "schema" / "case.schema.json")
    )
    for path in SUITE_PATHS.values():
        for case in _load_jsonl(path):
            validator.validate(case)


def test_manifest_minimum_case_counts_are_met() -> None:
    for split, path in SUITE_PATHS.items():
        records = _load_jsonl(path)
        assert len(records) >= _manifest_asset(split)["minimum_cases"]


def test_manifest_points_to_checked_in_frozen_chat01_assets() -> None:
    for split, path in SUITE_PATHS.items():
        asset = _manifest_asset(split)
        assert ROOT / asset["path"] == path
        assert path.is_file()
        assert len(asset["sha256"]) == 64


def test_builder_refuses_to_overwrite_frozen_v1() -> None:
    from eval.chat01.build_suites import build

    try:
        build()
    except RuntimeError as exc:
        assert "frozen" in str(exc)
    else:
        raise AssertionError("frozen CHAT-01 v1 was silently overwritten")


def test_frozen_single_category_distribution_matches_contract() -> None:
    records = _load_jsonl(SUITE_PATHS["frozen_single"])
    counts = Counter(record["category"] for record in records)
    minimums = _manifest_asset("frozen_single")["minimum_counts"]["categories"]
    assert counts == Counter(minimums)
    assert len(records) == 240
    assert {record["case_type"] for record in records} == {"single_turn"}


def test_multiturn_family_distribution_and_turn_bounds_match_contract() -> None:
    records = _load_jsonl(SUITE_PATHS["frozen_multiturn"])
    counts = Counter(record["scenario_family"] for record in records)
    minimums = _manifest_asset("frozen_multiturn")["minimum_counts"][
        "scenario_families"
    ]
    assert counts == Counter(minimums)
    assert len(records) == 30
    assert all(4 <= len(record["turns"]) <= 8 for record in records)
    assert all(record["case_type"] == "multi_turn" for record in records)
    for record in records:
        for index, turn in enumerate(record["turns"]):
            assert len(turn["allowed_prior_facts"]) == index
            assert all(
                prior["user_message"] in fact
                for prior, fact in zip(
                    record["turns"][:index], turn["allowed_prior_facts"], strict=True
                )
            )


def test_ids_and_leakage_groups_are_unique() -> None:
    records = [
        record
        for path in SUITE_PATHS.values()
        for record in _load_jsonl(path)
    ]
    case_ids = [record["case_id"] for record in records]
    leakage_groups = [record["leakage_group"] for record in records]
    assert len(case_ids) == len(set(case_ids))
    assert len(leakage_groups) == len(set(leakage_groups))


def test_single_turn_prompts_are_unique_and_do_not_cross_splits() -> None:
    dev_prompts = {
        record["messages"][0]["content"]
        for record in _load_jsonl(SUITE_PATHS["dev"])
    }
    frozen_records = _load_jsonl(SUITE_PATHS["frozen_single"])
    frozen_prompts = [record["messages"][0]["content"] for record in frozen_records]
    assert len(frozen_prompts) == len(set(frozen_prompts))
    assert dev_prompts.isdisjoint(frozen_prompts)


def test_frozen_assets_do_not_contain_retired_identity_anchors() -> None:
    frozen_text = "\n".join(
        path.read_text(encoding="utf-8")
        for split, path in SUITE_PATHS.items()
        if split.startswith("frozen_") or split == "human_blind"
    )
    assert "23岁" not in frozen_text
    assert "25岁" not in frozen_text
    assert "大叔" not in frozen_text


def test_experience_cases_use_three_seeds_and_hard_cases_keep_reviewable_oracles() -> None:
    records = [
        record
        for path in SUITE_PATHS.values()
        for record in _load_jsonl(path)
    ]
    for case in records:
        if case["generation"]["lane"] == "experience":
            assert len(case["generation"]["seed_set"]) >= 3
        if case["case_type"] == "single_turn" and case["risk"] == "blocker":
            assert case["oracle"]["required_behaviors"]
            assert case["oracle"]["forbidden_claims"]
