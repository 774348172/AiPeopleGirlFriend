from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any

import torch


ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parent
TRAINING_ROOT = ROOT / "training_packages" / "training_package_baiweixi_qwen35_27b"
MODEL_PATH = ROOT / "training_packages" / "models" / "Qwen3.5-27B"
ADAPTER_PATH = TRAINING_ROOT / "outputs" / "baiweixi_27b_unsloth"
FROZEN_INPUTS = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "qwen35_27b_gpt56_60turn_20260824-114447"
    / "shared_inputs.jsonl"
)
EXPECTED_INPUT_SHA256 = "0593a2606b470b7dd4e0ab34cf59ac96095d996fb2022bb8f8ba4ac2aa2e6720"
EXPECTED_ADAPTER_SHA256 = "d5b28db20d1f27a8a6c8cbcd5c236a0783c49588e39db643a512b044cdcd962e"
HISTORY_LIMIT_MESSAGES = 6
SEED_BASE = 2026082200

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_baiweixi_60turn_player_simulation as simulation


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _validate_inputs(values: list[dict[str, Any]]) -> None:
    if len(values) != 60:
        raise ValueError(f"frozen input count must be 60, got {len(values)}")
    for ordinal, value in enumerate(values, 1):
        if value.get("ordinal") != ordinal:
            raise ValueError(f"input ordinal mismatch at {ordinal}")
        for key in ("phase", "system", "player"):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise ValueError(f"input {ordinal} has invalid {key}")
        if {"expectation", "required_groups", "forbidden_facts"}.intersection(value):
            raise ValueError(f"input {ordinal} leaks oracle fields")


def _messages(turn: dict[str, Any], history: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": turn["system"]},
        *history[-HISTORY_LIMIT_MESSAGES:],
        {"role": "user", "content": turn["player"]},
    ]


