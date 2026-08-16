from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from runtime.adapters.llama_cpp import GenerationOptions

from .model_failures import (
    ClassifiedModelError,
    ModelAttemptObserver,
    classify_model_failure,
    record_attempt,
)
from .model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    FOREGROUND_SEMANTIC_TURN,
    GAME_REPLY,
    MIND_PATCH_V2,
    POST_REPLY_WORLD_MIND_RECONCILE,
    TURN_MIND_ADVANCE,
    WORLD_CONTINUITY_REVIEW,
    ContinuityReviewRequest,
    ContinuityReviewResult,
    ForegroundSemanticTurnRequest,
    ForegroundSemanticTurnResult,
    GameReplyRequest,
    GameReplyResult,
    HeroineDiegeticAction,
    MindAdvanceRequest,
    MindAdvanceResult,
    MindPatchV2Request,
    MindPatchV2Result,
    ReconcileMindRequest,
    ReconcileMindResult,
    ReconcileReviewRequest,
)
from .model_payloads import (
    build_game_reply_messages,
    build_mode_messages,
    continuity_review_payload,
    mind_advance_payload,
    reconcile_review_payload,
)
from .short_protocol import (
    B1_SYSTEM_BOUNDARY,
    M1_SYSTEM_BOUNDARY,
    M2_SYSTEM_BOUNDARY,
    b1_payload,
    compile_b1,
    compile_m1,
    compile_m2,
    m1_payload,
    m2_payload,
)
from .state import (
    HeroineMindPatch,
    LivingMindPatch,
    RelationshipPatch,
    ReplyFactAssertions,
)

SCHEMA_DIR = Path(__file__).parents[1] / "schemas"
GAME_REPLY_MAX_CHARACTERS = 1200


class WorldMindModelError(ClassifiedModelError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "model_contract_invalid",
        mode: str = "WORLD_MIND",
    ) -> None:
        super().__init__(message, code=code, mode=mode)


class WorldMindModelOutputError(WorldMindModelError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "model_schema_invalid",
        mode: str = "WORLD_MIND",
    ) -> None:
        super().__init__(message, code=code, mode=mode)


@dataclass(frozen=True, slots=True)
class WorldMindModelIdentity:
    model_id: str
    revision: str
    artifact_sha256: str
    character_id: str
    world_id: str
    protagonist_id: str

    def __post_init__(self) -> None:
        for name in (
            "model_id",
            "revision",
            "character_id",
            "world_id",
            "protagonist_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if len(self.artifact_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.artifact_sha256
        ):
            raise ValueError("artifact_sha256 must be lowercase SHA256")


@dataclass(frozen=True, slots=True)
class WorldMindModeProfile:
    max_tokens: int
    temperature: float
    top_p: float
    repeat_penalty: float
    timeout_seconds: float
    retries: int

    def __post_init__(self) -> None:
        GenerationOptions(
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            repeat_penalty=self.repeat_penalty,
        )
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.retries not in {0, 1, 2}:
            raise ValueError("retries must be between 0 and 2")

    def generation_options(self) -> GenerationOptions:
        return GenerationOptions(
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            repeat_penalty=self.repeat_penalty,
        )


DEFAULT_MODE_PROFILES = {
    FOREGROUND_SEMANTIC_TURN: WorldMindModeProfile(
        360, 0.55, 0.9, 1.08, 45.0, 1
    ),
    MIND_PATCH_V2: WorldMindModeProfile(180, 0.2, 0.85, 1.05, 30.0, 1),
    TURN_MIND_ADVANCE: WorldMindModeProfile(900, 0.2, 0.85, 1.05, 60.0, 1),
    WORLD_CONTINUITY_REVIEW: WorldMindModeProfile(650, 0.1, 0.8, 1.05, 45.0, 1),
    GAME_REPLY: WorldMindModeProfile(600, 0.75, 0.9, 1.1, 90.0, 1),
    POST_REPLY_WORLD_MIND_RECONCILE: WorldMindModeProfile(
        320, 0.2, 0.85, 1.05, 35.0, 0
    ),
    FIVE_MINUTE_WORLD_MIND_RECONCILE: WorldMindModeProfile(
        320, 0.2, 0.85, 1.05, 35.0, 0
    ),
}


