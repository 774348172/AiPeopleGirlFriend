"""Run the family-disjoint 48-case A/B for the V5 response-repair probe."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template


ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parent
CREATE_ROOT = ROOT.parent / "AiPeopleCreate"
DATA_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_v5_response_repair_preference_v1"
PACKAGE_ROOT = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b"
STARTING_ADAPTER = PACKAGE_ROOT / "outputs" / "baiweixi_v5_targeted_adapter"
REPAIRED_ADAPTER = PACKAGE_ROOT / "outputs" / "baiweixi_v5_response_repair_rpo_probe"
EXPECTED_SEALED_SHA256 = "37f535a2c4877054705d159e0e5214fc5cef7ce5188fdef4a80ff8cd3804ac78"
SEED_BASE = 2026092400

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_gemma4_12b_base_qlora_60turn as common


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_rows() -> list[dict[str, Any]]:
    path = DATA_ROOT / "sealed.jsonl"
    if sha256(path) != EXPECTED_SEALED_SHA256:
        raise RuntimeError("sealed48 hash does not match the frozen dataset")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 48 or len({row["pair_family"] for row in rows}) != 48:
        raise RuntimeError("sealed set must contain 48 unique scenario families")
    return rows


def load_model(adapter: Path):
    model, tokenizer = FastModel.from_pretrained(
        model_name=str(adapter),
        max_seq_length=1536,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        attn_implementation="eager",
    )
    if not getattr(model, "peft_config", None):
        raise RuntimeError(f"Adapter was not loaded: {adapter}")
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    return model, tokenizer


def run_arm(
    *,
    key: str,
    adapter: Path,
    rows: list[dict[str, Any]],
    output_dir: Path,
) -> list[dict[str, Any]]:
    partial_path = output_dir / f"{key}_responses.partial.jsonl"
    completed = common._load_jsonl(partial_path) if partial_path.is_file() else []
    if len(completed) > len(rows):
        raise RuntimeError(f"invalid partial result: {partial_path}")
    for index, item in enumerate(completed):
        if item.get("sample_id") != rows[index]["sample_id"] or item.get("arm") != key:
            raise RuntimeError(f"partial result does not match sealed input: {partial_path}")
    if len(completed) == len(rows):
        return completed

    model, tokenizer = load_model(adapter)
    try:
        for ordinal, item in enumerate(rows[len(completed):], len(completed) + 1):
            generated = common._generate(
                model,
                tokenizer,
                item["prompt"],
                seed=SEED_BASE + ordinal,
                adapter_enabled=True,
                max_new_tokens=96,
                temperature=0.3,
                top_p=0.9,
                repetition_penalty=1.05,
            )
            record = {
                "ordinal": ordinal,
                "arm": key,
                "sample_id": item["sample_id"],
                "prompt_sha256": hashlib.sha256(
                    json.dumps(item["prompt"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                **generated,
            }
            completed.append(record)
            common._write_jsonl(partial_path, completed)
            print(
                json.dumps(
                    {
                        "turn": ordinal,
                        "arm": key,
                        "cluster": item["cluster"],
                        "elapsed_ms": generated["elapsed_ms"],
                        "response": generated["response"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        del model, tokenizer
        gc.collect()
        torch.cuda.empty_cache()
    return completed


def speed_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    warm = items[1:]
    return {
        "count": len(items),
        "mean_complete_response_ms": round(statistics.fmean(item["elapsed_ms"] for item in items), 2),
        "warm_mean_complete_response_ms": round(statistics.fmean(item["elapsed_ms"] for item in warm), 2),
        "mean_first_visible_ms": round(
            statistics.fmean(item["first_visible_ms"] for item in items if item.get("first_visible_ms") is not None),
            2,
        ),
    }


def memory_text(messages: list[dict[str, str]]) -> str:
    system = messages[0]["content"]
    marker = "[相关记忆]\n"
    if marker not in system:
        return ""
    return system.split(marker, 1)[1].split("\n\n[", 1)[0].strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--starting-adapter", type=Path, default=STARTING_ADAPTER)
    parser.add_argument("--repaired-adapter", type=Path, default=REPAIRED_ADAPTER)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    starting_adapter = args.starting_adapter.resolve()
    repaired_adapter = args.repaired_adapter.resolve()
    rows = load_rows()

    base = run_arm(key="base", adapter=starting_adapter, rows=rows, output_dir=output_dir)
    repaired = run_arm(key="preference", adapter=repaired_adapter, rows=rows, output_dir=output_dir)
    cases = []
    for ordinal, (source, base_answer, repaired_answer) in enumerate(zip(rows, base, repaired), 1):
        cases.append(
            {
                "ordinal": ordinal,
                "sample_id": source["sample_id"],
                "pair_family": source["pair_family"],
                "category": source["cluster"],
                "condition": source["condition"],
                "question": source["prompt"][-1]["content"],
                "visible_memory": memory_text(source["prompt"]),
                "review_focus": (
                    f"期望：{source['chosen'][0]['content']}\n"
                    f"避免：{source['rejected'][0]['content']}"
                ),
                "messages": source["prompt"],
                "answers": {"base": base_answer, "preference": repaired_answer},
            }
        )

    report = {
        "schema_version": 1,
        "scope": "gemma4_v5_response_repair_rpo_sealed48_ab",
        "status": "awaiting_human_review",
        "generated_at": datetime.now().astimezone().isoformat(),
        "automatic_quality_judgment": False,
        "human_adjudication_is_authoritative": True,
        "arms": {
            "base": "A 当前 V5 targeted LoRA（56/60 起点）",
            "preference": "B V5 targeted LoRA + 16步低强度 RPO 修复",
        },
        "controls": {
            "rows": 48,
            "family_disjoint_from_training": True,
            "same_prompt": True,
            "same_seed_per_case": True,
            "same_decoding": True,
            "thinking": False,
            "temperature": 0.3,
            "top_p": 0.9,
            "repetition_penalty": 1.05,
            "max_new_tokens": 96,
            "full_60turn_rerun_forbidden_until_human_gate": True,
        },
        "adapters": {
            "base": {"path": str(starting_adapter), "sha256": sha256(starting_adapter / "adapter_model.safetensors")},
            "preference": {"path": str(repaired_adapter), "sha256": sha256(repaired_adapter / "adapter_model.safetensors")},
        },
        "sealed": {
            "path": str((DATA_ROOT / "sealed.jsonl").resolve()),
            "sha256": EXPECTED_SEALED_SHA256,
        },
        "speed": {"base": speed_summary(base), "preference": speed_summary(repaired)},
        "cases": cases,
    }
    write_json(output_dir / "report.json", report)
    print(f"REPORT={output_dir / 'report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
