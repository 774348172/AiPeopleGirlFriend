from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = ROOT / "eval" / "baiweixi_quality"
MANIFEST_PATH = EVAL_ROOT / "suite_manifest_v1.json"
SCHEMA_PATH = EVAL_ROOT / "schema" / "case_v1.schema.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from error
        if not isinstance(value, dict):
            raise ValueError(f"case must be an object at {path}:{line_number}")
        values.append(value)
    return values


def _canonical_manifest_sha256(manifest: dict[str, Any]) -> str:
    canonical = dict(manifest)
    canonical["manifest_sha256"] = None
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected_manifest_hash = manifest.get("manifest_sha256")
    actual_manifest_hash = _canonical_manifest_sha256(manifest)
    if expected_manifest_hash != actual_manifest_hash:
        raise ValueError("suite manifest SHA256 mismatch")
    if manifest.get("status") != "frozen":
        raise ValueError("suite status must be frozen")
    if manifest.get("character_id") != "baiweixi":
        raise ValueError("suite character_id must be baiweixi")
    if manifest.get("world_id") != "songjiangfu":
        raise ValueError("suite world_id must be songjiangfu")
    if manifest.get("protagonist_id") != "protagonist":
        raise ValueError("suite protagonist_id must be protagonist")

    for source in manifest["canonical_sources"]:
        path = ROOT / source["path"]
        if not path.is_file():
            raise ValueError(f"canonical source is missing: {source['path']}")
        if path.stat().st_size != source["bytes"] or _sha256(path) != source["sha256"]:
            raise ValueError(f"canonical source drift: {source['path']}")

    for asset in manifest["assets"]:
        path = ROOT / asset["path"]
        if not path.is_file():
            raise ValueError(f"frozen asset is missing: {asset['path']}")
        if path.stat().st_size != asset["bytes"] or _sha256(path) != asset["sha256"]:
            raise ValueError(f"frozen asset drift: {asset['path']}")

    leakage = json.loads((ROOT / manifest["leakage_report"]["path"]).read_text(encoding="utf-8"))
    if leakage.get("status") != "passed":
        raise ValueError("leakage report is not passed")
    if leakage.get("exact_matches") or leakage.get("near_matches"):
        raise ValueError("leakage report contains matches")
    if _sha256(ROOT / manifest["leakage_report"]["path"]) != manifest["leakage_report"]["sha256"]:
        raise ValueError("leakage report SHA256 mismatch")

    exclusion_path = ROOT / "eval" / "baiweixi_quality" / "training_exclusion_v1.json"
    exclusion = json.loads(exclusion_path.read_text(encoding="utf-8"))
    if exclusion.get("source_suite") != manifest["suite_id"]:
        raise ValueError("training exclusion is bound to another suite")
    if len(exclusion.get("case_ids", [])) != manifest["case_counts"]["total"]:
        raise ValueError("training exclusion case count mismatch")
    if len(exclusion.get("case_ids", [])) != len(set(exclusion["case_ids"])):
        raise ValueError("training exclusion contains duplicate case IDs")
    hashes = exclusion.get("normalized_user_text_sha256", [])
    if not hashes or len(hashes) != len(set(hashes)):
        raise ValueError("training exclusion hashes are missing or duplicated")

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    cases: list[dict[str, Any]] = []
    case_assets = [
        ROOT / asset["path"]
        for asset in manifest["assets"]
        if str(asset["path"]).startswith("eval/baiweixi_quality/cases/")
    ]
    for path in case_assets:
        cases.extend(_read_jsonl(path))
    identifiers: set[str] = set()
    frozen_seed_set = manifest["frozen_seed_set"]
    for case in cases:
        errors = sorted(validator.iter_errors(case), key=lambda error: list(error.path))
        if errors:
            raise ValueError(
                f"invalid case {case.get('case_id')}: "
                + "; ".join(error.message for error in errors[:5])
            )
        case_id = str(case["case_id"])
        if case_id in identifiers:
            raise ValueError(f"duplicate case_id: {case_id}")
        identifiers.add(case_id)
        if case["risk"] in {"blocker", "important"} and case["generation"]["seed_set"] != frozen_seed_set:
            raise ValueError(f"case does not use frozen three-seed set: {case_id}")
        for turn in case["turns"]:
            has_snapshot = turn["world_snapshot"] is not None
            if case["evaluation_layer"] == "character_direct" and has_snapshot:
                raise ValueError(f"direct case carries a world snapshot: {case_id}")
            if case["evaluation_layer"] != "character_direct" and not has_snapshot:
                raise ValueError(f"runtime case lacks a world snapshot: {case_id}")

    actual_counts = {
        "total": len(cases),
        "categories": dict(sorted(Counter(case["category"] for case in cases).items())),
        "evaluation_layers": dict(sorted(Counter(case["evaluation_layer"] for case in cases).items())),
        "risks": dict(sorted(Counter(case["risk"] for case in cases).items())),
    }
    if actual_counts != manifest["case_counts"]:
        raise ValueError("suite case counts do not match manifest")
    if actual_counts["categories"].get("protagonist_grounding", 0) < 10:
        raise ValueError("suite lacks protagonist grounding coverage")
    if actual_counts["categories"].get("heroine_state_continuity", 0) < 8:
        raise ValueError("suite lacks heroine state continuity coverage")
    if actual_counts["categories"].get("single_world", 0) < 6:
        raise ValueError("suite lacks single-world coverage")
    if actual_counts["evaluation_layers"].get("human_session", 0) < 4:
        raise ValueError("suite lacks required human sessions")

    print(
        json.dumps(
            {
                "suite_id": manifest["suite_id"],
                "status": manifest["status"],
                "case_counts": actual_counts,
                "manifest_sha256": actual_manifest_hash,
                "validated": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"VALIDATION_FAILED: {error}", file=sys.stderr)
        sys.exit(1)