@runtime_checkable
class WorldMindChatBackend(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def complete_chat(
        self,
        *,
        request_id: str,
        messages: Sequence[Mapping[str, str]],
        options: GenerationOptions,
        response_format: Mapping[str, object] | None = None,
    ) -> str: ...


class LlamaCppWorldMindModel:
    def __init__(
        self,
        backend: WorldMindChatBackend,
        *,
        identity: WorldMindModelIdentity,
        mode_profiles: Mapping[str, WorldMindModeProfile] | None = None,
        attempt_observer: ModelAttemptObserver | None = None,
    ) -> None:
        if not isinstance(backend, WorldMindChatBackend):
            raise TypeError("backend must implement WorldMindChatBackend")
        if not isinstance(identity, WorldMindModelIdentity):
            raise TypeError("identity must be WorldMindModelIdentity")
        profiles = dict(DEFAULT_MODE_PROFILES)
        if mode_profiles is not None:
            profiles.update(mode_profiles)
        if set(profiles) != {
            FOREGROUND_SEMANTIC_TURN,
            MIND_PATCH_V2,
            TURN_MIND_ADVANCE,
            WORLD_CONTINUITY_REVIEW,
            GAME_REPLY,
            POST_REPLY_WORLD_MIND_RECONCILE,
            FIVE_MINUTE_WORLD_MIND_RECONCILE,
        }:
            raise ValueError("mode_profiles must define all world-mind modes")
        if any(not isinstance(item, WorldMindModeProfile) for item in profiles.values()):
            raise TypeError("mode profile values must be WorldMindModeProfile")
        self.backend = backend
        self.identity = identity
        self.mode_profiles = profiles
        self.attempt_observer = attempt_observer
        self._schemas = {
            FOREGROUND_SEMANTIC_TURN: _load_schema(
                "foreground_semantic_turn_m1.schema.json"
            ),
            MIND_PATCH_V2: _load_schema("mind_patch_m2.schema.json"),
            TURN_MIND_ADVANCE: _load_schema("turn_mind_advance_v1.schema.json"),
            WORLD_CONTINUITY_REVIEW: _load_schema(
                "world_continuity_review_v1.schema.json"
            ),
            POST_REPLY_WORLD_MIND_RECONCILE: _load_schema(
                "background_mind_patch_b1.schema.json"
            ),
            FIVE_MINUTE_WORLD_MIND_RECONCILE: _load_schema(
                "background_mind_patch_b1.schema.json"
            ),
        }

    async def start(self) -> None:
        await self.backend.start()

    async def close(self) -> None:
        await self.backend.close()

    async def foreground_semantic_turn(
        self,
        request: ForegroundSemanticTurnRequest,
    ) -> ForegroundSemanticTurnResult:
        self._validate_prompt_identity(request.prompt)
        return await self._run_short_parsed(
            FOREGROUND_SEMANTIC_TURN,
            request.snapshot.request_id,
            request.prompt.system_prompt,
            M1_SYSTEM_BOUNDARY,
            m1_payload(request.snapshot),
            lambda raw: compile_m1(raw, request.snapshot),
        )

    async def propose_mind_patch_v2(
        self,
        request: MindPatchV2Request,
    ) -> MindPatchV2Result:
        self._validate_prompt_identity(request.prompt)
        return await self._run_short_parsed(
            MIND_PATCH_V2,
            request.snapshot.request_id,
            request.prompt.mind_patch_system_prompt,
            M2_SYSTEM_BOUNDARY,
            m2_payload(request.snapshot),
            lambda raw: compile_m2(raw, request.snapshot),
        )

    async def advance_mind(self, request: MindAdvanceRequest) -> MindAdvanceResult:
        self._validate_prompt_identity(request.prompt)
        return await self._run_parsed(
            TURN_MIND_ADVANCE,
            request.snapshot.request_id,
            _background_character_prompt(self.identity),
            mind_advance_payload(request),
            lambda raw: _checked_mind_result(raw, request.snapshot.snapshot_id),
        )

    async def review_continuity(
        self, request: ContinuityReviewRequest
    ) -> ContinuityReviewResult:
        self._validate_prompt_identity(request.prompt)
        return await self._run_parsed(
            WORLD_CONTINUITY_REVIEW,
            request.snapshot.request_id,
            _background_character_prompt(self.identity),
            continuity_review_payload(request),
            lambda raw: _checked_continuity_result(
                raw, request.snapshot.snapshot_id
            ),
        )

    async def generate_reply(self, request: GameReplyRequest) -> GameReplyResult:
        self._validate_prompt_identity(request.prompt)
        text = await self._run_text(
            GAME_REPLY,
            request.snapshot.request_id,
            build_game_reply_messages(request),
        )
        return GameReplyResult(
            text=text,
            fact_assertions=request.fact_assertions,
            snapshot_id=request.snapshot.snapshot_id,
            approved_mind_state_version=request.approved_state.version,
        )

    async def reconcile_mind(
        self,
        request: ReconcileMindRequest,
    ) -> ReconcileMindResult:
        self._validate_prompt_identity(request.prompt)
        return await self._run_short_parsed(
            request.mode,
            request.snapshot.job_id,
            _background_character_prompt(self.identity),
            B1_SYSTEM_BOUNDARY,
            b1_payload(request),
            lambda raw: compile_b1(raw, request),
        )

    async def review_reconciliation(
        self,
        request: ReconcileReviewRequest,
    ) -> ContinuityReviewResult:
        self._validate_prompt_identity(request.prompt)
        return await self._run_parsed(
            WORLD_CONTINUITY_REVIEW,
            request.snapshot.job_id,
            _background_character_prompt(self.identity),
            reconcile_review_payload(request),
            lambda raw: _checked_continuity_result(
                raw, request.snapshot.snapshot_id
            ),
        )

    async def _run_parsed(
        self,
        mode: str,
        request_id: str,
        character_prompt: str,
        payload: dict[str, object],
        parser,
    ):
        profile = self.mode_profiles[mode]
        messages = build_mode_messages(mode, character_prompt, payload)
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": f"{mode.lower()}_v1",
                "strict": True,
                "schema": _bind_evidence_refs(
                    _bind_snapshot_contract(self._schemas[mode], payload),
                    payload,
                ),
            },
        }
        last_error: BaseException | None = None
        for attempt in range(profile.retries + 1):
            try:
                async with asyncio.timeout(profile.timeout_seconds):
                    text = await self.backend.complete_chat(
                        request_id=f"{request_id}:{mode}:{attempt}",
                        messages=messages,
                        options=profile.generation_options(),
                        response_format=response_format,
                    )
                result = parser(_decode_json_object(text))
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=mode,
                    attempt=attempt,
                )
                return result
            except asyncio.CancelledError as error:
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=mode,
                    attempt=attempt,
                    error=error,
                )
                raise
            except BaseException as error:
                last_error = error
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=mode,
                    attempt=attempt,
                    error=error,
                )
        assert last_error is not None
        raise WorldMindModelError(
            f"{mode} failed after retries",
            code=classify_model_failure(last_error),
            mode=mode,
        ) from last_error

    async def _run_short_parsed(
        self,
        mode: str,
        request_id: str,
        character_prompt: str,
        boundary: str,
        payload: dict[str, object],
        parser,
    ):
        profile = self.mode_profiles[mode]
        messages = (
            {"role": "system", "content": f"{character_prompt}\n\n{boundary}"},
            {
                "role": "user",
                "content": json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ),
            },
        )
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": {
                    MIND_PATCH_V2: "mind_patch_m2",
                    FOREGROUND_SEMANTIC_TURN: "foreground_semantic_turn_m1",
                    POST_REPLY_WORLD_MIND_RECONCILE: "background_mind_patch_b1",
                    FIVE_MINUTE_WORLD_MIND_RECONCILE: "background_mind_patch_b1",
                }[mode],
                "strict": True,
                "schema": self._schemas[mode],
            },
        }
        last_error: BaseException | None = None
        for attempt in range(profile.retries + 1):
            try:
                async with asyncio.timeout(profile.timeout_seconds):
                    text = await self.backend.complete_chat(
                        request_id=f"{request_id}:{mode}:{attempt}",
                        messages=messages,
                        options=profile.generation_options(),
                        response_format=response_format,
                    )
                result = parser(_decode_json_object(text))
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=mode,
                    attempt=attempt,
                )
                return result
            except asyncio.CancelledError as error:
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=mode,
                    attempt=attempt,
                    error=error,
                )
                raise
            except BaseException as error:
                last_error = error
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=mode,
                    attempt=attempt,
                    error=error,
                )
        assert last_error is not None
        raise WorldMindModelError(
            f"{mode} failed after retries",
            code=classify_model_failure(last_error),
            mode=mode,
        ) from last_error

    async def _run_text(
        self,
        mode: str,
        request_id: str,
        messages: Sequence[Mapping[str, str]],
    ) -> str:
        profile = self.mode_profiles[mode]
        last_error: BaseException | None = None
        for attempt in range(profile.retries + 1):
            try:
                async with asyncio.timeout(profile.timeout_seconds):
                    raw_text = await self.backend.complete_chat(
                        request_id=f"{request_id}:{mode}:{attempt}",
                        messages=messages,
                        options=profile.generation_options(),
                        response_format=None,
                    )
                text = _plain_reply_text(raw_text)
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=mode,
                    attempt=attempt,
                )
                return text
            except asyncio.CancelledError as error:
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=mode,
                    attempt=attempt,
                    error=error,
                )
                raise
            except BaseException as error:
                last_error = error
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=mode,
                    attempt=attempt,
                    error=error,
                )
        assert last_error is not None
        raise WorldMindModelError(
            f"{mode} failed after retries",
            code=classify_model_failure(last_error),
            mode=mode,
        ) from last_error

    def _validate_prompt_identity(self, prompt) -> None:
        if (
            prompt.character_id != self.identity.character_id
            or prompt.world_id != self.identity.world_id
            or prompt.protagonist_id != self.identity.protagonist_id
        ):
            raise WorldMindModelError("model asset identity does not match request")


