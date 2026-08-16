from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Mapping, Protocol

from .prompt_composer import CharacterPrompt
from .state import (
    HeroineMindPatch,
    HeroineRuntime,
    ReconcileWorldSnapshot,
    ReplyFactAssertions,
    TurnWorldSnapshot,
)


TURN_MIND_ADVANCE = "TURN_MIND_ADVANCE"
FOREGROUND_SEMANTIC_TURN = "FOREGROUND_SEMANTIC_TURN"
MIND_PATCH_V2 = "MIND_PATCH_V2"
WORLD_CONTINUITY_REVIEW = "WORLD_CONTINUITY_REVIEW"
GAME_REPLY = "GAME_REPLY"
POST_REPLY_WORLD_MIND_RECONCILE = "POST_REPLY_WORLD_MIND_RECONCILE"
FIVE_MINUTE_WORLD_MIND_RECONCILE = "FIVE_MINUTE_WORLD_MIND_RECONCILE"
RECONCILE_MODES = frozenset(
    {POST_REPLY_WORLD_MIND_RECONCILE, FIVE_MINUTE_WORLD_MIND_RECONCILE}
)
CONTINUITY_DECISIONS = frozenset({"approve", "revise", "reject"})


@dataclass(frozen=True, slots=True)
class HeroineDiegeticAction:
    description: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("action description cannot be empty")
        if not isinstance(self.evidence_refs, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.evidence_refs
        ):
            raise TypeError("action evidence_refs must be non-empty strings")


@dataclass(frozen=True, slots=True)
class MindAdvanceRequest:
    prompt: CharacterPrompt
    snapshot: TurnWorldSnapshot


@dataclass(frozen=True, slots=True)
class ForegroundSemanticTurnRequest:
    prompt: CharacterPrompt
    snapshot: TurnWorldSnapshot


@dataclass(frozen=True, slots=True)
class MindPatchV2Request:
    prompt: CharacterPrompt
    snapshot: TurnWorldSnapshot


@dataclass(frozen=True, slots=True)
class MindPatchV2Result:
    mind_result: MindAdvanceResult
    changed_field_codes: tuple[int, ...] = ()
    raw_output: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mind_result, MindAdvanceResult):
            raise TypeError("mind_result must be MindAdvanceResult")
        if any(
            type(item) is not int or not 0 <= item <= 10
            for item in self.changed_field_codes
        ):
            raise ValueError("changed_field_codes contains an invalid M2 code")
        if self.raw_output is not None and not isinstance(self.raw_output, Mapping):
            raise TypeError("raw_output must be a mapping")

    @property
    def review_recommended(self) -> bool:
        return bool({6, 7, 8} & set(self.changed_field_codes)) or len(
            self.changed_field_codes
        ) >= 3


@dataclass(frozen=True, slots=True)
class MindAdvanceResult:
    patch: HeroineMindPatch
    reply_intent: str
    fact_assertions: ReplyFactAssertions
    parent_mind_state_version: int | None = None
    snapshot_id: str | None = None
    transition_basis: tuple[str, ...] = ()
    heroine_diegetic_actions: tuple[HeroineDiegeticAction, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.reply_intent, str) or not self.reply_intent.strip():
            raise ValueError("reply_intent cannot be empty")
        if self.parent_mind_state_version is not None and self.parent_mind_state_version <= 0:
            raise ValueError("parent_mind_state_version must be positive")
        if self.snapshot_id is not None and (
            not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip()
        ):
            raise ValueError("snapshot_id cannot be empty")
        if not isinstance(self.transition_basis, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.transition_basis
        ):
            raise TypeError("transition_basis must contain non-empty strings")
        if not isinstance(self.heroine_diegetic_actions, tuple) or any(
            not isinstance(item, HeroineDiegeticAction)
            for item in self.heroine_diegetic_actions
        ):
            raise TypeError(
                "heroine_diegetic_actions must be HeroineDiegeticAction values"
            )


@dataclass(frozen=True, slots=True)
class ContinuityReviewRequest:
    prompt: CharacterPrompt
    snapshot: TurnWorldSnapshot
    previous_state: HeroineRuntime
    proposed_state: HeroineRuntime
    mind_result: MindAdvanceResult


