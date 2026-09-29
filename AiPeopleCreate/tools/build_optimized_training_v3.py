"""Build an isolated, byte-reversible V3 presentation from Optimized V2."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "AiPeople"))
from AiPeople.runtime.world_mind.compact_game_reply_context import decode_system_v2  # noqa: E402
from AiPeople.runtime.world_mind.compact_game_reply_context_v3 import (  # noqa: E402
    UNKNOWN_GROUP, decode_system_v3, encode_system_v3,
)

SOURCE = ROOT / "AiPeopleCreate" / "优化数据V2"
DEST = ROOT / "AiPeopleCreate" / "优化数据V3候选"
FROZEN = ROOT / "AiPeople" / "training_packages" / "training_package_baiweixi_gemma4_12b_optimized_v2_2611_6144"
MAX_LENGTH = 6144


def sha_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                raise ValueError(f"Empty JSONL line: {path}")
            yield json.loads(line)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stats(values: list[int]) -> dict:
    ordered = sorted(values)
    return {
        "min": ordered[0], "mean": round(sum(ordered) / len(ordered), 1),
        "p50": ordered[(len(ordered) - 1) // 2],
        "p95": ordered[int((len(ordered) - 1) * .95)],
        "max": ordered[-1],
    }


def main() -> None:
    if DEST.exists():
        raise FileExistsError(f"Refusing to overwrite {DEST}")
    source_manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    frozen_manifest = json.loads((FROZEN / "manifest.json").read_text(encoding="utf-8"))
    if source_manifest["released_count"] != 2611:
        raise ValueError("Unexpected source count")
    for name in ("train", "metadata"):
        if sha_file(SOURCE / f"{name}.jsonl") != source_manifest[f"{name}_sha256"]:
            raise ValueError(f"Source {name} hash drift")
    if sha_file(SOURCE / "token_audit.json") != source_manifest["token_audit_sha256"]:
        raise ValueError("Source tokenizer audit hash drift")

    import unsloth  # noqa: F401 - prepare the same Gemma chat template as the frozen package
    from transformers import AutoTokenizer
    from unsloth.chat_templates import get_chat_template

    tokenizer = get_chat_template(
        AutoTokenizer.from_pretrained(frozen_manifest["model_path"], local_files_only=True),
        chat_template="gemma-4",
    )
    if hashlib.sha256(tokenizer.chat_template.encode("utf-8")).hexdigest() != frozen_manifest["chat_template_sha256"]:
        raise ValueError("Gemma template changed")

    old_tokens: list[int] = []
    new_tokens: list[int] = []
    prepared: list[tuple[dict, dict]] = []
    counts = Counter()
    seen_ids: set[str] = set()
    for index, (old, meta) in enumerate(zip(rows(SOURCE / "train.jsonl"), rows(SOURCE / "metadata.jsonl"), strict=True), 1):
        sid = meta["sample_id"]
        if meta["line"] != index or sid in seen_ids or digest(old) != meta["training_pair_sha256"]:
            raise ValueError(f"Source identity drift: {sid}")
        seen_ids.add(sid)
        original_system = old["prompt"][0]["content"]
        new_system = encode_system_v3(original_system)
        if decode_system_v3(new_system).encode("utf-8") != original_system.encode("utf-8"):
            raise ValueError(f"Nonreversible V3: {sid}")
        if decode_system_v2(decode_system_v3(new_system)) != decode_system_v2(original_system):
            raise ValueError(f"V1 fact/provenance drift: {sid}")
        new = json.loads(json.dumps(old, ensure_ascii=False))
        new["prompt"][0]["content"] = new_system
        if new["prompt"][1:] != old["prompt"][1:] or new["completion"] != old["completion"]:
            raise ValueError(f"Dialogue or answer changed: {sid}")
        old_length = len(tokenizer.apply_chat_template(
            [*old["prompt"], *old["completion"]], tokenize=True, add_generation_prompt=False,
        ))
        new_length = len(tokenizer.apply_chat_template(
            [*new["prompt"], *new["completion"]], tokenize=True, add_generation_prompt=False,
        ))
        if new_length > MAX_LENGTH:
            raise ValueError(f"V3 exceeds context budget: {sid}, {new_length}")
        old_tokens.append(old_length)
        new_tokens.append(new_length)
        counts["unknown_groups"] += new_system.count(UNKNOWN_GROUP)
        counts["merged_boundaries"] += new_system.count("【合并回答约束V3】")
        counts["rows_shorter"] += new_length < old_length
        counts["rows_longer"] += new_length > old_length
        new_meta = dict(meta)
        new_meta["v2_training_pair_sha256"] = meta["training_pair_sha256"]
        new_meta["v2_prompt_sha256"] = meta["prompt_sha256"]
        new_meta["training_pair_sha256"] = digest(new)
        new_meta["prompt_sha256"] = digest(new["prompt"])
        prepared.append((new, new_meta))
    if len(prepared) != source_manifest["released_count"] or sum(new_tokens) >= sum(old_tokens):
        raise ValueError("Incomplete source or no measured token savings")

    DEST.mkdir()
    with (DEST / "train.jsonl").open("w", encoding="utf-8", newline="\n") as train, (
        DEST / "metadata.jsonl"
    ).open("w", encoding="utf-8", newline="\n") as metadata:
        for pair, meta in prepared:
            train.write(json.dumps(pair, ensure_ascii=False) + "\n")
            metadata.write(json.dumps(meta, ensure_ascii=False) + "\n")
    audit = {
        "rows_verified": len(prepared), "v2_system_byte_roundtrips": len(prepared),
        "v1_system_equivalence": len(prepared), "dialogue_and_completion_unchanged": len(prepared),
        "gemma_chat_template_sha256": frozen_manifest["chat_template_sha256"],
        "before_tokens": stats(old_tokens), "after_tokens": stats(new_tokens),
        "total_tokens_saved": sum(old_tokens) - sum(new_tokens),
        "saved_percent": round(100 * (sum(old_tokens) - sum(new_tokens)) / sum(old_tokens), 2),
        "longest_sample_id": prepared[new_tokens.index(max(new_tokens))][1]["sample_id"],
        **counts,
    }
    write_json(DEST / "audit.json", audit)
    write_json(DEST / "manifest.json", {
        "dataset": "优化数据V3候选", "status": "experimental_candidate",
        "training_authorized": False, "runtime_alignment_pending": True,
        "source_manifest_sha256": sha_file(SOURCE / "manifest.json"),
        "source_train_sha256": source_manifest["train_sha256"],
        "source_metadata_sha256": source_manifest["metadata_sha256"],
        "train_sha256": sha_file(DEST / "train.jsonl"),
        "metadata_sha256": sha_file(DEST / "metadata.jsonl"),
        "audit_sha256": sha_file(DEST / "audit.json"),
    })
    (DEST / "README.md").write_text(
        "# 优化数据V3候选\n\n仅合并同来源分组内相邻同主体的明确未知字段，"
        "以及完全匹配的两句重复回答约束。按原顺序可逐字还原 V2，继而还原 V1；"
        "历史对话与目标回复不变。未接入正式运行时，未经回复质量对照或训练速度验证，"
        "不得直接用作正式训练或部署。详见 audit.json。\n",
        encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