def _bind_evidence_refs(
    schema: Mapping[str, object],
    payload: Mapping[str, object],
) -> dict[str, object]:
    # The complete allow-list stays in the prompt payload and is enforced after
    # generation by HardInvariantValidator. Expanding it into every JSON-schema
    # enum makes llama.cpp grammars grow without bound during long sessions.
    return deepcopy(dict(schema))


def _background_character_prompt(identity: WorldMindModelIdentity) -> str:
    return (
        "[后台角色边界]\n"
        f"当前女主角 character_id={identity.character_id}，"
        f"唯一世界 world_id={identity.world_id}，"
        f"唯一男主角 protagonist_id={identity.protagonist_id}。\n"
        "只处理输入中的当前角色状态、世界快照、相关记忆和事件证据；"
        "不得引入其他角色私有信息或输入外事实。"
    )


def _bind_snapshot_contract(
    schema: Mapping[str, object],
    payload: Mapping[str, object],
) -> dict[str, object]:
    bound = deepcopy(dict(schema))
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, Mapping):
        return bound
    snapshot_id = snapshot.get("snapshot_id")
    mind_state_version = snapshot.get("mind_state_version")
    live_world_version = snapshot.get("live_world_version")
    captured_game_time = snapshot.get("captured_game_time")
    protagonist = snapshot.get("protagonist")
    approved_runtime = payload.get("approved_heroine_runtime")

    properties = bound.get("properties")
    if isinstance(properties, dict):
        _set_property_const(properties, "snapshot_id", snapshot_id)
        _set_property_const(
            properties,
            "parent_mind_state_version",
            mind_state_version,
        )
        _set_property_const(properties, "latest_world_version", live_world_version)
        if isinstance(approved_runtime, Mapping):
            _set_property_const(
                properties,
                "approved_mind_state_version",
                approved_runtime.get("version"),
            )

    if not isinstance(protagonist, Mapping):
        return bound
    assertion_values = {
        "protagonist_location_id": protagonist.get("location_id"),
        "protagonist_activity": protagonist.get("activity"),
        "live_world_version": live_world_version,
        "captured_game_time": captured_game_time,
    }

    def visit(value: object) -> None:
        if isinstance(value, dict):
            nested_properties = value.get("properties")
            if isinstance(nested_properties, dict) and set(assertion_values) <= set(
                nested_properties
            ):
                for name, expected in assertion_values.items():
                    _set_property_const(nested_properties, name, expected)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(bound)
    return bound


