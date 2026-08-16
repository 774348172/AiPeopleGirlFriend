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
OLD_MANIFEST = ROOT / "eval/chat01/suites/chat01_suite_manifest_v5.json"
MANIFEST = ROOT / "eval/chat01/suites/chat01_suite_manifest_v6.json"
SNAPSHOT = ROOT / "eval/chat01/suites/canon_snapshot_v5.json"
DRIFT = ROOT / "eval/chat01/suites/canon_drift_v4.json"
CHANGE_NOTES = ROOT / "eval/chat01/suites/chat01_v6_change_notes.json"
LEAKAGE = ROOT / "eval/chat01/suites/leakage_report_v3.json"
MANIFEST_SCHEMA = ROOT / "eval/chat01/schema/suite_manifest.schema.json"
SNAPSHOT_SCHEMA = ROOT / "eval/chat01/schema/canon_snapshot.schema.json"
DRIFT_SCHEMA = ROOT / "eval/chat01/schema/canon_drift.schema.json"
CHANGE_NOTES_SCHEMA = ROOT / "eval/chat01v5/change_notes.schema.json"

CANON_SOURCES = (
    (1, "需求文档/项目框架需求.md", "product_requirements"),
    (2, "人物设定/秦/角色设定定稿.md", "character_prose"),
    (3, "人物设定/秦/bible.yaml", "character_bible"),
    (4, "人物设定/秦/canon.json", "character_facts"),
    (5, "人物设定/秦/timeline.yaml", "character_timeline"),
    (6, "设计文档/AI女友最小心智系统设计.md", "ai_implementation"),
)

ADDITIONAL_CONTRACT_SOURCES = (
    "设计文档/本地AI恋爱桌面宠物产品集成设计.md",
    "设计文档/AI聊天核心施工优先级总纲.md",
)

CONTRACT_SOURCE_PATHS = tuple(item[1] for item in CANON_SOURCES) + ADDITIONAL_CONTRACT_SOURCES

