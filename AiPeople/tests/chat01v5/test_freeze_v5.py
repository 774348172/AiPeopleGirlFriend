from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.chat01.freeze import FreezeVerificationError
from eval.chat01v5.freeze import (
    CHANGE_NOTES,
    MANIFEST,
    OLD_MANIFEST,
    verify,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_v5_contract_freeze_verifies() -> None:
    manifest = verify()
    assert manifest["suite_id"] == "chat01-qinweixi-v5"
    assert manifest["version"] == "v5"
    assert manifest["status"] == "frozen"
    assert len(manifest["canonical_sources"]) == 8
    assert len(manifest["assets"]) == 4


def test_v5_reuses_v4_cases_and_rubrics_byte_for_byte() -> None:
    old = _load(OLD_MANIFEST)
    current = _load(MANIFEST)
    assert [(item["path"], item["sha256"]) for item in current["assets"]] == [
        (item["path"], item["sha256"]) for item in old["assets"]
    ]
    assert current["rubrics"] == old["rubrics"]


def test_v5_records_new_scope_without_claiming_unbuilt_capabilities() -> None:
    notes = _load(CHANGE_NOTES)
    changes = {item["change_id"] for item in notes["accepted_changes"]}
    assert "model.qwen35-4b" in changes
    assert "self.timeline" in changes
    assert "initiative.intrinsic" in changes
    assert notes["next_checkpoint"] == "FREEZE-02"
    not_claimed = " ".join(notes["coverage_boundary"]["not_claimed"])
    assert "OFFSCREEN_UPDATE" in not_claimed
    assert "PROACTIVE_REPLY" in not_claimed


def test_v5_verifier_rejects_tampered_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _load(MANIFEST)
    value["freeze"]["manifest_sha256"] = "0" * 64
    tampered = tmp_path / "manifest.json"
    tampered.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("eval.chat01v5.freeze.MANIFEST", tampered)
    with pytest.raises(FreezeVerificationError, match="self hash"):
        verify()