@dataclass(frozen=True, slots=True)
class ContinuityReviewResult:
    decision: str
    reason: str
    revision_patch: HeroineMindPatch | None = None
    snapshot_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason cannot be empty")
        if self.decision not in CONTINUITY_DECISIONS:
            raise ValueError("unsupported continuity decision")
        if self.decision == "revise" and self.revision_patch is None:
            raise ValueError("revise decision requires revision_patch")
        if self.decision != "revise" and self.revision_patch is not None:
            raise ValueError("only revise decision may include revision_patch")
        if self.snapshot_id is not None and (
            not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip()
        ):
            raise ValueError("snapshot_id cannot be empty")

    @property
    def approved(self) -> bool:
        return self.decision != "reject"


@dataclass(frozen=True, slots=True)
class GameReplyRequest:
    prompt: CharacterPrompt
    snapshot: TurnWorldSnapshot
    approved_state: HeroineRuntime
    reply_intent: str
    fact_assertions: ReplyFactAssertions
    approved_actions: tuple[HeroineDiegeticAction, ...] = ()
    recent_dialogue: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.reply_intent, str) or not self.reply_intent.strip():
            raise ValueError("reply_intent cannot be empty")
        if not isinstance(self.approved_actions, tuple) or any(
            not isinstance(item, HeroineDiegeticAction)
            for item in self.approved_actions
        ):
            raise TypeError("approved_actions must be HeroineDiegeticAction values")
        if not isinstance(self.recent_dialogue, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or item[0] not in {"protagonist", "heroine"}
            or not isinstance(item[1], str)
            or not item[1].strip()
            for item in self.recent_dialogue
        ):
            raise TypeError("recent_dialogue must contain actor/text pairs")


@dataclass(frozen=True, slots=True)
class GameReplyResult:
    text: str
    fact_assertions: ReplyFactAssertions
    snapshot_id: str | None = None
    approved_mind_state_version: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("reply text cannot be empty")
        if self.snapshot_id is not None and (
            not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip()
        ):
            raise ValueError("snapshot_id cannot be empty")
        if (
            self.approved_mind_state_version is not None
            and self.approved_mind_state_version <= 0
        ):
            raise ValueError("approved_mind_state_version must be positive")


@dataclass(frozen=True, slots=True)
class ForegroundSemanticTurnResult:
    mind_result: MindAdvanceResult
    reply_result: GameReplyResult
    changed_field_codes: tuple[int, ...] = ()
    raw_output: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mind_result, MindAdvanceResult):
            raise TypeError("mind_result must be MindAdvanceResult")
        if not isinstance(self.reply_result, GameReplyResult):
            raise TypeError("reply_result must be GameReplyResult")
        if any(type(item) is not int or not 0 <= item <= 10 for item in self.changed_field_codes):
            raise ValueError("changed_field_codes contains an invalid M1 code")
        if self.raw_output is not None and not isinstance(self.raw_output, Mapping):
            raise TypeError("raw_output must be a mapping")

    @property
    def review_recommended(self) -> bool:
        return bool({6, 7, 8} & set(self.changed_field_codes)) or len(
            self.changed_field_codes
        ) >= 3


