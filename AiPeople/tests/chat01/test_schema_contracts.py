from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[2]
CHAT01 = ROOT / "eval" / "chat01"
SCHEMA_DIR = CHAT01 / "schema"
EXAMPLES_DIR = CHAT01 / "examples"
MANIFEST_PATH = CHAT01 / "suites" / "chat01_suite_manifest_v5.json"
CANON_SNAPSHOT_PATH = CHAT01 / "suites" / "canon_snapshot_v4.json"
CANON_DRIFT_PATH = CHAT01 / "suites" / "canon_drift_v3.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(schema_name: str) -> Draft202012Validator:
    schema = _load_json(SCHEMA_DIR / schema_name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise AssertionError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
    return records


@pytest.mark.parametrize(
    "schema_name",
    [
        "case.schema.json",
        "suite_manifest.schema.json",
        "auto_result.schema.json",
        "human_ballot.schema.json",
        "canon_snapshot.schema.json",
        "canon_drift.schema.json",
        "blocker_rules.schema.json",
        "semantic_review.schema.json",
        "quality_rubric.schema.json",
        "leakage_report.schema.json",
    ],
)
def test_schema_is_valid_draft_2020_12(schema_name: str) -> None:
    _validator(schema_name)


def test_valid_case_examples_parse_as_jsonl_and_validate() -> None:
    validator = _validator("case.schema.json")
    records = _load_jsonl(EXAMPLES_DIR / "cases.valid.jsonl")
    assert {record["case_type"] for record in records} == {
        "single_turn",
        "multi_turn",
        "human_long_session",
    }
    for record in records:
        validator.validate(record)


def test_frozen_suite_manifest_validates_and_declares_all_splits() -> None:
    manifest = _load_json(MANIFEST_PATH)
    _validator("suite_manifest.schema.json").validate(manifest)
    assert manifest["status"] == "frozen"
    assert manifest["freeze"]["hash_algorithm"] == "sha256"
    assert manifest["freeze"]["manifest_hash_mode"] == "canonical_json_with_null_self"
    assert manifest["freeze"]["immutable"] is True
    assert len(manifest["freeze"]["manifest_sha256"]) == 64
    assert {asset["split"] for asset in manifest["assets"]} == {
        "dev",
        "frozen_single",
        "frozen_multiturn",
        "human_blind",
    }


def test_manifest_schema_paths_and_frozen_suite_hashes_exist() -> None:
    manifest = _load_json(MANIFEST_PATH)
    for relative_path in manifest["schemas"].values():
        assert (ROOT / relative_path).is_file()
    for asset in manifest["assets"]:
        assert len(asset["sha256"]) == 64
    for source in manifest["canonical_sources"]:
        assert (ROOT / source["path"]).is_file()
        assert source["bytes"] > 0
        assert len(source["sha256"]) == 64


def test_valid_auto_result_and_blind_ballot_validate() -> None:
    _validator("auto_result.schema.json").validate(
        _load_json(EXAMPLES_DIR / "auto_result.valid.json")
    )
    _validator("human_ballot.schema.json").validate(
        _load_json(EXAMPLES_DIR / "human_ballot.valid.json")
    )
    _validator("semantic_review.schema.json").validate(
        _load_json(EXAMPLES_DIR / "semantic_review.pending.valid.json")
    )


def test_case_rejects_missing_leakage_group_and_unknown_fields() -> None:
    case = _load_jsonl(EXAMPLES_DIR / "cases.valid.jsonl")[0]
    validator = _validator("case.schema.json")

    missing_group = deepcopy(case)
    del missing_group["leakage_group"]
    with pytest.raises(ValidationError):
        validator.validate(missing_group)

    unknown_field = deepcopy(case)
    unknown_field["oracle_visible_to_model"] = True
    with pytest.raises(ValidationError):
        validator.validate(unknown_field)


def test_multiturn_case_rejects_fewer_than_four_turns() -> None:
    case = _load_jsonl(EXAMPLES_DIR / "cases.valid.jsonl")[1]
    case["turns"] = case["turns"][:3]
    with pytest.raises(ValidationError):
        _validator("case.schema.json").validate(case)


def test_unrevealed_ballot_rejects_model_mapping() -> None:
    ballot = _load_json(EXAMPLES_DIR / "human_ballot.valid.json")
    ballot["reveal"] = {
        "candidate_a_model_id": "model-a",
        "candidate_b_model_id": "model-b",
        "revealed_at": "2026-08-05T19:05:00+08:00",
    }
    with pytest.raises(ValidationError):
        _validator("human_ballot.schema.json").validate(ballot)


def test_frozen_manifest_requires_hash_and_immutable_flag() -> None:
    manifest = _load_json(MANIFEST_PATH)
    manifest["freeze"]["immutable"] = False
    manifest["freeze"]["manifest_sha256"] = None
    with pytest.raises(ValidationError):
        _validator("suite_manifest.schema.json").validate(manifest)


def test_pass_result_rejects_blocker_flags() -> None:
    result = _load_json(EXAMPLES_DIR / "auto_result.valid.json")
    result["blocker_flags"] = ["unsafe_advice"]
    with pytest.raises(ValidationError):
        _validator("auto_result.schema.json").validate(result)


def test_pass_result_rejects_unresolved_or_failed_checks() -> None:
    result = _load_json(EXAMPLES_DIR / "auto_result.valid.json")
    result["checks"][0]["status"] = "needs_review"
    with pytest.raises(ValidationError):
        _validator("auto_result.schema.json").validate(result)

    result["checks"][0]["status"] = "fail"
    with pytest.raises(ValidationError):
        _validator("auto_result.schema.json").validate(result)


def test_ballot_rejects_inconsistent_both_unacceptable_flag() -> None:
    ballot = _load_json(EXAMPLES_DIR / "human_ballot.valid.json")
    ballot["both_unacceptable"] = True
    with pytest.raises(ValidationError):
        _validator("human_ballot.schema.json").validate(ballot)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_canon_snapshot_validates_and_hashes_match_sources() -> None:
    snapshot = _load_json(CANON_SNAPSHOT_PATH)
    _validator("canon_snapshot.schema.json").validate(snapshot)
    assert [source["authority_rank"] for source in snapshot["sources"]] == list(
        range(1, 7)
    )
    for source in snapshot["sources"]:
        path = ROOT / source["path"]
        assert path.stat().st_size == source["bytes"]
        assert _sha256(path) == source["sha256"]


def test_manifest_canon_hashes_equal_snapshot() -> None:
    manifest = _load_json(MANIFEST_PATH)
    snapshot = _load_json(CANON_SNAPSHOT_PATH)
    manifest_sources = {
        source["path"]: (source["bytes"], source["sha256"])
        for source in manifest["canonical_sources"]
    }
    snapshot_sources = {
        source["path"]: (source["bytes"], source["sha256"])
        for source in snapshot["sources"]
    }
    assert snapshot_sources.items() <= manifest_sources.items()
    assert set(manifest_sources) - set(snapshot_sources) == {
        "设计文档/本地AI恋爱桌面宠物产品集成设计.md",
        "设计文档/AI聊天核心施工优先级总纲.md",
    }


def test_resolved_facts_match_structured_canon() -> None:
    snapshot = _load_json(CANON_SNAPSHOT_PATH)
    canon = _load_json(ROOT / "人物设定" / "秦" / "canon.json")["facts"]
    # T1 起角色名/年龄/性别由 bible identity 提供（canon 不再重复这些字段）
    bible = yaml.safe_load((ROOT / "人物设定" / "秦" / "bible.yaml").read_text(encoding="utf-8"))
    identity = bible["identity"]
    facts = snapshot["resolved_facts"]
    assert facts["character_name"] == identity["name"]
    assert facts["character_age"] == int(identity["age"])
    assert facts["player_name"] == "浩然"
    assert facts["player_age"] == int(canon["player_age"]["value"])
    assert facts["player_to_character_name"] == canon["your_nickname_for_her"]["value"]
    assert facts["character_to_player_names"] == ["B哥", "浩然"]
    assert "B哥" in canon["her_nickname_for_you"]["value"]
    assert facts["player_name"] in canon["her_nickname_for_you"]["value"]
    assert "浩然" in canon["her_nickname_for_you"]["value"]


def test_frozen_canon_drift_validates_snapshot_hashes_and_summary() -> None:
    drift = _load_json(CANON_DRIFT_PATH)
    _validator("canon_drift.schema.json").validate(drift)
    assert drift["canon_snapshot_id"] == _load_json(CANON_SNAPSHOT_PATH)["snapshot_id"]

    for finding in drift["findings"]:
        path = ROOT / finding["path"]
        assert path.is_file()
        assert finding["bytes"] > 0
        assert len(finding["sha256"]) == 64

    open_findings = [item for item in drift["findings"] if item["status"] == "open"]
    blocks_chat02 = [item for item in drift["findings"] if "CHAT-02" in item["blocks"]]
    blocks_chat03 = [item for item in drift["findings"] if "CHAT-03" in item["blocks"]]
    assert drift["summary"] == {
        "open": len(open_findings),
        "blocks_chat02": len(blocks_chat02),
        "blocks_chat03": len(blocks_chat03),
        "ignored_archives": len(drift["excluded_paths"]),
    }


def test_v5_drift_has_no_open_character_canon_findings() -> None:
    drift = _load_json(CANON_DRIFT_PATH)
    assert drift["findings"] == []
    assert drift["summary"]["open"] == 0
    assert drift["summary"]["blocks_chat02"] == 0


def test_drift_excludes_backups_and_history_from_active_findings() -> None:
    drift = _load_json(CANON_DRIFT_PATH)
    active_paths = [item["path"] for item in drift["findings"]]
    assert all(".bak-" not in path for path in active_paths)
    assert all("历史与调研文档" not in path for path in active_paths)
    excluded_patterns = {item["path_pattern"] for item in drift["excluded_paths"]}
    assert "profiles/qinweixi/sources/*.bak-*" in excluded_patterns
    assert "历史与调研文档/**" in excluded_patterns
