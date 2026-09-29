from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_NF4_BASE = ROOT / "release_assets" / "Gemma-4-12B-it-nf4"
DEFAULT_ADAPTER = (
    ROOT
    / "local_runtime"
    / "adapters"
    / "baiweixi-v3-2611-epoch1-final"
)
DEFAULT_RETRIEVAL = ROOT / "local_runtime" / "models" / "retrieval"
DEFAULT_OUTPUT = ROOT.parent / "release" / "build" / "portable-assets"
ADAPTER_FILES = (
    "adapter_model.safetensors",
    "adapter_config.json",
    "chat_template.jinja",
    "tokenizer.json",
    "tokenizer_config.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _copy_files(source: Path, target: Path, relative_paths: Iterable[str]) -> None:
    for relative in relative_paths:
        source_path = source / Path(relative)
        target_path = target / Path(relative)
        if not source_path.is_file():
            raise FileNotFoundError(f"required asset is missing: {source_path}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)


def _declared_paths(manifest: dict[str, Any], *fields: str) -> list[str]:
    paths: list[str] = []
    for field in fields:
        values = manifest.get(field)
        if not isinstance(values, list):
            raise ValueError(f"manifest field must be an array: {field}")
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError(f"invalid asset entry in {field}")
            paths.append(item["path"])
    return paths


def _copy_retrieval_package(source: Path, target: Path, manifest_name: str) -> dict[str, Any]:
    manifest = json.loads((source / manifest_name).read_text(encoding="utf-8"))
    fields = ("model_files",) if manifest_name == "bge_manifest.json" else (
        "weights",
        "tokenizer_files",
    )
    _copy_files(source, target, _declared_paths(manifest, *fields))
    shutil.copy2(source / manifest_name, target / manifest_name)
    return manifest


def build_assets(nf4_base: Path, adapter: Path, retrieval: Path, output: Path) -> dict[str, Any]:
    from runtime.gemma_nf4_assets import canonical_gemma_nf4_artifact_sha256

    nf4_base = nf4_base.resolve()
    adapter = adapter.resolve()
    retrieval = retrieval.resolve()
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(
            f"output directory is not empty: {output}. Choose a new directory."
        )
    output.mkdir(parents=True, exist_ok=True)

    export_manifest = json.loads(
        (nf4_base / "nf4_export_manifest.json").read_text(encoding="utf-8")
    )
    if export_manifest.get("format") != "bitsandbytes-nf4-prequantized":
        raise ValueError("base model is not the approved pre-quantized NF4 format")

    model_target = output / "models" / "Gemma-4-12B-it-nf4"
    model_paths = [
        item["path"]
        for item in export_manifest["files"]
        if item["path"] != "nf4_export_manifest.json"
    ]
    _copy_files(nf4_base, model_target, model_paths)
    model_config_path = model_target / "config.json"
    model_config = json.loads(model_config_path.read_text(encoding="utf-8"))
    model_config["model_name"] = "google/gemma-4-12B-it"
    quantization = dict(model_config["quantization_config"])
    quantization["llm_int8_skip_modules"] = [
        name for name in quantization["llm_int8_skip_modules"]
        if not name.startswith((
            "vision_tower", "audio_tower", "multi_modal_projector",
            "modality_projection",
        ))
    ]
    model_config["text_config"]["quantization_config"] = quantization
    model_config_path.write_text(
        json.dumps(model_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    adapter_target = output / "adapters" / "baiweixi-v3-2611-epoch1-final"
    _copy_files(adapter, adapter_target, ADAPTER_FILES)
    adapter_config_path = adapter_target / "adapter_config.json"
    adapter_config = json.loads(adapter_config_path.read_text(encoding="utf-8"))
    adapter_config["base_model_name_or_path"] = "../../models/Gemma-4-12B-it-nf4"
    adapter_config_path.write_text(
        json.dumps(adapter_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    bge_source = retrieval / "bge-small-zh-v1.5"
    reranker_source = retrieval / "qwen3-reranker-0.6b"
    retrieval_target = output / "retrieval"
    bge_target = retrieval_target / "bge-small-zh-v1.5"
    reranker_target = retrieval_target / "qwen3-reranker-0.6b"
    bge_manifest = _copy_retrieval_package(
        bge_source, bge_target, "bge_manifest.json"
    )
    reranker_manifest = _copy_retrieval_package(
        reranker_source, reranker_target, "reranker_manifest.json"
    )
    retrieval_manifest = {
        "schema_version": 1,
        "network_access_at_runtime": "forbidden",
        "bge": {
            "path": "bge-small-zh-v1.5",
            "revision": bge_manifest["revision"],
            "artifact_sha256": bge_manifest["artifact_sha256"],
            "size_bytes": sum(
                (bge_target / item).stat().st_size
                for item in _declared_paths(bge_manifest, "model_files")
            ),
        },
        "reranker": {
            "path": "qwen3-reranker-0.6b",
            "revision": reranker_manifest["revision"],
            "artifact_sha256": reranker_manifest["artifact_sha256"],
            "size_bytes": sum(
                (reranker_target / item).stat().st_size
                for item in _declared_paths(
                    reranker_manifest, "weights", "tokenizer_files"
                )
            ),
        },
    }
    (retrieval_target / "retrieval_assets.json").write_text(
        json.dumps(retrieval_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    model_entries = [_entry(model_target / item, model_target) for item in model_paths]
    adapter_entries = [
        _entry(adapter_target / item, adapter_target) for item in ADAPTER_FILES
    ]
    manifest = {
        "schema_version": 1,
        "model_id": "local/baiweixi-gemma-4-12B-v3-2611-epoch1",
        "base_model_id": "google/gemma-4-12B-it",
        "revision": "baiweixi-v3-2611-epoch1-step2332-20260926-portable-nf4",
        "base_revision": "707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7",
        "model_role": "heroine_reply",
        "character_id": "baiweixi",
        "world_id": "songjiangfu",
        "protagonist_id": "protagonist",
        "deployment_profile": "transformers-unsloth-bnb-nf4-prequantized-peft-lora-text-only",
        "adapter_id": "baiweixi-v3-2611-epoch1-final-adapter",
        "network_access": "forbidden",
        "thinking": False,
        "adapter_loaded": True,
        "quantization": "bitsandbytes-nf4-runtime+lora",
        "model_root": "models/Gemma-4-12B-it-nf4",
        "adapter_root": "adapters/baiweixi-v3-2611-epoch1-final",
        "artifact_sha256": None,
        "model_files": model_entries,
        "adapter_files": adapter_entries,
        "runtime": {
            "max_seq_length": 6144,
            "dtype": "bfloat16",
            "device": "cuda",
            "load_in_4bit": True,
            "text_only": True,
            "trust_remote_code": True,
            "use_exact_model_name": True,
            "attention_implementation": "sdpa",
        },
    }
    manifest_path = output / "gemma4_nf4_runtime_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest["artifact_sha256"] = canonical_gemma_nf4_artifact_sha256(manifest_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = {
        "schema_version": 1,
        "format": "portable-bitsandbytes-nf4-base-plus-separate-lora",
        "model_manifest": manifest_path.name,
        "model_artifact_sha256": manifest["artifact_sha256"],
        "base_weight_sha256": next(
            item["sha256"]
            for item in model_entries
            if item["path"] == "model.safetensors"
        ),
        "adapter_weight_sha256": next(
            item["sha256"]
            for item in adapter_entries
            if item["path"] == "adapter_model.safetensors"
        ),
        "total_bytes": sum(
            path.stat().st_size for path in output.rglob("*") if path.is_file()
        ),
    }
    (output / "portable_assets.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build movable NF4 base + separate LoRA runtime assets"
    )
    parser.add_argument("--nf4-base", type=Path, default=DEFAULT_NF4_BASE)
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = build_assets(args.nf4_base, args.adapter, args.retrieval, args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    raise SystemExit(main())
