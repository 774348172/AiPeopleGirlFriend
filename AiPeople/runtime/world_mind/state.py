from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from runtime._selected_memory import SelectedMemoryFrame

from .canon_loader import InitialHeroineRuntimeSeed
from .contracts import RuntimeSessionIdentity, _require_identifier


def _require_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _freeze_text_mapping(value: Mapping[str, str], name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        normalized[_require_text(key, f"{name} key")] = _require_text(
            item,
            f"{name}.{key}",
        )
    return MappingProxyType(normalized)


def _freeze_patch_mapping(
    value: Mapping[str, str | None], name: str
) -> Mapping[str, str | None]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    normalized: dict[str, str | None] = {}
    for key, item in value.items():
        normalized_key = _require_text(key, f"{name} key")
        normalized[normalized_key] = (
            None if item is None else _require_text(item, f"{name}.{key}")
        )
    return MappingProxyType(normalized)


def _require_game_time(value: datetime, name: str = "game_time") -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is not None:
        raise ValueError(f"{name} must not use a real-world timezone")
    return value


@dataclass(frozen=True, slots=True)
class ProtagonistLiveState:
    protagonist_id: str
    location_id: str
    location_label: str
    activity: str
    body_state: Mapping[str, str]
    held_item_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.protagonist_id, "protagonist_id")
        _require_identifier(self.location_id, "location_id")
        _require_text(self.location_label, "location_label")
        _require_text(self.activity, "activity")
        object.__setattr__(
            self,
            "body_state",
            _freeze_text_mapping(self.body_state, "body_state"),
        )
        normalized_items = tuple(self.held_item_ids)
        for item_id in normalized_items:
            _require_identifier(item_id, "held_item_ids item")
        if len(set(normalized_items)) != len(normalized_items):
            raise ValueError("held_item_ids cannot contain duplicates")
        object.__setattr__(self, "held_item_ids", normalized_items)


@dataclass(frozen=True, slots=True)
class ActiveSceneState:
    scene_id: str
    location_label: str
    present_character_ids: tuple[str, ...]
    item_states: Mapping[str, str]

    def __post_init__(self) -> None:
        _require_identifier(self.scene_id, "scene_id")
        _require_text(self.location_label, "location_label")
        characters = tuple(self.present_character_ids)
        for character_id in characters:
            _require_identifier(character_id, "present_character_ids item")
        if len(set(characters)) != len(characters):
            raise ValueError("present_character_ids cannot contain duplicates")
        object.__setattr__(self, "present_character_ids", characters)
        object.__setattr__(
            self,
            "item_states",
            _freeze_text_mapping(self.item_states, "item_states"),
        )


@dataclass(frozen=True, slots=True)
class LiveWorldState:
    save_id: str
    world_id: str
    protagonist_id: str
    version: int
    protagonist: ProtagonistLiveState
    scene: ActiveSceneState
    last_changed_game_time: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.save_id, "save_id")
        _require_identifier(self.world_id, "world_id")
        _require_identifier(self.protagonist_id, "protagonist_id")
        if self.version <= 0:
            raise ValueError("version must be positive")
        if self.protagonist.protagonist_id != self.protagonist_id:
            raise ValueError("protagonist live state identity mismatch")
        if self.protagonist.location_label != self.scene.location_label:
            raise ValueError("protagonist and scene location labels must match")
        _require_game_time(self.last_changed_game_time, "last_changed_game_time")


@dataclass(frozen=True, slots=True)
class WorldEventDelta:
    event_id: str
    save_id: str
    from_version: int
    to_version: int
    changed_fields: tuple[str, ...]
    game_time: datetime

    def __post_init__(self) -> None:
        _require_text(self.event_id, "event_id")
        _require_identifier(self.save_id, "save_id")
        if self.from_version < 0 or self.to_version != self.from_version + 1:
            raise ValueError("world event versions must advance by one")
        fields = tuple(_require_text(item, "changed_fields item") for item in self.changed_fields)
        if not fields:
            raise ValueError("changed_fields cannot be empty")
        object.__setattr__(self, "changed_fields", fields)
        _require_game_time(self.game_time)


@dataclass(frozen=True, slots=True)
class ModelReadableWorldState:
    live_world_version: int
    scene_text: str
    protagonist_text: str

    def __post_init__(self) -> None:
        if self.live_world_version <= 0:
            raise ValueError("live_world_version must be positive")
        _require_text(self.scene_text, "scene_text")
        _require_text(self.protagonist_text, "protagonist_text")


