from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import replace

from runtime.contracts import Completed, Failed, ReplyEvent, TurnMetrics
from runtime._selected_memory import SelectedMemoryFrame

from .background_scheduler import WorldBackgroundScheduler
from .coordinator import SaveTurnCoordinator, WorldUpdateCoordinator
from .contracts import RuntimeSessionIdentity
from .game_interface import ActionOutcome, GameWorldInterface
from .game_clock import GameClockService
from .model_gateway import (
    ContinuityReviewRequest,
    ContinuityReviewResult,
    ForegroundSemanticTurnRequest,
    GameReplyRequest,
    GameReplyResult,
    JudgeRequest,
    MindAdvanceRequest,
    MindAdvanceResult,
    MindPatchV2Request,
    WorldMindModel,
    _snapshot_assertions,
)
from .memory_repository import (
    HeroineMemoryRepositoryError,
    HeroineMemoryRepositoryFactory,
)
from .memory_contracts import HeroineMemoryProposer
from .memory_worker import R1MemoryWorker
from .persistence import (
    MindStateConflictError,
    TurnRequestConflictError,
    WorldMindStore,
    WorldMindStoreError,
)
from .prompt_composer import CharacterPackagePromptComposer
from .reconciliation import WorldMindReconcileWorker
from .settings import WorldMindRuntimeConfig, WorldMindRuntimeConfigError
from .state import HeroineMindPatch, TurnWorldSnapshot
from .turn_request import TurnRequest
from .validation import HardInvariantError, HardInvariantValidator
from .world_state import (
    WorldStateProjection,
    WorldStateProvider,
    WorldStateUnavailableError,
)


