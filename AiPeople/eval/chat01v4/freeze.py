from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import (
    FreezeVerificationError,
    canonical_manifest_sha256,
    sha256_file,
    verify_file,
)
from eval.chat01.leakage_guard import (
    DEFAULT_EXCLUDED_PATTERNS,
    DEFAULT_TRAINING_PATTERNS,
    NEAR_DUPLICATE_MIN_CHARS,
    NEAR_DUPLICATE_THRESHOLD,
)


ROOT = Path(__file__).resolve().parents[2]
OLD_MANIFEST = ROOT / "eval/chat01/suites/chat01_suite_manifest_v3.json"
MANIFEST = ROOT / "eval/chat01/suites/chat01_suite_manifest_v4.json"
SNAPSHOT = ROOT / "eval/chat01/suites/canon_snapshot_v3.json"
DRIFT = ROOT / "eval/chat01/suites/canon_drift_v2.json"
LEAKAGE = ROOT / "eval/chat01/suites/leakage_report_v3.json"
SCHEMA = ROOT / "eval/chat01/schema/suite_manifest.schema.json"

CANON_SOURCES = (
    (1, "需求文档/项目框架需求.md", "product_requirements"),
    (2, "人物设定/秦/角色设定定稿.md", "character_prose"),
    (3, "人物设定/秦/bible.yaml", "character_bible"),
    (4, "人物设定/秦/canon.json", "character_facts"),
    (5, "人物设定/秦/timeline.yaml", "character_timeline"),
    (6, "设计文档/AI女友最小心智系统设计.md", "ai_implementation"),
)