def _set_property_const(
    properties: dict[str, object],
    name: str,
    value: object,
) -> None:
    target = properties.get(name)
    if value is not None and isinstance(target, dict):
        target["const"] = value


def _mind_result_from_dict(value: dict[str, object]) -> MindAdvanceResult:
    _exact_keys(
        value,
        {
            "schema_version",
            "mode",
            "parent_mind_state_version",
            "snapshot_id",
            "transition_basis",
            "heroine_patch",
            "heroine_diegetic_actions",
            "reply_intent",
            "reply_state_assertions",
        },
        "TURN_MIND_ADVANCE",
    )
    if value["schema_version"] != 1 or value["mode"] != TURN_MIND_ADVANCE:
        raise WorldMindModelOutputError("invalid mind result version or mode")
    return MindAdvanceResult(
        patch=_patch_from_dict(value["heroine_patch"]),
        reply_intent=_text(value["reply_intent"], "reply_intent"),
        fact_assertions=_assertions_from_dict(value["reply_state_assertions"]),
        parent_mind_state_version=_positive_int(
            value["parent_mind_state_version"], "parent_mind_state_version"
        ),
        snapshot_id=_text(value["snapshot_id"], "snapshot_id"),
        transition_basis=_text_tuple(value["transition_basis"], "transition_basis"),
        heroine_diegetic_actions=_actions_from_value(
            value["heroine_diegetic_actions"]
        ),
    )