@dataclass(frozen=True, slots=True)
class LivingMind:
    form: str
    body: str
    emotion: str
    attention: str
    current_activity: str
    immediate_intent: str

    def __post_init__(self) -> None:
        for name in (
            "form",
            "body",
            "emotion",
            "attention",
            "current_activity",
            "immediate_intent",
        ):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class RelationshipState:
    protagonist_id: str
    stage: str
    trust: str
    unresolved_tension: str

    def __post_init__(self) -> None:
        _require_identifier(self.protagonist_id, "protagonist_id")
        _require_text(self.stage, "stage")
        _require_text(self.trust, "trust")
        _require_text(self.unresolved_tension, "unresolved_tension")


@dataclass(frozen=True, slots=True)
class HeroineRuntime:
    save_id: str
    world_id: str
    character_id: str
    version: int
    living_mind: LivingMind
    relationship: RelationshipState
    evidence_refs: tuple[str, ...]
    motive_state: Mapping[str, str] = field(default_factory=dict)
    knowledge_state: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.save_id, "save_id")
        _require_identifier(self.world_id, "world_id")
        _require_identifier(self.character_id, "character_id")
        if self.version <= 0:
            raise ValueError("version must be positive")
        refs = tuple(_require_text(item, "evidence_refs item") for item in self.evidence_refs)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(
            self,
            "motive_state",
            _freeze_text_mapping(self.motive_state, "motive_state"),
        )
        object.__setattr__(
            self,
            "knowledge_state",
            _freeze_text_mapping(self.knowledge_state, "knowledge_state"),
        )

    @classmethod
    def from_seed(
        cls,
        session: RuntimeSessionIdentity,
        seed: InitialHeroineRuntimeSeed,
    ) -> HeroineRuntime:
        if seed.character_id != session.active_character_id:
            raise ValueError("initial runtime seed character does not match session")
        living = seed.living_mind
        relationship = seed.relationship
        return cls(
            save_id=session.save_id,
            world_id=session.world_id,
            character_id=session.active_character_id,
            version=1,
            living_mind=LivingMind(
                form=living["form"],
                body=living["body"],
                emotion=living["emotion"],
                attention=living["attention"],
                current_activity=living["current_activity"],
                immediate_intent=living["immediate_intent"],
            ),
            relationship=RelationshipState(
                protagonist_id=relationship["protagonist_id"],
                stage=relationship["stage"],
                trust=relationship["trust"],
                unresolved_tension=relationship["unresolved_tension"],
            ),
            evidence_refs=("character_package_initial_runtime",),
            motive_state={},
            knowledge_state={},
        )


@dataclass(frozen=True, slots=True)
class LivingMindPatch:
    form: str | None = None
    body: str | None = None
    emotion: str | None = None
    attention: str | None = None
    current_activity: str | None = None
    immediate_intent: str | None = None


@dataclass(frozen=True, slots=True)
class RelationshipPatch:
    stage: str | None = None
    trust: str | None = None
    unresolved_tension: str | None = None