class WorldMindRuntime:
    def __init__(
        self,
        *,
        config: WorldMindRuntimeConfig,
        store: WorldMindStore,
        world_state_provider: WorldStateProvider,
        game_clock: GameClockService,
        model: WorldMindModel,
        coordinator: SaveTurnCoordinator | None = None,
        projection: WorldStateProjection | None = None,
        validator: HardInvariantValidator | None = None,
        memory_repository_factory: HeroineMemoryRepositoryFactory | None = None,
        memory_proposer_provider: (
            Callable[[RuntimeSessionIdentity], HeroineMemoryProposer] | None
        ) = None,
        background_scheduler: WorldBackgroundScheduler | None = None,
        periodic_reconcile_seconds: float = 300.0,
        required_reconcile_before_foreground: bool = True,
        coalesce_pending_required_reconcile: bool = True,
        game_world: GameWorldInterface | None = None,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
        close_store_on_close: bool = False,
    ) -> None:
        self.config = config
        self.store = store
        self.world_state_provider = world_state_provider
        self.game_clock = game_clock
        self.model = model
        self.game_world = game_world
        self.coordinator = coordinator or WorldUpdateCoordinator()
        self.projection = projection or WorldStateProjection()
        self.validator = validator or HardInvariantValidator()
        self.memory_repository_factory = memory_repository_factory
        if (memory_repository_factory is None) != (memory_proposer_provider is None):
            if memory_proposer_provider is not None:
                raise ValueError(
                    "memory_proposer_provider requires memory_repository_factory"
                )
        self.memory_proposer_provider = memory_proposer_provider
        self.prompt_composer = CharacterPackagePromptComposer(config)
        self._id_factory = id_factory
        self.reconcile_worker = WorldMindReconcileWorker(
            config=config,
            store=store,
            world_state_provider=world_state_provider,
            game_clock=game_clock,
            model=model,
            coordinator=self.coordinator,
            prompt_composer=self.prompt_composer,
            projection=self.projection,
            validator=self.validator,
            memory_repository_factory=memory_repository_factory,
            id_factory=id_factory,
        )
        self.memory_worker = (
            R1MemoryWorker(
                store=store,
                repository_factory=memory_repository_factory,
                proposer_provider=memory_proposer_provider,
            )
            if memory_repository_factory is not None
            and memory_proposer_provider is not None
            else None
        )
        self.background_scheduler = background_scheduler or WorldBackgroundScheduler(
            store=store,
            game_clock=game_clock,
            worker=self.reconcile_worker,
            memory_worker=self.memory_worker,
            periodic_interval_seconds=periodic_reconcile_seconds,
            required_before_foreground=required_reconcile_before_foreground,
            coalesce_pending_required=coalesce_pending_required_reconcile,
        )
        self._close_store_on_close = close_store_on_close
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("world mind runtime is closed")
        if self._started:
            return
        await self.coordinator.start()
        try:
            if self.memory_repository_factory is not None:
                await self.memory_repository_factory.start()
            await self.model.start()
            await self.background_scheduler.start()
        except Exception:
            try:
                await self.background_scheduler.close()
            finally:
                try:
                    await self.model.close()
                finally:
                    try:
                        if self.memory_repository_factory is not None:
                            await self.memory_repository_factory.close()
                    finally:
                        await self.coordinator.close()
            raise
        self._started = True

    async def close(self) -> None:
        if self._closed:
            return
        try:
            if self._started:
                try:
                    await self.background_scheduler.close()
                finally:
                    try:
                        await self.model.close()
                    finally:
                        try:
                            if self.memory_repository_factory is not None:
                                await self.memory_repository_factory.close()
                        finally:
                            await self.coordinator.close()
        finally:
            try:
                self.game_clock.close()
            finally:
                if self._close_store_on_close:
                    self.store.close()
                self._started = False
                self._closed = True

    async def handle_turn(self, request: TurnRequest) -> ReplyEvent:
        started = time.perf_counter()
        if not isinstance(request, TurnRequest):
            raise TypeError("request must be a TurnRequest")
        if not self._started or self._closed:
            return Failed(
                request_id=request.request_id,
                user_event_id=None,
                code="runtime_not_running",
                retryable=True,
            )
        try:
            self.config.validate_session(request.session)
            async with self.background_scheduler.foreground(request.session):
                async with self.coordinator.foreground_turn(request.session.save_id):
                    return await self._handle_locked_turn(request, started)
        except WorldMindRuntimeConfigError:
            return _failed(request, "identity_mismatch", False)
        except TurnRequestConflictError:
            return _failed(request, "request_conflict", False)
        except MindStateConflictError:
            return _failed(request, "mind_state_conflict", True)
        except WorldStateUnavailableError:
            return _failed(request, "world_state_unavailable", True)
        except HeroineMemoryRepositoryError:
            return _failed(request, "memory_repository_error", True)
        except HardInvariantError:
            return _failed(request, "hard_invariant_rejected", True)
        except WorldMindStoreError:
            return _failed(request, "persistence_error", True)
        except Exception:
            return _failed(request, "model_or_runtime_error", True)

    async def activate_session(self, session: RuntimeSessionIdentity) -> None:
        self.config.validate_session(session)
        await self.background_scheduler.activate_session(session)

    async def trigger_periodic_reconcile(
        self,
        session: RuntimeSessionIdentity,
    ):
        self.config.validate_session(session)
        return await self.background_scheduler.trigger_periodic(session)

    async def wait_background_idle(
        self,
        session: RuntimeSessionIdentity,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        await self.background_scheduler.wait_idle(
            session,
            timeout_seconds=timeout_seconds,
        )

    async def _handle_locked_turn(
        self,
        request: TurnRequest,
        started: float,
    ) -> ReplyEvent:
        replay = self.store.find_committed_turn(request)
        if replay is not None:
            return Completed(
                request_id=request.request_id,
                user_event_id=replay.user_event_id,
                assistant_event_id=replay.assistant_event_id,
                text=replay.text,
                metrics=_metrics(
                    started=started,
                    model_ms=0.0,
                    commit_ms=0.0,
                    output_chars=len(replay.text),
                    replayed=True,
                ),
            )

        prompt = self.prompt_composer.compose(request.session)
        live_world = await self.world_state_provider.get_latest(request.session)
        captured_game_time = self.game_clock.current_time(request.session)
        seed = self.prompt_composer.initial_runtime_seed(
            request.session.active_character_id
        )
        heroine_runtime = self.store.get_or_create_heroine_runtime(
            request.session,
            seed,
        )
        selected_memory_frame = SelectedMemoryFrame.empty()
        if self.memory_repository_factory is not None:
            repository = self.memory_repository_factory.open(request.session)
            selected_memory_frame = (
                await repository.recall(
                    request.text,
                    game_time=captured_game_time,
                )
            ).frame
        # 游戏世界投影（接口契约 §二/§五）：游戏是唯一事实源，runtime 只读。
        # 游戏未接入（game_world=None）时保持原 sqlite 世界状态行为。
        scene_state = live_world.scene
        view_runtime = heroine_runtime
        pending_actions: tuple[dict[str, object], ...] = ()
        game_feedback: tuple[str, ...] = ()
        if self.game_world is not None:
            projection = await self.game_world.project()
            pending_actions = projection.pending_actions
            game_feedback = projection.recent_feedback
            if projection.item_states is not None:
                scene_state = replace(
                    scene_state, item_states=projection.item_states
                )
            if projection.heroine_activity is not None:
                view_runtime = replace(
                    heroine_runtime,
                    living_mind=replace(
                        heroine_runtime.living_mind,
                        current_activity=projection.heroine_activity,
                    ),
                )
        snapshot = TurnWorldSnapshot(
            snapshot_id=self._id_factory(),
            request_id=request.request_id,
            session=request.session,
            captured_game_time=captured_game_time,
            live_world_version=live_world.version,
            mind_state_version=heroine_runtime.version,
            world_state=self.projection.project(live_world),
            protagonist=live_world.protagonist,
            scene=scene_state,
            heroine_runtime=view_runtime,
            protagonist_utterance=request.text,
            protagonist_utterance_event_id=self._id_factory(),
            selected_memory_frame=selected_memory_frame,
            pending_actions=pending_actions,
            game_feedback=game_feedback,
        )

        model_started = time.perf_counter()
        patch_v2 = None
        if self.config.foreground_protocol == "plain_reply_v1":
            # Dialogue-only model releases do not generate state JSON in the
            # foreground. Keep program-owned state stable and use GAME_REPLY
            # with the frozen world/memory snapshot as the sole evidence.
            mind_result = MindAdvanceResult(
                patch=HeroineMindPatch(evidence_refs=(snapshot.snapshot_id,)),
                reply_intent="直接回应男主本轮对白",
                fact_assertions=_snapshot_assertions(snapshot),
                parent_mind_state_version=snapshot.mind_state_version,
                snapshot_id=snapshot.snapshot_id,
                transition_basis=(snapshot.snapshot_id,),
            )
            proposed_state = mind_result.patch.apply(heroine_runtime)
            self.validator.validate_mind_advance(
                snapshot,
                heroine_runtime,
                proposed_state,
                mind_result,
            )
            approved_state = proposed_state
            review = ContinuityReviewResult(
                decision="approve",
                reason="plain_reply_v1 keeps program-owned mind state stable",
                snapshot_id=snapshot.snapshot_id,
            )
            reply = await self.model.generate_reply(
                GameReplyRequest(
                    prompt=prompt,
                    snapshot=snapshot,
                    approved_state=approved_state,
                    reply_intent=mind_result.reply_intent,
                    fact_assertions=mind_result.fact_assertions,
                    recent_dialogue=self.store.list_recent_dialogue(
                        request.session
                    ),
                )
            )
            self.validator.validate_reply(snapshot, approved_state, reply)
        elif self.config.foreground_protocol == "judge_v1":
            judge = await self.model.judge_turn(
                JudgeRequest(
                    prompt=prompt,
                    snapshot=snapshot,
                    recent_dialogue=self.store.list_recent_dialogue(
                        request.session
                    ),
                )
            )
            mind_result = MindAdvanceResult(
                patch=judge.mind_patch
                if judge.mind_patch is not None
                else HeroineMindPatch(),
                reply_intent="单次判断",
                fact_assertions=_snapshot_assertions(snapshot),
                parent_mind_state_version=snapshot.mind_state_version,
                snapshot_id=snapshot.snapshot_id,
                transition_basis=(snapshot.snapshot_id,),
                heroine_diegetic_actions=judge.actions,
            )
            proposed_state = mind_result.patch.apply(heroine_runtime)
            self.validator.validate_mind_advance(
                snapshot,
                heroine_runtime,
                proposed_state,
                mind_result,
            )
            approved_state = proposed_state
            review = ContinuityReviewResult(
                decision="approve",
                reason="judge_v1 单次判断（Critic 未启用）",
                snapshot_id=snapshot.snapshot_id,
            )
            if self.config.judge_review_enabled and (
                judge.actions or judge.mind_patch is not None
            ):
                # 定向审查（可选兜底，默认关）：有动作提议或心智变化时
                # 核对与游戏投影状态的语义一致性。revise 仅修正心智，
                # 回复以原判断为准（单次判断语义；兜底场景可接受）。
                review = await self.model.review_continuity(
                    ContinuityReviewRequest(
                        prompt=prompt,
                        snapshot=snapshot,
                        previous_state=heroine_runtime,
                        proposed_state=proposed_state,
                        mind_result=mind_result,
                    )
                )
                if not review.approved:
                    return Failed(
                        request_id=request.request_id,
                        user_event_id=None,
                        code="continuity_rejected",
                        retryable=True,
                    )
                if review.decision == "revise":
                    if review.revision_patch is None:
                        raise HardInvariantError(
                            "continuity revise decision is missing revision patch"
                        )
                    approved_state = review.revision_patch.revise(proposed_state)
                self.validator.validate_continuity_review(
                    snapshot,
                    heroine_runtime,
                    proposed_state,
                    approved_state,
                    review,
                )
            reply = GameReplyResult(
                text=judge.reply,
                fact_assertions=mind_result.fact_assertions,
                snapshot_id=snapshot.snapshot_id,
                approved_mind_state_version=approved_state.version,
            )
            self.validator.validate_reply(snapshot, approved_state, reply)
            # 动作转发（接口契约 §四）：合法动作交给游戏执行；拒绝/失败
            # 原因由游戏记录，经下一轮投影 recent_feedback 回注给模型。
            if self.game_world is not None:
                for action in judge.actions:
                    try:
                        await self.game_world.execute_action(
                            action.action_id, action.params
                        )
                    except Exception:
                        continue
        else:
            semantic_turn = None
            if self.config.foreground_protocol == "mind_patch_v2":
                patch_v2 = await self.model.propose_mind_patch_v2(
                    MindPatchV2Request(prompt=prompt, snapshot=snapshot)
                )
                mind_result = patch_v2.mind_result
            elif self.config.foreground_protocol == "short_semantic_v1":
                semantic_turn = await self.model.foreground_semantic_turn(
                    ForegroundSemanticTurnRequest(prompt=prompt, snapshot=snapshot)
                )
                mind_result = semantic_turn.mind_result
            else:
                mind_result = await self.model.advance_mind(
                    MindAdvanceRequest(prompt=prompt, snapshot=snapshot)
                )
            proposed_state = mind_result.patch.apply(heroine_runtime)
            self.validator.validate_mind_advance(
                snapshot,
                heroine_runtime,
                proposed_state,
                mind_result,
            )
            short_result = patch_v2 if patch_v2 is not None else semantic_turn
            if short_result is not None and not short_result.review_recommended:
                review = ContinuityReviewResult(
                    decision="approve",
                    reason="short patch passed hard invariants without semantic risk trigger",
                    snapshot_id=snapshot.snapshot_id,
                )
            else:
                review = await self.model.review_continuity(
                    ContinuityReviewRequest(
                        prompt=prompt,
                        snapshot=snapshot,
                        previous_state=heroine_runtime,
                        proposed_state=proposed_state,
                        mind_result=mind_result,
                    )
                )
            if not review.approved:
                return Failed(
                    request_id=request.request_id,
                    user_event_id=None,
                    code="continuity_rejected",
                    retryable=True,
                )
            approved_state = proposed_state
            if review.decision == "revise":
                if short_result is not None:
                    return Failed(
                        request_id=request.request_id,
                        user_event_id=None,
                        code="continuity_revision_requires_regeneration",
                        retryable=True,
                    )
                if review.revision_patch is None:
                    raise HardInvariantError(
                        "continuity revise decision is missing revision patch"
                    )
                approved_state = review.revision_patch.revise(proposed_state)
            self.validator.validate_continuity_review(
                snapshot,
                heroine_runtime,
                proposed_state,
                approved_state,
                review,
            )
            if semantic_turn is not None:
                reply = semantic_turn.reply_result
            else:
                reply = await self.model.generate_reply(
                    GameReplyRequest(
                        prompt=prompt,
                        snapshot=snapshot,
                        approved_state=approved_state,
                        reply_intent=mind_result.reply_intent,
                        fact_assertions=mind_result.fact_assertions,
                        approved_actions=mind_result.heroine_diegetic_actions,
                        recent_dialogue=self.store.list_recent_dialogue(
                            request.session
                        ),
                    )
                )
            self.validator.validate_reply(snapshot, approved_state, reply)
        model_ms = (time.perf_counter() - model_started) * 1000

        commit_started = time.perf_counter()
        async with self.coordinator.mind_commit(request.session.save_id):
            committed = self.store.commit_turn(
                request,
                snapshot,
                approved_state,
                mind_result,
                review,
                reply,
                _model_identity(self.model),
                enqueue_r1_memory=self.memory_worker is not None,
            )
        commit_ms = (time.perf_counter() - commit_started) * 1000
        return Completed(
            request_id=request.request_id,
            user_event_id=committed.user_event_id,
            assistant_event_id=committed.assistant_event_id,
            text=committed.text,
            metrics=_metrics(
                started=started,
                model_ms=model_ms,
                commit_ms=commit_ms,
                output_chars=len(committed.text),
            ),
        )


def _failed(request: TurnRequest, code: str, retryable: bool) -> Failed:
    return Failed(
        request_id=request.request_id,
        user_event_id=None,
        code=code,
        retryable=retryable,
    )


def _model_identity(model: object) -> dict[str, object]:
    identity = getattr(model, "identity", None)
    if identity is None:
        return {"model_type": type(model).__name__, "mode": "test_or_unbound"}
    fields = (
        "model_id",
        "revision",
        "artifact_sha256",
        "character_id",
        "world_id",
        "protagonist_id",
    )
    return {
        name: getattr(identity, name)
        for name in fields
        if hasattr(identity, name)
    }


def _metrics(
    *,
    started: float,
    model_ms: float,
    commit_ms: float,
    output_chars: int,
    replayed: bool = False,
) -> TurnMetrics:
    return TurnMetrics(
        ledger_user_commit_ms=0.0,
        model_first_delta_ms=None,
        model_total_ms=model_ms,
        ledger_reply_commit_ms=commit_ms,
        total_ms=(time.perf_counter() - started) * 1000,
        output_chars=output_chars,
        delta_count=0,
        replayed=replayed,
    )
