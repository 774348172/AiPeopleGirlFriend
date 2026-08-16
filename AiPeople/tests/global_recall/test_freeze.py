from __future__ import annotations

import json

import pytest

from eval.global_recall.freeze import (
    FROZEN_ASSET_PATHS,
    FreezeVerificationError,
    build_manifest,
    verify_manifest,
    write_manifest,
)


def test_recall02_04_frozen_chain_verifies() -> None:
    manifest = verify_manifest()
    assert manifest["scope"]["next_checkpoint"] == "SELECT-01"
    assert manifest["upstream"]["contract_id"] == (
        "recall01-selector-query-representation-v1"
    )
    assert "real local BGE global_recall_at_32" in manifest["scope"][
        "pending_external_assets"
    ]


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


def test_frozen_asset_paths_are_sorted_and_unique() -> None:
    assert FROZEN_ASSET_PATHS == tuple(sorted(set(FROZEN_ASSET_PATHS)))

