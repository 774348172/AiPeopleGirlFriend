from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from runtime._real_assets import (
    LocalBgePackage,
    LocalQwen35Package,
    LocalRealAssetBundle,
    RealAssetError,
)
from tests.real_select_e2e._assets import make_bge_package, make_bundle, make_reply_package


ROOT = Path(__file__).resolve().parents[2]


def test_bge_reply_and_bundle_manifests_verify_without_network(tmp_path) -> None:
    bundle_path, _, _, _ = make_bundle(tmp_path)
    bundle = LocalRealAssetBundle.load(bundle_path)
    assert bundle.bge.identity.model_id == "BAAI/bge-small-zh-v1.5"
    assert bundle.bge.identity.dimension == 512
    assert bundle.reply.identity.model_id == "Qwen/Qwen3.5-4B"
    assert bundle.reply.identity.model_role == "heroine_reply"
    assert bundle.reply.identity.character_id == "baiweixi"
    assert bundle.reply.identity.world_id == "songjiangfu"
    assert bundle.reply.identity.protagonist_id == "protagonist"
    assert bundle.reranker.identity.deployment_profile == "cuda-fp16"


def test_bge_package_rejects_profile_drift_tampering_and_unsafe_paths(tmp_path) -> None:
    root = make_bge_package(tmp_path / "bge")
    manifest_path = root / "bge_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pooling"] = "cls"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RealAssetError, match="pooling"):
        LocalBgePackage.load(root)

    root = make_bge_package(tmp_path / "bge2")
    (root / "model.safetensors").write_bytes(b"tampered")
    with pytest.raises(RealAssetError, match="SHA256 mismatch"):
        LocalBgePackage.load(root)

    root = make_bge_package(tmp_path / "bge3")
    manifest_path = root / "bge_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["model_files"][0]["path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RealAssetError, match="unsafe path"):
        LocalBgePackage.load(root)


def test_reply_package_rejects_wrong_role_and_gguf_tampering(tmp_path) -> None:
    root = make_reply_package(tmp_path / "reply")
    (root / "model.gguf").write_bytes(b"tampered")
    with pytest.raises(RealAssetError, match="SHA256 mismatch"):
        LocalQwen35Package.load(root)

    root = make_reply_package(tmp_path / "reply2")
    path = root / "reply_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["model_role"] = "legacy_model"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RealAssetError, match="role"):
        LocalQwen35Package.load(root)


def test_bundle_requires_absolute_paths_and_exact_component_hashes(tmp_path) -> None:
    bundle_path, _, _, _ = make_bundle(tmp_path)
    manifest = json.loads(bundle_path.read_text(encoding="utf-8"))
    manifest["bge_package"] = "relative/bge"
    bundle_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RealAssetError, match="absolute path"):
        LocalRealAssetBundle.load(bundle_path)

    bundle_path, _, _, _ = make_bundle(tmp_path / "second")
    manifest = json.loads(bundle_path.read_text(encoding="utf-8"))
    manifest["expected_artifact_sha256"]["bge"] = "0" * 64
    bundle_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RealAssetError, match="component SHA256"):
        LocalRealAssetBundle.load(bundle_path)


@pytest.mark.parametrize(
    "schema_name,manifest_path",
    [
        ("bge_manifest_v1.schema.json", "bge/bge_manifest.json"),
        ("reranker_manifest_v1.schema.json", "reranker/reranker_manifest.json"),
        ("reply_manifest_v2.schema.json", "reply/reply_manifest.json"),
        ("real_asset_bundle_v1.schema.json", "real_asset_bundle.json"),
    ],
)
def test_delivered_fixture_manifests_match_json_schemas(
    tmp_path, schema_name: str, manifest_path: str
) -> None:
    bundle_path, _, _, _ = make_bundle(tmp_path)
    path = bundle_path.parent / manifest_path
    schema = json.loads((ROOT / "runtime" / "schemas" / schema_name).read_text(encoding="utf-8"))
    value = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)
