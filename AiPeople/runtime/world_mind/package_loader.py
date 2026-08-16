from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .contracts import CharacterPackageManifest


_MANIFEST_FIELDS = (
    "schema_version",
    "character_id",
    "display_name",
    "role",
    "package_version",
    "world_id",
    "protagonist_id",
    "canon_files",
    "initial_runtime_pointer",
)


class CharacterPackageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CharacterPackage:
    package_dir: Path
    manifest_path: Path
    manifest: CharacterPackageManifest
    canon_paths: Mapping[str, Path]


def load_character_package(package_dir: Path) -> CharacterPackage:
    root = Path(package_dir).resolve()
    manifest_path = root / "package.json"
    if not manifest_path.is_file():
        raise CharacterPackageError(f"character package manifest is missing: {manifest_path}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CharacterPackageError("character package manifest is not valid UTF-8 JSON") from error
    if not isinstance(raw, dict):
        raise CharacterPackageError("character package manifest must be an object")
    if tuple(raw.keys()) != _MANIFEST_FIELDS:
        raise CharacterPackageError(f"character package fields must be {_MANIFEST_FIELDS}")
    try:
        manifest = CharacterPackageManifest(**raw)
    except (TypeError, ValueError) as error:
        raise CharacterPackageError(str(error)) from error

    canon_paths = {}
    for key, relative in manifest.canon_files.items():
        path = (root / relative).resolve()
        if root not in path.parents:
            raise CharacterPackageError(f"canon_files.{key} escapes the package directory")
        if not path.is_file():
            raise CharacterPackageError(f"canon file is missing: {relative}")
        canon_paths[key] = path
    return CharacterPackage(
        package_dir=root,
        manifest_path=manifest_path,
        manifest=manifest,
        canon_paths=MappingProxyType(canon_paths),
    )
