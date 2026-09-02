"""Generate resumable on-policy unknown responses from the clean Gemma 4 base."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_12b_base_qlora_60turn as common


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT.parent / "AiPeopleCreate" / "训练数据" / "baiweixi_counterfactual_preference_medium_v2"
SOURCE_PATH = DATA_ROOT / "train.jsonl"
MODEL_PATH = ROOT / "training_packages" / "models" / "Gemma-4-12B-it"
DEFAULT_OUTPUT = (
    ROOT.parent
    / "AiPeopleCreate"
    / "训练数据"
    / "baiweixi_counterfactual_rpo_v1"
    / "base_unknown_candidates.jsonl"
)
SEED_BASE = 2026093000
EXPLICIT_UNKNOWN_MARKERS = (
    "不知道", "不清楚", "不确定", "不记得", "没记住", "没有记住", "没有记录",
    "没有可靠", "没有能确认", "没有答案", "无法确认", "不能确定", "不能随便猜",
    "不敢乱猜", "不想凭空猜", "你有记录吗", "你再提醒", "你告诉我",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def prompt_sha256(messages: list[dict[str, str]]) -> str:
    text = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    if hasattr(__import__("sys").stdout, "reconfigure"):
        __import__("sys").stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--candidates-per-prompt", type=int, default=2)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    source_rows = [row for row in load_jsonl(SOURCE_PATH) if row["condition"] == "unknown"]
    if len(source_rows) != 120:
        raise RuntimeError(f"expected 120 unknown source rows, got {len(source_rows)}")
    existing = load_jsonl(output) if output.is_file() else []
    done = {(row["sample_id"], int(row["candidate_index"])) for row in existing}

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
    for row_index, row in enumerate(source_rows):
        for candidate_index in range(args.candidates_per_prompt):
            key = (row["sample_id"], candidate_index)
            if key in done:
                continue
            generated = common._generate(
                model,
                tokenizer,
                row["prompt"],
                seed=SEED_BASE + row_index * 10 + candidate_index,
                adapter_enabled=True,
                max_new_tokens=64,
                temperature=0.7,
                top_p=0.9,
                repetition_penalty=1.05,
            )
            response = generated["response"]
            record = {
                "sample_id": row["sample_id"],
                "pair_family": row["pair_family"],
                "category": row["category"],
                "candidate_index": candidate_index,
                "seed": SEED_BASE + row_index * 10 + candidate_index,
                "prompt_sha256": prompt_sha256(row["prompt"]),
                "response": response,
                "explicit_unknown_marker": next((marker for marker in EXPLICIT_UNKNOWN_MARKERS if marker in response), None),
                "generation": generated,
                "generated_at": datetime.now().astimezone().isoformat(),
            }
            existing.append(record)
            done.add(key)
            write_jsonl(output, existing)
            print(
                json.dumps(
                    {
                        "completed": len(existing),
                        "total": len(source_rows) * args.candidates_per_prompt,
                        "sample_id": row["sample_id"],
                        "candidate": candidate_index,
                        "explicit_unknown": record["explicit_unknown_marker"],
                        "response": response,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    summary = {
        "rows": len(existing),
        "explicit_unknown": sum(row["explicit_unknown_marker"] is not None for row in existing),
        "eligible_real_negatives": sum(row["explicit_unknown_marker"] is None for row in existing),
    }
    (output.parent / "base_unknown_candidates_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
