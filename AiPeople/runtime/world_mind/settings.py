from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .contracts import RuntimeSessionIdentity, _require_identifier
from .package_loader import CharacterPackage, CharacterPackageError, load_character_package


class WorldMindRuntimeConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorldMindRuntimeConfig:
    expected_world_id: str
    expected_protagonist_id: str
    world_canon_dir: Path
    protagonist_canon_dir: Path
    character_package_dirs: Mapping[str, Path]
    p0_allowed_character_ids: tuple[str, ...]
    foreground_protocol: str = "mind_patch_v2"
    _character_packages: Mapping[str, CharacterPackage] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _require_identifier(self.expected_world_id, "expected_world_id")
        _require_identifier(self.expected_protagonist_id, "expected_protagonist_id")
        if self.foreground_protocol not in {
            "mind_patch_v2",
            "legacy_v1",
            "short_semantic_v1",
        }:
            raise WorldMindRuntimeConfigError(
                "foreground_protocol must be mind_patch_v2, legacy_v1, or short_semantic_v1"
            )

        world_canon_dir = self._require_directory(
            self.world_canon_dir,
            "world_canon_dir",
        )
        protagonist_canon_dir = self._require_directory(
            self.protagonist_canon_dir,
            "protagonist_canon_dir",
        )
        object.__setattr__(self, "world_canon_dir", world_canon_dir)
        object.__setattr__(self, "protagonist_canon_dir", protagonist_canon_dir)

        if not isinstance(self.character_package_dirs, Mapping):
            raise TypeError("character_package_dirs must be a mapping")
        if not self.character_package_dirs:
            raise WorldMindRuntimeConfigError(
                "character_package_dirs cannot be empty"
            )

        package_dirs: dict[str, Path] = {}
        character_packages: dict[str, CharacterPackage] = {}
        for character_id, package_dir in self.character_package_dirs.items():
            _require_identifier(character_id, "character_package_dirs key")
            resolved_package_dir = self._require_directory(
                package_dir,
                f"character package directory for {character_id}",
            )
            try:
                package = load_character_package(resolved_package_dir)
            except CharacterPackageError as error:
                raise WorldMindRuntimeConfigError(
                    f"invalid character package {character_id}: {error}"
                ) from error
            if package.manifest.character_id != character_id:
                raise WorldMindRuntimeConfigError(
                    "character package registry key must match manifest character_id: "
                    f"{character_id} != {package.manifest.character_id}"
                )
            if package.manifest.world_id != self.expected_world_id:
                raise WorldMindRuntimeConfigError(
                    f"character package {character_id} world_id must be "
                    f"{self.expected_world_id}"
                )
            if package.manifest.protagonist_id != self.expected_protagonist_id:
                raise WorldMindRuntimeConfigError(
                    f"character package {character_id} protagonist_id must be "
                    f"{self.expected_protagonist_id}"
                )
            package_dirs[character_id] = resolved_package_dir
            character_packages[character_id] = package

        allowed_character_ids = self._normalize_allowed_character_ids(
            self.p0_allowed_character_ids
        )
        for character_id in allowed_character_ids:
            if character_id not in character_packages:
                raise WorldMindRuntimeConfigError(
                    f"P0 allowed character is not registered: {character_id}"
                )

        object.__setattr__(
            self,
            "character_package_dirs",
            MappingProxyType(package_dirs),
        )
        object.__setattr__(
            self,
            "p0_allowed_character_ids",
            allowed_character_ids,
        )
        object.__setattr__(
            self,
            "_character_packages",
            MappingProxyType(character_packages),
        )

    def character_package(self, character_id: str) -> CharacterPackage:
        _require_identifier(character_id, "character_id")
        try:
            return self._character_packages[character_id]
        except KeyError as error:
            raise WorldMindRuntimeConfigError(
                f"character is not registered: {character_id}"
            ) from error

    def validate_session(self, session: RuntimeSessionIdentity) -> None:
        if not isinstance(session, RuntimeSessionIdentity):
            raise TypeError("session must be a RuntimeSessionIdentity")
        if session.world_id != self.expected_world_id:
            raise WorldMindRuntimeConfigError(
                f"session world_id must be {self.expected_world_id}"
            )
        if session.protagonist_id != self.expected_protagonist_id:
            raise WorldMindRuntimeConfigError(
                f"session protagonist_id must be {self.expected_protagonist_id}"
            )
        if session.active_character_id not in self._character_packages:
            raise WorldMindRuntimeConfigError(
                "session active character is not registered: "
                f"{session.active_character_id}"
            )
        if session.active_character_id not in self.p0_allowed_character_ids:
            raise WorldMindRuntimeConfigError(
                "session active character is not allowed in P0: "
                f"{session.active_character_id}"
            )

    @staticmethod
    def _require_directory(value: Path, name: str) -> Path:
        try:
            resolved = Path(value).resolve()
        except TypeError as error:
            raise TypeError(f"{name} must be path-like") from error
        if not resolved.is_dir():
            raise WorldMindRuntimeConfigError(
                f"{name} does not exist or is not a directory: {resolved}"
            )
        return resolved

    @staticmethod
    def _normalize_allowed_character_ids(
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if isinstance(values, (str, bytes)):
            raise TypeError("p0_allowed_character_ids must be a sequence of IDs")
        try:
            normalized = tuple(values)
        except TypeError as error:
            raise TypeError(
                "p0_allowed_character_ids must be a sequence of IDs"
            ) from error
        if not normalized:
            raise WorldMindRuntimeConfigError(
                "p0_allowed_character_ids cannot be empty"
            )
        if len(set(normalized)) != len(normalized):
            raise WorldMindRuntimeConfigError(
                "p0_allowed_character_ids cannot contain duplicates"
            )
        for character_id in normalized:
            _require_identifier(character_id, "p0_allowed_character_ids item")
        return normalized
