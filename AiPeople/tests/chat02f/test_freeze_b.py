from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import FreezeVerificationError
from eval.chat02f.freeze import (
    CONTRACT_PATH,
    CONTRACT_SCHEMA,
    REPORT_PATH,
    REPORT_SCHEMA,
    freeze_current,
    verify_frozen,
)


ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_frozen_report_and_contract_validate_and_verify_live_assets() -> None:
    report = _load(REPORT_PATH)
    contract = verify_frozen()
    for value, schema_path in ((report, REPORT_SCHEMA), (contract, CONTRACT_SCHEMA)):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def test_suite_v3_and_case_inventory_are_frozen() -> None:
    contract = _load(CONTRACT_PATH)
    assert contract["suite"]["suite_id"] == "chat01-qinweixi-v3"
    assert contract["suite"]["manifest"]["sha256"] == "745f0ef6bc50cab7da1915953265ae7ca718130b05fc123611b4061ff2aed369"
    assert contract["suite"]["manifest_self_sha256"] == "3cead71fea2dab18911560ac51e5da50ae98ce243b1a90ec3f53a9b39e742061"
    assert contract["case_inventory"] == {
        "frozen_single": 240,
        "frozen_multiturn": 30,
        "human_pair": 60,
        "human_long_session": 8,
    }


def test_candidate_corpus_hashes_and_provenance_limit_are_explicit() -> None:
    report = _load(REPORT_PATH)
    sources = {source["path"]: source for source in report["training_sources"]}
    assert sources["training_package_m3_v2/data/qin_v4_2500.jsonl"]["sha256"] == "0323637f7d24aedaf0b911c5f14a3776b8edf495e5ef5b9c1c6757ac9b5da9ed"
    assert sources["training_package_m3_v2/data/train.jsonl"]["sha256"] == "8110619a38cc007b4125bc53585d44f21c83fe43e9af026ccf684b1fc1257e80"
    assert sources["training_package_m3_v2/data/valid.jsonl"]["sha256"] == "d41dee54ad7fd3daf0e4457658f68902f29931945463cfc6d5c62f887f9f457b"
    assert [sources[path]["records"] for path in sources] == [2500, 2323, 266]
    assert report["corpus_provenance"]["status"] == "local_candidate_unverified"
    assert report["corpus_provenance"]["strict_training_attribution"] is False


def test_exact_leak_is_preserved_and_blocks_next_stage() -> None:
    report = _load(REPORT_PATH)
    contract = _load(CONTRACT_PATH)
    assert report["status"] == "blocked"
    assert report["summary"] == {
        "evaluation_texts": 532,
        "training_records": 5089,
        "training_user_texts": 13739,
        "exact": 1,
        "normalized": 0,
        "near_duplicate": 0,
        "blocking_findings": 1,
    }
    finding = report["findings"][0]
    assert finding["kind"] == "exact"
    assert finding["evaluation"]["record_id"] == "frozen.identity.name.message-1"
    assert finding["training"]["path"] == "training_package_m3_v2/data/qin_v4_2500.jsonl"
    assert finding["training"]["line"] == 237
    assert finding["evaluation"]["text_sha256"] == finding["training"]["text_sha256"]
    assert contract["status"] == "frozen_blocked"
    assert contract["gates"]["can_start_chat02f_c"] is False
    assert contract["gates"]["blockers"]


def test_comparison_binds_only_base_and_v2500_with_shared_prompt() -> None:
    contract = _load(CONTRACT_PATH)
    assert contract["only_active_variable"] == "model_weights"
    assert [model["model_id"] for model in contract["models"]] == [
        "qwen3-4b-base-q4_k_m",
        "qinweixi-v2500-final-q4_k_m",
    ]
    assert contract["prompt_renderer"]["path"] == "runtime/_prompt.py"
    assert contract["prompt_renderer"]["sha256"] == "1ba13513f6fcc449acdbd59f8f477730a1b775c9906347b2178cae0f07325567"
    assert contract["run_order"]["sequence"] == [
        "qwen3-4b-base-q4_k_m",
        "qinweixi-v2500-final-q4_k_m",
    ]


def test_prior_chat02e_human_assets_are_excluded_from_new_denominators() -> None:
    excluded = _load(CONTRACT_PATH)["excluded_prior_human_assets"]
    assert excluded["policy"] == "excluded_from_chat02f_denominators_and_assignments"
    assert [root["path"] for root in excluded["roots"]] == [
        "eval/chat02/human/chat02e-v1/public",
        "eval/chat02/human/chat02e-v1/private",
        "eval/chat02/human/chat02e-v1/submissions",
    ]
    assert [root["files"] for root in excluded["roots"]] == [3, 2, 80]


def test_frozen_v1_refuses_overwrite() -> None:
    with pytest.raises(FreezeVerificationError, match="already exists"):
        freeze_current()
