from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runtime._real_assets import RealAssetError
from runtime.gemma_nf4_assets import (
    GEMMA4_ADAPTER_IDS,
    GEMMA4_BASE_MODEL_ID,
    GEMMA4_MODEL_ID,
    LocalGemmaNF4Package,
    canonical_gemma_nf4_artifact_sha256,
)


FILES = (
    "model.safetensors",
    "config.json",
    "chat_template.jinja",
    "tokenizer.json",
    "tokenizer_config.json",
)
ADAPTER_FILES = (
    "adapter_model.safetensors",
    "adapter_config.json",
    "chat_template.jinja",
    "tokenizer.json",
    "tokenizer_config.json",
)


def make_asset(tmp_path: Path, *, relative_paths: bool = False) -> Path:
    root = tmp_path / "model"
    root.mkdir()
    entries = []
    for index, name in enumerate(FILES, 1):
        value = f"asset-{index}".encode()
        path = root / name
        path.write_bytes(value)
        entries.append(
            {
                "path": name,
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
        )
    adapter_root = tmp_path / "adapter"
    adapter_root.mkdir()
    adapter_entries = []
    for index, name in enumerate(ADAPTER_FILES, 1):
        path = adapter_root / name
        if name == "adapter_config.json":
            value = json.dumps(
                {
                    "peft_type": "LORA",
                    "inference_mode": True,
                    "base_model_name_or_path": (
                        "../model" if relative_paths else str(root.resolve())
                    ),
                }
            ).encode()
        else:
            value = f"adapter-{index}".encode()
        path.write_bytes(value)
        adapter_entries.append(
            {
                "path": name,
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
        )
    manifest = {
        "schema_version": 1,
        "model_id": GEMMA4_MODEL_ID,
        "base_model_id": GEMMA4_BASE_MODEL_ID,
        "revision": "adapter-revision-test",
        "base_revision": "revision-test",
        "model_role": "heroine_reply",
        "character_id": "baiweixi",
        "world_id": "songjiangfu",
        "protagonist_id": "protagonist",
        "deployment_profile": "transformers-unsloth-bnb-nf4-peft-lora-text-only",
        "adapter_id": GEMMA4_ADAPTER_IDS[GEMMA4_MODEL_ID],
        "network_access": "forbidden",
        "thinking": False,
        "adapter_loaded": True,
        "quantization": "bitsandbytes-nf4-runtime+lora",
        "model_root": "model" if relative_paths else str(root.resolve()),
        "adapter_root": "adapter" if relative_paths else str(adapter_root.resolve()),
        "artifact_sha256": None,
        "model_files": entries,
        "adapter_files": adapter_entries,
        "runtime": {
            "max_seq_length": 4096,
            "dtype": "bfloat16",
            "device": "cuda",
            "load_in_4bit": True,
            "text_only": True,
            "trust_remote_code": True,
            "use_exact_model_name": True,
            "attention_implementation": "eager",
        },
    }
    path = tmp_path / "gemma4_nf4_runtime_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest["artifact_sha256"] = canonical_gemma_nf4_artifact_sha256(path)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_gemma_nf4_asset_freezes_identity_files_and_runtime(tmp_path: Path) -> None:
    asset = LocalGemmaNF4Package.load(make_asset(tmp_path))

    assert asset.identity.model_id == GEMMA4_MODEL_ID
    assert asset.base_model_id == GEMMA4_BASE_MODEL_ID
    assert asset.identity.character_id == "baiweixi"
    assert asset.identity.artifact_sha256
    assert asset.adapter_root.name == "adapter"
    assert asset.runtime.max_seq_length == 4096
    assert {path.name for path in asset.model_files} == set(FILES)
    assert {path.name for path in asset.adapter_files} == set(ADAPTER_FILES)


def test_gemma_nf4_asset_rejects_changed_model_file(tmp_path: Path) -> None:
    manifest = make_asset(tmp_path)
    root = Path(json.loads(manifest.read_text())["model_root"])
    (root / "model.safetensors").write_bytes(b"changed")

    with pytest.raises(RealAssetError, match="size mismatch|SHA256 mismatch"):
        LocalGemmaNF4Package.load(manifest)


@pytest.mark.parametrize("attention", ["eager", "sdpa", "unverified_backend"])
def test_attention_profile_is_explicit_and_integrity_checked(tmp_path, attention):
    path = make_asset(tmp_path)
    manifest = json.loads(path.read_text())
    manifest["runtime"]["attention_implementation"] = attention
    path.write_text(json.dumps(manifest))
    manifest["artifact_sha256"] = canonical_gemma_nf4_artifact_sha256(path)
    path.write_text(json.dumps(manifest))
    if attention == "unverified_backend":
        with pytest.raises(RealAssetError, match="frozen stack"):
            LocalGemmaNF4Package.load(path)
    else:
        assert LocalGemmaNF4Package.load(path).runtime.attention_implementation == attention


def test_gemma_nf4_asset_supports_movable_relative_paths(tmp_path: Path) -> None:
    source = tmp_path / "source package"
    source.mkdir()
    manifest = make_asset(source, relative_paths=True)
    moved = tmp_path / "moved package"
    source.rename(moved)

    asset = LocalGemmaNF4Package.load(moved / manifest.name)

    assert asset.model_root == (moved / "model").resolve()
    assert asset.adapter_root == (moved / "adapter").resolve()
    assert asset.identity.artifact_sha256 == canonical_gemma_nf4_artifact_sha256(
        moved / manifest.name
    )
