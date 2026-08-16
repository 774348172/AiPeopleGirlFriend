from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import (
    FreezeVerificationError,
    canonical_manifest_sha256,
    verify_file,
)
from eval.chat01.leakage_guard import resolve_training_paths, scan_leakage
from eval.chat01v5.freeze import MANIFEST as MANIFEST_PATH, verify as verify_manifest


ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_packager_module():
    path = (
        ROOT
        / "training_packages"
        / "training_package_m3_v2"
        / "01_prepare_data.py"
    )
    spec = importlib.util.spec_from_file_location("m3_v2_prepare_data", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_manifest_and_all_asset_hashes_verify() -> None:
    manifest = verify_manifest()
    assert manifest["status"] == "frozen"
    assert manifest["freeze"]["manifest_sha256"] == canonical_manifest_sha256(manifest)
    assert manifest["training_exclusions"] == ["eval/chat01/"]


def test_modified_temporary_asset_fails_verification(tmp_path: Path) -> None:
    path = tmp_path / "asset.json"
    path.write_bytes(b"original\n")
    entry = {
        "path": "asset.json",
        "bytes": path.stat().st_size,
        "sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
    }
    path.write_bytes(b"tampered\n")
    with pytest.raises(FreezeVerificationError):
        verify_file(tmp_path, entry)


def test_leakage_report_validates_and_current_scan_passes() -> None:
    schema = _load_json(ROOT / "eval/chat01/schema/leakage_report.schema.json")
    report = _load_json(ROOT / "eval/chat01/suites/leakage_report_v2.json")
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
    assert report["status"] == "passed"
    assert report["summary"]["blocking_findings"] == 0

    current = scan_leakage()
    assert current["status"] == "passed"
    assert current["summary"]["blocking_findings"] == 0


def test_eval_path_cannot_resolve_as_training_source() -> None:
    with pytest.raises(ValueError, match="evaluation asset cannot be a training source"):
        resolve_training_paths(
            patterns=("eval/chat01/suites/chat01_dev_v1.jsonl",),
            excluded_patterns=(),
        )


def test_packager_rejects_missing_and_empty_blocklist(tmp_path: Path, monkeypatch) -> None:
    packager = _load_packager_module()
    # 双契约（T3）：目录缺失 → FileNotFoundError
    missing = tmp_path / "missing_dir"
    monkeypatch.setattr(packager, "EVAL_EXCLUSIONS", missing)
    with pytest.raises(FileNotFoundError):
        packager.load_eval_exclusion_hashes()

    # chat01 存在但 chat02 缺失 → FileNotFoundError
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "chat01_v1.json").write_text(
        json.dumps(
            {
                "contract_id": "chat01-training-exclusions-v1",
                "normalized_text_sha256": ["a" * 64],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(packager, "EVAL_EXCLUSIONS", partial)
    with pytest.raises(FileNotFoundError, match="chat02"):
        packager.load_eval_exclusion_hashes()

    # 双契约齐全但 chat01 哈希为空 → ValueError
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "chat01_v1.json").write_text(
        json.dumps(
            {
                "contract_id": "chat01-training-exclusions-v1",
                "normalized_text_sha256": [],
            }
        ),
        encoding="utf-8",
    )
    (empty / "chat02_v1.json").write_text(
        json.dumps(
            {
                "contract_id": "chat02-training-exclusions-v1",
                "normalized_text_sha256": ["b" * 64],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(packager, "EVAL_EXCLUSIONS", empty)
    with pytest.raises(ValueError, match="为空"):
        packager.load_eval_exclusion_hashes()


def test_packager_filters_whole_conversation_on_matching_human_turn() -> None:
    packager = _load_packager_module()
    normalized = packager.normalize_for_eval_exclusion("你叫什么名字？")
    digest = __import__("hashlib").sha256(normalized.encode("utf-8")).hexdigest()
    rows = [
        {
            "id": "overlap",
            "conversations": [
                {"from": "human", "value": "你叫什么名字？"},
                {"from": "gpt", "value": "秦未晞。"},
            ],
        },
        {
            "id": "safe",
            "conversations": [
                {"from": "human", "value": "今天忙不忙？"},
                {"from": "gpt", "value": "还行。"},
            ],
        },
    ]
    kept, excluded = packager.exclude_eval_overlaps(rows, {digest})
    assert excluded == 1
    assert [row["id"] for row in kept] == ["safe"]


def test_manifest_file_is_the_verified_contract() -> None:
    assert MANIFEST_PATH == ROOT / "eval/chat01/suites/chat01_suite_manifest_v6.json"  # 2026-08-07 v6 冻结（契约源文档外部更新）
