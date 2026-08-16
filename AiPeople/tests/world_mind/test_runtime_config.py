from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.world_mind import (
    RuntimeSessionIdentity,
    WorldMindRuntimeConfig,
    WorldMindRuntimeConfigError,
)


ROOT = Path(__file__).resolve().parents[2]
WORLD_CANON_DIR = ROOT / "世界设定" / "松江府"
PROTAGONIST_CANON_DIR = ROOT / "人物设定" / "主角"
BAIWEIXI_PACKAGE_DIR = ROOT / "人物设定" / "白未晞"


def _config(**overrides: object) -> WorldMindRuntimeConfig:
    values: dict[str, object] = {
        "expected_world_id": "songjiangfu",
        "expected_protagonist_id": "protagonist",
        "world_canon_dir": WORLD_CANON_DIR,
        "protagonist_canon_dir": PROTAGONIST_CANON_DIR,
        "character_package_dirs": {"baiweixi": BAIWEIXI_PACKAGE_DIR},
        "p0_allowed_character_ids": ("baiweixi",),
    }
    values.update(overrides)
    return WorldMindRuntimeConfig(**values)  # type: ignore[arg-type]


def _session(
    *,
    world_id: str = "songjiangfu",
    protagonist_id: str = "protagonist",
    active_character_id: str = "baiweixi",
) -> RuntimeSessionIdentity:
    return RuntimeSessionIdentity(
        save_id="save_001",
        world_id=world_id,
        protagonist_id=protagonist_id,
        active_character_id=active_character_id,
        conversation_id="conversation-a",
    )


def _write_character_package(
    root: Path,
    character_id: str,
    *,
    world_id: str = "songjiangfu",
    protagonist_id: str = "protagonist",
) -> Path:
    package_dir = root / character_id
    package_dir.mkdir()
    canon_files = {
        "readme": "README.md",
        "prose": "角色设定定稿.md",
        "bible": "bible.yaml",
        "canon": "canon.json",
        "timeline": "timeline.yaml",
    }
    for relative_path in canon_files.values():
        (package_dir / relative_path).write_text("placeholder", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "character_id": character_id,
        "display_name": "测试角色",
        "role": "heroine",
        "package_version": "1.0.0",
        "world_id": world_id,
        "protagonist_id": protagonist_id,
        "canon_files": canon_files,
        "initial_runtime_pointer": "bible.yaml#initial_runtime",
    }
    (package_dir / "package.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return package_dir


def test_current_v6_runtime_config_loads() -> None:
    config = _config()

    package = config.character_package("baiweixi")

    assert package.manifest.character_id == "baiweixi"
    assert config.p0_allowed_character_ids == ("baiweixi",)


def test_runtime_config_resolves_all_paths() -> None:
    config = _config(
        world_canon_dir=WORLD_CANON_DIR / ".." / "松江府",
        protagonist_canon_dir=PROTAGONIST_CANON_DIR / ".." / "主角",
        character_package_dirs={
            "baiweixi": BAIWEIXI_PACKAGE_DIR / ".." / "白未晞"
        },
    )

    assert config.world_canon_dir == WORLD_CANON_DIR.resolve()
    assert config.protagonist_canon_dir == PROTAGONIST_CANON_DIR.resolve()
    assert config.character_package_dirs["baiweixi"] == BAIWEIXI_PACKAGE_DIR.resolve()


def test_character_registry_is_immutable() -> None:
    config = _config()

    with pytest.raises(TypeError):
        config.character_package_dirs["future_heroine"] = BAIWEIXI_PACKAGE_DIR  # type: ignore[index]


def test_registry_key_must_match_manifest_character_id(tmp_path: Path) -> None:
    package_dir = _write_character_package(tmp_path, "test_heroine")

    with pytest.raises(
        WorldMindRuntimeConfigError,
        match="registry key must match manifest character_id",
    ):
        _config(
            character_package_dirs={"other_heroine": package_dir},
            p0_allowed_character_ids=("other_heroine",),
        )


def test_character_package_world_must_match_runtime(tmp_path: Path) -> None:
    package_dir = _write_character_package(
        tmp_path,
        "test_heroine",
        world_id="other_world",
    )

    with pytest.raises(WorldMindRuntimeConfigError, match="world_id must be songjiangfu"):
        _config(
            character_package_dirs={"test_heroine": package_dir},
            p0_allowed_character_ids=("test_heroine",),
        )


def test_character_package_protagonist_must_match_runtime(tmp_path: Path) -> None:
    package_dir = _write_character_package(
        tmp_path,
        "test_heroine",
        protagonist_id="other_protagonist",
    )

    with pytest.raises(
        WorldMindRuntimeConfigError,
        match="protagonist_id must be protagonist",
    ):
        _config(
            character_package_dirs={"test_heroine": package_dir},
            p0_allowed_character_ids=("test_heroine",),
        )


def test_p0_allowed_character_must_be_registered() -> None:
    with pytest.raises(
        WorldMindRuntimeConfigError,
        match="P0 allowed character is not registered: future_heroine",
    ):
        _config(p0_allowed_character_ids=("future_heroine",))


def test_p0_allowed_character_ids_must_be_unique() -> None:
    with pytest.raises(
        WorldMindRuntimeConfigError,
        match="p0_allowed_character_ids cannot contain duplicates",
    ):
        _config(p0_allowed_character_ids=("baiweixi", "baiweixi"))


@pytest.mark.parametrize(
    ("override", "error"),
    (
        ({"world_canon_dir": Path("missing-world")}, "world_canon_dir"),
        (
            {"protagonist_canon_dir": Path("missing-protagonist")},
            "protagonist_canon_dir",
        ),
        (
            {"character_package_dirs": {"baiweixi": Path("missing-package")}},
            "character package directory for baiweixi",
        ),
    ),
)
def test_runtime_config_requires_all_directories(
    override: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(WorldMindRuntimeConfigError, match=error):
        _config(**override)


def test_valid_session_passes_runtime_config_validation() -> None:
    config = _config()

    assert config.validate_session(_session()) is None


def test_session_world_must_match_runtime_config() -> None:
    config = _config()

    with pytest.raises(WorldMindRuntimeConfigError, match="session world_id"):
        config.validate_session(_session(world_id="other_world"))


def test_session_protagonist_must_match_runtime_config() -> None:
    config = _config()

    with pytest.raises(WorldMindRuntimeConfigError, match="session protagonist_id"):
        config.validate_session(_session(protagonist_id="other_protagonist"))


def test_session_character_must_be_registered() -> None:
    config = _config()

    with pytest.raises(
        WorldMindRuntimeConfigError,
        match="session active character is not registered",
    ):
        config.validate_session(_session(active_character_id="future_heroine"))


def test_registered_character_must_also_be_allowed_in_p0(tmp_path: Path) -> None:
    future_package_dir = _write_character_package(tmp_path, "future_heroine")
    config = _config(
        character_package_dirs={
            "baiweixi": BAIWEIXI_PACKAGE_DIR,
            "future_heroine": future_package_dir,
        },
    )

    with pytest.raises(
        WorldMindRuntimeConfigError,
        match="session active character is not allowed in P0",
    ):
        config.validate_session(_session(active_character_id="future_heroine"))


def test_runtime_config_has_no_default_save_or_active_character() -> None:
    config = _config()

    assert not hasattr(config, "save_id")
    assert not hasattr(config, "active_character_id")
