from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .package_loader import CharacterPackage


class CanonLoadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CanonFact:
    key: str
    value: str
    visibility: str
    disclosure_policy: str


@dataclass(frozen=True, slots=True)
class SharedWorldCanon:
    world_id: str
    world_name: str
    root: Path
    facts: Mapping[str, CanonFact]
    source_paths: Mapping[str, Path]


@dataclass(frozen=True, slots=True)
class ProtagonistCanon:
    protagonist_id: str
    root: Path
    facts: Mapping[str, CanonFact]
    source_paths: Mapping[str, Path]


@dataclass(frozen=True, slots=True)
class InitialHeroineRuntimeSeed:
    character_id: str
    living_mind: Mapping[str, str]
    relationship: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CharacterCanon:
    character_id: str
    display_name: str
    package: CharacterPackage
    facts: Mapping[str, CanonFact]
    style_rules: Mapping[str, Any]
    initial_runtime: InitialHeroineRuntimeSeed


class WorldCanonLoader:
    _REQUIRED_FILES = {
        "readme": "README.md",
        "prose": "世界设定定稿.md",
        "canon": "canon.json",
        "timeline": "timeline.yaml",
    }

    def load(self, root: Path, expected_world_id: str) -> SharedWorldCanon:
        resolved, paths = _require_canon_directory(
            root,
            self._REQUIRED_FILES,
            "world canon",
        )
        raw = _load_json_object(paths["canon"], "world canon")
        facts = _load_facts(raw, "world canon")
        world_id = _fact_value(facts, "world_id", "world canon")
        if world_id != expected_world_id:
            raise CanonLoadError(
                f"world canon world_id must be {expected_world_id}: {world_id}"
            )
        world_name = _fact_value(facts, "world_name", "world canon")
        return SharedWorldCanon(
            world_id=world_id,
            world_name=world_name,
            root=resolved,
            facts=facts,
            source_paths=MappingProxyType(paths),
        )


class ProtagonistCanonLoader:
    _REQUIRED_FILES = {
        "readme": "README.md",
        "prose": "角色设定定稿.md",
        "bible": "bible.yaml",
        "canon": "canon.json",
        "timeline": "timeline.yaml",
    }

    def load(self, root: Path, expected_protagonist_id: str) -> ProtagonistCanon:
        resolved, paths = _require_canon_directory(
            root,
            self._REQUIRED_FILES,
            "protagonist canon",
        )
        raw = _load_json_object(paths["canon"], "protagonist canon")
        facts = _load_facts(raw, "protagonist canon")
        protagonist_id = _fact_value(facts, "character_id", "protagonist canon")
        if protagonist_id != expected_protagonist_id:
            raise CanonLoadError(
                "protagonist canon character_id must be "
                f"{expected_protagonist_id}: {protagonist_id}"
            )
        return ProtagonistCanon(
            protagonist_id=protagonist_id,
            root=resolved,
            facts=facts,
            source_paths=MappingProxyType(paths),
        )


class CharacterCanonLoader:
    def load(self, package: CharacterPackage) -> CharacterCanon:
        raw = _load_json_object(package.canon_paths["canon"], "character canon")
        facts = _load_facts(raw, "character canon")
        character_id = _fact_value(facts, "character_id", "character canon")
        if character_id != package.manifest.character_id:
            raise CanonLoadError(
                "character canon character_id must match package manifest: "
                f"{character_id} != {package.manifest.character_id}"
            )
        style_rules = raw.get("style_rules", {})
        if not isinstance(style_rules, dict):
            raise CanonLoadError("character canon style_rules must be an object")
        initial_runtime = _load_initial_runtime(
            package.canon_paths["bible"],
            package.manifest.character_id,
            package.manifest.protagonist_id,
        )
        return CharacterCanon(
            character_id=character_id,
            display_name=package.manifest.display_name,
            package=package,
            facts=facts,
            style_rules=MappingProxyType(dict(style_rules)),
            initial_runtime=initial_runtime,
        )


def _require_canon_directory(
    root: Path,
    required_files: Mapping[str, str],
    label: str,
) -> tuple[Path, dict[str, Path]]:
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise CanonLoadError(f"{label} directory is missing: {resolved}")
    paths: dict[str, Path] = {}
    for key, relative in required_files.items():
        path = (resolved / relative).resolve()
        if resolved not in path.parents or not path.is_file():
            raise CanonLoadError(f"{label} file is missing: {relative}")
        _read_utf8(path, label)
        paths[key] = path
    return resolved, paths


