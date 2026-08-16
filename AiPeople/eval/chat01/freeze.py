from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.leakage_guard import (
    DEFAULT_EXCLUDED_PATTERNS,
    DEFAULT_TRAINING_PATTERNS,
    NEAR_DUPLICATE_MIN_CHARS,
    NEAR_DUPLICATE_THRESHOLD,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "eval" / "chat01" / "suites" / "chat01_suite_manifest_v6.json"  # 2026-08-07：旧工具引用升级到最新冻结清单（v3-v5 保持历史）

FROZEN_ASSETS: tuple[tuple[str, str], ...] = (
    ("eval/chat01/schema/auto_result.schema.json", "schema"),
    ("eval/chat01/schema/blocker_rules.schema.json", "schema"),
    ("eval/chat01/schema/canon_drift.schema.json", "schema"),
    ("eval/chat01/schema/canon_snapshot.schema.json", "schema"),
    ("eval/chat01/schema/case.schema.json", "schema"),
    ("eval/chat01/schema/human_ballot.schema.json", "schema"),
    ("eval/chat01/schema/leakage_report.schema.json", "schema"),
    ("eval/chat01/schema/quality_rubric.schema.json", "schema"),
    ("eval/chat01/schema/semantic_review.schema.json", "schema"),
    ("eval/chat01h/leakage_report.schema.json", "schema"),
    ("eval/chat01/suites/canon_drift.json", "suite"),
    ("eval/chat01/suites/canon_snapshot_v2.json", "suite"),
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
    ("eval/chat01/freeze.py", "evaluator"),
    ("eval/chat01/leakage_guard.py", "evaluator"),
    ("eval/chat01/rules.py", "evaluator"),
    ("eval/chat01/suites/leakage_report_v2.json", "leakage_evidence"),
    ("training_package_m3_v2/eval_exclusions/chat01_v1.json", "leakage_evidence"),
)


class FreezeVerificationError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_manifest_sha256(manifest: dict[str, Any]) -> str:
    canonical = deepcopy(manifest)
    canonical["freeze"]["manifest_sha256"] = None
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _asset_entry(root: Path, relative: str, role: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        raise FreezeVerificationError(f"missing frozen asset: {relative}")
    return {
        "path": relative,
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def verify_file(root: Path, entry: dict[str, Any]) -> None:
    path = root / entry["path"]
    if not path.is_file():
        raise FreezeVerificationError(f"missing frozen asset: {entry['path']}")
    if path.stat().st_size != entry["bytes"]:
        raise FreezeVerificationError(f"byte count changed: {entry['path']}")
    if sha256_file(path) != entry["sha256"]:
        raise FreezeVerificationError(f"SHA256 changed: {entry['path']}")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FreezeVerificationError(f"expected JSON object: {path}")
    return value


def _validate_json(root: Path, value: dict[str, Any], schema_relative: str) -> None:
    schema = _load_json(root / schema_relative)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _verify_canon_sources(root: Path, manifest: dict[str, Any]) -> None:
    snapshot = _load_json(root / "eval/chat01/suites/canon_snapshot_v2.json")
    snapshot_sources = {source["path"]: source for source in snapshot["sources"]}
    for source in manifest["canonical_sources"]:
        verify_file(root, source)
        snapshot_source = snapshot_sources.get(source["path"])
        if snapshot_source is None:
            raise FreezeVerificationError(f"canon source absent from snapshot: {source['path']}")
        if (source["bytes"], source["sha256"]) != (
            snapshot_source["bytes"],
            snapshot_source["sha256"],
        ):
            raise FreezeVerificationError(f"canon snapshot mismatch: {source['path']}")


def _verify_leakage_report(root: Path, report: dict[str, Any]) -> None:
    _validate_json(root, report, "eval/chat01/schema/leakage_report.schema.json")
    if report["status"] != "passed" or report["summary"]["blocking_findings"] != 0:
        raise FreezeVerificationError("leakage report is not passing")
    for source in report["evaluation_sources"] + report["training_sources"]:
        verify_file(root, source)


def freeze_manifest(
    manifest_path: Path = MANIFEST_PATH,
    *,
    root: Path = ROOT,
    frozen_at: str | None = None,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    if manifest.get("status") == "frozen":
        raise FreezeVerificationError("manifest is already frozen; create a new version")

    _verify_canon_sources(root, manifest)
    report_relative = "eval/chat01/suites/leakage_report_v1.json"
    report = _load_json(root / report_relative)
    _verify_leakage_report(root, report)

    frozen_assets = [_asset_entry(root, path, role) for path, role in FROZEN_ASSETS]
    frozen_by_path = {asset["path"]: asset for asset in frozen_assets}
    for suite in manifest["assets"]:
        suite["sha256"] = frozen_by_path[suite["path"]]["sha256"]

    manifest["schemas"]["leakage_report"] = "eval/chat01/schema/leakage_report.schema.json"
    manifest["frozen_assets"] = frozen_assets
    manifest["leakage"] = {
        "report_path": report_relative,
        "status": "passed",
        "report_sha256": frozen_by_path[report_relative]["sha256"],
        "scan_contract": {
            "training_patterns": list(DEFAULT_TRAINING_PATTERNS),
            "excluded_patterns": list(DEFAULT_EXCLUDED_PATTERNS),
            "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
            "near_duplicate_min_chars": NEAR_DUPLICATE_MIN_CHARS,
        },
    }
    manifest["status"] = "frozen"
    manifest["frozen_at"] = frozen_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    manifest["freeze"] = {
        "hash_algorithm": "sha256",
        "manifest_hash_mode": "canonical_json_with_null_self",
        "immutable": True,
        "manifest_sha256": None,
    }
    manifest["training_exclusions"] = ["eval/chat01/"]
    manifest["notes"] = (
        "CHAT-01F frozen contract. Any byte change to a listed asset requires a new suite version. "
        "CHAT-01 evaluation assets are excluded from training; the hash-only blocklist is enforced by the M3_v2 packager."
    )
    manifest["freeze"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
    _validate_json(root, manifest, "eval/chat01/schema/suite_manifest.schema.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def verify_manifest(
    manifest_path: Path = MANIFEST_PATH,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    _validate_json(root, manifest, "eval/chat01/schema/suite_manifest.schema.json")
    if manifest["status"] != "frozen" or not manifest["freeze"]["immutable"]:
        raise FreezeVerificationError("manifest is not frozen")
    expected_self_hash = canonical_manifest_sha256(manifest)
    if manifest["freeze"]["manifest_sha256"] != expected_self_hash:
        raise FreezeVerificationError("manifest self SHA256 changed")

    paths = [asset["path"] for asset in manifest["frozen_assets"]]
    if paths != [path for path, _role in FROZEN_ASSETS] or len(paths) != len(set(paths)):
        raise FreezeVerificationError("frozen asset inventory changed")
    for asset in manifest["frozen_assets"]:
        verify_file(root, asset)
    _verify_canon_sources(root, manifest)

    report = _load_json(root / manifest["leakage"]["report_path"])
    _verify_leakage_report(root, report)
    if sha256_file(root / manifest["leakage"]["report_path"]) != manifest["leakage"]["report_sha256"]:
        raise FreezeVerificationError("leakage report SHA256 changed")

    frozen_by_path = {asset["path"]: asset for asset in manifest["frozen_assets"]}
    for suite in manifest["assets"]:
        if suite["sha256"] != frozen_by_path[suite["path"]]["sha256"]:
            raise FreezeVerificationError(f"suite SHA256 disagrees with inventory: {suite['path']}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze or verify the CHAT-01 evaluation contract.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    args = parser.parse_args()
    try:
        manifest = (
            freeze_manifest(args.manifest)
            if args.freeze
            else verify_manifest(args.manifest)
        )
    except (FreezeVerificationError, OSError, ValueError) as exc:
        print(f"CHAT-01 freeze verification failed: {exc}")
        return 2
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "frozen_assets": len(manifest["frozen_assets"]),
                "manifest_sha256": manifest["freeze"]["manifest_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