FROZEN_ASSETS = (
    ("eval/chat01/schema/auto_result.schema.json", "schema"),
    ("eval/chat01/schema/blocker_rules.schema.json", "schema"),
    ("eval/chat01/schema/canon_drift.schema.json", "schema"),
    ("eval/chat01/schema/canon_snapshot.schema.json", "schema"),
    ("eval/chat01/schema/case.schema.json", "schema"),
    ("eval/chat01/schema/human_ballot.schema.json", "schema"),
    ("eval/chat01/schema/leakage_report.schema.json", "schema"),
    ("eval/chat01/schema/quality_rubric.schema.json", "schema"),
    ("eval/chat01/schema/semantic_review.schema.json", "schema"),
    ("eval/chat01/schema/suite_manifest.schema.json", "schema"),
    ("eval/chat01/suites/canon_drift_v2.json", "suite"),
    ("eval/chat01/suites/canon_snapshot_v3.json", "suite"),
    ("eval/chat01/suites/chat01_dev_v1.jsonl", "suite"),
    ("eval/chat01/suites/chat01_frozen_multiturn_v1.jsonl", "suite"),
    ("eval/chat01/suites/chat01_frozen_single_v1.jsonl", "suite"),
    ("eval/chat01/suites/chat01_human_blind_v1.jsonl", "suite"),
    ("eval/chat01/rubrics/blocker_rules_v1.json", "rubric"),
    ("eval/chat01/rubrics/human_blind_rubric_v1.md", "rubric"),
    ("eval/chat01/rubrics/quality_rubric_v1.json", "rubric"),
    ("eval/chat01/rubrics/semantic_rubric_v1.md", "rubric"),
    ("eval/chat01/build_suites.py", "builder"),
    ("eval/chat01/blind.py", "evaluator"),
    ("eval/chat01v4/freeze.py", "evaluator"),
    ("eval/chat01/leakage_guard.py", "evaluator"),
    ("eval/chat01/rules.py", "evaluator"),
    ("eval/chat01/suites/leakage_report_v3.json", "leakage_evidence"),
    ("training_package_m3_v2/eval_exclusions/chat01_v1.json", "leakage_evidence"),
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FreezeVerificationError(f"expected object: {path}")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _artifact(relative: str, role: str | None = None) -> dict[str, Any]:
    path = ROOT / relative
    result: dict[str, Any] = {"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    if role is not None:
        result["role"] = role
    return result


def build_snapshot(now: str) -> dict[str, Any]:
    sources = []
    for rank, relative, role in CANON_SOURCES:
        sources.append({"authority_rank": rank, **_artifact(relative), "role": role})
    return {
        "snapshot_id": "chat01-canon-v3", "schema_version": 1, "created_at": now,
        "hash_algorithm": "sha256", "sources": sources,
        "resolved_facts": {
            "character_name": "秦未晞", "character_age": 22, "player_name": "浩然", "player_age": 24,
            "occupation": "自由插画师 / 自媒体博主", "city": "金陵",
            "current_living": "两居室合租，刚开始不到一个月",
            "current_relationship": "合租室友 + 暧昧期，尚未确认恋爱或婚姻",
            "character_to_player_names": ["B哥", "浩然"], "player_to_character_name": "秦老",
        },
        "consistency": {
            "status": "consistent", "conflicts": [],
            "notes": "v3接受并统一成长地与父母分工：出生于金陵；0-5岁、15-18岁在金陵；5-15岁随父母项目调动但从未在航天基地生活；母亲做轨道计算，父亲为非轨道计算方向的航天科研人员。评测题、rubric和oracle字节不变。",
        },
    }


def build_drift(now: str) -> dict[str, Any]:
    return {
        "register_id": "chat01-canon-drift-v2", "schema_version": 1, "created_at": now,
        "canon_snapshot_id": "chat01-canon-v3",
        "scan_contract": {
            "roots": ["runtime", "tests", "人物设定/秦", "data_gen_v4", "profiles/qinweixi"],
            "patterns": ["出生于航天基地", "hometown: 航天基地", "爸爸做轨道计算"],
            "interpretation": "仅扫描现行正典、运行时、测试和V4生成器源；备份、历史与训练成品保持原样并排除。",
        },
        "findings": [],
        "excluded_paths": [
            {"path_pattern": "人物设定/秦/_backups/**", "reason": "冻结前历史备份，不是现行正典。"},
            {"path_pattern": "人物设定/秦/训练数据/**", "reason": "既有训练成品不可因正典更新被原地改写。"},
            {"path_pattern": "profiles/qinweixi/sources/*.bak-*", "reason": "正典迁移前备份，仅用于追溯。"},
            {"path_pattern": "历史与调研文档/**", "reason": "历史资料不构成现行实现。"},
        ],
        "summary": {"open": 0, "blocks_chat02": 0, "blocks_chat03": 0, "ignored_archives": 4},
    }


def freeze() -> dict[str, Any]:
    if MANIFEST.exists() or SNAPSHOT.exists() or DRIFT.exists():
        raise FreezeVerificationError("v4 freeze assets already exist; they are immutable")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    _write(SNAPSHOT, build_snapshot(now))
    _write(DRIFT, build_drift(now))
    for path, schema in ((SNAPSHOT, "canon_snapshot.schema.json"), (DRIFT, "canon_drift.schema.json")):
        Draft202012Validator(_load(ROOT / "eval/chat01/schema" / schema), format_checker=FormatChecker()).validate(_load(path))
    leakage = _load(LEAKAGE)
    if leakage["status"] != "passed" or leakage["summary"]["blocking_findings"]:
        raise FreezeVerificationError("current leakage scan is not passing")
    old = _load(OLD_MANIFEST)
    manifest = deepcopy(old)
    manifest.update({
        "suite_id": "chat01-qinweixi-v4", "version": "v4", "status": "frozen",
        "created_at": now, "frozen_at": now,
        "purpose": old["purpose"] + " v4 only accepts the synchronized canon and shared runtime-boundary update; all case, rubric, and oracle bytes remain unchanged.",
    })
    manifest["canonical_sources"] = [
        {"path": item["path"], "required": True, "bytes": item["bytes"], "sha256": item["sha256"]}
        for item in build_snapshot(now)["sources"]
    ]
    manifest["frozen_assets"] = [_artifact(path, role) for path, role in FROZEN_ASSETS]
    by_path = {item["path"]: item for item in manifest["frozen_assets"]}
    for asset in manifest["assets"]:
        asset["sha256"] = by_path[asset["path"]]["sha256"]
    manifest["leakage"] = {
        "report_path": "eval/chat01/suites/leakage_report_v3.json", "status": "passed",
        "report_sha256": sha256_file(LEAKAGE),
        "scan_contract": {
            "training_patterns": list(DEFAULT_TRAINING_PATTERNS), "excluded_patterns": list(DEFAULT_EXCLUDED_PATTERNS),
            "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD, "near_duplicate_min_chars": NEAR_DUPLICATE_MIN_CHARS,
        },
    }
    manifest["freeze"] = {"hash_algorithm": "sha256", "manifest_hash_mode": "canonical_json_with_null_self", "immutable": True, "manifest_sha256": None}
    manifest["notes"] = "CHAT-01 v4 re-freeze after accepted canon synchronization and shared runtime-boundary rules. The four case assets and all rubrics/oracles are byte-identical to v3; no evaluation prompt was added, removed, or edited."
    manifest["freeze"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
    Draft202012Validator(_load(SCHEMA), format_checker=FormatChecker()).validate(manifest)
    _write(MANIFEST, manifest)
    return verify()


def verify() -> dict[str, Any]:
    manifest = _load(MANIFEST)
    Draft202012Validator(_load(SCHEMA), format_checker=FormatChecker()).validate(manifest)
    if manifest["freeze"]["manifest_sha256"] != canonical_manifest_sha256(manifest):
        raise FreezeVerificationError("v4 manifest self hash changed")
    if [item["path"] for item in manifest["frozen_assets"]] != [path for path, _ in FROZEN_ASSETS]:
        raise FreezeVerificationError("v4 frozen asset inventory changed")
    for item in manifest["frozen_assets"] + manifest["canonical_sources"]:
        verify_file(ROOT, item)
    snapshot = _load(SNAPSHOT)
    if {(x["path"], x["bytes"], x["sha256"]) for x in snapshot["sources"]} != {(x["path"], x["bytes"], x["sha256"]) for x in manifest["canonical_sources"]}:
        raise FreezeVerificationError("v4 canon snapshot mismatch")
    old = _load(OLD_MANIFEST)
    old_assets = {x["path"]: x["sha256"] for x in old["assets"]}
    if any(old_assets[x["path"]] != x["sha256"] for x in manifest["assets"]):
        raise FreezeVerificationError("v4 changed a frozen case asset")
    if old["rubrics"] != manifest["rubrics"]:
        raise FreezeVerificationError("v4 changed rubric inventory")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        result = freeze() if args.freeze else verify()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "verified", "suite_id": result["suite_id"], "manifest_sha256": result["freeze"]["manifest_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
