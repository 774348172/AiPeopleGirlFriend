from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
EXCLUSIONS = ROOT / "eval_exclusions"
ANCHOR = (ROOT / "system_anchor.txt").read_text(encoding="utf-8").rstrip("\n")

EXCLUSION_CONTRACTS = (
    ("chat01_v1.json", "chat01-training-exclusions-v1"),
    ("chat02_v1.json", "chat02-training-exclusions-v1"),
    ("chat02f_v2.json", "chat02f-smoke-exclusions-v2"),
)

RISK_PATTERNS = {
    "old_canon": re.compile(r"23岁|25岁|大叔|航天基地城市"),
    "attribute_recitation": re.compile(r"我(?:叫秦未晞|今年(?:是)?22岁)|自由插画师|自媒体博主"),
    "secret_spill": re.compile(r"地堡|那一年|灰白色?裂缝|防空洞|灰蒙蒙|废墟"),
    "unsupported_memory_cue": re.compile(r"上次|以前|你总是|还记得|每次|又在"),
    "unsupported_physical_claim": re.compile(r"我刚从|我看见|我看到你|一看你|看你那|你脸色"),
}
SERIOUS_INPUT = re.compile(r"手术|去世|死亡|胸(?:口)?痛|呼吸困难|冷汗|着火|火灾|燃气|煤气|中毒|住院|急诊|自杀|跳楼|割腕|抽搐|昏迷|大出血|严重过敏")


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in value if ch.isalnum())


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_exclusions() -> set[str]:
    hashes: set[str] = set()
    for filename, contract_id in EXCLUSION_CONTRACTS:
        value = json.loads((EXCLUSIONS / filename).read_text(encoding="utf-8"))
        if value.get("contract_id") != contract_id:
            raise ValueError(f"exclusion contract mismatch: {filename}")
        current = value.get("normalized_text_sha256")
        if not isinstance(current, list) or not current:
            raise ValueError(f"empty exclusion contract: {filename}")
        hashes.update(current)
    return hashes


def replace_anchor(row: dict[str, Any]) -> dict[str, Any]:
    copied = deepcopy(row)
    conversations = copied.get("conversations")
    if not isinstance(conversations, list):
        raise ValueError("missing conversations")
    system = next((item for item in conversations if item.get("from") == "system"), None)
    if system is None:
        conversations.insert(0, {"from": "system", "value": ANCHOR})
    else:
        system["value"] = ANCHOR
    return copied


def conversation_hash(row: dict[str, Any]) -> str:
    payload = json.dumps(row["conversations"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def eval_overlap(row: dict[str, Any], exclusions: set[str]) -> bool:
    for message in row["conversations"]:
        if message.get("from") != "human":
            continue
        text = normalize(str(message.get("value", "")))
        if text and hashlib.sha256(text.encode("utf-8")).hexdigest() in exclusions:
            return True
    return False


def risk_reasons(row: dict[str, Any]) -> set[str]:
    humans = "\n".join(str(item.get("value", "")) for item in row["conversations"] if item.get("from") == "human")
    assistants = "\n".join(str(item.get("value", "")) for item in row["conversations"] if item.get("from") == "gpt")
    reasons = {name for name, pattern in RISK_PATTERNS.items() if pattern.search(assistants)}
    if SERIOUS_INPUT.search(humans):
        reasons.add("replace_old_serious_scene")
    return reasons


def prepare_legacy(rows: list[dict[str, Any]], exclusions: set[str]) -> tuple[list[dict[str, Any]], Counter[str]]:
    kept: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for original in rows:
        row = replace_anchor(original)
        if eval_overlap(row, exclusions):
            counts["eval_overlap"] += 1
            continue
        reasons = risk_reasons(row)
        if reasons:
            counts.update(reasons)
            counts["filtered_records"] += 1
            continue
        kept.append(row)
    return kept, counts


def validate_repair(rows: list[dict[str, Any]], expected_split: str, exclusions: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        row = replace_anchor(row)
        if eval_overlap(row, exclusions):
            raise ValueError(f"repair {expected_split} overlaps frozen evaluation")
        meta = row.get("_meta", {})
        if meta.get("source") != "human_curated_deterministic_matrix":
            raise ValueError("repair source metadata missing")
        result.append(row)
    return result


def deduplicate(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    kept = []
    for row in rows:
        digest = conversation_hash(row)
        if digest in seen:
            continue
        seen.add(digest)
        kept.append(row)
    return kept, len(rows) - len(kept)


def artifact(path: Path) -> dict[str, Any]:
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8", newline="\n")


def main() -> None:
    exclusions = load_exclusions()
    main_rows, main_filter = prepare_legacy(load_jsonl(DATA / "qin_v4_2530.jsonl"), exclusions)
    correction_rows, correction_filter = prepare_legacy(load_jsonl(DATA / "qin_corrections.jsonl"), exclusions)
    repair_train = validate_repair(load_jsonl(DATA / "qin_repair_v1_train.jsonl"), "train", exclusions)
    repair_valid = validate_repair(load_jsonl(DATA / "qin_repair_v1_valid.jsonl"), "valid", exclusions)

    general_train, general_valid = [], []
    for row in main_rows + correction_rows:
        (general_valid if int(conversation_hash(row)[:8], 16) % 10 == 0 else general_train).append(row)
    train, train_dupes = deduplicate(general_train + repair_train)
    valid, valid_dupes = deduplicate(general_valid + repair_valid)
    train_hashes = {conversation_hash(row) for row in train}
    valid_hashes = {conversation_hash(row) for row in valid}
    if train_hashes & valid_hashes:
        raise ValueError("train/valid conversation leakage")

    train_path, valid_path = DATA / "qin_v5_train.jsonl", DATA / "qin_v5_valid.jsonl"
    write_jsonl(train_path, train)
    write_jsonl(valid_path, valid)
    report = {
        "dataset_id": "qinweixi-v2500-repair-v1", "schema_version": 1,
        "system_anchor_sha256": hashlib.sha256(ANCHOR.encode("utf-8")).hexdigest(),
        "sources": [artifact(path) for path in (DATA / "qin_v4_2530.jsonl", DATA / "qin_corrections.jsonl", DATA / "qin_repair_v1_train.jsonl", DATA / "qin_repair_v1_valid.jsonl")],
        "filter_counts": {"main": dict(main_filter), "corrections": dict(correction_filter)},
        "outputs": {"train": {**artifact(train_path), "records": len(train)}, "valid": {**artifact(valid_path), "records": len(valid)}},
        "composition": {
            "general_train": len(general_train), "repair_train": len(repair_train),
            "general_valid": len(general_valid), "repair_valid": len(repair_valid),
            "repair_train_categories": dict(Counter(row["_meta"]["scenario_type"] for row in repair_train)),
            "repair_valid_categories": dict(Counter(row["_meta"]["scenario_type"] for row in repair_valid)),
        },
        "deduplicated": {"train": train_dupes, "valid": valid_dupes},
        "contracts": {"evaluation_exact_overlap": 0, "train_valid_overlap": 0, "old_system_anchor_replaced": True},
    }
    (ROOT / "data_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
