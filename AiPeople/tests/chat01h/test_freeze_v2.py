from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.chat01.freeze import MANIFEST_PATH as V3_MANIFEST_PATH, FreezeVerificationError
from eval.chat01h.freeze import REPORT_PATH, V1_MANIFEST_PATH, verify_manifest_v2
from eval.chat01v5.freeze import verify as verify_manifest_v5


ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_active_manifest_and_current_training_sources_verify() -> None:
    # v5 为现行契约；v2-v4 为历史契约，权威文档已演进，不能再对当前文件直接验证。
    manifest = verify_manifest_v5()
    assert manifest["suite_id"] == "chat01-qinweixi-v5"
    assert manifest["status"] == "frozen"
    report = _load(REPORT_PATH)
    assert report["report_id"] == "chat01-leakage-v2"
    assert report["status"] == "passed"
    assert report["summary"]["blocking_findings"] == 0


def test_v2_reuses_byte_identical_v1_evaluation_assets() -> None:
    v1 = _load(V1_MANIFEST_PATH)
    v2 = _load(V3_MANIFEST_PATH)
    assert [(item["path"], item["sha256"]) for item in v2["assets"]] == [
        (item["path"], item["sha256"]) for item in v1["assets"]
    ]
    assert v2["rubrics"] == v1["rubrics"]


def test_v2_verifier_rejects_tampered_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = _load(V3_MANIFEST_PATH)
    value["freeze"]["manifest_sha256"] = "0" * 64
    tampered = tmp_path / "manifest.json"
    tampered.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("eval.chat01h.freeze.MANIFEST_PATH", tampered)
    with pytest.raises(FreezeVerificationError, match="self SHA256"):
        verify_manifest_v2()
