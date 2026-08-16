from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from eval.global_recall.freeze import verify_manifest as verify_recall02_04_manifest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "eval" / "memory_e2e" / "select_e2e_contract_v1.json"
FROZEN_ASSET_PATHS = tuple(
    sorted(
        (
            "eval/global_recall/recall02_04_contract_v1.json",
            "eval/memory_e2e/freeze.py",
            "eval/memory_e2e/select_e2e_engineering_report_v1.json",
            "eval/memory_e2e/select_e2e_profile_v1.json",
            "eval/memory_e2e/select_real_asset_status_v1.json",
            "runtime/__init__.py",
            "runtime/_context.py",
            "runtime/_memory_aware_model.py",
            "runtime/_memory_selection.py",
            "runtime/_prompt.py",
            "runtime/_reranker.py",
            "runtime/_reranker_assets.py",
            "runtime/_selected_memory.py",
            "设计文档/AI设计/当前权威设计/SELECT接口骨架与E2E-01-05长期记忆在线闭环施工文档.md",
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
        "contract_id": "select-e2e-long-term-memory-online-v1",
        "schema_version": 1,
        "status": "frozen_with_real_model_gates_pending",
        "frozen_at": frozen_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "upstream": {
            "contract_id": "recall02-04-global-exact-top32-v1",
            "manifest": "eval/global_recall/recall02_04_contract_v1.json",
        },
        "scope": {
            "includes": "SELECT interface, local package verification, strict reranker output validation, 256-token SelectedMemoryFrame, final prompt injection, single REPLY composition, online SQLite write-read, stale exclusion, restart recovery and failure degradation",
            "passed": [
                "SELECT-01 local package verifier and immutable interface contract",
                "SELECT-05 protocol adapter boundary and fail-closed paths",
                "E2E-01 SelectedMemoryFrame assembly",
                "E2E-02 final prompt injection and exactly one REPLY call",
                "E2E-03 online write-read and stale correction exclusion",
                "E2E-04 persisted generation restart recovery",
                "E2E-05 automated failure and regression coverage",
            ],
            "pending_external_assets": [
                "delivered Qwen3-Reranker-0.6B package and real inference adapter",
                "SELECT-04 BF16 FP16 8bit 4bit comparison on the frozen set",
                "real semantic selection and no-memory rejection quality",
                "real selector_total_ms P95 below 300ms",
                "combined GPU peak below 5120 MiB and one-hour stability",
                "real Qwen3.5 CHAT-01 and long-dialogue regression",
            ],
            "excludes": [
                "model download or training",
                "FakeMemoryReranker quality or performance claims",
                "SELF-01 through SELF-05",
            ],
            "next_checkpoint": "real SELECT and E2E asset gates before SELF-01",
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
        verify_recall02_04_manifest()
    if manifest_path.exists():
        raise FreezeVerificationError(
            "SELECT/E2E contract manifest already exists and is immutable"
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
        verify_recall02_04_manifest()
    if not manifest_path.is_file():
        raise FreezeVerificationError("SELECT/E2E contract manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contract_id") != "select-e2e-long-term-memory-online-v1":
        raise FreezeVerificationError("unexpected SELECT/E2E contract_id")
    if manifest.get("status") != "frozen_with_real_model_gates_pending":
        raise FreezeVerificationError("unexpected SELECT/E2E contract status")
    freeze = manifest.get("freeze")
    if not isinstance(freeze, dict) or freeze.get("immutable") is not True:
        raise FreezeVerificationError("SELECT/E2E manifest is not immutable")
    if freeze.get("manifest_sha256") != canonical_manifest_sha256(manifest):
        raise FreezeVerificationError("SELECT/E2E manifest self SHA256 mismatch")
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
    parser = argparse.ArgumentParser(description="Freeze or verify SELECT/E2E assets")
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
