from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "training_packages" / "models" / "Gemma-4-12B-it"
DEFAULT_OUTPUT = ROOT / "release_assets" / "Gemma-4-12B-it-nf4"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _quantization_config(config: Any) -> dict[str, Any]:
    value = getattr(config, "quantization_config", None)
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, dict):
        value = config.to_dict().get("quantization_config")
    if not isinstance(value, dict):
        raise RuntimeError("saved model is missing its bitsandbytes quantization config")
    if value.get("quant_method") != "bitsandbytes" or not value.get("load_in_4bit"):
        raise RuntimeError("saved model is not a bitsandbytes 4-bit checkpoint")
    if value.get("bnb_4bit_quant_type") != "nf4":
        raise RuntimeError("saved model quantization type is not NF4")
    return value


def export_nf4_base(source: Path, output: Path) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"source model directory does not exist: {source}")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(
            f"output directory is not empty: {output}. Choose a new directory."
        )
    output.mkdir(parents=True, exist_ok=True)

    import torch
    from unsloth import FastModel

    if not torch.cuda.is_available():
        raise RuntimeError("export requires an NVIDIA CUDA GPU")
    model, tokenizer = FastModel.from_pretrained(
        model_name=str(source),
        max_seq_length=4096,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        local_files_only=True,
        attn_implementation="eager",
    )
    _quantization_config(model.config)
    model.save_pretrained(
        output,
        safe_serialization=True,
        max_shard_size="50GB",
    )
    tokenizer.save_pretrained(output)

    for name in (
        "chat_template.jinja",
        "generation_config.json",
        "processor_config.json",
    ):
        source_path = source / name
        target_path = output / name
        if source_path.is_file() and not target_path.is_file():
            shutil.copy2(source_path, target_path)

    config_path = output / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    quantization = config.get("quantization_config")
    if not isinstance(quantization, dict):
        raise RuntimeError("exported config.json is missing quantization_config")
    if quantization.get("bnb_4bit_quant_type") != "nf4":
        raise RuntimeError("exported config.json is not NF4")
    # Unsloth records the source directory in this non-standard field. Keep a
    # stable model identity in release assets instead of leaking a build-host
    # path or making the checkpoint depend on where it was exported.
    config["model_name"] = "google/gemma-4-12B-it"
    # Unsloth's Gemma4 text-only loader reads the nested text config. Keep the
    # same NF4 metadata there so it recognizes a pre-quantized checkpoint and
    # does not attempt to mutate its protected config proxy during reload.
    text_config = config.get("text_config")
    if isinstance(text_config, dict):
        text_config["quantization_config"] = quantization
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    weight_files = sorted(output.glob("*.safetensors"))
    if len(weight_files) != 1 or weight_files[0].name != "model.safetensors":
        raise RuntimeError("portable NF4 export must contain one model.safetensors")
    files = []
    for path in sorted(item for item in output.iterdir() if item.is_file()):
        files.append(
            {
                "path": path.name,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "source_model": "google/gemma-4-12B-it",
        "source_path": str(source),
        "format": "bitsandbytes-nf4-prequantized",
        "load_in_4bit": True,
        "text_only": True,
        "files": files,
        "total_bytes": sum(item["bytes"] for item in files),
    }
    manifest_path = output / "nf4_export_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the approved Gemma 4 base as a reusable NF4 checkpoint"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = export_nf4_base(args.source, args.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
