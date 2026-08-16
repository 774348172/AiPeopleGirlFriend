from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.memory_propose.freeze import (
    FROZEN_ASSET_PATHS,
    MANIFEST_PATH,
    FreezeVerificationError,
    canonical_manifest_sha256,
    verify_manifest,
    write_manifest,
)


def test_asset_paths_are_unique_and_sorted() -> None:
    assert FROZEN_ASSET_PATHS == tuple(sorted(FROZEN_ASSET_PATHS))
    assert len(FROZEN_ASSET_PATHS) == len(set(FROZEN_ASSET_PATHS))


def test_freeze_refuses_overwrite_and_detects_drift(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "asset.txt").write_text("alpha", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    write_manifest(
        manifest_path=manifest_path,
        root=root,
        asset_paths=("asset.txt",),
        verify_upstream=False,
    )
    verify_manifest(
        manifest_path=manifest_path,
        root=root,
        expected_asset_paths=("asset.txt",),
        verify_upstream=False,
    )
    with pytest.raises(FreezeVerificationError, match="already exists"):
        write_manifest(
            manifest_path=manifest_path,
            root=root,
            asset_paths=("asset.txt",),
            verify_upstream=False,
        )
    (root / "asset.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(FreezeVerificationError, match="drifted"):
        verify_manifest(
            manifest_path=manifest_path,
            root=root,
            expected_asset_paths=("asset.txt",),
            verify_upstream=False,
        )


def test_manifest_self_hash_detects_tampering(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "asset.txt").write_text("alpha", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest = write_manifest(
        manifest_path=manifest_path,
        root=root,
        asset_paths=("asset.txt",),
        verify_upstream=False,
    )
    assert manifest["freeze"]["manifest_sha256"] == canonical_manifest_sha256(manifest)
    manifest["scope"]["next_checkpoint"] = "tampered"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(FreezeVerificationError, match="self SHA256"):
        verify_manifest(
            manifest_path=manifest_path,
            root=root,
            expected_asset_paths=("asset.txt",),
            verify_upstream=False,
        )


def test_repository_mem02_manifest_verifies_with_mem01_upstream() -> None:
    assert MANIFEST_PATH.is_file(), "run python -m eval.memory_propose.freeze --freeze"
    verify_manifest()
