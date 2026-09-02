from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import torch
from unsloth import FastModel

import run_gemma4_12b_base_qlora_60turn as comparison


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b"
OUTPUT_ROOT = PACKAGE_ROOT / "outputs" / "baiweixi_gemma4_12b_unsloth"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_step(path: Path, explicit_step: str | None) -> int | None:
    if explicit_step is not None:
        return int(explicit_step)
    match = re.fullmatch(r"checkpoint-(\d+)", path.name)
    return int(match.group(1)) if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Gemma 4 QLoRA training checkpoints")
    parser.add_argument("output_dir")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--checkpoint", choices=("200", "247"))
    source.add_argument("--adapter-path", type=Path)
    parser.add_argument("--key")
    parser.add_argument("--label")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        raise ValueError("--limit must be between 1 and 10")

    checkpoint_path = (
        args.adapter_path.resolve()
        if args.adapter_path is not None
        else OUTPUT_ROOT / f"checkpoint-{args.checkpoint}"
    )
    checkpoint_step = _checkpoint_step(checkpoint_path, args.checkpoint)
    adapter_file = checkpoint_path / "adapter_model.safetensors"
    if not adapter_file.is_file():
        raise FileNotFoundError(adapter_file)

    inputs = comparison._load_jsonl(comparison.FROZEN_INPUTS)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison._write_jsonl(output_dir / "shared_inputs.jsonl", inputs[: args.limit])

    from unsloth.chat_templates import get_chat_template

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(checkpoint_path),
        max_seq_length=4096,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    adapter_sha256 = _sha256(adapter_file)
    key = args.key or (f"checkpoint_{checkpoint_step}" if checkpoint_step is not None else "adapter")
    label = args.label or (
        f"Gemma 4 12B NF4 + Baiweixi QLoRA checkpoint-{checkpoint_step}"
        if checkpoint_step is not None
        else f"Gemma 4 12B NF4 + Baiweixi QLoRA {checkpoint_path.name}"
    )
    arm = comparison._run_arm(
        model,
        tokenizer,
        key=key,
        label=label,
        inputs=inputs,
        output_dir=output_dir,
        limit=args.limit,
        adapter_enabled=True,
        max_new_tokens=512,
        temperature=0.75,
        top_p=0.9,
        repetition_penalty=1.1,
    )
    arm["adapter_sha256"] = adapter_sha256
    result = {
        "schema_version": 1,
        "scope": "gemma4_12b_qlora_adapter_probe",
        "checkpoint": checkpoint_step,
        "adapter_path": str(checkpoint_path),
        "adapter_sha256": adapter_sha256,
        "controls": {
            "turns": args.limit,
            "input_sha256": comparison.EXPECTED_INPUT_SHA256,
            "history_limit_messages": comparison.HISTORY_LIMIT_MESSAGES,
            "thinking": False,
            "temperature": 0.75,
            "top_p": 0.9,
            "repetition_penalty": 1.1,
            "seed_base": comparison.SEED_BASE,
        },
        "arm": arm,
    }
    result_path = output_dir / "probe.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PROBE={result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
