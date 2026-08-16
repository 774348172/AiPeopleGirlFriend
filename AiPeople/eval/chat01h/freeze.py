from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import FreezeVerificationError, canonical_manifest_sha256, sha256_file, verify_file
from eval.chat01.leakage_guard import (
    DEFAULT_EXCLUDED_PATTERNS,
    DEFAULT_TRAINING_PATTERNS,
    NEAR_DUPLICATE_MIN_CHARS,
    NEAR_DUPLICATE_THRESHOLD,
    scan_leakage,
)


ROOT = Path(__file__).resolve().parents[2]
V1_MANIFEST_PATH = ROOT / "eval" / "chat01" / "suites" / "chat01_suite_manifest_v1.json"
MANIFEST_PATH = ROOT / "eval" / "chat01" / "suites" / "chat01_suite_manifest_v2.json"
REPORT_PATH = ROOT / "eval" / "chat01" / "suites" / "leakage_report_v2.json"
REPORT_SCHEMA_PATH = ROOT / "eval" / "chat01h" / "leakage_report.schema.json"
SUITE_SCHEMA_PATH = ROOT / "eval" / "chat01" / "schema" / "suite_manifest.schema.json"

FROZEN_ASSETS_V2: tuple[tuple[str, str], ...] = (
    ("eval/chat01/schema/auto_result.schema.json", "schema"),
    ("eval/chat01/schema/blocker_rules.schema.json", "schema"),
    ("eval/chat01/schema/canon_drift.schema.json", "schema"),
    ("eval/chat01/schema/canon_snapshot.schema.json", "schema"),
    ("eval/chat01/schema/case.schema.json", "schema"),
    ("eval/chat01/schema/human_ballot.schema.json", "schema"),
    ("eval/chat01/schema/quality_rubric.schema.json", "schema"),
    ("eval/chat01/schema/semantic_review.schema.json", "schema"),
    ("eval/chat01/schema/suite_manifest.schema.json", "schema"),
    ("eval/chat01h/leakage_report.schema.json", "schema"),
    ("eval/chat01/suites/canon_drift.json", "suite"),
    ("eval/chat01/suites/canon_snapshot_v1.json", "suite"),
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
    ("eval/chat01/rules.py", "evaluator"),
    ("eval/chat01/leakage_guard.py", "evaluator"),
    ("eval/chat01h/freeze.py", "evaluator"),
    ("eval/chat01/suites/leakage_report_v2.json", "leakage_evidence"),
    ("training_package_m3_v2/eval_exclusions/chat01_v1.json", "leakage_evidence"),
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FreezeVerificationError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _validate(value: dict[str, Any], schema_path: Path) -> None:
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _asset_entry(relative: str, role: str) -> dict[str, Any]:
    path = ROOT / relative
    if not path.is_file():
        raise FreezeVerificationError(f"missing frozen asset: {relative}")
    return {"path": relative, "role": role, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def build_leakage_report() -> dict[str, Any]:
    report = scan_leakage()
    report["report_id"] = "chat01-leakage-v2"
    _validate(report, REPORT_SCHEMA_PATH)
    return report


def _verify_report(report: dict[str, Any]) -> None:
    _validate(report, REPORT_SCHEMA_PATH)
    if report["status"] != "passed" or report["summary"]["blocking_findings"] != 0:
        raise FreezeVerificationError("CHAT-01H leakage report is not passing")
    for source in report["evaluation_sources"] + report["training_sources"]:
        verify_file(ROOT, source)


def _draft_manifest() -> dict[str, Any]:
    manifest = deepcopy(_load_json(V1_MANIFEST_PATH))
    manifest.update(
        suite_id="chat01-qinweixi-v2",
        version="v2",
        status="draft",
        created_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        frozen_at=None,
        purpose="Reuse the byte-identical CHAT-01 v1 evaluation assets while re-freezing leakage evidence against the current Qin Weixi training corpus.",
        frozen_assets=[],
        notes="CHAT-01H draft. Evaluation cases, rubrics, and oracles remain byte-identical to v1; only current training-corpus leakage evidence is refreshed.",
    )
    manifest["schemas"]["leakage_report"] = "eval/chat01h/leakage_report.schema.json"
    manifest["leakage"] = {
        "report_path": "eval/chat01/suites/leakage_report_v2.json",
        "status": "pending",
        "report_sha256": None,
        "scan_contract": {
            "training_patterns": list(DEFAULT_TRAINING_PATTERNS),
            "excluded_patterns": list(DEFAULT_EXCLUDED_PATTERNS),
            "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
            "near_duplicate_min_chars": NEAR_DUPLICATE_MIN_CHARS,
        },
    }
    manifest["freeze"] = {"hash_algorithm": "sha256", "manifest_hash_mode": "canonical_json_with_null_self", "immutable": False, "manifest_sha256": None}
    return manifest


def freeze_current(*, frozen_at: str | None = None) -> dict[str, Any]:
    if MANIFEST_PATH.exists():
        existing = _load_json(MANIFEST_PATH)
        if existing.get("status") == "frozen":
            raise FreezeVerificationError("CHAT-01H v2 manifest is already frozen")
    report = build_leakage_report()
    _verify_report(report)
    _write_json(REPORT_PATH, report)

    manifest = _draft_manifest()
    frozen_assets = [_asset_entry(path, role) for path, role in FROZEN_ASSETS_V2]
    frozen_by_path = {asset["path"]: asset for asset in frozen_assets}
    for suite in manifest["assets"]:
        suite["sha256"] = frozen_by_path[suite["path"]]["sha256"]
    manifest["frozen_assets"] = frozen_assets
    manifest["leakage"]["status"] = "passed"
    manifest["leakage"]["report_sha256"] = frozen_by_path["eval/chat01/suites/leakage_report_v2.json"]["sha256"]
    manifest["status"] = "frozen"
    manifest["frozen_at"] = frozen_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    manifest["freeze"].update(immutable=True, manifest_sha256=None)
    manifest["notes"] = (
        "CHAT-01H v2 frozen contract. Evaluation cases, oracles, rubrics, canonical sources, and exclusion hashes are byte-identical to v1. "
        "The leakage report alone is refreshed against the current training corpus; v1 remains immutable historical evidence."
    )
    manifest["freeze"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
    _validate(manifest, SUITE_SCHEMA_PATH)
    _write_json(MANIFEST_PATH, manifest)
    return verify_manifest_v2()


def verify_manifest_v2() -> dict[str, Any]:
    manifest = _load_json(MANIFEST_PATH)
    _validate(manifest, SUITE_SCHEMA_PATH)
    if manifest["status"] != "frozen" or not manifest["freeze"]["immutable"]:
        raise FreezeVerificationError("CHAT-01H v2 manifest is not frozen")
    if manifest["freeze"]["manifest_sha256"] != canonical_manifest_sha256(manifest):
        raise FreezeVerificationError("CHAT-01H v2 manifest self SHA256 changed")
    paths = [asset["path"] for asset in manifest["frozen_assets"]]
    expected = [path for path, _role in FROZEN_ASSETS_V2]
    if paths != expected or len(paths) != len(set(paths)):
        raise FreezeVerificationError("CHAT-01H v2 frozen asset inventory changed")
    for asset in manifest["frozen_assets"]:
        verify_file(ROOT, asset)
    for source in manifest["canonical_sources"]:
        verify_file(ROOT, source)
    report = _load_json(REPORT_PATH)
    _verify_report(report)
    if sha256_file(REPORT_PATH) != manifest["leakage"]["report_sha256"]:
        raise FreezeVerificationError("CHAT-01H leakage report SHA256 changed")
    frozen_by_path = {asset["path"]: asset for asset in manifest["frozen_assets"]}
    for suite in manifest["assets"]:
        if suite["sha256"] != frozen_by_path[suite["path"]]["sha256"]:
            raise FreezeVerificationError(f"suite SHA256 disagrees with v2 inventory: {suite['path']}")
    v1 = _load_json(V1_MANIFEST_PATH)
    if [(item["path"], item["sha256"]) for item in manifest["assets"]] != [(item["path"], item["sha256"]) for item in v1["assets"]]:
        raise FreezeVerificationError("CHAT-01 evaluation assets differ between v1 and v2")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze or verify CHAT-01H current-corpus leakage evidence.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        manifest = freeze_current() if args.freeze else verify_manifest_v2()
    except (FreezeVerificationError, OSError, ValueError) as exc:
        print(f"CHAT-01H freeze verification failed: {exc}")
        return 2
    print(json.dumps({"status": manifest["status"], "suite_id": manifest["suite_id"], "frozen_assets": len(manifest["frozen_assets"]), "manifest_sha256": manifest["freeze"]["manifest_sha256"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
