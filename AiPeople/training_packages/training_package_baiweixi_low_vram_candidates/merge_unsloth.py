from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import torch
from unsloth import FastModel
from safetensors import safe_open


ROOT = Path(__file__).resolve().parent
PACKAGES_ROOT = ROOT.parent
CONFIGS = {
    "qwen35_9b": {
        "model": PACKAGES_ROOT / "models" / "Qwen3.5-9B",
        "adapter": ROOT / "outputs" / "baiweixi_qwen35_9b_unsloth",
        "merged": ROOT / "outputs" / "baiweixi_qwen35_9b_merged_text_v2",
    },
    "qwen3_14b": {
        "model": PACKAGES_ROOT / "models" / "Qwen3-14B",
        "adapter": ROOT / "outputs" / "baiweixi_qwen3_14b_unsloth",
        "merged": ROOT / "outputs" / "baiweixi_qwen3_14b_merged_text_v2",
    },
    "qwen3_14b_thinking_preserve_v2": {
        "model": PACKAGES_ROOT / "models" / "Qwen3-14B",
        "adapter": ROOT / "outputs" / "baiweixi_qwen3_14b_thinking_preserve_v2",
        "merged": ROOT / "outputs" / "baiweixi_qwen3_14b_thinking_preserve_v2_merged",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge a Baiweixi adapter with the matching Unsloth text model")
    parser.add_argument("model_key", choices=sorted(CONFIGS))
    args = parser.parse_args()
    config = CONFIGS[args.model_key]
    for key in ("model", "adapter"):
        if not config[key].is_dir():
            raise FileNotFoundError(config[key])

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(config["adapter"]),
        max_seq_length=4096,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
    )
    adapter_modules = sum(1 for name, _ in model.named_parameters() if "lora_" in name)
    if adapter_modules == 0:
        raise RuntimeError("adapter loaded without any LoRA parameter tensors")

    started = time.time()
    model.save_pretrained_merged(
        str(config["merged"]),
        tokenizer,
        save_method="merged_16bit",
    )
    merged_config_path = config["merged"] / "config.json"
    merged_config = json.loads(merged_config_path.read_text(encoding="utf-8"))
    removed_quantization_metadata = merged_config.pop("quantization_config", None) is not None
    merged_config_path.write_text(
        json.dumps(merged_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tensor_names: list[str] = []
    tensor_dtypes: set[str] = set()
    for shard in sorted(config["merged"].glob("*.safetensors")):
        with safe_open(str(shard), framework="pt") as handle:
            for name in handle.keys():
                tensor_names.append(name)
                tensor_dtypes.add(str(handle.get_slice(name).get_dtype()))
    forbidden_tensor_names = [
        name for name in tensor_names if "lora_" in name or ".base_layer." in name
    ]
    if forbidden_tensor_names:
        raise RuntimeError(
            f"merged output still contains adapter/quantized wrapper tensors: {forbidden_tensor_names[:5]}"
        )
    if not tensor_names or not tensor_dtypes.issubset({"BF16", "F16", "F32"}):
        raise RuntimeError(f"unexpected merged tensor dtypes: {sorted(tensor_dtypes)}")
    metrics = {
        "model_key": args.model_key,
        "base_model": str(config["model"].resolve()),
        "adapter": str(config["adapter"].resolve()),
        "merged": str(config["merged"].resolve()),
        "adapter_parameter_tensors_loaded": adapter_modules,
        "merge_seconds": time.time() - started,
        "merge_runtime": "Unsloth FastModel text_only + PEFT",
        "save_method": "merged_16bit",
        "removed_stale_quantization_metadata": removed_quantization_metadata,
        "merged_tensor_count": len(tensor_names),
        "merged_tensor_dtypes": sorted(tensor_dtypes),
        "forbidden_tensor_names": forbidden_tensor_names,
    }
    (config["merged"] / "merge_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