def _checked_mind_result(
    value: dict[str, object], snapshot_id: str
) -> MindAdvanceResult:
    result = _mind_result_from_dict(value)
    if result.snapshot_id != snapshot_id:
        raise WorldMindModelOutputError("mind result snapshot_id mismatch")
    return result


def _checked_continuity_result(
    value: dict[str, object], snapshot_id: str
) -> ContinuityReviewResult:
    result = _continuity_result_from_dict(value)
    if result.snapshot_id != snapshot_id:
        raise WorldMindModelOutputError("continuity snapshot_id mismatch")
    return result


def _plain_reply_text(value: str) -> str:
    if not isinstance(value, str):
        raise WorldMindModelOutputError("reply output must be text", mode=GAME_REPLY)
    text = value.strip()
    if not text:
        raise WorldMindModelOutputError("reply output cannot be empty", mode=GAME_REPLY)
    if len(text) > GAME_REPLY_MAX_CHARACTERS:
        raise WorldMindModelOutputError(
            "reply output exceeds the plain-text safety limit", mode=GAME_REPLY
        )
    if text.startswith("{") or text.startswith("["):
        raise WorldMindModelOutputError(
            "reply output must not be JSON", mode=GAME_REPLY
        )
    return text


def _checked_reconcile_result(
    value: dict[str, object],
    mode: str,
    snapshot_id: str,
    latest_world_version: int,
) -> ReconcileMindResult:
    result = _reconcile_result_from_dict(value, mode)
    if result.snapshot_id != snapshot_id:
        raise WorldMindModelOutputError("reconcile snapshot_id mismatch")
    if result.latest_world_version != latest_world_version:
        raise WorldMindModelOutputError("reconcile world version mismatch")
    return result


def _continuity_result_from_dict(
    value: dict[str, object]
) -> ContinuityReviewResult:
    _exact_keys(
        value,
        {
            "schema_version",
            "mode",
            "snapshot_id",
            "decision",
            "reason",
            "revision_patch",
        },
        "WORLD_CONTINUITY_REVIEW",
    )
    if value["schema_version"] != 1 or value["mode"] != WORLD_CONTINUITY_REVIEW:
        raise WorldMindModelOutputError("invalid continuity version or mode")
    decision = _text(value["decision"], "decision")
    raw_patch = value["revision_patch"]
    revision_patch = None
    if decision == "revise":
        revision_patch = _patch_from_dict(raw_patch)
    elif raw_patch is not None:
        raise WorldMindModelOutputError(
            "approve/reject continuity result cannot include revision_patch"
        )
    return ContinuityReviewResult(
        decision=decision,
        reason=_text(value["reason"], "reason"),
        revision_patch=revision_patch,
        snapshot_id=_text(value["snapshot_id"], "snapshot_id"),
    )


def _reconcile_result_from_dict(
    value: dict[str, object],
    expected_mode: str,
) -> ReconcileMindResult:
    _exact_keys(
        value,
        {
            "schema_version",
            "mode",
            "operation",
            "reason",
            "parent_mind_state_version",
            "snapshot_id",
            "latest_world_version",
            "transition_basis",
            "heroine_patch",
            "memory_candidates",
            "timeline_candidates",
        },
        expected_mode,
    )
    if value["schema_version"] != 1 or value["mode"] != expected_mode:
        raise WorldMindModelOutputError("invalid reconcile version or mode")
    operation = _text(value["operation"], "operation")
    raw_patch = value["heroine_patch"]
    patch = None
    if operation == "update":
        patch = _patch_from_dict(raw_patch)
    elif operation == "keep":
        patch = None
    else:
        raise WorldMindModelOutputError("unsupported reconcile operation")
    return ReconcileMindResult(
        operation=operation,
        reason=_text(value["reason"], "reason"),
        parent_mind_state_version=_positive_int(
            value["parent_mind_state_version"], "parent_mind_state_version"
        ),
        snapshot_id=_text(value["snapshot_id"], "snapshot_id"),
        latest_world_version=_positive_int(
            value["latest_world_version"], "latest_world_version"
        ),
        transition_basis=_text_tuple(
            value["transition_basis"], "transition_basis"
        ),
        patch=patch,
        memory_candidates=_text_tuple(
            value["memory_candidates"], "memory_candidates"
        ),
        timeline_candidates=_text_tuple(
            value["timeline_candidates"], "timeline_candidates"
        ),
    )


