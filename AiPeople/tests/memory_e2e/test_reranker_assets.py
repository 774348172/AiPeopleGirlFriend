from __future__ import annotations

import hashlib
import json

import pytest

from runtime._reranker_assets import (
    RerankerAssetError,
    LocalRerankerPackage,
    canonical_package_sha256,
)


def make_package(tmp_path):
    root = tmp_path / "reranker"
    root.mkdir(parents=True)
    weight = root / "model.safetensors"
    tokenizer = root / "tokenizer.json"
    weight.write_bytes(b"weights")
    tokenizer.write_bytes(b"tokenizer")
    manifest = {
        "schema_version": 1,
        "model_id": "Qwen/Qwen3-Reranker-0.6B",
        "revision": "delivered-revision",
        "deployment_profile": "bf16-test-package",
        "activation_threshold": 0.73,
        "relative_top_floor": 0.1,
        "relative_top_margin": 0.05,
        "maximum_pair_tokens": 128,
        "output_contract": "yes_no_logits_per_memory_id",
        "weights": [
            {
                "path": "model.safetensors",
                "sha256": hashlib.sha256(weight.read_bytes()).hexdigest(),
            }
        ],
        "tokenizer_files": [
            {
                "path": "tokenizer.json",
                "sha256": hashlib.sha256(tokenizer.read_bytes()).hexdigest(),
            }
        ],
        "artifact_sha256": None,
    }
    path = root / "reranker_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest["artifact_sha256"] = canonical_package_sha256(root)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_local_reranker_package_verifies_identity_threshold_and_files(tmp_path) -> None:
    package = LocalRerankerPackage.load(make_package(tmp_path))
    assert package.identity.model_id == "Qwen/Qwen3-Reranker-0.6B"
    assert package.identity.revision == "delivered-revision"
    assert package.activation_threshold == 0.73
    assert package.relative_top_floor == 0.1
    assert package.relative_top_margin == 0.05
    assert package.weights[0].name == "model.safetensors"
    assert package.tokenizer_files[0].name == "tokenizer.json"


def test_local_reranker_package_rejects_tampering_and_unsafe_paths(tmp_path) -> None:
    root = make_package(tmp_path)
    (root / "model.safetensors").write_bytes(b"tampered")
    with pytest.raises(RerankerAssetError, match="SHA256 mismatch"):
        LocalRerankerPackage.load(root)

    root = make_package(tmp_path / "second")
    path = root / "reranker_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["weights"][0]["path"] = "../outside.bin"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RerankerAssetError, match="unsafe path"):
        LocalRerankerPackage.load(root)


def test_missing_package_never_triggers_a_download(tmp_path) -> None:
    with pytest.raises(RerankerAssetError, match="does not exist"):
        LocalRerankerPackage.load(tmp_path / "not-delivered")