@dataclass(frozen=True, slots=True)
class HeroineMindPatch:
    living_mind: LivingMindPatch = LivingMindPatch()
    relationship: RelationshipPatch = RelationshipPatch()
    evidence_refs: tuple[str, ...] = ()
    motive_updates: Mapping[str, str | None] = field(default_factory=dict)
    knowledge_updates: Mapping[str, str | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        refs = tuple(_require_text(item, "evidence_refs item") for item in self.evidence_refs)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(
            self,
            "motive_updates",
            _freeze_patch_mapping(self.motive_updates, "motive_updates"),
        )
        object.__setattr__(
            self,
            "knowledge_updates",
            _freeze_patch_mapping(self.knowledge_updates, "knowledge_updates"),
        )

    def apply(self, current: HeroineRuntime) -> HeroineRuntime:
        return self._apply(current, version=current.version + 1)

    def revise(self, candidate: HeroineRuntime) -> HeroineRuntime:
        return self._apply(candidate, version=candidate.version)

    def _apply(self, current: HeroineRuntime, *, version: int) -> HeroineRuntime:
        living_updates = {
            name: value
            for name in (
                "form",
                "body",
                "emotion",
                "attention",
                "current_activity",
                "immediate_intent",
            )
            if (value := getattr(self.living_mind, name)) is not None
        }
        relationship_updates = {
            name: value
            for name in ("stage", "trust", "unresolved_tension")
            if (value := getattr(self.relationship, name)) is not None
        }
        return HeroineRuntime(
            save_id=current.save_id,
            world_id=current.world_id,
            character_id=current.character_id,
            version=version,
            living_mind=replace(current.living_mind, **living_updates),
            relationship=replace(current.relationship, **relationship_updates),
            evidence_refs=current.evidence_refs + self.evidence_refs,
            motive_state=_apply_patch_mapping(
                current.motive_state, self.motive_updates
            ),
            knowledge_state=_apply_patch_mapping(
                current.knowledge_state, self.knowledge_updates
            ),
        )


def _apply_patch_mapping(
    current: Mapping[str, str], patch: Mapping[str, str | None]
) -> dict[str, str]:
    updated = dict(current)
    for key, value in patch.items():
        if value is None:
            updated.pop(key, None)
        else:
            updated[key] = value
    return updated


@dataclass(frozen=True, slots=True)
class TurnWorldSnapshot:
    snapshot_id: str
    request_id: str
    session: RuntimeSessionIdentity
    captured_game_time: datetime
    live_world_version: int
    mind_state_version: int
    world_state: ModelReadableWorldState
    protagonist: ProtagonistLiveState
    scene: ActiveSceneState
    heroine_runtime: HeroineRuntime
    protagonist_utterance: str
    protagonist_utterance_event_id: str | None = None
    selected_memory_frame: SelectedMemoryFrame = SelectedMemoryFrame.empty()
    pending_actions: tuple[dict[str, object], ...] = ()
    game_feedback: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.snapshot_id, "snapshot_id")
        _require_text(self.request_id, "request_id")
        _require_game_time(self.captured_game_time, "captured_game_time")
        if self.live_world_version != self.world_state.live_world_version:
            raise ValueError("snapshot live world versions do not match")
        if self.mind_state_version != self.heroine_runtime.version:
            raise ValueError("snapshot mind state versions do not match")
        if self.session.save_id != self.heroine_runtime.save_id:
            raise ValueError("snapshot heroine save identity mismatch")
        _require_text(self.protagonist_utterance, "protagonist_utterance")
        if self.protagonist_utterance_event_id is not None:
            _require_text(
                self.protagonist_utterance_event_id,
                "protagonist_utterance_event_id",
            )
        if not isinstance(self.selected_memory_frame, SelectedMemoryFrame):
            raise TypeError("selected_memory_frame must be SelectedMemoryFrame")
        if not isinstance(self.pending_actions, tuple) or any(
            not isinstance(item, dict) for item in self.pending_actions
        ):
            raise TypeError("pending_actions must contain dict items")
        if not isinstance(self.game_feedback, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.game_feedback
        ):
            raise TypeError("game_feedback must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class ReconcileWorldSnapshot:
    snapshot_id: str
    job_id: str
    session: RuntimeSessionIdentity
    captured_game_time: datetime
    live_world_version: int
    mind_state_version: int
    world_state: ModelReadableWorldState
    protagonist: ProtagonistLiveState
    scene: ActiveSceneState
    heroine_runtime: HeroineRuntime
    event_deltas: tuple[WorldEventDelta, ...] = ()
    source_event_ids: tuple[str, ...] = ()
    selected_memory_frame: SelectedMemoryFrame = SelectedMemoryFrame.empty()

    def __post_init__(self) -> None:
        _require_text(self.snapshot_id, "snapshot_id")
        _require_text(self.job_id, "job_id")
        _require_game_time(self.captured_game_time, "captured_game_time")
        if self.live_world_version != self.world_state.live_world_version:
            raise ValueError("reconcile snapshot live world versions do not match")
        if self.mind_state_version != self.heroine_runtime.version:
            raise ValueError("reconcile snapshot mind state versions do not match")
        if self.session.save_id != self.heroine_runtime.save_id:
            raise ValueError("reconcile snapshot heroine save identity mismatch")
        if not isinstance(self.event_deltas, tuple) or any(
            not isinstance(item, WorldEventDelta) for item in self.event_deltas
        ):
            raise TypeError("event_deltas must contain WorldEventDelta values")
        if any(item.save_id != self.session.save_id for item in self.event_deltas):
            raise ValueError("reconcile event delta save identity mismatch")
        if self.event_deltas and self.event_deltas[-1].to_version > self.live_world_version:
            raise ValueError("reconcile event delta exceeds live world version")
        source_event_ids = tuple(
            _require_text(item, "source_event_ids item")
            for item in self.source_event_ids
        )
        object.__setattr__(self, "source_event_ids", source_event_ids)
        if not isinstance(self.selected_memory_frame, SelectedMemoryFrame):
            raise TypeError("selected_memory_frame must be SelectedMemoryFrame")


@dataclass(frozen=True, slots=True)
class ReplyFactAssertions:
    protagonist_location_id: str
    protagonist_activity: str
    live_world_version: int
    captured_game_time: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.protagonist_location_id, "protagonist_location_id")
        _require_text(self.protagonist_activity, "protagonist_activity")
        if self.live_world_version <= 0:
            raise ValueError("live_world_version must be positive")
        _require_game_time(self.captured_game_time, "captured_game_time")