def _patch_from_dict(value: object) -> HeroineMindPatch:
    item = _object(value, "heroine_patch")
    _exact_keys(
        item,
        {
            "living_mind_patch",
            "relationship_patch",
            "motive_updates",
            "knowledge_updates",
            "evidence_refs",
        },
        "heroine_patch",
    )
    living = _object(item["living_mind_patch"], "living_mind_patch")
    _exact_keys(
        living,
        {
            "form",
            "body",
            "emotion",
            "attention",
            "current_activity",
            "immediate_intent",
        },
        "living_mind_patch",
    )
    relationship = _object(item["relationship_patch"], "relationship_patch")
    _exact_keys(
        relationship,
        {"stage", "trust", "unresolved_tension"},
        "relationship_patch",
    )
    return HeroineMindPatch(
        living_mind=LivingMindPatch(
            **{key: _optional_text(value, key) for key, value in living.items()}
        ),
        relationship=RelationshipPatch(
            **{
                key: _optional_text(value, key)
                for key, value in relationship.items()
            }
        ),
        motive_updates=_patch_mapping(item["motive_updates"], "motive_updates"),
        knowledge_updates=_patch_mapping(
            item["knowledge_updates"], "knowledge_updates"
        ),
        evidence_refs=_text_tuple(item["evidence_refs"], "evidence_refs"),
    )


def _actions_from_value(value: object) -> tuple[HeroineDiegeticAction, ...]:
    if not isinstance(value, list):
        raise WorldMindModelOutputError("heroine_diegetic_actions must be an array")
    actions = []
    for raw in value:
        item = _object(raw, "heroine_diegetic_action")
        _exact_keys(item, {"description", "evidence_refs"}, "action")
        actions.append(
            HeroineDiegeticAction(
                description=_text(item["description"], "description"),
                evidence_refs=_text_tuple(item["evidence_refs"], "evidence_refs"),
            )
        )
    return tuple(actions)


def _assertions_from_dict(value: object) -> ReplyFactAssertions:
    item = _object(value, "reply_state_assertions")
    _exact_keys(
        item,
        {
            "protagonist_location_id",
            "protagonist_activity",
            "live_world_version",
            "captured_game_time",
        },
        "reply_state_assertions",
    )
    captured = datetime.fromisoformat(
        _text(item["captured_game_time"], "captured_game_time")
    )
    return ReplyFactAssertions(
        protagonist_location_id=_text(
            item["protagonist_location_id"], "protagonist_location_id"
        ),
        protagonist_activity=_text(
            item["protagonist_activity"], "protagonist_activity"
        ),
        live_world_version=_positive_int(
            item["live_world_version"], "live_world_version"
        ),
        captured_game_time=captured,
    )


def _decode_json_object(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise WorldMindModelOutputError(
            "model returned invalid JSON",
            code="model_invalid_json",
        ) from error
    return _object(value, "model output")


def _load_schema(name: str) -> dict[str, object]:
    try:
        value = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorldMindModelError(f"could not load schema {name}") from error
    return _object(value, name)


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise WorldMindModelOutputError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise WorldMindModelOutputError(f"{name} keys must be strings")
    return value


def _exact_keys(value: dict[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise WorldMindModelOutputError(f"{name} fields do not match schema")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldMindModelOutputError(f"{name} must be non-empty text")
    return value.strip()


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise WorldMindModelOutputError(f"{name} must be a positive integer")
    return value


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WorldMindModelOutputError(f"{name} must be an array")
    values = tuple(_text(item, name) for item in value)
    if len(set(values)) != len(values):
        raise WorldMindModelOutputError(f"{name} must not contain duplicates")
    return values


def _patch_mapping(value: object, name: str) -> dict[str, str | None]:
    item = _object(value, name)
    return {
        _text(key, f"{name} key"): _optional_text(raw, f"{name}.{key}")
        for key, raw in item.items()
    }
