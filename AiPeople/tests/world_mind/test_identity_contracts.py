from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.world_mind import (
    CharacterPackageError,
    RuntimeSessionIdentity,
    SaveIdentity,
    load_character_package,
)


ROOT = Path(__file__).resolve().parents[2]


def test_save_identity_requires_active_character_to_be_installed() -> None:
    with pytest.raises(ValueError, match="active_character_id must be installed"):
        SaveIdentity(
            save_id="save_001",
            world_id="songjiangfu",
            protagonist_id="protagonist",
            active_character_id="baiweixi",
            installed_character_ids=("future_heroine",),
        )


def test_runtime_session_identity_is_derived_from_save() -> None:
    save = SaveIdentity(
        save_id="save_001",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        installed_character_ids=("baiweixi",),
        state_version=7,
    )

    session = RuntimeSessionIdentity.from_save(save, "conversation-a")

    assert session.save_id == "save_001"
    assert session.world_id == "songjiangfu"
    assert session.protagonist_id == "protagonist"
    assert session.active_character_id == "baiweixi"
    assert session.conversation_id == "conversation-a"


def test_current_baiweixi_package_loads_from_manifest() -> None:
    package = load_character_package(ROOT / "人物设定" / "白未晞")

    assert package.manifest.character_id == "baiweixi"
    assert package.manifest.world_id == "songjiangfu"
    assert package.manifest.protagonist_id == "protagonist"
    assert package.manifest.package_version == "1.0.0"
    assert tuple(package.canon_paths) == (
        "readme",
        "prose",
        "bible",
        "canon",
        "timeline",
    )


def test_character_package_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    for name in ("README.md", "角色设定定稿.md", "bible.yaml", "canon.json"):
        (package_dir / name).write_text("placeholder", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "character_id": "test_heroine",
        "display_name": "测试角色",
        "role": "heroine",
        "package_version": "1.0.0",
        "world_id": "songjiangfu",
        "protagonist_id": "protagonist",
        "canon_files": {
            "readme": "README.md",
            "prose": "角色设定定稿.md",
            "bible": "bible.yaml",
            "canon": "canon.json",
            "timeline": "../outside.md"
        },
        "initial_runtime_pointer": "bible.yaml#initial_runtime"
    }
    (package_dir / "package.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(CharacterPackageError, match="escapes the package directory"):
        load_character_package(package_dir)
