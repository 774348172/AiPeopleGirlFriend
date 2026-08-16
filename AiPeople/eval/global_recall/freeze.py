from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from eval.selector_query.freeze import verify_manifest as verify_recall01_manifest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "eval" / "global_recall" / "recall02_04_contract_v1.json"
FROZEN_ASSET_PATHS = tuple(
    sorted(
        (
            "pyproject.toml",
            "runtime/_global_recall.py",
            "runtime/_recall_candidates.py",
            "eval/global_recall/benchmark.py",
            "eval/global_recall/evaluate.py",
            "eval/global_recall/freeze.py",
            "eval/global_recall/recall02_04_profile_v1.json",
            "eval/global_recall/recall04_engineering_benchmark_v1.json",
            "eval/global_recall/recall04_semantic_cases_v1.json",
            "eval/global_recall/recall04_semantic_status_v1.json",
            "eval/selector_query/recall01_contract_v1.json",
            "设计文档/AI设计/当前权威设计/RelationshipRuntime_全局记忆选择器训练与模型决策方案_V2.md",
            "设计文档/AI设计/当前权威设计/RECALL-02-04_全局精确扫描Top32与长库验收施工文档.md",
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
        "contract_id": "recall02-04-global-exact-top32-v1",
        "schema_version": 1,
        "status": "frozen_with_external_semantic_gate_pending",
        "frozen_at": frozen_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "upstream": {
            "contract_id": "recall01-selector-query-representation-v1",
            "manifest": "eval/selector_query/recall01_contract_v1.json",
        },
        "scope": {
            "includes": "global exact float32 scan, memory_id max-view aggregation, stable Top32, current metadata loading, stale guards, 100k-event engineering benchmark and frozen semantic evaluator",
            "passed": [
                "RECALL-02 exact full-pool scan",
                "RECALL-03 unique stable Top32 candidate contract",
                "RECALL-04 target-scale exact scan engineering P95",
            ],
            "pending_external_assets": [
                "real local BGE global_recall_at_32",
                "full selector_total_ms with Qwen reranker",
            ],
            "excludes": [
                "ANN",
                "reranker inference",
                "final memory selection",
                "model download or training",
            ],
            "next_checkpoint": "SELECT-01",
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
        verify_recall01_manifest()
    if manifest_path.exists():
        raise FreezeVerificationError(
            "RECALL-02-04 contract manifest already exists and is immutable"
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
    verify_upstream: bool = True,
) -> dict[str, Any]:
    if verify_upstream:
        verify_recall01_manifest()
    if not manifest_path.is_file():
        raise FreezeVerificationError("RECALL-02-04 contract manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contract_id") != "recall02-04-global-exact-top32-v1":
        raise FreezeVerificationError("unexpected RECALL-02-04 contract_id")
    freeze = manifest.get("freeze")
    if not isinstance(freeze, dict) or freeze.get("immutable") is not True:
        raise FreezeVerificationError("RECALL-02-04 manifest is not immutable")
    if freeze.get("manifest_sha256") != canonical_manifest_sha256(manifest):
        raise FreezeVerificationError("RECALL-02-04 manifest self SHA256 mismatch")
    assets = manifest.get("frozen_assets")
    if not isinstance(assets, list):
        raise FreezeVerificationError("frozen_assets must be an array")
    paths = [asset.get("path") for asset in assets if isinstance(asset, dict)]
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
    parser = argparse.ArgumentParser(description="Freeze or verify RECALL-02-04 assets")
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

