"""Freeze optimized V2 with the original leakage-safe train/dev assignments."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import build_gemma_v2_training_package as original

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "AiPeopleCreate" / "优化数据V2"
OLD = ROOT / "AiPeople" / "training_packages" / "training_package_baiweixi_gemma4_12b_v2_2611_9216"
DEST = ROOT / "AiPeople" / "training_packages" / "training_package_baiweixi_gemma4_12b_optimized_v2_2611_6144"
MAX_LENGTH = 6144


def main() -> None:
    if DEST.exists():
        raise FileExistsError(DEST)
    from transformers import AutoTokenizer
    from unsloth.chat_templates import get_chat_template
    from data_gen_v4.runtime_grounded.renderer import IGNORE_INDEX, build_current_assistant_example

    source_manifest = original.read_json(SOURCE / "manifest.json")
    old_manifest = original.read_json(OLD / "manifest.json")
    if source_manifest["status"] != "experimental_candidate" or source_manifest["released_count"] != 2611:
        raise ValueError("Optimized candidate is not the audited 2611 rows")
    for name in ("train", "metadata"):
        if original.sha_file(SOURCE / f"{name}.jsonl") != source_manifest[f"{name}_sha256"]:
            raise ValueError(f"Optimized {name} hash drift")
    if original.sha_file(SOURCE / "token_audit.json") != source_manifest["token_audit_sha256"]:
        raise ValueError("Optimized token audit drift")
    for name, expected in old_manifest["files"].items():
        if original.sha_file(OLD / name) != expected:
            raise ValueError(f"Original frozen package drift: {name}")
    old_rows = {}
    for split in ("train", "dev"):
        for row in original.read_jsonl(OLD / "data" / f"{split}.jsonl"):
            if row["sample_id"] in old_rows:
                raise ValueError("Duplicated original sample")
            old_rows[row["sample_id"]] = row
    source_rows = original.read_jsonl(SOURCE / "train.jsonl")
    metadata = original.read_jsonl(SOURCE / "metadata.jsonl")
    if len(source_rows) != len(metadata) or len(metadata) != len(old_rows):
        raise ValueError("Row count drift")
    tokenizer = get_chat_template(
        AutoTokenizer.from_pretrained(old_manifest["model_path"], local_files_only=True),
        chat_template="gemma-4",
    )
    if original.sha_bytes(tokenizer.chat_template.encode("utf-8")) != old_manifest["chat_template_sha256"]:
        raise ValueError("Gemma template drift")
    sys.path.insert(0, str(ROOT / "AiPeople" / "runtime" / "world_mind"))
    from compact_game_reply_context import decode_system_v2

    outputs = {"train": [], "dev": []}
    longest = {"sequence_tokens": 0}
    lengths = []
    split_counts = Counter()
    for index, (pair, meta) in enumerate(zip(source_rows, metadata, strict=True), 1):
        sid = meta["sample_id"]
        if meta["line"] != index or sid not in old_rows:
            raise ValueError(f"Identity drift: {sid}")
        old = old_rows[sid]
        if original.stable_digest(pair) != meta["training_pair_sha256"] or original.stable_digest(old["messages"][:-1]) != meta["original_prompt_sha256"]:
            raise ValueError(f"Pair ancestry mismatch: {sid}")
        messages = [*pair["prompt"], *pair["completion"]]
        if decode_system_v2(messages[0]["content"]) != old["messages"][0]["content"] or messages[1:] != old["messages"][1:]:
            raise ValueError(f"V1 byte roundtrip or target drift: {sid}")
        row = dict(old)
        row["messages"] = messages
        row["render_profile_id"] = "GAME_REPLY_CONTEXT_V2_COMPACT"
        row["training_pair_sha256"] = meta["training_pair_sha256"]
        example, span = build_current_assistant_example(
            tokenizer, original.make_record(row), max_seq_length=MAX_LENGTH, row_index=index - 1,
        )
        if any(label != IGNORE_INDEX for label in example["labels"][:span.start]):
            raise ValueError(f"Prompt entered loss mask: {sid}")
        if sum(label != IGNORE_INDEX for label in example["labels"]) != span.token_count:
            raise ValueError(f"Supervision drift: {sid}")
        row["sequence_tokens"] = len(example["input_ids"])
        row["supervised_tokens"] = span.token_count
        row["supervised_special_tokens"] = span.special_token_count
        if row["sequence_tokens"] > MAX_LENGTH:
            raise ValueError(f"Over max length: {sid}")
        outputs[row["split"]].append(row)
        split_counts[row["split"]] += 1
        lengths.append(row["sequence_tokens"])
        if row["sequence_tokens"] > longest["sequence_tokens"]:
            longest = {k: row[k] for k in ("sample_id", "family_id", "leakage_group_id", "split", "sequence_tokens")}
    if dict(split_counts) != old_manifest["counts"]:
        raise ValueError("Original train/dev assignment count changed")

    (DEST / "data").mkdir(parents=True)
    (DEST / "audit").mkdir()
    for split in ("train", "dev"):
        original.write_jsonl(DEST / "data" / f"{split}.jsonl", outputs[split])
    original.write_json(DEST / "audit" / "preflight_report.json", {
        "result": "passed", "rows": len(lengths), "max_seq_length": MAX_LENGTH,
        "longest_sequence": longest, "max_tokens": max(lengths),
        "mean_tokens": round(sum(lengths) / len(lengths), 2),
        "loss_scope": "final_assistant_only", "truncation": False,
        "original_split_counts": dict(split_counts),
        "source_roundtrip_audit_sha256": source_manifest["roundtrip_audit_sha256"],
    })
    files = {p.relative_to(DEST).as_posix(): original.sha_file(p) for p in DEST.rglob("*") if p.is_file()}
    frozen = dict(old_manifest)
    frozen.update(
        dataset_id=DEST.name, source_v2=str(SOURCE),
        source_manifest_sha256=original.sha_file(SOURCE / "manifest.json"),
        source_train_sha256=source_manifest["train_sha256"],
        source_metadata_sha256=source_manifest["metadata_sha256"],
        render_profile_id="GAME_REPLY_CONTEXT_V2_COMPACT",
        max_seq_length=MAX_LENGTH, longest_sequence=longest, files=files,
        source_original_package_manifest_sha256=original.sha_file(OLD / "manifest.json"),
        runtime_alignment_pending=True,
    )
    original.write_json(DEST / "manifest.json", frozen)
    (DEST / "README.md").write_text(
        "# Optimized V2 Gemma training candidate\n\n"
        "Frozen from reversible optimized V2. Original family/turn-source train/dev split is preserved. "
        "Runtime V2 rendering is not deployed; do not replace the production adapter without alignment evaluation.\n",
        encoding="utf-8",
    )
    print(json.dumps({"package": str(DEST), "rows": len(lengths), "splits": dict(split_counts), "longest": longest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