UNCHANGED_EVALUATION_ASSETS = (
    "eval/chat01/suites/chat01_dev_v1.jsonl",
    "eval/chat01/suites/chat01_frozen_single_v1.jsonl",
    "eval/chat01/suites/chat01_frozen_multiturn_v1.jsonl",
    "eval/chat01/suites/chat01_human_blind_v1.jsonl",
    "eval/chat01/rubrics/blocker_rules_v1.json",
    "eval/chat01/rubrics/human_blind_rubric_v1.md",
    "eval/chat01/rubrics/quality_rubric_v1.json",
    "eval/chat01/rubrics/semantic_rubric_v1.md",
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
    ("eval/chat01v5/change_notes.schema.json", "schema"),
    ("eval/chat01/suites/canon_drift_v3.json", "suite"),
    ("eval/chat01/suites/canon_snapshot_v4.json", "suite"),
    ("eval/chat01/suites/chat01_v6_change_notes.json", "suite"),
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
    ("eval/chat01v5/freeze.py", "evaluator"),
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
    result: dict[str, Any] = {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if role is not None:
        result["role"] = role
    return result


def _validate(path: Path, schema_path: Path) -> None:
    Draft202012Validator(
        _load(schema_path), format_checker=FormatChecker()
    ).validate(_load(path))


def _verify_v4_evaluation_assets(old: dict[str, Any]) -> None:
    if old["freeze"]["manifest_sha256"] != canonical_manifest_sha256(old):
        raise FreezeVerificationError("v4 manifest self hash changed")
    old_frozen = {item["path"]: item for item in old["frozen_assets"]}
    for relative in UNCHANGED_EVALUATION_ASSETS:
        if relative not in old_frozen:
            raise FreezeVerificationError(f"v4 missing inherited asset: {relative}")
        verify_file(ROOT, old_frozen[relative])


def build_snapshot(now: str) -> dict[str, Any]:
    return {
        "snapshot_id": "chat01-canon-v4",
        "schema_version": 1,
        "created_at": now,
        "hash_algorithm": "sha256",
        "sources": [
            {"authority_rank": rank, **_artifact(relative), "role": role}
            for rank, relative, role in CANON_SOURCES
        ],
        "resolved_facts": {
            "character_name": "秦未晞",
            "character_age": 22,
            "player_name": "浩然",
            "player_age": 24,
            "occupation": "自由插画师 / 自媒体博主",
            "city": "金陵",
            "current_living": "两居室合租，刚开始不到一个月",
            "current_relationship": "合租室友 + 暧昧期，尚未确认恋爱或婚姻",
            "character_to_player_names": ["B哥", "浩然"],
            "player_to_character_name": "秦老",
        },
        "consistency": {
            "status": "consistent",
            "conflicts": [],
            "notes": (
                "v5 接受当前产品路线更新：P0 主模型改为 Qwen3.5-4B；约 1000 条仅是首个工程候选；"
                "自我时间线、离线经历、内生欲望和主动联系成为 P0 能力。角色硬事实与 v4 一致。"
            ),
        },
    }


def build_drift(now: str) -> dict[str, Any]:
    return {
        "register_id": "chat01-canon-drift-v3",
        "schema_version": 1,
        "created_at": now,
        "canon_snapshot_id": "chat01-canon-v4",
        "scan_contract": {
            "roots": ["runtime", "tests", "人物设定/秦", "data_gen_v4", "profiles/qinweixi"],
            "patterns": ["出生于航天基地", "hometown: 航天基地", "爸爸做轨道计算"],
            "interpretation": (
                "FREEZE-01 复核角色正典硬事实漂移；Qwen3.5、记忆、自我时间线和主动性路线变化"
                "记录在 v5 change notes，不把尚未施工的阶段 2-6 误报为现有运行时漂移。"
            ),
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


def build_change_notes(now: str, old: dict[str, Any]) -> dict[str, Any]:
    return {
        "change_id": "chat01-v5-contract-refresh",
        "schema_version": 1,
        "created_at": now,
        "previous_suite": {
            "suite_id": old["suite_id"],
            "manifest_path": "eval/chat01/suites/chat01_suite_manifest_v4.json",
            "manifest_sha256": old["freeze"]["manifest_sha256"],
        },
        "current_suite": {
            "suite_id": "chat01-qinweixi-v5",
            "manifest_path": "eval/chat01/suites/chat01_suite_manifest_v5.json",
            "manifest_hash_mode": "canonical_json_with_null_self",
        },
        "contract_sources": [_artifact(path) for path in CONTRACT_SOURCE_PATHS],
        "accepted_changes": [
            {
                "change_id": "model.qwen35-4b",
                "summary": "P0 主生成模型固定为本地 Qwen3.5-4B；旧 Qwen3-4B/v2500 仅作历史对照。",
                "effect_on_chat01_v5": "后续基座与训练候选必须使用同一 Qwen3.5-4B revision、模板和执行 profile；本次不运行模型。",
            },
            {
                "change_id": "training.first-1000",
                "summary": "首个候选使用约 1000 条新秦未晞数据，且数量不等于质量通过。",
                "effect_on_chat01_v5": "保留冻结题作为独立评测输入；训练数据边界与新泄漏扫描交由 FREEZE-02。",
            },
            {
                "change_id": "gates.engineering-vs-product",
                "summary": "工程施工入口与最终 P0 产品闸门分离。",
                "effect_on_chat01_v5": "v5 结果可支持工程准入比较，但不能单独证明长期记忆、主动性或最终产品通过。",
            },
            {
                "change_id": "memory.fixed-local-route",
                "summary": "记忆选择固定为 CPU bge-small-zh-v1.5 全库扫描 Top32，再由 GPU Qwen3-Reranker-0.6B 精排。",
                "effect_on_chat01_v5": "CHAT-01 继续评估可见回复基础；记忆选择质量与 P95 由阶段 3-5 专项合同验收。",
            },
            {
                "change_id": "self.timeline",
                "summary": "角色必须记得自己说过的话，并维护带证据的自我时间线与冲突修正链。",
                "effect_on_chat01_v5": "作为 P0 权威需求冻结；旧多轮集只覆盖当前场景连续性，不宣称覆盖跨日自我时间线。",
            },
            {
                "change_id": "initiative.intrinsic",
                "summary": "主动行为必须来自角色经历、心境、关系和欲望评估，定时器只能触发评估，不能直接决定发送。",
                "effect_on_chat01_v5": "作为 P0 权威需求冻结；专项 OFFSCREEN/MOTIVE/PROACTIVE 评测在阶段 6 建立。",
            },
            {
                "change_id": "resources.local-5gb",
                "summary": "P0 主模型、reranker 与运行时 GPU 总峰值必须低于 5120 MiB。",
                "effect_on_chat01_v5": "本冻结不作性能结论；目标机实测属于 SELECT-05 与 FINAL-04。",
            },
        ],
        "unchanged_evaluation_assets": [_artifact(path) for path in UNCHANGED_EVALUATION_ASSETS],
        "coverage_boundary": {
            "covered_now": [
                "REPLY 的身份、正典、关系边界、未知现实、安全、通用能力、相关性、情绪和表达自然度",
                "当前测试场景内的多轮承接、话题切换、关系试探、注入与恢复",
                "既有人工盲测与长聊主题包",
            ],
            "not_claimed": [
                "MEMORY_PROPOSE、全库 Top32、Qwen3-Reranker 和 SelectedMemoryFrame 的质量或性能",
                "跨日角色自我时间线、OFFSCREEN_UPDATE、MOTIVE_EVALUATE 和 PROACTIVE_REPLY 的完整能力",
                "Qwen3.5-4B 候选、约 1000 条数据或 5GB 显存目标已经通过",
            ],
            "rule": (
                "FREEZE-01 只刷新权威合同并保持既有评测题、oracle 与 rubric 字节不变。"
                "新增能力必须在对应施工阶段建立专项评测，不得用 CHAT-01 v5 总分替代。"
            ),
        },
        "next_checkpoint": "FREEZE-02",
    }


def freeze() -> dict[str, Any]:
    generated = (SNAPSHOT, DRIFT, CHANGE_NOTES, MANIFEST)
    if any(path.exists() for path in generated):
        raise FreezeVerificationError("v6 freeze assets already exist; they are immutable")

    old = _load(OLD_MANIFEST)
    _verify_v4_evaluation_assets(old)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        _write(SNAPSHOT, build_snapshot(now))
        _write(DRIFT, build_drift(now))
        change_notes = build_change_notes(now, old)
        _write(CHANGE_NOTES, change_notes)
        _validate(SNAPSHOT, SNAPSHOT_SCHEMA)
        _validate(DRIFT, DRIFT_SCHEMA)
        _validate(CHANGE_NOTES, CHANGE_NOTES_SCHEMA)

        leakage = _load(LEAKAGE)
        if leakage["status"] != "passed" or leakage["summary"]["blocking_findings"]:
            raise FreezeVerificationError("inherited leakage evidence is not passing")

        manifest = deepcopy(old)
        manifest.update(
            {
                "suite_id": "chat01-qinweixi-v6",
                "version": "v6",
                "status": "frozen",
                "created_at": now,
                "frozen_at": now,
                "purpose": (
                    "FREEZE-02 contract refresh (2026-08-07): canonical source "
                    "设计文档/AI聊天核心施工优先级总纲.md updated externally; v6 re-freezes contract sources. "
                    "Existing cases, oracles, and rubrics remain byte-identical to v5."
                ),
            }
        )
        manifest["canonical_sources"] = [
            {"path": item["path"], "required": True, "bytes": item["bytes"], "sha256": item["sha256"]}
            for item in change_notes["contract_sources"]
        ]
        manifest["frozen_assets"] = [_artifact(path, role) for path, role in FROZEN_ASSETS]
        by_path = {item["path"]: item for item in manifest["frozen_assets"]}
        for asset in manifest["assets"]:
            asset["sha256"] = by_path[asset["path"]]["sha256"]
        manifest["leakage"] = {
            "report_path": "eval/chat01/suites/leakage_report_v3.json",
            "status": "passed",
            "report_sha256": sha256_file(LEAKAGE),
            "scan_contract": {
                "training_patterns": list(DEFAULT_TRAINING_PATTERNS),
                "excluded_patterns": list(DEFAULT_EXCLUDED_PATTERNS),
                "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
                "near_duplicate_min_chars": NEAR_DUPLICATE_MIN_CHARS,
            },
        }
        manifest["freeze"] = {
            "hash_algorithm": "sha256",
            "manifest_hash_mode": "canonical_json_with_null_self",
            "immutable": True,
            "manifest_sha256": None,
        }
        manifest["notes"] = (
            "CHAT-01 v6 supersedes v5 for new work. All evaluation cases and rubrics are byte-identical to v5. "
            "v6 exists because the contract source 设计文档/AI聊天核心施工优先级总纲.md was updated (external edit); "
            "canonical sources are re-frozen at current state. The inherited leakage report only proves the unchanged "
            "evaluation text is isolated from the training inputs it scanned; FREEZE-02 must freeze and rescan the new "
            "approximately 1000-sample training corpus."
        )
        manifest["freeze"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
        _validate_manifest_value(manifest)
        _write(MANIFEST, manifest)
        return verify()
    except Exception:
        for path in reversed(generated):
            if path.exists():
                path.unlink()
        raise


def _validate_manifest_value(manifest: dict[str, Any]) -> None:
    Draft202012Validator(
        _load(MANIFEST_SCHEMA), format_checker=FormatChecker()
    ).validate(manifest)


def verify() -> dict[str, Any]:
    manifest = _load(MANIFEST)
    _validate_manifest_value(manifest)
    if manifest["freeze"]["manifest_sha256"] != canonical_manifest_sha256(manifest):
        raise FreezeVerificationError("v5 manifest self hash changed")
    if [item["path"] for item in manifest["frozen_assets"]] != [path for path, _ in FROZEN_ASSETS]:
        raise FreezeVerificationError("v5 frozen asset inventory changed")
    if [item["path"] for item in manifest["canonical_sources"]] != list(CONTRACT_SOURCE_PATHS):
        raise FreezeVerificationError("v5 contract source inventory changed")
    for item in manifest["frozen_assets"] + manifest["canonical_sources"]:
        verify_file(ROOT, item)

    _validate(SNAPSHOT, SNAPSHOT_SCHEMA)
    _validate(DRIFT, DRIFT_SCHEMA)
    _validate(CHANGE_NOTES, CHANGE_NOTES_SCHEMA)
    snapshot = _load(SNAPSHOT)
    manifest_sources = {item["path"]: item for item in manifest["canonical_sources"]}
    for source in snapshot["sources"]:
        current = manifest_sources[source["path"]]
        if (source["bytes"], source["sha256"]) != (current["bytes"], current["sha256"]):
            raise FreezeVerificationError("v5 canon snapshot mismatch")

    old = _load(OLD_MANIFEST)
    _verify_v4_evaluation_assets(old)
    old_assets = {item["path"]: item["sha256"] for item in old["assets"]}
    if any(old_assets[item["path"]] != item["sha256"] for item in manifest["assets"]):
        raise FreezeVerificationError("v5 changed a frozen case asset")
    if old["rubrics"] != manifest["rubrics"]:
        raise FreezeVerificationError("v5 changed rubric inventory")

    notes = _load(CHANGE_NOTES)
    notes_sources = {(item["path"], item["bytes"], item["sha256"]) for item in notes["contract_sources"]}
    manifest_source_refs = {(item["path"], item["bytes"], item["sha256"]) for item in manifest["canonical_sources"]}
    if notes_sources != manifest_source_refs:
        raise FreezeVerificationError("v5 change notes source mismatch")
    if notes["previous_suite"]["manifest_sha256"] != old["freeze"]["manifest_sha256"]:
        raise FreezeVerificationError("v5 previous suite reference changed")
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
    print(
        json.dumps(
            {
                "status": "verified",
                "suite_id": result["suite_id"],
                "manifest_sha256": result["freeze"]["manifest_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
