"""Run the V5 phase-4 frozen 10-turn base/old/new Adapter comparison."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from datetime import datetime
from pathlib import Path

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_12b_base_qlora_60turn as common

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b"
OLD_ADAPTER = PACKAGE / "outputs" / "baiweixi_gemma4_12b_all_assistant_v2"
NEW_ADAPTER = PACKAGE / "outputs" / "baiweixi_v5_pilot_adapter"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_arm_model(adapter: Path):
    model, tokenizer = FastModel.from_pretrained(
        model_name=str(adapter), max_seq_length=4096, dtype=torch.bfloat16,
        load_in_4bit=True, text_only=True, trust_remote_code=True,
        use_exact_model_name=True,
    )
    return model, get_chat_template(tokenizer, chat_template="gemma-4")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if args.limit != 10:
        raise ValueError("V5 freeze comparison is fixed at 10 turns")
    if not OLD_ADAPTER.is_dir() or not NEW_ADAPTER.is_dir():
        raise FileNotFoundError("old or new Adapter directory is missing")
    inputs = common._load_jsonl(common.FROZEN_INPUTS)
    if len(inputs) < args.limit:
        raise RuntimeError("frozen input set is shorter than 10 turns")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    common._write_jsonl(args.output_dir / "shared_inputs.jsonl", inputs[:args.limit])

    new_model, new_tokenizer = load_arm_model(NEW_ADAPTER)
    FastModel.for_inference(new_model)
    base = common._run_arm(new_model, new_tokenizer, key="base", label="Gemma 4 12B NF4 裸基座", inputs=inputs, output_dir=args.output_dir, limit=args.limit, adapter_enabled=False, max_new_tokens=512, temperature=0.75, top_p=0.9, repetition_penalty=1.1)
    new = common._run_arm(new_model, new_tokenizer, key="new_v5", label="Gemma 4 12B NF4 + V5 白未晞 Adapter", inputs=inputs, output_dir=args.output_dir, limit=args.limit, adapter_enabled=True, max_new_tokens=512, temperature=0.75, top_p=0.9, repetition_penalty=1.1)
    del new_model, new_tokenizer
    gc.collect()
    torch.cuda.empty_cache()

    old_model, old_tokenizer = load_arm_model(OLD_ADAPTER)
    FastModel.for_inference(old_model)
    old = common._run_arm(old_model, old_tokenizer, key="old", label="Gemma 4 12B NF4 + 旧白未晞 Adapter", inputs=inputs, output_dir=args.output_dir, limit=args.limit, adapter_enabled=True, max_new_tokens=512, temperature=0.75, top_p=0.9, repetition_penalty=1.1)
    del old_model, old_tokenizer
    gc.collect()
    torch.cuda.empty_cache()

    generated_at = datetime.now().astimezone().isoformat()
    report = {
        "schema_version": 1,
        "scope": "gemma4_12b_v5_freeze10_base_old_new",
        "status": "completed",
        "generated_at": generated_at,
        "controls": {
            "turns": args.limit,
            "input_sha256": common.EXPECTED_INPUT_SHA256,
            "same_base_checkpoint": True,
            "same_nf4_runtime": True,
            "same_prompt_and_decoding": True,
            "history_isolated_per_arm": True,
            "thinking": False,
            "temperature": 0.75,
            "top_p": 0.9,
            "repetition_penalty": 1.1,
            "max_new_tokens": 512,
        },
        "adapters": {
            "old": {"path": str(OLD_ADAPTER.resolve()), "sha256": sha256(OLD_ADAPTER / "adapter_model.safetensors")},
            "new_v5": {"path": str(NEW_ADAPTER.resolve()), "sha256": sha256(NEW_ADAPTER / "adapter_model.safetensors")},
        },
        "arms": {"base": base, "old": old, "new_v5": new},
        "turns": [
            {"turn": source["ordinal"], "phase": source["phase"], "player": source["player"], "authoritative_world": source.get("world"), "expectation": source.get("expectation"), "base": base["turns"][index], "old": old["turns"][index], "new_v5": new["turns"][index]}
            for index, source in enumerate(inputs[:args.limit])
        ],
        "summary": {
            "automatic_quality_judgment": False,
            "manual_review_required": True,
            "speed": {key: common._speed_summary(value["turns"]) for key, value in (("base", base), ("old", old), ("new_v5", new))},
        },
    }
    path = args.output_dir / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "manifest.json").write_text(json.dumps({"schema_version": 1, "report_sha256": sha256(path), "input_sha256": common.EXPECTED_INPUT_SHA256, "old_adapter_sha256": report["adapters"]["old"]["sha256"], "new_v5_adapter_sha256": report["adapters"]["new_v5"]["sha256"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
