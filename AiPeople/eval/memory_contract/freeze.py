from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "eval" / "memory_contract" / "mem01_contract_v1.json"
FROZEN_ASSET_PATHS = tuple(
    sorted(
        (
            "runtime/_memory_contracts.py",
            "runtime/schemas/memory_proposal_draft_v1.schema.json",
            "runtime/schemas/memory_representation_v1.schema.json",
            "eval/memory_contract/mem01_vocabulary_v1.json",
            "eval/memory_contract/build_fixtures.py",
            "eval/memory_contract/freeze.py",
            "eval/memory_contract/fixtures/valid/semantic_boundaries_v1.json",
            "eval/memory_contract/fixtures/valid/zero_proposals_v1.json",
            "eval/memory_contract/fixtures/invalid/evidence_boundaries_v1.json",
            "需求文档/项目框架需求.md",
            "设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md",
            "设计文档/AI设计/当前权威设计/MEM-01_MemoryRepresentation_schema与证据边界施工文档.md",
        )
    )
)


class FreezeVerificationError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    canonical = copy.deepcopy(manifest)
    canonical["freeze"]["manifest_sha256"] = None
    return json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_manifest_sha256(manifest: dict[str, Any]) -> str:
    return sha256_bytes(canonical_manifest_bytes(manifest))


def build_manifest(
    *,
    root: Path = ROOT,
    asset_paths: Sequence[str] = FROZEN_ASSET_PATHS,
    frozen_at: str | None = None,
) -> dict[str, Any]:
    normalized_paths = tuple(sorted(asset_paths))
    if tuple(asset_paths) != normalized_paths:
        raise FreezeVerificationError("frozen asset paths must be in exact sorted order")
    if len(normalized_paths) != len(set(normalized_paths)):
        raise FreezeVerificationError("frozen asset paths must be unique")

    assets = []
    for relative in normalized_paths:
        path = root / Path(relative)
        if not path.is_file():
            raise FreezeVerificationError(f"frozen asset is missing: {relative}")
        raw = path.read_bytes()
        assets.append(
            {"path": relative, "bytes": len(raw), "sha256": sha256_bytes(raw)}
        )

    manifest = {
        "contract_id": "mem01-memory-representation-v1",
        "schema_version": 1,
        "status": "frozen",
        "frozen_at": frozen_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "scope": {
            "includes": "MemoryRepresentation schema, pure validation and evidence-boundary fixtures",
            "excludes": [
                "model inference",
                "database access or migration",
                "embedding or vector projection",
                "training assets",
            ],
            "next_checkpoint": "MEM-02",
        },
        "frozen_assets": assets,
        "freeze": {
            "hash_algorithm": "sha256",
            "manifest_hash_mode": "canonical_json_with_null_self",
            "immutable": True,
            "manifest_sha256": None,
        },
    }
    manifest["freeze"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
    return manifest


def write_manifest(
    *,
    manifest_path: Path = MANIFEST_PATH,
    root: Path = ROOT,
    asset_paths: Sequence[str] = FROZEN_ASSET_PATHS,
) -> dict[str, Any]:
    if manifest_path.exists():
        raise FreezeVerificationError(
            "MEM-01 contract manifest already exists and is immutable"
        )
    manifest = build_manifest(root=root, asset_paths=asset_paths)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def verify_manifest(
    *,
    manifest_path: Path = MANIFEST_PATH,
    root: Path = ROOT,
    expected_asset_paths: Sequence[str] = FROZEN_ASSET_PATHS,
) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise FreezeVerificationError("MEM-01 contract manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FreezeVerificationError("MEM-01 manifest is not valid UTF-8 JSON") from error

    if manifest.get("contract_id") != "mem01-memory-representation-v1":
        raise FreezeVerificationError("unexpected MEM-01 contract_id")
    freeze = manifest.get("freeze")
    if not isinstance(freeze, dict) or freeze.get("immutable") is not True:
        raise FreezeVerificationError("MEM-01 manifest is not immutable")
    if freeze.get("manifest_sha256") != canonical_manifest_sha256(manifest):
        raise FreezeVerificationError("MEM-01 manifest self SHA256 mismatch")

    expected = list(expected_asset_paths)
    actual_assets = manifest.get("frozen_assets")
    if not isinstance(actual_assets, list):
        raise FreezeVerificationError("frozen_assets must be an array")
    actual_paths = [asset.get("path") for asset in actual_assets if isinstance(asset, dict)]
    if actual_paths != expected or len(actual_paths) != len(actual_assets):
        raise FreezeVerificationError("frozen asset path order or membership drifted")

    for asset in actual_assets:
        relative = asset["path"]
        path = root / Path(relative)
        if not path.is_file():
            raise FreezeVerificationError(f"frozen asset is missing: {relative}")
        raw = path.read_bytes()
        if asset.get("bytes") != len(raw):
            raise FreezeVerificationError(f"frozen asset byte count drifted: {relative}")
        if asset.get("sha256") != sha256_bytes(raw):
            raise FreezeVerificationError(f"frozen asset SHA256 drifted: {relative}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze or verify MEM-01 contract assets")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    result = write_manifest() if args.freeze else verify_manifest()
    print(
        json.dumps(
            {
                "status": "frozen" if args.freeze else "verified",
                "contract_id": result["contract_id"],
                "frozen_assets": len(result["frozen_assets"]),
                "manifest_sha256": result["freeze"]["manifest_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
