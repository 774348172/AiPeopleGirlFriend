from __future__ import annotations

import json

import pytest

from eval.memory_e2e.freeze import (
    FROZEN_ASSET_PATHS,
    FreezeVerificationError,
    build_manifest,
    verify_manifest,
    write_manifest,
)


def test_select_e2e_frozen_chain_verifies() -> None:
    manifest = verify_manifest()
    assert manifest["status"] == "frozen_with_real_model_gates_pending"
    assert manifest["upstream"]["contract_id"] == (
        "recall02-04-global-exact-top32-v1"
    )
    assert "real selector_total_ms P95 below 300ms" in manifest["scope"][
        "pending_external_assets"
    ]
    assert "SELF-01 through SELF-05" in manifest["scope"]["excludes"]


def test_freeze_refuses_to_overwrite_existing_manifest(tmp_path) -> None:
    path = tmp_path / "contract.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(FreezeVerificationError, match="immutable"):
        write_manifest(manifest_path=path, verify_upstream=False)


def test_manifest_detects_asset_drift(tmp_path) -> None:
    asset = tmp_path / "asset.txt"
    asset.write_text("original", encoding="utf-8")
    manifest = build_manifest(
        root=tmp_path,
        asset_paths=("asset.txt",),
        frozen_at="2026-08-08T00:00:00Z",
    )
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    asset.write_text("changed", encoding="utf-8")
    with pytest.raises(FreezeVerificationError, match="drifted"):
        verify_manifest(
            manifest_path=path,
            root=tmp_path,
            expected_asset_paths=("asset.txt",),
            verify_upstream=False,
        )


def test_manifest_detects_self_hash_drift(tmp_path) -> None:
    asset = tmp_path / "asset.txt"
    asset.write_text("original", encoding="utf-8")
    manifest = build_manifest(
        root=tmp_path,
        asset_paths=("asset.txt",),
        frozen_at="2026-08-08T00:00:00Z",
    )
    manifest["scope"]["next_checkpoint"] = "silently changed"
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(FreezeVerificationError, match="self SHA256 mismatch"):
        verify_manifest(
            manifest_path=path,
            root=tmp_path,
            expected_asset_paths=("asset.txt",),
            verify_upstream=False,
        )


def test_frozen_asset_paths_are_sorted_and_unique() -> None:
    assert FROZEN_ASSET_PATHS == tuple(sorted(set(FROZEN_ASSET_PATHS)))
