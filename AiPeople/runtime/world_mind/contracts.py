from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_CANON_FILE_KEYS = ("readme", "prose", "bible", "canon", "timeline")


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must match {_IDENTIFIER_PATTERN.pattern}")


@dataclass(frozen=True, slots=True)
class SaveIdentity:
    save_id: str
    world_id: str
    protagonist_id: str
    active_character_id: str
    installed_character_ids: tuple[str, ...]
    schema_version: int = 1
    state_version: int = 1

    def __post_init__(self) -> None:
        _require_identifier(self.save_id, "save_id")
        _require_identifier(self.world_id, "world_id")
        _require_identifier(self.protagonist_id, "protagonist_id")
        _require_identifier(self.active_character_id, "active_character_id")
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        if self.state_version <= 0:
            raise ValueError("state_version must be positive")
        if not self.installed_character_ids:
            raise ValueError("installed_character_ids cannot be empty")
        if len(set(self.installed_character_ids)) != len(self.installed_character_ids):
            raise ValueError("installed_character_ids cannot contain duplicates")
        for character_id in self.installed_character_ids:
            _require_identifier(character_id, "installed_character_ids item")
        if self.active_character_id not in self.installed_character_ids:
            raise ValueError("active_character_id must be installed")


@dataclass(frozen=True, slots=True)
class RuntimeSessionIdentity:
    save_id: str
    world_id: str
    protagonist_id: str
    active_character_id: str
    conversation_id: str

    def __post_init__(self) -> None:
        _require_identifier(self.save_id, "save_id")
        _require_identifier(self.world_id, "world_id")
        _require_identifier(self.protagonist_id, "protagonist_id")
        _require_identifier(self.active_character_id, "active_character_id")
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id cannot be empty")

    @classmethod
    def from_save(cls, save: SaveIdentity, conversation_id: str) -> RuntimeSessionIdentity:
        return cls(
            save_id=save.save_id,
            world_id=save.world_id,
            protagonist_id=save.protagonist_id,
            active_character_id=save.active_character_id,
            conversation_id=conversation_id,
        )


@dataclass(frozen=True, slots=True)
class CharacterPackageManifest:
    schema_version: int
    character_id: str
    display_name: str
    role: str
    package_version: str
    world_id: str
    protagonist_id: str
    canon_files: Mapping[str, str]
    initial_runtime_pointer: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        _require_identifier(self.character_id, "character_id")
        _require_identifier(self.world_id, "world_id")
        _require_identifier(self.protagonist_id, "protagonist_id")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("display_name cannot be empty")
        if self.role != "heroine":
            raise ValueError("role must be heroine")
        if not isinstance(self.package_version, str) or _VERSION_PATTERN.fullmatch(
            self.package_version
        ) is None:
            raise ValueError("package_version must use major.minor.patch")
        if tuple(self.canon_files.keys()) != _CANON_FILE_KEYS:
            raise ValueError(f"canon_files keys must be {_CANON_FILE_KEYS}")
        normalized = {}
        for key, value in self.canon_files.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"canon_files.{key} cannot be empty")
            normalized[key] = value
        object.__setattr__(self, "canon_files", MappingProxyType(normalized))
        if self.initial_runtime_pointer != "bible.yaml#initial_runtime":
            raise ValueError(
                "initial_runtime_pointer must be bible.yaml#initial_runtime"
            )
