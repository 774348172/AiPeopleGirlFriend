"""Test clean Gemma 4 12B with the Qwen-validated grounding principle."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "training_packages" / "models" / "Gemma-4-12B-it"
QWEN_REPORT = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "qwen35_27b_grounding_unknown24_known8_20260901"
    / "report.json"
)
SEED_BASE = 2026092000


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def generate(
    model: Any,
    tokenizer: Any,
    messages: list[dict[str, str]],
    seed: int,
) -> dict[str, Any]:
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    encoded = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_tensors="pt",
        return_dict=True,
    )
    encoded = {key: value.to("cuda") for key, value in encoded.items()}
    started = time.perf_counter()
    with torch.inference_mode():
        output = model.generate(
            **encoded,
            max_new_tokens=96,
            do_sample=False,
            repetition_penalty=1.05,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=list(
                dict.fromkeys(
                    token_id
                    for token_id in (tokenizer.eos_token_id, tokenizer.eot_token_id)
                    if token_id is not None
                )
            ),
            use_cache=True,
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    prompt_tokens = int(encoded["input_ids"].shape[-1])
    generated_ids = output[0, prompt_tokens:]
    parsed = tokenizer.parse_response(generated_ids)
    response = str(parsed.get("content") or "").strip()
    thinking = str(parsed.get("thinking") or "").strip()
    if not response:
        response = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    if not response:
        raise RuntimeError("Gemma returned an empty response")
    eval_count = int(generated_ids.shape[-1])
    return {
        "response": response,
        "thinking": thinking or None,
        "elapsed_ms": elapsed_ms,
        "prompt_eval_count": prompt_tokens,
        "eval_count": eval_count,
        "eval_tokens_per_second": round(eval_count / max(elapsed_ms / 1000, 1e-9), 2),
        "done_reason": "stop",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--max-cases", type=int)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    qwen_report = json.loads(QWEN_REPORT.read_text(encoding="utf-8"))
    cases = qwen_report["cases"]
    if args.max_cases is not None:
        cases = cases[: args.max_cases]

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(MODEL_PATH),
        max_seq_length=896,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        attn_implementation="eager",
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)

    results: list[dict[str, Any]] = []
    for position, case in enumerate(cases, 1):
        generated = generate(
            model,
            tokenizer,
            case["messages"],
            SEED_BASE + int(case["ordinal"]),
        )
        result = {
            **case,
            "answers": {
                "base": case["answers"]["preference"],
                "preference": generated,
            },
        }
        results.append(result)
        write_json(output_dir / "report.partial.json", {"cases": results})
        print(
            json.dumps(
                {
                    "case": position,
                    "ordinal": case["ordinal"],
                    "condition": case["condition"],
                    "response": generated["response"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    timings = [case["answers"]["preference"] for case in results]
    report = {
        "schema_version": 1,
        "scope": "qwen35_27b_vs_gemma4_12b_same_grounding_principle",
        "status": "awaiting_human_review",
        "generated_at": datetime.now().astimezone().isoformat(),
        "automatic_quality_judgment": False,
        "human_adjudication_is_authoritative": True,
        "arms": {
            "base": "A Qwen3.5-27B Q4 裸基座（已通过冻结结果）",
            "preference": "B Gemma 4 12B NF4 裸基座（本次生成）",
        },
        "controls": {
            "case_count": len(results),
            "same_messages": True,
            "same_grounding_principle": True,
            "same_seed_per_case": True,
            "temperature": 0.0,
            "greedy_decoding": True,
            "repeat_penalty": 1.05,
            "max_new_tokens": 96,
            "thinking": False,
            "adapter_loaded": False,
            "limitations": [
                "Qwen and Gemma use different inference runtimes and native chat templates.",
                "Qwen timings are from Ollama; Gemma timings are from Transformers/Unsloth.",
            ],
        },
        "models": {
            "qwen": qwen_report["models"]["qwen"],
            "qwen_digest": qwen_report["models"]["qwen_digest"],
            "gemma": str(MODEL_PATH.resolve()),
        },
        "sources": {
            "qwen_report": str(QWEN_REPORT.resolve()),
            "qwen_report_sha256": sha256(QWEN_REPORT),
        },
        "speed": {
            "gemma_mean_elapsed_ms": round(statistics.fmean(item["elapsed_ms"] for item in timings), 2),
            "gemma_mean_eval_tokens_per_second": round(
                statistics.fmean(item["eval_tokens_per_second"] for item in timings), 2
            ),
        },
        "cases": results,
    }
    write_json(output_dir / "report.json", report)
    print(f"REPORT={output_dir / 'report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
