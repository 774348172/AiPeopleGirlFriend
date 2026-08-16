from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
CHAT01 = ROOT / "eval" / "chat01" / "suites"
CHAT02 = ROOT / "eval" / "chat02"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_chat02a_alignment_schema_and_record_validate() -> None:
    schema = _load_json(CHAT02 / "schema" / "canon_alignment.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        _load_json(CHAT02 / "canon_alignment_v2.json")
    )


def test_alignment_is_derived_from_frozen_chat01_contract() -> None:
    alignment = _load_json(CHAT02 / "canon_alignment_v2.json")
    snapshot = _load_json(CHAT01 / "canon_snapshot_v2.json")
    manifest = _load_json(CHAT01 / "chat01_suite_manifest_v3.json")
    assert alignment["source_contract"] == {
        "canon_snapshot_id": snapshot["snapshot_id"],
        "suite_id": manifest["suite_id"],
        "suite_manifest_sha256": manifest["freeze"]["manifest_sha256"],
    }
    assert alignment["resolved_facts"] == snapshot["resolved_facts"]


def test_alignment_preserves_frozen_before_hashes_and_verifies_current_hashes() -> None:
    alignment = _load_json(CHAT02 / "canon_alignment_v2.json")
    drift = _load_json(CHAT01 / "canon_drift.json")
    frozen = {finding["finding_id"]: finding for finding in drift["findings"]}
    for resolution in alignment["resolutions"]:
        finding = frozen[resolution["finding_id"]]
        assert resolution["path"] == finding["path"]
        assert resolution["frozen_bytes"] == finding["bytes"]
        assert resolution["frozen_sha256"] == finding["sha256"]
        current = ROOT / resolution["path"]
        assert current.stat().st_size == resolution["current_bytes"]
        assert _sha256(current) == resolution["current_sha256"]
        assert resolution["status"] == "resolved"


def test_active_runtime_prompt_uses_only_current_identity_anchors() -> None:
    prompt = (ROOT / "runtime" / "_prompt.py").read_text(encoding="utf-8")
    for current in (
        "秦未晞，22岁",
        "玩家叫浩然，24岁",
        "B哥",
        "秦老",
        "没有说破但都懂",
    ):
        assert current in prompt
    for retired in ("23岁", "25岁", "大叔"):
        assert retired not in prompt
