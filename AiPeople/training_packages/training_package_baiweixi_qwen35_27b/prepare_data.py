from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent
SOURCE_DATA = (
    PACKAGE_ROOT.parent
    / "training_package_baiweixi_qwen35_4b"
    / "data"
    / "baiweixi_ready.jsonl"
)
OUTPUT_DATA = PACKAGE_ROOT / "data" / "baiweixi_27b_ready.jsonl"
DATASET_INFO = PACKAGE_ROOT / "data" / "dataset_info.json"
MANIFEST = PACKAGE_ROOT / "data" / "data_manifest.json"

ACTION_MARKUP = re.compile(r"（[^）]*）|\([^)]{2,}\)|\*[^*]+\*")
PURE_DIALOGUE_RULE = (
    "\n最终回复只输出玩家能够直接听见的对白纯文本，不输出动作、表情、神态、"
    "姿态、视线、心理或环境旁白，也不用括号、星号或标签包装舞台说明。"
    "先直接回应玩家当前这句话；没有必要时不要补充无依据的行动、关心或新事实。"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean_assistant(value: str) -> str:
    cleaned = ACTION_MARKUP.sub("", value)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _prepare_record(record: dict[str, Any]) -> tuple[dict[str, Any], int]:
    conversations = record.get("conversations")
    if not isinstance(conversations, list) or not conversations:
        raise ValueError("record has no conversations")

    modified_assistant_messages = 0
    output_messages: list[dict[str, str]] = []
    for message in conversations:
        role = message.get("from")
        value = message.get("value")
        if role not in {"system", "human", "gpt"} or not isinstance(value, str):
            raise ValueError("invalid ShareGPT message")
        if role == "system":
            if PURE_DIALOGUE_RULE.strip() not in value:
                value = value.rstrip() + PURE_DIALOGUE_RULE
        elif role == "gpt":
            cleaned = _clean_assistant(value)
            if cleaned != value:
                modified_assistant_messages += 1
            value = cleaned
            if not value:
                raise ValueError("assistant message became empty after cleaning")
        output_messages.append({"from": role, "value": value})
    return {"conversations": output_messages}, modified_assistant_messages


def main() -> int:
    if not SOURCE_DATA.is_file():
        raise FileNotFoundError(SOURCE_DATA)
    OUTPUT_DATA.parent.mkdir(parents=True, exist_ok=True)

    output_records: list[dict[str, Any]] = []
    modified_rows = 0
    modified_assistant_messages = 0
    for line_number, raw in enumerate(
        SOURCE_DATA.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw.strip():
            continue
        source_record = json.loads(raw)
        record, changed = _prepare_record(source_record)
        output_records.append(record)
        modified_rows += changed > 0
        modified_assistant_messages += changed
        for message in record["conversations"]:
            if message["from"] == "gpt" and ACTION_MARKUP.search(message["value"]):
                raise ValueError(f"action markup remains at source line {line_number}")

    OUTPUT_DATA.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in output_records
        ),
        encoding="utf-8",
    )
    DATASET_INFO.write_text(
        json.dumps(
            {
                "baiweixi_27b_ready": {
                    "file_name": OUTPUT_DATA.name,
                    "columns": {"messages": "conversations"},
                    "formatting": "sharegpt",
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    MANIFEST.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": datetime.now().astimezone().isoformat(),
                "source_path": str(SOURCE_DATA.resolve()),
                "source_sha256": _sha256(SOURCE_DATA),
                "output_path": str(OUTPUT_DATA.resolve()),
                "output_sha256": _sha256(OUTPUT_DATA),
                "rows": len(output_records),
                "system_rules_appended": len(output_records),
                "rows_with_action_markup_cleaned": modified_rows,
                "assistant_messages_cleaned": modified_assistant_messages,
                "evaluation_examples_added": 0,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"OUTPUT={OUTPUT_DATA}")
    print(f"ROWS={len(output_records)}")
    print(f"CLEANED_ROWS={modified_rows}")
    print(f"OUTPUT_SHA256={_sha256(OUTPUT_DATA)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
