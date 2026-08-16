from __future__ import annotations

import hashlib
import json
from pathlib import Path

from runtime._real_assets import (
    canonical_bge_package_sha256,
    canonical_reply_package_sha256,
)
from runtime._reranker_assets import canonical_package_sha256


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_bge_package(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "1_Pooling").mkdir()
    (root / "modules.json").write_text(
        json.dumps([{"idx": 1, "path": "1_Pooling", "type": "sentence_transformers.models.Pooling"}]),
        encoding="utf-8",
    )
    (root / "sentence_bert_config.json").write_text(
        json.dumps({"max_seq_length": 512}), encoding="utf-8"
    )
    (root / "1_Pooling" / "config.json").write_text(
        json.dumps(
            {
                "word_embedding_dimension": 512,
                "pooling_mode_mean_tokens": True,
                "pooling_mode_cls_token": False,
            }
        ),
        encoding="utf-8",
    )
    (root / "model.safetensors").write_bytes(b"bge-weights")
    files = [
        "1_Pooling/config.json",
        "model.safetensors",
        "modules.json",
        "sentence_bert_config.json",
    ]
    manifest = {
        "schema_version": 1,
        "model_id": "BAAI/bge-small-zh-v1.5",
        "revision": "bge-delivered-r1",
        "artifact_sha256": None,
        "dimension": 512,
        "maximum_sequence_length": 512,
        "pooling": "mean",
        "normalization": "runtime_l2",
        "device": "cpu",
        "network_access": "forbidden",
        "model_files": [{"path": value, "sha256": sha(root / value)} for value in files],
    }
    path = root / "bge_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest["artifact_sha256"] = canonical_bge_package_sha256(root)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return root


def make_reranker_package(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "model.safetensors").write_bytes(b"reranker-weights")
    (root / "tokenizer.json").write_bytes(b"reranker-tokenizer")
    manifest = {
        "schema_version": 1,
        "model_id": "Qwen/Qwen3-Reranker-0.6B",
        "revision": "reranker-delivered-r1",
        "deployment_profile": "cuda-fp16",
        "activation_threshold": 0.7,
        "relative_top_floor": 0.1,
        "relative_top_margin": 0.05,
        "maximum_pair_tokens": 128,
        "output_contract": "yes_no_logits_per_memory_id",
        "weights": [{"path": "model.safetensors", "sha256": sha(root / "model.safetensors")}],
        "tokenizer_files": [{"path": "tokenizer.json", "sha256": sha(root / "tokenizer.json")}],
        "artifact_sha256": None,
    }
    path = root / "reranker_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest["artifact_sha256"] = canonical_package_sha256(root)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return root


def make_reply_package(
    root: Path,
    *,
    model_role: str = "heroine_reply",
    character_id: str = "baiweixi",
    world_id: str = "songjiangfu",
    protagonist_id: str = "protagonist",
) -> Path:
    root.mkdir(parents=True)
    (root / "model.gguf").write_bytes(b"qwen35-gguf")
    (root / "chat_template.jinja").write_text("{{ messages }}", encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "model_id": "Qwen/Qwen3.5-4B",
        "revision": "baiweixi-reply-r1",
        "base_revision": "qwen35-base-r1",
        "model_role": model_role,
        "character_id": character_id,
        "world_id": world_id,
        "protagonist_id": protagonist_id,
        "deployment_profile": "gguf-q4_k_m",
        "artifact_sha256": None,
        "network_access": "forbidden",
        "thinking": False,
        "gguf": {"path": "model.gguf", "sha256": sha(root / "model.gguf"), "quantization": "Q4_K_M"},
        "chat_template": {"path": "chat_template.jinja", "sha256": sha(root / "chat_template.jinja")},
    }
    path = root / "reply_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest["artifact_sha256"] = canonical_reply_package_sha256(root)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return root


def make_bundle(
    root: Path,
    *,
    reply_model_role: str = "heroine_reply",
    character_id: str = "baiweixi",
    world_id: str = "songjiangfu",
    protagonist_id: str = "protagonist",
) -> tuple[Path, Path, Path, Path]:
    bge_root = make_bge_package(root / "bge")
    reranker_root = make_reranker_package(root / "reranker")
    reply_root = make_reply_package(
        root / "reply",
        model_role=reply_model_role,
        character_id=character_id,
        world_id=world_id,
        protagonist_id=protagonist_id,
    )
    runtime_manifest = root / "llama_runtime_manifest.json"
    runtime_manifest.write_text("{}", encoding="utf-8")
    bge = json.loads((bge_root / "bge_manifest.json").read_text(encoding="utf-8"))
    reranker = json.loads((reranker_root / "reranker_manifest.json").read_text(encoding="utf-8"))
    reply = json.loads((reply_root / "reply_manifest.json").read_text(encoding="utf-8"))
    bundle = {
        "schema_version": 1,
        "network_access": "forbidden",
        "bge_package": str(bge_root.resolve()),
        "reranker_package": str(reranker_root.resolve()),
        "reply_package": str(reply_root.resolve()),
        "llama_runtime_manifest": str(runtime_manifest.resolve()),
        "expected_artifact_sha256": {
            "bge": bge["artifact_sha256"],
            "reranker": reranker["artifact_sha256"],
            "reply": reply["artifact_sha256"],
        },
    }
    bundle_path = root / "real_asset_bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    return bundle_path, bge_root, reranker_root, reply_root