def _read_utf8(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CanonLoadError(f"{label} file is not readable UTF-8: {path}") from error


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_read_utf8(path, label))
    except json.JSONDecodeError as error:
        raise CanonLoadError(f"{label} canon.json is invalid JSON") from error
    if not isinstance(value, dict):
        raise CanonLoadError(f"{label} canon.json must be an object")
    return value


def _load_facts(raw: Mapping[str, Any], label: str) -> Mapping[str, CanonFact]:
    raw_facts = raw.get("facts")
    if not isinstance(raw_facts, dict) or not raw_facts:
        raise CanonLoadError(f"{label} facts must be a non-empty object")
    facts: dict[str, CanonFact] = {}
    for key, raw_fact in raw_facts.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(raw_fact, dict):
            raise CanonLoadError(f"{label} contains an invalid fact")
        value = raw_fact.get("value")
        visibility = raw_fact.get("visibility")
        disclosure_policy = raw_fact.get("disclosure_policy")
        if not isinstance(value, str) or not value.strip():
            raise CanonLoadError(f"{label} fact {key} has no text value")
        if not isinstance(visibility, str) or not visibility.strip():
            raise CanonLoadError(f"{label} fact {key} has no visibility")
        if not isinstance(disclosure_policy, str) or not disclosure_policy.strip():
            raise CanonLoadError(f"{label} fact {key} has no disclosure_policy")
        facts[key] = CanonFact(
            key=key,
            value=value.strip(),
            visibility=visibility.strip(),
            disclosure_policy=disclosure_policy.strip(),
        )
    return MappingProxyType(facts)


def _fact_value(facts: Mapping[str, CanonFact], key: str, label: str) -> str:
    try:
        return facts[key].value
    except KeyError as error:
        raise CanonLoadError(f"{label} is missing required fact: {key}") from error


def _load_initial_runtime(
    bible_path: Path,
    expected_character_id: str,
    expected_protagonist_id: str,
) -> InitialHeroineRuntimeSeed:
    text = _read_utf8(bible_path, "character bible")
    section = _extract_indented_section(text, "initial_runtime")
    character_id = _section_scalar(section, 2, "character_id")
    if character_id != expected_character_id:
        raise CanonLoadError(
            "initial_runtime character_id must match package manifest: "
            f"{character_id} != {expected_character_id}"
        )
    living_mind = _section_mapping(section, "living_mind", 2)
    relationship = _section_mapping(section, "relationship", 2)
    if relationship.get("protagonist_id") != expected_protagonist_id:
        raise CanonLoadError(
            "initial_runtime relationship protagonist_id must match package manifest"
        )
    required_living = {
        "form",
        "body",
        "emotion",
        "attention",
        "current_activity",
        "immediate_intent",
    }
    required_relationship = {
        "protagonist_id",
        "stage",
        "trust",
        "unresolved_tension",
    }
    if not required_living <= set(living_mind):
        missing = sorted(required_living - set(living_mind))
        raise CanonLoadError(f"initial_runtime living_mind is missing: {missing}")
    if not required_relationship <= set(relationship):
        missing = sorted(required_relationship - set(relationship))
        raise CanonLoadError(f"initial_runtime relationship is missing: {missing}")
    return InitialHeroineRuntimeSeed(
        character_id=character_id,
        living_mind=MappingProxyType(living_mind),
        relationship=MappingProxyType(relationship),
    )


def _extract_indented_section(text: str, section_name: str) -> tuple[str, ...]:
    lines = text.splitlines()
    marker = f"{section_name}:"
    start = next(
        (index for index, line in enumerate(lines) if line.rstrip() == marker),
        None,
    )
    if start is None:
        raise CanonLoadError(f"character bible is missing {section_name}")
    collected: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith(" "):
            break
        collected.append(line)
    return tuple(collected)


def _section_scalar(lines: tuple[str, ...], indent: int, key: str) -> str:
    prefix = " " * indent + f"{key}:"
    for line in lines:
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            if value:
                return value
    raise CanonLoadError(f"initial_runtime is missing {key}")


def _section_mapping(
    lines: tuple[str, ...],
    section_name: str,
    section_indent: int,
) -> dict[str, str]:
    marker = " " * section_indent + f"{section_name}:"
    start = next(
        (index for index, line in enumerate(lines) if line.rstrip() == marker),
        None,
    )
    if start is None:
        raise CanonLoadError(f"initial_runtime is missing {section_name}")
    item_indent = section_indent + 2
    result: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        indentation = len(line) - len(line.lstrip(" "))
        if indentation <= section_indent:
            break
        if indentation != item_indent or ":" not in line:
            continue
        key, value = line.strip().split(":", 1)
        if value.strip():
            result[key] = value.strip()
    return result