@dataclass(frozen=True, slots=True)
class ReconcileSourceTurn:
    request_id: str
    user_event_id: str
    assistant_event_id: str
    protagonist_utterance: str
    heroine_reply: str
    approved_actions: tuple[HeroineDiegeticAction, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "request_id",
            "user_event_id",
            "assistant_event_id",
            "protagonist_utterance",
            "heroine_reply",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if not isinstance(self.approved_actions, tuple) or any(
            not isinstance(item, HeroineDiegeticAction)
            for item in self.approved_actions
        ):
            raise TypeError("approved_actions must be HeroineDiegeticAction values")


@dataclass(frozen=True, slots=True)
class ReconcileMindRequest:
    mode: str
    prompt: CharacterPrompt
    snapshot: ReconcileWorldSnapshot
    elapsed_game_seconds: float
    missed_intervals: int = 0
    source_turn: ReconcileSourceTurn | None = None
    review_feedback: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in RECONCILE_MODES:
            raise ValueError("unsupported reconcile mode")
        if self.elapsed_game_seconds < 0:
            raise ValueError("elapsed_game_seconds cannot be negative")
        if self.missed_intervals < 0:
            raise ValueError("missed_intervals cannot be negative")
        if self.mode == POST_REPLY_WORLD_MIND_RECONCILE and self.source_turn is None:
            raise ValueError("post-reply reconcile requires source_turn")
        if self.mode == FIVE_MINUTE_WORLD_MIND_RECONCILE and self.source_turn is not None:
            raise ValueError("five-minute reconcile cannot include source_turn")
        if self.review_feedback is not None and not self.review_feedback.strip():
            raise ValueError("review_feedback cannot be empty")


@dataclass(frozen=True, slots=True)
class ReconcileMindResult:
    operation: str
    reason: str
    parent_mind_state_version: int
    snapshot_id: str
    latest_world_version: int
    transition_basis: tuple[str, ...]
    patch: HeroineMindPatch | None = None
    memory_candidates: tuple[str, ...] = ()
    timeline_candidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.operation not in {"keep", "update"}:
            raise ValueError("reconcile operation must be keep or update")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reconcile reason cannot be empty")
        if self.parent_mind_state_version <= 0:
            raise ValueError("parent_mind_state_version must be positive")
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("snapshot_id cannot be empty")
        if self.latest_world_version <= 0:
            raise ValueError("latest_world_version must be positive")
        if not self.transition_basis or any(
            not isinstance(item, str) or not item.strip()
            for item in self.transition_basis
        ):
            raise ValueError("transition_basis must contain evidence IDs")
        if self.operation == "keep" and self.patch is not None:
            raise ValueError("keep reconcile cannot include a patch")
        if self.operation == "update" and self.patch is None:
            raise ValueError("update reconcile requires a patch")
        for name in ("memory_candidates", "timeline_candidates"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise TypeError(f"{name} must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class ReconcileReviewRequest:
    mode: str
    prompt: CharacterPrompt
    snapshot: ReconcileWorldSnapshot
    previous_state: HeroineRuntime
    proposed_state: HeroineRuntime
    reconcile_result: ReconcileMindResult

    def __post_init__(self) -> None:
        if self.mode not in RECONCILE_MODES:
            raise ValueError("unsupported reconcile review mode")


class WorldMindModel(Protocol):
    async def start(self) -> None: ...

    async def foreground_semantic_turn(
        self,
        request: ForegroundSemanticTurnRequest,
    ) -> ForegroundSemanticTurnResult: ...

    async def propose_mind_patch_v2(
        self,
        request: MindPatchV2Request,
    ) -> MindPatchV2Result: ...

    async def advance_mind(self, request: MindAdvanceRequest) -> MindAdvanceResult: ...

    async def review_continuity(
        self,
        request: ContinuityReviewRequest,
    ) -> ContinuityReviewResult: ...

    async def generate_reply(self, request: GameReplyRequest) -> GameReplyResult: ...

    async def reconcile_mind(
        self,
        request: ReconcileMindRequest,
    ) -> ReconcileMindResult: ...

    async def review_reconciliation(
        self,
        request: ReconcileReviewRequest,
    ) -> ContinuityReviewResult: ...

    async def close(self) -> None: ...


MindPatchFactory = Callable[
    [MindAdvanceRequest],
    HeroineMindPatch | Awaitable[HeroineMindPatch],
]
ReplyFactory = Callable[
    [GameReplyRequest],
    str | Awaitable[str],
]
ForegroundFactory = Callable[
    [ForegroundSemanticTurnRequest],
    ForegroundSemanticTurnResult | Awaitable[ForegroundSemanticTurnResult],
]
ContinuityFactory = Callable[
    [ContinuityReviewRequest],
    ContinuityReviewResult | Awaitable[ContinuityReviewResult],
]
ReconcileFactory = Callable[
    [ReconcileMindRequest],
    ReconcileMindResult | Awaitable[ReconcileMindResult],
]
ReconcileReviewFactory = Callable[
    [ReconcileReviewRequest],
    ContinuityReviewResult | Awaitable[ContinuityReviewResult],
]


class FakeWorldMindModel:
    def __init__(
        self,
        *,
        mind_patch_factory: MindPatchFactory | None = None,
        reply_factory: ReplyFactory | None = None,
        continuity_factory: ContinuityFactory | None = None,
        reconcile_factory: ReconcileFactory | None = None,
        reconcile_review_factory: ReconcileReviewFactory | None = None,
        foreground_factory: ForegroundFactory | None = None,
        continuity_approved: bool = True,
        wait_event: asyncio.Event | None = None,
        fail_phase: str | None = None,
    ) -> None:
        if fail_phase not in {
            None,
            "advance",
            "review",
            "reply",
            "reconcile",
            "reconcile_review",
            "foreground",
        }:
            raise ValueError("unsupported fake model fail_phase")
        self.mind_patch_factory = mind_patch_factory
        self.reply_factory = reply_factory
        self.continuity_factory = continuity_factory
        self.reconcile_factory = reconcile_factory
        self.reconcile_review_factory = reconcile_review_factory
        self.foreground_factory = foreground_factory
        self.continuity_approved = continuity_approved
        self.wait_event = wait_event
        self.fail_phase = fail_phase
        self.advance_requests: list[MindAdvanceRequest] = []
        self.foreground_requests: list[ForegroundSemanticTurnRequest] = []
        self.review_requests: list[ContinuityReviewRequest] = []
        self.reply_requests: list[GameReplyRequest] = []
        self.reconcile_requests: list[ReconcileMindRequest] = []
        self.reconcile_review_requests: list[ReconcileReviewRequest] = []
        self.start_calls = 0
        self.close_calls = 0
        self.reply_started = asyncio.Event()
        self.foreground_started = asyncio.Event()

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def foreground_semantic_turn(
        self,
        request: ForegroundSemanticTurnRequest,
    ) -> ForegroundSemanticTurnResult:
        self.foreground_requests.append(request)
        self.foreground_started.set()
        if self.wait_event is not None:
            await self.wait_event.wait()
        if self.fail_phase == "foreground":
            raise RuntimeError("configured fake foreground failure")
        if self.foreground_factory is not None:
            return await _maybe_await(self.foreground_factory(request))
        mind_request = MindAdvanceRequest(request.prompt, request.snapshot)
        patch = HeroineMindPatch(evidence_refs=(request.snapshot.snapshot_id,))
        if self.mind_patch_factory is not None:
            patch = await _maybe_await(self.mind_patch_factory(mind_request))
        mind_result = MindAdvanceResult(
            patch=patch,
            reply_intent="从更新后的自身状态回应男主",
            fact_assertions=_snapshot_assertions(request.snapshot),
            parent_mind_state_version=request.snapshot.mind_state_version,
            snapshot_id=request.snapshot.snapshot_id,
            transition_basis=(request.snapshot.snapshot_id,),
        )
        proposed = patch.apply(request.snapshot.heroine_runtime)
        reply_request = GameReplyRequest(
            prompt=request.prompt,
            snapshot=request.snapshot,
            approved_state=proposed,
            reply_intent=mind_result.reply_intent,
            fact_assertions=mind_result.fact_assertions,
        )
        text = "我听见了。"
        if self.reply_factory is not None:
            text = await _maybe_await(self.reply_factory(reply_request))
        return ForegroundSemanticTurnResult(
            mind_result=mind_result,
            reply_result=GameReplyResult(
                text=text,
                fact_assertions=_snapshot_assertions(request.snapshot),
                snapshot_id=request.snapshot.snapshot_id,
                approved_mind_state_version=proposed.version,
            ),
        )

    async def propose_mind_patch_v2(
        self,
        request: MindPatchV2Request,
    ) -> MindPatchV2Result:
        self.foreground_requests.append(
            ForegroundSemanticTurnRequest(request.prompt, request.snapshot)
        )
        self.foreground_started.set()
        if self.wait_event is not None:
            await self.wait_event.wait()
        if self.fail_phase == "foreground":
            raise RuntimeError("configured fake foreground failure")
        mind_request = MindAdvanceRequest(request.prompt, request.snapshot)
        patch = HeroineMindPatch(evidence_refs=(request.snapshot.snapshot_id,))
        if self.mind_patch_factory is not None:
            patch = await _maybe_await(self.mind_patch_factory(mind_request))
        changed_codes = _changed_field_codes(patch)
        return MindPatchV2Result(
            mind_result=MindAdvanceResult(
                patch=patch,
                reply_intent="从批准后的自身状态回应男主",
                fact_assertions=_snapshot_assertions(request.snapshot),
                parent_mind_state_version=request.snapshot.mind_state_version,
                snapshot_id=request.snapshot.snapshot_id,
                transition_basis=patch.evidence_refs,
            ),
            changed_field_codes=changed_codes,
        )

    async def advance_mind(self, request: MindAdvanceRequest) -> MindAdvanceResult:
        self.advance_requests.append(request)
        if self.fail_phase == "advance":
            raise RuntimeError("configured fake advance failure")
        patch = HeroineMindPatch(evidence_refs=(request.snapshot.snapshot_id,))
        if self.mind_patch_factory is not None:
            patch = await _maybe_await(self.mind_patch_factory(request))
        return MindAdvanceResult(
            patch=patch,
            reply_intent="回应男主角当前说的话",
            fact_assertions=_snapshot_assertions(request.snapshot),
            parent_mind_state_version=request.snapshot.mind_state_version,
            snapshot_id=request.snapshot.snapshot_id,
            transition_basis=(request.snapshot.snapshot_id,),
        )

    async def review_continuity(
        self,
        request: ContinuityReviewRequest,
    ) -> ContinuityReviewResult:
        self.review_requests.append(request)
        if self.fail_phase == "review":
            raise RuntimeError("configured fake review failure")
        if self.continuity_factory is not None:
            return await _maybe_await(self.continuity_factory(request))
        return ContinuityReviewResult(
            decision="approve" if self.continuity_approved else "reject",
            reason="fake continuity approved" if self.continuity_approved else "fake rejection",
            snapshot_id=request.snapshot.snapshot_id,
        )

    async def generate_reply(self, request: GameReplyRequest) -> GameReplyResult:
        self.reply_requests.append(request)
        self.reply_started.set()
        if self.wait_event is not None:
            await self.wait_event.wait()
        if self.fail_phase == "reply":
            raise RuntimeError("configured fake reply failure")
        text = "我听见了。"
        if self.reply_factory is not None:
            text = await _maybe_await(self.reply_factory(request))
        return GameReplyResult(
            text=text,
            fact_assertions=_snapshot_assertions(request.snapshot),
            snapshot_id=request.snapshot.snapshot_id,
            approved_mind_state_version=request.approved_state.version,
        )

    async def reconcile_mind(
        self,
        request: ReconcileMindRequest,
    ) -> ReconcileMindResult:
        self.reconcile_requests.append(request)
        if self.fail_phase == "reconcile":
            raise RuntimeError("configured fake reconcile failure")
        if self.reconcile_factory is not None:
            return await _maybe_await(self.reconcile_factory(request))
        return ReconcileMindResult(
            operation="keep",
            reason="fake reconcile found no meaningful change",
            parent_mind_state_version=request.snapshot.mind_state_version,
            snapshot_id=request.snapshot.snapshot_id,
            latest_world_version=request.snapshot.live_world_version,
            transition_basis=(request.snapshot.snapshot_id,),
        )

    async def review_reconciliation(
        self,
        request: ReconcileReviewRequest,
    ) -> ContinuityReviewResult:
        self.reconcile_review_requests.append(request)
        if self.fail_phase == "reconcile_review":
            raise RuntimeError("configured fake reconcile review failure")
        if self.reconcile_review_factory is not None:
            return await _maybe_await(self.reconcile_review_factory(request))
        return ContinuityReviewResult(
            decision="approve",
            reason="fake reconcile continuity approved",
            snapshot_id=request.snapshot.snapshot_id,
        )


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _snapshot_assertions(snapshot: TurnWorldSnapshot) -> ReplyFactAssertions:
    return ReplyFactAssertions(
        protagonist_location_id=snapshot.protagonist.location_id,
        protagonist_activity=snapshot.protagonist.activity,
        live_world_version=snapshot.live_world_version,
        captured_game_time=snapshot.captured_game_time,
    )


def _changed_field_codes(patch: HeroineMindPatch) -> tuple[int, ...]:
    codes: list[int] = []
    living_codes = {
        "form": 0,
        "body": 1,
        "emotion": 2,
        "attention": 3,
        "current_activity": 4,
        "immediate_intent": 5,
    }
    relationship_codes = {"stage": 6, "trust": 7, "unresolved_tension": 8}
    for field, code in living_codes.items():
        if getattr(patch.living_mind, field) is not None:
            codes.append(code)
    for field, code in relationship_codes.items():
        if getattr(patch.relationship, field) is not None:
            codes.append(code)
    if patch.motive_updates:
        codes.append(9)
    if patch.knowledge_updates:
        codes.append(10)
    return tuple(codes)
