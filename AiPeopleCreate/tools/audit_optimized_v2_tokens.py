"""Measure old/new full-message token counts with the frozen Gemma tokenizer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from build_optimized_training_v2 import SOURCE, DEST, digest, rows, sha_file, write_json


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "AiPeople" / "training_packages" / "training_package_baiweixi_gemma4_12b_v2_2611_9216"


def stats(values: list[int]) -> dict:
    values = sorted(values)
    return {
        "min": values[0], "mean": round(sum(values) / len(values), 1),
        "p50": values[int((len(values) - 1) * .50)],
        "p90": values[int((len(values) - 1) * .90)],
        "p95": values[int((len(values) - 1) * .95)],
        "max": values[-1], "over_9216": sum(x > 9216 for x in values),
    }


def main() -> None:
    import unsloth  # noqa: F401 - patches Transformers before import
    from transformers import AutoTokenizer
    from unsloth.chat_templates import get_chat_template

    report_path = DEST / "token_audit.json"
    if report_path.exists():
        raise FileExistsError(report_path)
    manifest_path = DEST / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    if sha_file(DEST / "train.jsonl") != manifest["train_sha256"] or sha_file(SOURCE / "train.jsonl") != manifest["source_train_sha256"]:
        raise ValueError("Source/candidate hash drift")
    tokenizer = get_chat_template(
        AutoTokenizer.from_pretrained(package["model_path"], local_files_only=True),
        chat_template="gemma-4",
    )
    template_hash = hashlib.sha256(tokenizer.chat_template.encode("utf-8")).hexdigest()
    if template_hash != package["chat_template_sha256"]:
        raise ValueError("Chat template drift")

    before, after, changes = [], [], []
    for old, new, meta in zip(rows(SOURCE / "train.jsonl"), rows(DEST / "train.jsonl"), rows(DEST / "metadata.jsonl"), strict=True):
        if digest(old) != meta["original_training_pair_sha256"] or digest(new) != meta["training_pair_sha256"]:
            raise ValueError(f"Pair hash drift: {meta['sample_id']}")
        old_messages = [*old["prompt"], *old["completion"]]
        new_messages = [*new["prompt"], *new["completion"]]
        old_length = len(tokenizer.apply_chat_template(old_messages, tokenize=True, add_generation_prompt=False))
        new_length = len(tokenizer.apply_chat_template(new_messages, tokenize=True, add_generation_prompt=False))
        before.append(old_length)
        after.append(new_length)
        changes.append((old_length - new_length, meta["sample_id"]))
    if len(before) != manifest["released_count"]:
        raise ValueError("Row count drift")
    report = {
        "rows": len(before), "tokenizer": package["base_model_id"],
        "chat_template_sha256": template_hash,
        "method": "Full prompt plus completion through frozen Gemma 4 apply_chat_template, no truncation. This is a token audit, not a training preflight or quality comparison.",
        "original": stats(before), "optimized": stats(after),
        "total_original": sum(before), "total_optimized": sum(after),
        "total_saved": sum(before) - sum(after),
        "saved_percent": round((sum(before) - sum(after)) / sum(before) * 100, 2),
        "rows_larger": sum(a > b for a, b in zip(after, before)),
        "largest_optimized_sample": changes[after.index(max(after))][1],
    }
    write_json(report_path, report)
    manifest["token_audit_sha256"] = sha_file(report_path)
    write_json(manifest_path, manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
