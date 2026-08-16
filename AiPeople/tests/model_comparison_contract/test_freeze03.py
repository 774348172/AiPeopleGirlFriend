from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.chat01.freeze import FreezeVerificationError, sha256_file
from eval.model_comparison_contract.freeze import (
    CONTRACT,
    METRICS,
    PROFILE,
    canonical_hash,
    nearest_rank,
    validate_model_pair,
    verify,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _model_manifest(
    directory: Path,
    *,
    model_id: str,
    role: str,
    gguf_bytes: bytes,
    base_id: str | None = None,
    base_gguf_sha256: str | None = None,
) -> Path:
    directory.mkdir(parents=True)
    gguf = directory / "model.gguf"
    gguf.write_bytes(gguf_bytes)
    manifest = {
        "model_id": model_id,
        "schema_version": 1,
        "role": role,
        "status": "verified",
        "created_at": "2026-08-07T09:00:00+08:00",
        "upstream": {
            "repository": "verified/repository",
            "revision": "abcdef1234567890",
            "architecture": "VerifiedQwen35ForCausalLM",
            "model_family": "Qwen3.5",
            "parameter_class": "4B",
            "config_sha256": "1" * 64,
        },
        "tokenizer": {"sha256": "2" * 64, "eos_token_id": 12345},
        "chat_template": {
            "sha256": "3" * 64,
            "source": "verified_model_native_template",
            "thinking_disabled_verified": True,
        },
        "gguf": {
            "path": "model.gguf",
            "bytes": gguf.stat().st_size,
            "sha256": sha256_file(gguf),
            "quantization": "Q4_K_M",
            "conversion_tool_sha256": "4" * 64,
        },
        "backend": {"name": "llama.cpp", "build_sha256": "5" * 64, "protocol_smoke_passed": True},
        "lineage": None,
        "freeze": {
            "hash_algorithm": "sha256",
            "manifest_hash_mode": "canonical_json_with_null_self",
            "immutable": True,
            "manifest_sha256": None,
        },
    }
    if role == "qinweixi_lora_merged":
        manifest["lineage"] = {
            "base_model_id": base_id,
            "base_gguf_sha256": base_gguf_sha256,
            "dataset_manifest_sha256": "6" * 64,
            "adapter_sha256": "7" * 64,
            "merged_weight_sha256": "8" * 64,
        }
    manifest["freeze"]["manifest_sha256"] = canonical_hash(manifest)
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def test_freeze03_contract_and_pending_profile_verify() -> None:
    contract = verify()
    profile = _load(PROFILE)
    assert contract["status"] == "frozen"
    assert contract["next_checkpoint"] == "MODEL-01"
    assert profile["status"] == "contract_frozen_assets_pending"
    assert profile["comparison"]["model_family"] == "Qwen3.5"
    assert profile["launch"]["context_size"] == 4096
    assert profile["readiness"]["real_run_allowed"] is False


def test_profile_does_not_guess_qwen3_specific_template_or_backend() -> None:
    rendered = json.dumps(_load(PROFILE), ensure_ascii=False)
    assert "embedded_qwen3" not in rendered
    assert "b10256" not in rendered
    assert "pending_MODEL-01" in rendered


def test_metrics_freeze_population_latency_and_vram_rules() -> None:
    metrics = _load(METRICS)
    assert metrics["population"]["execute_frozen_single"] == 240
    assert metrics["population"]["primary_frozen_single"] == 239
    assert metrics["percentiles"]["algorithm"] == "nearest_rank"
    assert metrics["timing"]["first_visible_token_ms"].startswith("request_write_complete")
    assert metrics["decision_gates"]["final_p0"]["first_visible_token_p95_ms_max"] == 2000
    assert metrics["resource_metrics"]["p0_combined_peak_mib_threshold"] == 5120


def test_nearest_rank_is_frozen_and_deterministic() -> None:
    values = [9.0, 1.0, 5.0, 3.0, 7.0]
    assert nearest_rank(values, 0.5) == 5.0
    assert nearest_rank(values, 0.95) == 9.0
    with pytest.raises(ValueError):
        nearest_rank([], 0.95)


def test_same_architecture_model_pair_passes(tmp_path: Path) -> None:
    base = _model_manifest(
        tmp_path / "base", model_id="qwen35-4b-base-v1", role="untrained_base", gguf_bytes=b"base-gguf"
    )
    base_value = _load(base)
    candidate = _model_manifest(
        tmp_path / "candidate",
        model_id="qinweixi-qwen35-4b-v1",
        role="qinweixi_lora_merged",
        gguf_bytes=b"candidate-gguf",
        base_id=base_value["model_id"],
        base_gguf_sha256=base_value["gguf"]["sha256"],
    )
    report = validate_model_pair(base, candidate)
    assert report["status"] == "passed"
    assert report["findings"] == []


def test_model_pair_rejects_template_or_revision_change(tmp_path: Path) -> None:
    base = _model_manifest(
        tmp_path / "base", model_id="qwen35-4b-base-v1", role="untrained_base", gguf_bytes=b"base-gguf"
    )
    base_value = _load(base)
    candidate = _model_manifest(
        tmp_path / "candidate",
        model_id="qinweixi-qwen35-4b-v1",
        role="qinweixi_lora_merged",
        gguf_bytes=b"candidate-gguf",
        base_id=base_value["model_id"],
        base_gguf_sha256=base_value["gguf"]["sha256"],
    )
    value = _load(candidate)
    value["upstream"]["revision"] = "different-revision"
    value["chat_template"]["sha256"] = "9" * 64
    value["freeze"]["manifest_sha256"] = canonical_hash(value)
    candidate.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    report = validate_model_pair(base, candidate)
    assert report["status"] == "blocked"
    assert "upstream.revision differs" in report["findings"]
    assert "chat template differs" in report["findings"]


def test_contract_verifier_rejects_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = _load(CONTRACT)
    value["freeze"]["manifest_sha256"] = "0" * 64
    tampered = tmp_path / "contract.json"
    tampered.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("eval.model_comparison_contract.freeze.CONTRACT", tampered)
    with pytest.raises(FreezeVerificationError, match="self hash"):
        verify()
