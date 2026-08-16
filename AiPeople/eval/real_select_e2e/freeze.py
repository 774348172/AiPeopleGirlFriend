from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from eval.memory_e2e.freeze import verify_manifest as verify_select_e2e_manifest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "eval" / "real_select_e2e" / "real01_03_contract_v1.json"
FROZEN_ASSET_PATHS = tuple(
    sorted(
        (
            "eval/memory_e2e/select_e2e_contract_v1.json",
            "eval/real_select_e2e/__init__.py",
            "eval/real_select_e2e/acceptance.py",
            "eval/real_select_e2e/freeze.py",
            "eval/real_select_e2e/real_acceptance_profile_v1.json",
            "eval/real_select_e2e/real_asset_inventory_v1.json",
            "eval/real_select_e2e/recall04_semantic_cases_v2.json",
            "eval/real_select_e2e/recall_v2.py",
            "runtime/_real_assets.py",
            "runtime/_real_stack.py",
            "runtime/adapters/local_bge.py",
            "runtime/adapters/qwen3_reranker.py",
            "runtime/schemas/bge_manifest_v1.schema.json",
            "runtime/schemas/real_asset_bundle_v1.schema.json",
            "runtime/schemas/reply_manifest_v1.schema.json",
            "runtime/schemas/reranker_manifest_v1.schema.json",
            "设计文档/AI设计/当前权威设计/REAL-01-03_真实模型验收基础设施施工文档.md",
        )
    )
)


class FreezeVerificationError(RuntimeError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_manifest_sha256(manifest: dict[str, Any]) -> str:
    canonical = copy.deepcopy(manifest)
    canonical["freeze"]["manifest_sha256"] = None
    return _sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def build_manifest(
    *,
    root: Path = ROOT,
    asset_paths: Sequence[str] = FROZEN_ASSET_PATHS,
    frozen_at: str | None = None,
) -> dict[str, Any]:
    if tuple(asset_paths) != tuple(sorted(asset_paths)):
        raise FreezeVerificationError("frozen asset paths must be in exact sorted order")
    if len(asset_paths) != len(set(asset_paths)):
        raise FreezeVerificationError("frozen asset paths must be unique")
    assets = []
    for relative in asset_paths:
        path = root / Path(relative)
        if not path.is_file():
            raise FreezeVerificationError(f"frozen asset is missing: {relative}")
        raw = path.read_bytes()
        assets.append({"path": relative, "bytes": len(raw), "sha256": _sha256(raw)})
    manifest = {
        "contract_id": "real01-03-acceptance-infrastructure-v1",
        "schema_version": 1,
        "status": "frozen_with_real_assets_and_product_dataset_pending",
        "frozen_at": frozen_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "upstream": {
            "contract_id": "select-e2e-long-term-memory-online-v1",
            "manifest": "eval/memory_e2e/select_e2e_contract_v1.json",
        },
        "scope": {
            "passed": [
                "REAL-01 shared global pool RECALL-04 v2 evaluator and preregistered gates",
                "REAL-02 BGE reranker Qwen3.5 and bundle manifest verification",
                "REAL-03 offline BGE and reranker adapters plus bound real stack factory",
            ],
            "pending_external_assets": [
                "delivered local BGE package",
                "reranker baseline candidate and quantized packages",
                "selector validation test_frozen and long_memory_e2e splits",
                "Qwen3.5 untrained-base and Qin Weixi candidate GGUF packages",
            ],
            "not_claimed": [
                "real semantic quality",
                "real latency or GPU memory",
                "one-hour stability",
                "product acceptance",
            ],
            "next_checkpoint": "REAL-04 through REAL-07 after asset delivery",
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
    verify_upstream: bool = True,
) -> dict[str, Any]:
    if verify_upstream:
        verify_select_e2e_manifest()
    if manifest_path.exists():
        raise FreezeVerificationError("REAL-01-03 manifest already exists and is immutable")
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
    verify_upstream: bool = True,
) -> dict[str, Any]:
    if verify_upstream:
        verify_select_e2e_manifest()
    if not manifest_path.is_file():
        raise FreezeVerificationError("REAL-01-03 manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contract_id") != "real01-03-acceptance-infrastructure-v1":
        raise FreezeVerificationError("unexpected REAL-01-03 contract_id")
    if manifest.get("status") != "frozen_with_real_assets_and_product_dataset_pending":
        raise FreezeVerificationError("unexpected REAL-01-03 status")
    freeze = manifest.get("freeze")
    if not isinstance(freeze, dict) or freeze.get("immutable") is not True:
        raise FreezeVerificationError("REAL-01-03 manifest is not immutable")
    if freeze.get("manifest_sha256") != canonical_manifest_sha256(manifest):
        raise FreezeVerificationError("REAL-01-03 manifest self SHA256 mismatch")
    assets = manifest.get("frozen_assets")
    if not isinstance(assets, list):
        raise FreezeVerificationError("frozen_assets must be an array")
    paths = [item.get("path") for item in assets if isinstance(item, dict)]
    if paths != list(expected_asset_paths) or len(paths) != len(assets):
        raise FreezeVerificationError("frozen asset path order or membership drifted")
    for asset in assets:
        path = root / Path(asset["path"])
        if not path.is_file():
            raise FreezeVerificationError(f"frozen asset is missing: {asset['path']}")
        raw = path.read_bytes()
        if asset.get("bytes") != len(raw) or asset.get("sha256") != _sha256(raw):
            raise FreezeVerificationError(f"frozen asset drifted: {asset['path']}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze or verify REAL-01 through REAL-03")
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
