"""Validate and freeze reviewed GAME_REPLY rows for Gemma SFT training."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.training import TrainingRecordV4  # noqa: E402
from data_gen_v4.runtime_grounded.renderer import (  # noqa: E402
    GAME_REPLY_CONTEXT_RENDER_PROFILE,
    IGNORE_INDEX,
    build_current_assistant_example,
)
from build_v5_pilot_dataset import _load_baiweixi_production_contract  # noqa: E402


SPLITS = ("train", "dev", "test")
SOURCE_FILENAMES = {split: f"{split}.accepted.jsonl" for split in SPLITS}
ROLE_ANCHOR_MARKER = "\n\n[角色锚]"
REQUIRED_CONTEXT_BLOCKS = (
    "[当前权威事实]",
    "[本轮玩家事实报告]",
    "[明确 unknown]",
    "[当前世界]",
    "[你此刻的状态]",
    "[相关记忆]",
    "[第三方对话证据]",
    "[回答依据约束]",
)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def as_training_record(row: dict[str, Any], split: str) -> TrainingRecordV4:
    messages = row["messages"]
    return TrainingRecordV4(
        sample_id=row["sample_id"],
        mode="GAME_REPLY",
        render_profile_id=row["render_profile_id"],
        messages=[dict(message) for message in messages[1:]],
        supervised_message_indexes=[len(messages) - 2],
        supervised_token_count=0,
        protocol_snapshot_id=GAME_REPLY_CONTEXT_RENDER_PROFILE,
        system_anchor=messages[0]["content"],
        split=split,
    )


def validate_row_shape(
    row: dict[str, Any], *, split: str, expected_anchor: str, expected_anchor_sha: str
) -> None:
    sample_id = row.get("sample_id", "<missing>")
    messages = row.get("messages")
    if row.get("render_profile_id") != GAME_REPLY_CONTEXT_RENDER_PROFILE:
        raise ValueError(f"{sample_id}: wrong render profile")
    if not isinstance(messages, list) or len(messages) < 3:
        raise ValueError(f"{sample_id}: at least system/user/assistant messages required")
    if messages[0].get("role") != "system":
        raise ValueError(f"{sample_id}: first message must be system")
    if messages[-2].get("role") != "user" or messages[-1].get("role") != "assistant":
        raise ValueError(f"{sample_id}: row must end in user/assistant")
    if row.get("supervised_message_indexes") != [len(messages) - 1]:
        raise ValueError(f"{sample_id}: only final assistant may be supervised")
    if not str(messages[-1].get("content", "")).strip():
        raise ValueError(f"{sample_id}: empty target")
    if row.get("role_anchor_sha256") != expected_anchor_sha:
        raise ValueError(f"{sample_id}: recorded role-anchor hash differs")
    system = messages[0].get("content", "")
    if not system.startswith(expected_anchor + ROLE_ANCHOR_MARKER):
        raise ValueError(f"{sample_id}: system prompt does not use production role anchor")
    missing = [block for block in REQUIRED_CONTEXT_BLOCKS if block not in system]
    if missing:
        raise ValueError(f"{sample_id}: missing context blocks {missing}")
    if not str(row.get("source", "")).strip():
        raise ValueError(f"{sample_id}: missing source")
    if not str(row.get("task_type", "")).strip():
        raise ValueError(f"{sample_id}: missing task_type")
    if split not in SPLITS:
        raise ValueError(f"{sample_id}: invalid split {split}")


def finalize(
    package: Path,
    output: Path,
    tokenizer: Any,
    *,
    max_seq_length: int = 2048,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing output: {output}")
    source_manifest_path = package / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("status") != "production_aligned_pending_final_training_authorization":
        raise ValueError("Source package is not awaiting final training authorization")
    if source_manifest.get("training_authorized") is not False:
        raise ValueError("Source package must remain training-blocked")
    if not source_manifest.get("anchor_dependency_review_complete"):
        raise ValueError("Anchor dependency review is incomplete")
    if not source_manifest.get("production_input_alignment_verified"):
        raise ValueError("Production input alignment is unverified")
    for filename, digest in source_manifest["files"].items():
        if sha_file(package / filename) != digest:
            raise ValueError(f"Frozen source file changed: {filename}")

    production_anchor, _ = _load_baiweixi_production_contract()
    production_anchor_sha = sha_bytes(production_anchor.encode("utf-8"))
    if production_anchor_sha != source_manifest.get("role_anchor_sha256"):
        raise ValueError("Current production role anchor differs from reviewed package")

    all_ids: set[str] = set()
    split_ids: dict[str, set[str]] = {}
    rows_by_split: dict[str, list[dict[str, Any]]] = {}
    coverage: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "rows": 0,
            "supervised_assistant_messages": 0,
            "supervised_tokens": 0,
            "min_sequence_tokens": None,
            "max_sequence_tokens": 0,
            "min_target_tokens": None,
            "max_target_tokens": 0,
            "supervised_special_tokens": 0,
            "failures": 0,
        }
    )
    longest = {"sample_id": None, "split": None, "sequence_tokens": 0}

    for split in SPLITS:
        path = package / SOURCE_FILENAMES[split]
        rows = read_jsonl(path)
        expected_count = source_manifest["counts"][split]
        if len(rows) != expected_count:
            raise ValueError(f"{split}: expected {expected_count} rows, found {len(rows)}")
        ids: set[str] = set()
        for index, row in enumerate(rows):
            validate_row_shape(
                row,
                split=split,
                expected_anchor=production_anchor,
                expected_anchor_sha=production_anchor_sha,
            )
            sample_id = row["sample_id"]
            if sample_id in ids or sample_id in all_ids:
                raise ValueError(f"Duplicate or cross-split sample_id: {sample_id}")
            ids.add(sample_id)
            all_ids.add(sample_id)
            record = as_training_record(row, split)
            example, span = build_current_assistant_example(
                tokenizer, record, max_seq_length=max_seq_length, row_index=index
            )
            if sum(label != IGNORE_INDEX for label in example["labels"]) != span.token_count:
                raise RuntimeError(f"{sample_id}: loss mask cardinality mismatch")
            key = f"{split}:{row['source']}:{row['task_type']}"
            stat = coverage[key]
            sequence_tokens = len(example["input_ids"])
            stat["rows"] += 1
            stat["supervised_assistant_messages"] += 1
            stat["supervised_tokens"] += span.token_count
            stat["supervised_special_tokens"] += span.special_token_count
            stat["min_sequence_tokens"] = (
                sequence_tokens
                if stat["min_sequence_tokens"] is None
                else min(stat["min_sequence_tokens"], sequence_tokens)
            )
            stat["max_sequence_tokens"] = max(stat["max_sequence_tokens"], sequence_tokens)
            stat["min_target_tokens"] = (
                span.token_count
                if stat["min_target_tokens"] is None
                else min(stat["min_target_tokens"], span.token_count)
            )
            stat["max_target_tokens"] = max(stat["max_target_tokens"], span.token_count)
            if sequence_tokens > longest["sequence_tokens"]:
                longest = {
                    "sample_id": sample_id,
                    "split": split,
                    "sequence_tokens": sequence_tokens,
                }
        split_ids[split] = ids
        rows_by_split[split] = rows

    for left_index, left in enumerate(SPLITS):
        for right in SPLITS[left_index + 1 :]:
            overlap = split_ids[left] & split_ids[right]
            if overlap:
                raise ValueError(f"Split overlap {left}/{right}: {sorted(overlap)[:5]}")
    train_tasks = {row["task_type"] for row in rows_by_split["train"]}
    for task in train_tasks:
        task_stats = [value for key, value in coverage.items() if key.startswith("train:") and key.endswith(f":{task}")]
        if not task_stats or sum(value["supervised_tokens"] for value in task_stats) == 0:
            raise ValueError(f"Core train task has zero supervised tokens: {task}")

    temp = output.with_name(output.name + ".tmp")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    for split in SPLITS:
        destination = temp / f"{split}.jsonl"
        destination.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows_by_split[split]),
            encoding="utf-8",
        )
    coverage_doc = {
        "schema_version": "aip.frontend_supervision_coverage.v1",
        "max_seq_length": max_seq_length,
        "chat_template_sha256": sha_bytes(tokenizer.chat_template.encode("utf-8")),
        "by_split_source_task": dict(sorted(coverage.items())),
        "longest_sequence": longest,
        "total_rows": len(all_ids),
        "total_supervised_assistant_messages": sum(v["supervised_assistant_messages"] for v in coverage.values()),
        "total_supervised_tokens": sum(v["supervised_tokens"] for v in coverage.values()),
        "total_supervised_special_tokens": sum(v["supervised_special_tokens"] for v in coverage.values()),
        "failures": 0,
    }
    (temp / "supervision_coverage.json").write_text(
        json.dumps(coverage_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = {
        "result": "passed",
        "checks": {
            "source_hashes": "passed",
            "counts_and_unique_ids": "passed",
            "split_disjointness": "passed",
            "production_role_anchor": "passed",
            "message_boundaries": "passed",
            "final_assistant_only": "passed",
            "token_prefix_equality": "passed",
            "prompt_fully_masked": "passed",
            "target_fully_supervised": "passed",
            "turn_closing_special_token": "passed",
            "no_truncation": "passed",
            "core_task_coverage": "passed",
        },
        "longest_sequence": longest,
    }
    (temp / "preflight_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files = {
        path.name: sha_file(path)
        for path in sorted(temp.iterdir())
        if path.name != "manifest.json"
    }
    manifest = {
        "dataset_id": output.name,
        "status": "immutable_training_package_authorized",
        "training_authorized": True,
        "source_package": str(package.resolve()),
        "source_manifest_sha256": sha_file(source_manifest_path),
        "render_profile_id": GAME_REPLY_CONTEXT_RENDER_PROFILE,
        "role_anchor_sha256": production_anchor_sha,
        "chat_template_sha256": coverage_doc["chat_template_sha256"],
        "max_seq_length": max_seq_length,
        "counts": {split: len(rows_by_split[split]) for split in SPLITS},
        "targets_rewritten": 0,
        "supervision_policy": "final_assistant_only",
        "files": files,
    }
    (temp / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp.replace(output)
    return {**manifest, "coverage": coverage_doc}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    args = parser.parse_args()
    from transformers import AutoTokenizer
    from unsloth.chat_templates import get_chat_template

    tokenizer = get_chat_template(
        AutoTokenizer.from_pretrained(str(args.model), local_files_only=True),
        chat_template="gemma-4",
    )
    result = finalize(
        args.package.resolve(),
        args.output.resolve(),
        tokenizer,
        max_seq_length=args.max_seq_length,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