def _generate(model: Any, tokenizer: Any, messages: list[dict[str, str]], seed: int) -> dict[str, Any]:
    encoded = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_tensors="pt",
        return_dict=True,
    ).to("cuda")
    prompt_tokens = int(encoded["input_ids"].shape[-1])
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    started = time.perf_counter()
    with torch.inference_mode():
        generated = model.generate(
            **encoded,
            max_new_tokens=180,
            do_sample=True,
            temperature=0.75,
            top_p=0.9,
            repetition_penalty=1.1,
            use_cache=True,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    continuation = generated[0, prompt_tokens:]
    reply = tokenizer.decode(continuation, skip_special_tokens=True).strip()
    if not reply:
        raise RuntimeError("model returned an empty response")
    return {
        "response": reply,
        "elapsed_ms": elapsed_ms,
        "prompt_tokens": prompt_tokens,
        "output_tokens": int(continuation.numel()),
    }


def _load_partial(path: Path, arm: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values = _load_jsonl(path)
    for ordinal, value in enumerate(values, 1):
        if value.get("ordinal") != ordinal or value.get("arm") != arm:
            raise ValueError(f"invalid partial result in {path} at row {ordinal}")
    return values


def _run_arm(
    key: str,
    model: Any,
    tokenizer: Any,
    inputs: list[dict[str, Any]],
    output_dir: Path,
    adapter_enabled: bool,
    limit: int,
) -> dict[str, Any]:
    partial_path = output_dir / f"{key}_responses.partial.jsonl"
    turns = _load_partial(partial_path, key)
    history: list[dict[str, str]] = []
    for source, completed in zip(inputs, turns):
        history.extend(
            (
                {"role": "user", "content": source["player"]},
                {"role": "assistant", "content": completed["response"]},
            )
        )

    selected = inputs[:limit]
    context = nullcontext() if adapter_enabled else model.disable_adapter()
    with context:
        for turn in selected[len(turns) :]:
            ordinal = int(turn["ordinal"])
            messages = _messages(turn, history)
            prompt = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
            generated = _generate(model, tokenizer, messages, SEED_BASE + ordinal)
            record = {
                "arm": key,
                "ordinal": ordinal,
                "messages": messages,
                "prompt_sha256": _sha256_text(prompt),
                "seed": SEED_BASE + ordinal,
                **generated,
            }
            turns.append(record)
            history.extend(
                (
                    {"role": "user", "content": turn["player"]},
                    {"role": "assistant", "content": generated["response"]},
                )
            )
            _write_jsonl(partial_path, turns)
            print(
                json.dumps(
                    {
                        "arm": key,
                        "turn": ordinal,
                        "elapsed_ms": generated["elapsed_ms"],
                        "response": generated["response"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return {
        "key": key,
        "label": "Qwen3.5-27B NF4 裸基座" if key == "base" else "Qwen3.5-27B NF4 + 白未晞 LoRA",
        "turns": turns,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fair Qwen3.5-27B NF4 base vs Baiweixi LoRA 60-turn test")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.limit <= 60:
        raise ValueError("--limit must be between 1 and 60")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if _sha256(FROZEN_INPUTS) != EXPECTED_INPUT_SHA256:
        raise ValueError("frozen 60-turn input SHA256 mismatch")
    adapter_file = ADAPTER_PATH / "adapter_model.safetensors"
    if _sha256(adapter_file) != EXPECTED_ADAPTER_SHA256:
        raise ValueError("trained adapter SHA256 mismatch")
    inputs = _load_jsonl(FROZEN_INPUTS)
    _validate_inputs(inputs)
    _write_jsonl(output_dir / "shared_inputs.jsonl", inputs)

    from unsloth import FastModel
    from peft import PeftModel

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(MODEL_PATH),
        max_seq_length=4096,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
    )
    model = PeftModel.from_pretrained(model, str(ADAPTER_PATH), is_trainable=False)
    if not model.config.architectures:
        model.config.architectures = [model.get_base_model().__class__.__name__]
    FastModel.for_inference(model)
    model.eval()

    base_arm = _run_arm("base", model, tokenizer, inputs, output_dir, False, args.limit)
    lora_arm = _run_arm("lora", model, tokenizer, inputs, output_dir, True, args.limit)
    if args.limit != 60:
        print(json.dumps({"status": "probe_completed", "turns_per_arm": args.limit}, ensure_ascii=False))
        return 0

    report_turns = []
    for input_turn, source, base, lora in zip(inputs, simulation.TURNS, base_arm["turns"], lora_arm["turns"], strict=True):
        report_turns.append(
            {
                "turn": input_turn["ordinal"],
                "phase": input_turn["phase"],
                "player": input_turn["player"],
                "authoritative_world": source["world"],
                "expectation": source["expectation"],
                "base": base,
                "lora": lora,
            }
        )
    all_elapsed = {
        key: [float(turn["elapsed_ms"]) for turn in arm["turns"]]
        for key, arm in (("base", base_arm), ("lora", lora_arm))
    }
    report = {
        "schema_version": 1,
        "scope": "qwen35_27b_nf4_base_vs_baiweixi_lora_stateful_60turn",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "completed",
        "controls": {
            "turn_count": 60,
            "history_limit_messages": HISTORY_LIMIT_MESSAGES,
            "same_frozen_oracle_free_inputs": True,
            "input_sha256": EXPECTED_INPUT_SHA256,
            "same_base_weights": True,
            "same_nf4_quantized_model_instance": True,
            "only_independent_variable": "LoRA adapter enabled or disabled",
            "each_arm_retains_own_history": True,
            "arm_order": ["base", "lora"],
            "generation": {
                "temperature": 0.75,
                "top_p": 0.9,
                "repetition_penalty": 1.1,
                "max_context_tokens": 4096,
                "max_new_tokens": 180,
                "seed_base": SEED_BASE,
                "thinking": False,
            },
            "base_model_path": str(MODEL_PATH),
            "adapter_path": str(ADAPTER_PATH),
            "adapter_sha256": EXPECTED_ADAPTER_SHA256,
            "limitations": [
                "After turn 1 each arm has different self-generated history by design.",
                "This single trajectory is for paired human review, not a general online error-rate estimate.",
                "Latency is affected by arm order and shared kernel/model caches; quality comparison is the primary purpose.",
            ],
        },
        "arms": {"base": base_arm, "lora": lora_arm},
        "turns": report_turns,
        "summary": {
            "turns": 60,
            "base_completed": len(base_arm["turns"]),
            "lora_completed": len(lora_arm["turns"]),
            "base_mean_elapsed_ms": round(statistics.fmean(all_elapsed["base"]), 2),
            "lora_mean_elapsed_ms": round(statistics.fmean(all_elapsed["lora"]), 2),
            "automatic_quality_judgment": False,
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "generated_at": report["generated_at"],
        "report_sha256": _sha256(report_path),
        "input_sha256": EXPECTED_INPUT_SHA256,
        "adapter_sha256": EXPECTED_ADAPTER_SHA256,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
