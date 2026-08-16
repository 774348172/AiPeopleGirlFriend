from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import FreezeVerificationError, sha256_file, verify_file
from eval.chat01.leakage_guard import (
    EVALUATION_PATHS,
    _evaluation_texts,
    normalize_for_leakage,
    trigram_jaccard,
)
from eval.chat01v5.freeze import (
    MANIFEST as CHAT01_MANIFEST,
    SNAPSHOT as CANON_SNAPSHOT,
    verify as verify_chat01_v5,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "eval/training_contract/freeze02_contract_v2.json"  # 2026-08-07 v2：authority 升级到 chat01 v6
BLOCKLIST = ROOT / "eval/training_contract/eval_blocklist_v2.json"
SCHEMA_DIR = ROOT / "eval/training_contract/schemas"
CONTRACT_SCHEMA = SCHEMA_DIR / "freeze_contract.schema.json"
BLOCKLIST_SCHEMA = SCHEMA_DIR / "eval_blocklist.schema.json"
DATASET_MANIFEST_SCHEMA = SCHEMA_DIR / "dataset_manifest.schema.json"

RECORD_SCHEMAS = {
    "visible_reply": SCHEMA_DIR / "reply_record.schema.json",
    "background_structured": SCHEMA_DIR / "background_record.schema.json",
    "memory_reranker": SCHEMA_DIR / "reranker_record.schema.json",
}

FAMILY_CONTRACTS = (
    {
        "family": "visible_reply",
        "physical_root": "data/training/qinweixi/visible_reply/{dataset_version}",
        "schema": "eval/training_contract/schemas/reply_record.schema.json",
        "allowed_modes": ["REPLY", "PROACTIVE_REPLY"],
        "first_candidate_modes": ["REPLY"],
        "data_admission": "allowed_after_dataset_freeze",
        "separation_rules": [
            "只包含玩家可见的角色自然语言正文。",
            "禁止 MEMORY_PROPOSE、自我时间线、欲望 JSON、reranker 标签和动作标签。",
            "首个约 1000 条工程候选只允许 REPLY，不允许 PROACTIVE_REPLY。",
        ],
    },
    {
        "family": "background_structured",
        "physical_root": "data/training/qinweixi/background_structured/{dataset_version}",
        "schema": "eval/training_contract/schemas/background_record.schema.json",
        "allowed_modes": ["MEMORY_PROPOSE", "SELF_TIMELINE_PROPOSE", "OFFSCREEN_UPDATE", "MOTIVE_EVALUATE"],
        "first_candidate_modes": [],
        "data_admission": "blocked_until_mode_schemas_frozen",
        "separation_rules": [
            "只包含 grammar 约束的结构化输入与 JSON 目标，不包含可见角色回复。",
            "每条记录必须绑定已冻结的 mode payload schema 路径与 SHA256。",
            "MEM-01、SELF-01、OFFSCREEN-01、MOTIVE-01 完成前禁止生产或混训对应数据。",
        ],
    },
    {
        "family": "memory_reranker",
        "physical_root": "data/training/qinweixi/memory_reranker/{dataset_version}",
        "schema": "eval/training_contract/schemas/reranker_record.schema.json",
        "allowed_modes": ["MEMORY_RERANK"],
        "first_candidate_modes": [],
        "data_admission": "blocked_until_select01",
        "separation_rules": [
            "只包含 query、候选记忆、相关性分数、困难负样本和全拒绝标签。",
            "不得使用普通玩家回复 SFT 直接充当 reranker 数据。",
            "SELECT-01 冻结重排标签合同前禁止生产或训练。",
        ],
    },
)

FROZEN_ASSETS = (
    ("eval/training_contract/schemas/reply_record.schema.json", "schema"),
    ("eval/training_contract/schemas/background_record.schema.json", "schema"),
    ("eval/training_contract/schemas/reranker_record.schema.json", "schema"),
    ("eval/training_contract/schemas/dataset_manifest.schema.json", "schema"),
    ("eval/training_contract/schemas/freeze_contract.schema.json", "schema"),
    ("eval/training_contract/schemas/eval_blocklist.schema.json", "schema"),
    ("eval/training_contract/freeze.py", "verifier"),
    ("eval/chat01/suites/chat01_suite_manifest_v6.json", "evaluation_contract"),
    ("eval/chat01/suites/canon_snapshot_v5.json", "evaluation_contract"),
    ("eval/training_contract/eval_blocklist_v1.json", "blocklist"),
)

SPLITS = ("train", "dev", "test")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FreezeVerificationError(f"expected object: {path}")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _artifact(relative: str, role: str | None = None) -> dict[str, Any]:
    path = ROOT / relative
    result: dict[str, Any] = {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if role is not None:
        result["role"] = role
    return result


def _canonical_hash(value: dict[str, Any]) -> str:
    canonical = deepcopy(value)
    canonical["freeze"]["manifest_sha256"] = None
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validator(path: Path) -> Draft202012Validator:
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def build_blocklist(now: str, suite: dict[str, Any]) -> dict[str, Any]:
    hashes: set[str] = set()
    sources: list[dict[str, Any]] = []
    for relative in EVALUATION_PATHS:
        path = ROOT / relative
        refs, records = _evaluation_texts(path)
        for ref in refs:
            normalized = normalize_for_leakage(ref.text)
            if normalized:
                hashes.add(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
        sources.append({**_artifact(relative), "records": records, "extracted_texts": len(refs)})
    return {
        "contract_id": "freeze02-eval-exclusions-v1",
        "schema_version": 1,
        "created_at": now,
        "source_suite": {
            "suite_id": suite["suite_id"],
            "manifest_sha256": suite["freeze"]["manifest_sha256"],
        },
        "evaluation_sources": sources,
        "normalization": "nfkc_lower_alnum_cjk",
        "hash_algorithm": "sha256",
        "diagnostic_only_case_ids": ["frozen.identity.name"],
        "normalized_text_sha256": sorted(hashes),
    }


def build_contract(now: str, suite: dict[str, Any]) -> dict[str, Any]:
    families = []
    for item in FAMILY_CONTRACTS:
        family = dict(item)
        schema_path = family.pop("schema")
        family["record_schema"] = _artifact(schema_path)
        families.append(family)
    evaluation_sources = [_artifact(relative) for relative in EVALUATION_PATHS]
    contract: dict[str, Any] = {
        "contract_id": "freeze02-training-boundary-v1",
        "schema_version": 1,
        "status": "frozen",
        "created_at": now,
        "purpose": (
            "Freeze the physical and semantic boundary between visible replies, background structured modes, and memory reranker data. "
            "No training corpus is approved by this contract alone; every produced dataset requires its own immutable manifest and passing leakage report."
        ),
        "authority": {
            "chat01_suite": _artifact("eval/chat01/suites/chat01_suite_manifest_v6.json"),
            "canon_snapshot": _artifact("eval/chat01/suites/canon_snapshot_v5.json"),
        },
        "dataset_families": families,
        "split_contract": {
            "ratios": {"train": 0.8, "dev": 0.1, "test": 0.1},
            "max_ratio_deviation": 0.02,
            "isolation_keys": ["conversation_group_id", "leakage_group_id", "source_record_ids"],
            "cross_split_near_duplicate": {
                "algorithm": "character_trigram_jaccard",
                "threshold": 0.9,
                "minimum_normalized_characters": 12,
                "blocking": True,
            },
        },
        "leakage_contract": {
            "evaluation_sources": evaluation_sources,
            "blocklist": _artifact("eval/training_contract/eval_blocklist_v1.json"),
            "exact": {"algorithm": "utf8_text_equality", "blocking": True},
            "normalized": {"algorithm": "nfkc_lower_alnum_cjk", "blocking": True},
            "near_duplicate": {
                "algorithm": "character_trigram_jaccard",
                "threshold": 0.82,
                "minimum_normalized_characters": 12,
                "minimum_length_ratio": 0.7,
                "blocking": True,
            },
            "failure_policy": "reject_whole_sample_and_fail_dataset_freeze",
        },
        "evaluation_contamination": {
            "diagnostic_only_case_ids": ["frozen.identity.name"],
            "execution": "execute_with_full_suite",
            "primary_statistics": "exclude_from_weighted_gain_denominator",
            "training_use": "always_forbidden",
        },
        "frozen_assets": [_artifact(path, role) for path, role in FROZEN_ASSETS],
        "freeze": {
            "hash_algorithm": "sha256",
            "manifest_hash_mode": "canonical_json_with_null_self",
            "immutable": True,
            "manifest_sha256": None,
        },
        "next_checkpoint": "FREEZE-03",
    }
    contract["freeze"]["manifest_sha256"] = _canonical_hash(contract)
    return contract


def freeze() -> dict[str, Any]:
    if CONTRACT.exists() or BLOCKLIST.exists():
        raise FreezeVerificationError("FREEZE-02 assets already exist; they are immutable")
    suite = verify_chat01_v5()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        _write(BLOCKLIST, build_blocklist(now, suite))
        _validator(BLOCKLIST_SCHEMA).validate(_load(BLOCKLIST))
        contract = build_contract(now, suite)
        _validator(CONTRACT_SCHEMA).validate(contract)
        _write(CONTRACT, contract)
        return verify()
    except Exception:
        for path in (CONTRACT, BLOCKLIST):
            if path.exists():
                path.unlink()
        raise


def verify() -> dict[str, Any]:
    suite = verify_chat01_v5()
    contract = _load(CONTRACT)
    _validator(CONTRACT_SCHEMA).validate(contract)
    if contract["freeze"]["manifest_sha256"] != _canonical_hash(contract):
        raise FreezeVerificationError("FREEZE-02 contract self hash changed")
    expected_paths = [path for path, _role in FROZEN_ASSETS]
    if [item["path"] for item in contract["frozen_assets"]] != expected_paths:
        raise FreezeVerificationError("FREEZE-02 frozen asset inventory changed")
    for item in contract["frozen_assets"]:
        verify_file(ROOT, item)
    _validator(BLOCKLIST_SCHEMA).validate(_load(BLOCKLIST))
    if contract["authority"]["chat01_suite"]["sha256"] != sha256_file(CHAT01_MANIFEST):
        raise FreezeVerificationError("CHAT-01 v5 authority changed")
    if contract["authority"]["canon_snapshot"]["sha256"] != sha256_file(CANON_SNAPSHOT):
        raise FreezeVerificationError("canon snapshot authority changed")
    blocklist = _load(BLOCKLIST)
    if blocklist["source_suite"]["manifest_sha256"] != suite["freeze"]["manifest_sha256"]:
        raise FreezeVerificationError("blocklist suite reference changed")
    return contract


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise FreezeVerificationError(f"expected object at {path}:{line_number}")
        records.append(value)
    if not records:
        raise FreezeVerificationError(f"dataset split is empty: {path}")
    return records


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _training_texts(record: dict[str, Any]) -> list[str]:
    family = record["dataset_family"]
    if family == "visible_reply":
        return [message["content"].strip() for message in record["messages"]]
    if family == "background_structured":
        return list(dict.fromkeys(_strings({"input": record["input"], "target": record["target"]})))
    return [record["query_text"].strip(), record["candidate_memory_text"].strip(), record["label_evidence"].strip()]


def _evaluation_index() -> tuple[dict[str, str], dict[str, str], list[tuple[str, str]]]:
    exact: dict[str, str] = {}
    normalized: dict[str, str] = {}
    for relative in EVALUATION_PATHS:
        refs, _records = _evaluation_texts(ROOT / relative)
        for ref in refs:
            exact.setdefault(ref.text.strip(), ref.record_id)
            norm = normalize_for_leakage(ref.text)
            if norm:
                normalized.setdefault(norm, ref.record_id)
    return exact, normalized, list(normalized.items())


def _leak_kind(text: str, exact: dict[str, str], normalized: dict[str, str], candidates: list[tuple[str, str]]) -> tuple[str, str, float] | None:
    stripped = text.strip()
    if stripped in exact:
        return "exact", exact[stripped], 1.0
    norm = normalize_for_leakage(stripped)
    if norm and norm in normalized:
        return "normalized", normalized[norm], 1.0
    if len(norm) < 12:
        return None
    best_id = ""
    best = 0.0
    for candidate, record_id in candidates:
        if len(candidate) < 12:
            continue
        ratio = min(len(norm), len(candidate)) / max(len(norm), len(candidate))
        if ratio < 0.7:
            continue
        score = trigram_jaccard(norm, candidate)
        if score > best:
            best, best_id = score, record_id
    if best >= 0.82:
        return "near_duplicate", best_id, best
    return None


def _finding(kind: str, sample_id: str, detail: str) -> dict[str, str]:
    return {"kind": kind, "sample_id": sample_id, "detail": detail}


def validate_dataset(manifest_path: Path) -> dict[str, Any]:
    contract = verify()
    manifest = _load(manifest_path)
    _validator(DATASET_MANIFEST_SCHEMA).validate(manifest)
    if manifest["freeze"]["manifest_sha256"] != _canonical_hash(manifest):
        raise FreezeVerificationError("dataset manifest self hash changed")
    if manifest["contract"]["sha256"] != contract["freeze"]["manifest_sha256"]:
        raise FreezeVerificationError("dataset uses a different FREEZE-02 contract")
    if manifest["canon_snapshot"]["sha256"] != sha256_file(CANON_SNAPSHOT):
        raise FreezeVerificationError("dataset canon snapshot changed")

    family = manifest["dataset_family"]
    family_contract = next(item for item in contract["dataset_families"] if item["family"] == family)
    expected_root = family_contract["physical_root"].replace("{dataset_version}", manifest["dataset_version"])
    findings: list[dict[str, str]] = []
    if manifest["logical_root"] != expected_root:
        findings.append(_finding("physical_boundary", manifest["dataset_id"], f"expected logical_root {expected_root}"))
    if family_contract["data_admission"] != "allowed_after_dataset_freeze":
        findings.append(_finding("admission_blocked", manifest["dataset_id"], family_contract["data_admission"]))
    if manifest["release_scope"] == "first_engineering_candidate" and family != "visible_reply":
        findings.append(_finding("first_candidate_family", manifest["dataset_id"], "only visible_reply is allowed"))

    file_entries = {item["split"]: item for item in manifest["files"]}
    if set(file_entries) != set(SPLITS):
        raise FreezeVerificationError("dataset manifest must contain train/dev/test exactly once")
    if {item["path"] for item in manifest["files"]} != {f"{split}.jsonl" for split in SPLITS}:
        findings.append(_finding("physical_boundary", manifest["dataset_id"], "split filenames must be train.jsonl/dev.jsonl/test.jsonl"))

    schema = _validator(RECORD_SCHEMAS[family])
    all_records: dict[str, list[dict[str, Any]]] = {}
    sample_ids: set[str] = set()
    exact_eval, normalized_eval, eval_candidates = _evaluation_index()
    split_keys = {split: {"conversation": set(), "leakage": set(), "source": set()} for split in SPLITS}
    split_texts: dict[str, list[tuple[str, str]]] = {split: [] for split in SPLITS}
    allowed_modes = set(family_contract["allowed_modes"])
    first_modes = set(family_contract["first_candidate_modes"])

    for split in SPLITS:
        entry = file_entries[split]
        path = manifest_path.parent / entry["path"]
        resolved = path.resolve()
        if resolved.parent != manifest_path.parent.resolve():
            raise FreezeVerificationError(f"split path escapes dataset directory: {entry['path']}")
        verify_file(manifest_path.parent, entry)
        records = _read_jsonl(path)
        if len(records) != entry["records"]:
            raise FreezeVerificationError(f"record count changed: {entry['path']}")
        all_records[split] = records
        for record in records:
            schema.validate(record)
            sample_id = record["sample_id"]
            if sample_id in sample_ids:
                findings.append(_finding("duplicate_sample_id", sample_id, "sample_id must be globally unique"))
            sample_ids.add(sample_id)
            if record["split"] != split:
                findings.append(_finding("split_mismatch", sample_id, f"record says {record['split']}, file says {split}"))
            if record["mode"] not in allowed_modes:
                findings.append(_finding("mode_boundary", sample_id, record["mode"]))
            if manifest["release_scope"] == "first_engineering_candidate" and record["mode"] not in first_modes:
                findings.append(_finding("first_candidate_mode", sample_id, record["mode"]))
            if record["canon_snapshot"]["sha256"] != sha256_file(CANON_SNAPSHOT):
                findings.append(_finding("canon_trace", sample_id, "record canon SHA256 mismatch"))
            split_keys[split]["conversation"].add(record["conversation_group_id"])
            split_keys[split]["leakage"].add(record["leakage_group_id"])
            split_keys[split]["source"].update(record["source_record_ids"])

            if family == "visible_reply":
                for index in record["supervised_message_indexes"]:
                    if index >= len(record["messages"]) or record["messages"][index]["role"] != "assistant":
                        findings.append(_finding("loss_mask", sample_id, f"invalid supervised index {index}"))
                for index in record["supervised_message_indexes"]:
                    if index < len(record["messages"]):
                        target = record["messages"][index]["content"].strip()
                        try:
                            parsed = json.loads(target)
                        except (json.JSONDecodeError, TypeError):
                            parsed = None
                        if isinstance(parsed, (dict, list)):
                            findings.append(_finding("structured_target_in_visible_reply", sample_id, "supervised target parses as JSON"))

            for text in _training_texts(record):
                leak = _leak_kind(text, exact_eval, normalized_eval, eval_candidates)
                if leak is not None:
                    kind, evaluation_id, score = leak
                    findings.append(_finding(f"evaluation_{kind}", sample_id, f"{evaluation_id}; similarity={score:.6f}"))
                norm = normalize_for_leakage(text)
                if norm:
                    split_texts[split].append((norm, sample_id))

    for left_index, left in enumerate(SPLITS):
        for right in SPLITS[left_index + 1 :]:
            for key in ("conversation", "leakage", "source"):
                overlap = split_keys[left][key] & split_keys[right][key]
                for value in sorted(overlap):
                    findings.append(_finding("cross_split_group", str(value), f"{key} appears in {left} and {right}"))
            right_exact = {text: sample_id for text, sample_id in split_texts[right]}
            for text, sample_id in split_texts[left]:
                if text in right_exact:
                    findings.append(_finding("cross_split_normalized", sample_id, f"matches {right_exact[text]} in {right}"))
                    continue
                if len(text) < 12:
                    continue
                for candidate, candidate_id in split_texts[right]:
                    if len(candidate) < 12:
                        continue
                    ratio = min(len(text), len(candidate)) / max(len(text), len(candidate))
                    if ratio >= 0.7 and trigram_jaccard(text, candidate) >= 0.9:
                        findings.append(_finding("cross_split_near_duplicate", sample_id, f"matches {candidate_id} in {right}"))
                        break

    counts = {split: len(records) for split, records in all_records.items()}
    total = sum(counts.values())
    if total >= 50:
        targets = {"train": 0.8, "dev": 0.1, "test": 0.1}
        for split, target in targets.items():
            if abs(counts[split] / total - target) > 0.02:
                findings.append(_finding("split_ratio", manifest["dataset_id"], f"{split}={counts[split]}/{total}"))
    return {
        "status": "passed" if not findings else "blocked",
        "contract_id": contract["contract_id"],
        "dataset_id": manifest["dataset_id"],
        "dataset_family": family,
        "records": counts,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--validate-dataset", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if args.freeze:
            result = freeze()
            output = {"status": "verified", "contract_id": result["contract_id"], "sha256": result["freeze"]["manifest_sha256"]}
        elif args.verify:
            result = verify()
            output = {"status": "verified", "contract_id": result["contract_id"], "sha256": result["freeze"]["manifest_sha256"]}
        else:
            output = validate_dataset(args.validate_dataset)
            if args.report:
                _write(args.report, output)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if output["status"] in {"verified", "passed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

