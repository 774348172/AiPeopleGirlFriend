"""Clean ChatHaruhi Yu Qian samples and create deterministic data splits."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


SYSTEM_PROMPT = (
    "你是相声演员于谦，以本人身份与用户自然对话。你的表达简短、沉稳、机敏，"
    "擅长接话和适度调侃，保持捧哏式节奏。优先直接回答用户的问题，不要把每句话"
    "都变成相声，不要自称AI，不要替郭德纲或其他人说话。不了解的事情坦率说明。"
)

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
REPEATED_FRAGMENT_RE = re.compile(r"(.{1,12}?)\1{4,}")
AI_TERMS_RE = re.compile(r"(?:人工智能|语言模型|ChatGPT|聊天机器人)", re.IGNORECASE)


def strip_wrapping_quotes(text: str) -> str:
    text = text.strip()
    while len(text) >= 2 and (text[0], text[-1]) in {
        ("「", "」"),
        ("『", "』"),
        ('"', '"'),
        ("'", "'"),
    }:
        text = text[1:-1].strip()
    return re.sub(r"[ \t]+", " ", text)


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_record(question: str, answer: str) -> dict[str, object]:
    return {
        "system": SYSTEM_PROMPT,
        "conversations": [
            {"from": "human", "value": question},
            {"from": "gpt", "value": answer},
        ],
    }


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("GithubData/chat/Haruhi_54K_v1.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("training/data_yuqian"),
    )
    parser.add_argument("--small-train-size", type=int, default=500)
    parser.add_argument("--small-val-size", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    counters: Counter[str] = Counter()
    cleaned: dict[str, tuple[str, str]] = {}
    source_bytes = args.input.read_bytes()

    with args.input.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            counters["input_rows"] += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                counters["invalid_json"] += 1
                continue

            if raw.get("agent_role_name_en") != "yuqian":
                continue
            counters["target_role_rows"] += 1

            question = strip_wrapping_quotes(str(raw.get("user_question", "")))
            answer = strip_wrapping_quotes(str(raw.get("agent_response", "")))
            combined = f"{question} {answer}"

            if not HAN_RE.search(combined) or ASCII_LETTER_RE.search(combined):
                counters["dropped_non_chinese"] += 1
                continue
            if not (3 <= len(question) <= 200 and 3 <= len(answer) <= 100):
                counters["dropped_length"] += 1
                continue
            if AI_TERMS_RE.search(combined):
                counters["dropped_ai_terms"] += 1
                continue
            if REPEATED_FRAGMENT_RE.search(answer):
                counters["dropped_repetition"] += 1
                continue

            key = f"{question}\n{answer}"
            digest = stable_hash(key)
            if digest in cleaned:
                counters["dropped_duplicate"] += 1
                continue
            cleaned[digest] = (question, answer)

    ordered = sorted(cleaned.items())
    all_rows = [make_record(question, answer) for _, (question, answer) in ordered]
    total = len(all_rows)
    train_end = int(total * 0.8)
    val_end = train_end + int(total * 0.1)

    train_rows = all_rows[:train_end]
    val_rows = all_rows[train_end:val_end]
    test_rows = all_rows[val_end:]
    train_small = train_rows[: min(args.small_train_size, len(train_rows))]
    val_small = val_rows[: min(args.small_val_size, len(val_rows))]

    outputs = {
        "yuqian_style_train.jsonl": train_rows,
        "yuqian_style_validation.jsonl": val_rows,
        "yuqian_style_test.jsonl": test_rows,
        "yuqian_style_train_small.jsonl": train_small,
        "yuqian_style_validation_small.jsonl": val_small,
    }
    for name, rows in outputs.items():
        write_jsonl(args.output_dir / name, rows)

    report = {
        "source": str(args.input.resolve()),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "system_prompt": SYSTEM_PROMPT,
        "filters": {
            "role": "agent_role_name_en == yuqian",
            "body_language": "contains Han characters and no ASCII letters",
            "question_length": [3, 200],
            "answer_length": [3, 100],
            "deduplicate": "sha256(normalized question + answer)",
            "repeated_fragment": "drop a 1-12 character fragment repeated at least 5 times",
        },
        "counts": dict(sorted(counters.items())),
        "clean_total": total,
        "splits": {name: len(rows) for name, rows in outputs.items()},
    }
    (args.output_dir / "cleaning_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
