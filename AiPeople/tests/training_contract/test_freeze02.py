from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from eval.chat01.freeze import FreezeVerificationError, sha256_file
from eval.training_contract.freeze import (
    BLOCKLIST,
    CANON_SNAPSHOT,
    CONTRACT,
    _canonical_hash,
    validate_dataset,
    verify,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _reply(sample_id: str, split: str, *, mode: str = "REPLY", target: str | None = None) -> dict:
    canon_sha = sha256_file(CANON_SNAPSHOT)
    suffix = {"train": "红色茶杯", "dev": "绿色书签", "test": "银色钥匙"}[split]
    return {
        "sample_id": sample_id,
        "schema_version": 1,
        "dataset_family": "visible_reply",
        "character_id": "qinweixi",
        "mode": mode,
        "split": split,
        "conversation_group_id": f"conversation:{sample_id}",
        "leakage_group_id": f"leakage:{sample_id}",
        "scenario_family": f"scenario:{split}",
        "source_record_ids": [f"source:{sample_id}"],
        "canon_snapshot": {"snapshot_id": "chat01-canon-v4", "sha256": canon_sha},
        "generation": {
            "run_id": "run:freeze02-test",
            "generator_version": "test-v1",
            "created_at": "2026-08-07T08:00:00+08:00",
        },
        "content_kind": "visible_character_text",
        "messages": [
            {"role": "user", "content": f"我在整理一件只属于这组测试的{suffix}，你觉得放哪边顺眼？"},
            {"role": "assistant", "content": target or f"{suffix}放窗边吧，光线好一点。"},
        ],
        "supervised_message_indexes": [1],
    }


def _write_dataset(
    root: Path,
    *,
    records: dict[str, list[dict]] | None = None,
    family: str = "visible_reply",
    release_scope: str = "first_engineering_candidate",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    selected = records or {split: [_reply(f"sample:{split}", split)] for split in ("train", "dev", "test")}
    files = []
    for split in ("train", "dev", "test"):
        path = root / f"{split}.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected[split]),
            encoding="utf-8",
            newline="\n",
        )
        files.append(
            {
                "split": split,
                "path": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "records": len(selected[split]),
            }
        )
    contract = verify()
    manifest = {
        "dataset_id": "qinweixi-visible-reply-test-v1",
        "schema_version": 1,
        "dataset_version": "v1",
        "dataset_family": family,
        "release_scope": release_scope,
        "logical_root": f"data/training/qinweixi/{family}/v1",
        "character_id": "qinweixi",
        "status": "frozen",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "contract": {"contract_id": contract["contract_id"], "sha256": contract["freeze"]["manifest_sha256"]},
        "canon_snapshot": {"snapshot_id": "chat01-canon-v4", "sha256": sha256_file(CANON_SNAPSHOT)},
        "generator": {"generator_id": "generator:test", "version": "v1", "config_sha256": "a" * 64},
        "split_policy": {
            "train": 0.8,
            "dev": 0.1,
            "test": 0.1,
            "max_ratio_deviation": 0.02,
            "isolation_keys": ["conversation_group_id", "leakage_group_id", "source_record_ids"],
        },
        "files": files,
        "freeze": {
            "hash_algorithm": "sha256",
            "manifest_hash_mode": "canonical_json_with_null_self",
            "immutable": True,
            "manifest_sha256": None,
        },
    }
    manifest["freeze"]["manifest_sha256"] = _canonical_hash(manifest)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest_path


def test_freeze02_contract_verifies_and_has_three_physical_families() -> None:
    contract = verify()
    assert contract["status"] == "frozen"
    assert contract["next_checkpoint"] == "FREEZE-03"
    families = {item["family"]: item for item in contract["dataset_families"]}
    assert set(families) == {"visible_reply", "background_structured", "memory_reranker"}
    assert families["visible_reply"]["first_candidate_modes"] == ["REPLY"]
    assert families["background_structured"]["data_admission"] == "blocked_until_mode_schemas_frozen"
    assert families["memory_reranker"]["data_admission"] == "blocked_until_select01"


def test_eval_blocklist_is_bound_to_v6_and_keeps_diagnostic_case_forbidden() -> None:
    blocklist = _load(BLOCKLIST)
    assert blocklist["source_suite"]["suite_id"] == "chat01-qinweixi-v6"  # 2026-08-07 v6 冻结
    assert blocklist["diagnostic_only_case_ids"] == ["frozen.identity.name"]
    assert len(blocklist["normalized_text_sha256"]) > 500


def test_clean_visible_reply_dataset_passes(tmp_path: Path) -> None:
    report = validate_dataset(_write_dataset(tmp_path / "dataset"))
    assert report["status"] == "passed"
    assert report["records"] == {"train": 1, "dev": 1, "test": 1}


def test_first_candidate_rejects_proactive_and_structured_targets(tmp_path: Path) -> None:
    records = {split: [_reply(f"sample:{split}", split)] for split in ("train", "dev", "test")}
    records["train"][0]["mode"] = "PROACTIVE_REPLY"
    records["dev"][0]["messages"][1]["content"] = '{"action":"NO_OP"}'
    report = validate_dataset(_write_dataset(tmp_path / "dataset", records=records))
    kinds = {item["kind"] for item in report["findings"]}
    assert report["status"] == "blocked"
    assert "first_candidate_mode" in kinds
    assert "structured_target_in_visible_reply" in kinds


def test_eval_overlap_and_cross_split_source_are_blocking(tmp_path: Path) -> None:
    records = {split: [_reply(f"sample:{split}", split)] for split in ("train", "dev", "test")}
    records["train"][0]["messages"][0]["content"] = "你叫什么名字？"
    records["dev"][0]["source_record_ids"] = records["test"][0]["source_record_ids"]
    report = validate_dataset(_write_dataset(tmp_path / "dataset", records=records))
    kinds = {item["kind"] for item in report["findings"]}
    assert report["status"] == "blocked"
    assert "evaluation_exact" in kinds or "evaluation_normalized" in kinds
    assert "cross_split_group" in kinds


def test_contract_verifier_rejects_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = _load(CONTRACT)
    value["freeze"]["manifest_sha256"] = "0" * 64
    tampered = tmp_path / "contract.json"
    tampered.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("eval.training_contract.freeze.CONTRACT", tampered)
    with pytest.raises(FreezeVerificationError, match="self hash"):
        verify()
