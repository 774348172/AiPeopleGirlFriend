from __future__ import annotations

import json
from pathlib import Path

from unsloth.chat_templates import get_chat_template

from transformers import AutoTokenizer


ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT.parent / "models" / "Gemma-4-12B-it"
DATA_PATH = (
    ROOT.parent
    / "training_package_baiweixi_qwen35_27b"
    / "data"
    / "baiweixi_27b_ready.jsonl"
)
ROLE_MAP = {"system": "system", "human": "user", "gpt": "assistant"}


def main() -> None:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    stats: list[tuple[int, int, int, int]] = []
    for line_number, line in enumerate(DATA_PATH.read_text(encoding="utf-8").splitlines(), 1):
        record = json.loads(line)
        messages = [
            {"role": ROLE_MAP[item["from"]], "content": item["value"]}
            for item in record["conversations"]
        ]
        full_text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False
        )
        prefix_text = tokenizer.apply_chat_template(
            messages[:-1], tokenize=False, add_generation_prompt=True
        )
        full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]
        prefix_ids = tokenizer(prefix_text, add_special_tokens=False)["input_ids"]
        if not full_text.startswith(prefix_text):
            raise ValueError(f"Assistant prefix mismatch at source line {line_number}")
        stats.append(
            (len(full_ids), len(prefix_ids), len(full_ids) - len(prefix_ids), line_number)
        )

    stats.sort(reverse=True)
    result = {
        "rows": len(stats),
        "max_full_tokens": stats[0][0],
        "max_prefix_tokens": max(item[1] for item in stats),
        "max_assistant_tokens": max(item[2] for item in stats),
        "over_1152": sum(item[0] > 1152 for item in stats),
        "over_1536": sum(item[0] > 1536 for item in stats),
        "over_2048": sum(item[0] > 2048 for item in stats),
        "longest_20": [
            {
                "full_tokens": full,
                "prefix_tokens": prefix,
                "assistant_tokens": answer,
                "source_line": line_number,
            }
            for full, prefix, answer, line_number in stats[:20]
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
