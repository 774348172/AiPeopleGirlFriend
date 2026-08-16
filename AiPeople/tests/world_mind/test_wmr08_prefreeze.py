from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import jsonschema

from runtime.world_mind.wmr08 import (
    WMR08_MODES,
    build_engineering_freeze_manifest,
    evaluate_engineering_prefreeze,
    nearest_rank_summary,
)

ROOT = Path(__file__).resolve().parents[2]


def test_wmr08_nearest_rank_summary_uses_frozen_percentile_rule() -> None:
    assert nearest_rank_summary([5.0, 1.0, 4.0, 2.0, 3.0]) == {
        "count": 5,
        "min": 1.0,
        "p50": 3.0,
        "p95": 5.0,
        "max": 5.0,
    }


def test_wmr08_freeze_manifest_covers_modes_schemas_and_v6_migrations() -> None:
    manifest = build_engineering_freeze_manifest(
        ROOT,
        probe_model={
            "ollama_name": "qinweixi-qwen35",
            "weights_character": "qinweixi_legacy",
            "artifact_sha256": "a" * 64,
        },
    )
    assert tuple(manifest["mode_prompts"]) == WMR08_MODES
    assert tuple(manifest["mode_schemas"]) == WMR08_MODES
    assert WMR08_MODES[-1] == "MEMORY_PROPOSE"
    assert manifest["mode_schemas"]["MEMORY_PROPOSE"]["path"] == (
        "heroine_memory_r2.schema.json"
    )
    assert manifest["mode_schemas"]["MIND_PATCH_V2"]["path"] == (
        "mind_patch_m2.schema.json"
    )
    assert manifest["mode_schemas"]["GAME_REPLY"]["path"] == (
        "game_reply_plain_text_v2.schema.json"
    )
    assert manifest["evaluation_contract"]["path"] == (
        "wmr08_engineering_prefreeze_contract_v1.json"
    )
    assert {item["path"] for item in manifest["runtime_schemas"]} >= {
        "memory_representation_v2.schema.json",
        "save_identity_v1.schema.json",
        "wmr08_engineering_report_v1.schema.json",
    }
    assert [item["path"] for item in manifest["database_migrations"]] == [
        "008_world_mind_v6_foundation.sql",
        "009_heroine_memory_v2.sql",
        "010_world_mind_model_decisions.sql",
            "011_world_mind_reconciliation.sql",
            "012_world_mind_projection_rebuild.sql",
            "013_world_mind_r1_memory_jobs.sql",
            "014_world_mind_background_backpressure.sql",
        ]
    assert len(manifest["manifest_sha256"]) == 64
    assert manifest["not_final_p0"] is True


def test_wmr08_engineering_gate_never_claims_formal_p0() -> None:
    report = {
        "successful_modes": list(WMR08_MODES),
        "turns": [{"result_type": "Completed"}],
        "database": {
            "model_decisions": 1,
            "reconcile_jobs_completed": 2,
            "reconcile_jobs_failed": 0,
            "memory_proposals": 1,
            "r1_memory_jobs_failed": 0,
        },
        "freeze_manifest": {"manifest_sha256": "a" * 64},
    }
    decision = evaluate_engineering_prefreeze(report)
    assert decision["engineering_chain_passed"] is True
    assert decision["formal_p0_gate"] == "deferred"
    assert decision["formal_p0_blockers"]
    failed_r1 = deepcopy(report)
    failed_r1["database"]["r1_memory_jobs_failed"] = 1
    assert evaluate_engineering_prefreeze(failed_r1)["engineering_chain_passed"] is False


def test_wmr08_report_schema_accepts_engineering_prefreeze_shape() -> None:
    schema = json.loads(
        (ROOT / "runtime" / "schemas" / "wmr08_engineering_report_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    report = {
        "schema_version": 1,
        "scope": "wmr08_engineering_prefreeze",
        "test_only": True,
        "model": {
            "ollama_name": "qinweixi-qwen35",
            "weights_character": "qinweixi_legacy",
            "artifact_sha256": "a" * 64,
        },
        "freeze_manifest": {
            "manifest_sha256": "b" * 64,
            "mode_prompts": {},
            "mode_schemas": {},
            "database_migrations": [],
        },
        "turns": [{"result_type": "Completed"}],
        "successful_modes": list(WMR08_MODES),
        "database": {},
        "performance": {},
        "decision": {
            "engineering_chain_passed": True,
            "engineering_gate": "passed",
            "formal_p0_gate": "deferred",
            "formal_p0_blockers": ["白未晞正式模型未接入"],
        },
    }
    jsonschema.Draft202012Validator(schema).validate(report)


def test_short_protocol_schemas_avoid_unsupported_boolean_items() -> None:
    for name in (
        "mind_patch_m2.schema.json",
        "background_mind_patch_b1.schema.json",
        "heroine_memory_r2.schema.json",
    ):
        schema = json.loads(
            (ROOT / "runtime" / "schemas" / name).read_text(encoding="utf-8")
        )
        encoded = json.dumps(schema, sort_keys=True)
        assert '"items": false' not in encoded
