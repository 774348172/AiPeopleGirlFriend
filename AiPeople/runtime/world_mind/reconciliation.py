from __future__ import annotations

import uuid
from collections.abc import Callable

from runtime._selected_memory import SelectedMemoryFrame

from .coordinator import SaveTurnCoordinator
from .game_clock import GameClockService
from .memory_repository import HeroineMemoryRepositoryFactory
from .model_gateway import (
    ContinuityReviewResult,
    ReconcileMindRequest,
    ReconcileReviewRequest,
    WorldMindModel,
)
from .persistence import MindStateConflictError, ReconcileJob, WorldMindStore
from .prompt_composer import CharacterPackagePromptComposer
from .settings import WorldMindRuntimeConfig
from .state import ReconcileWorldSnapshot
from .validation import HardInvariantValidator
from .world_state import WorldStateProjection, WorldStateProvider


class ReconcileExecutionError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class WorldMindReconcileWorker:
    def __init__(
        self,
        *,
        config: WorldMindRuntimeConfig,
        store: WorldMindStore,
        world_state_provider: WorldStateProvider,
        game_clock: GameClockService,
        model: WorldMindModel,
        coordinator: SaveTurnCoordinator,
        prompt_composer: CharacterPackagePromptComposer,
        projection: WorldStateProjection,
        validator: HardInvariantValidator,
        memory_repository_factory: HeroineMemoryRepositoryFactory | None = None,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self.config = config
        self.store = store
        self.world_state_provider = world_state_provider
        self.game_clock = game_clock
        self.model = model
        self.coordinator = coordinator
        self.prompt_composer = prompt_composer
        self.projection = projection
        self.validator = validator
        self.memory_repository_factory = memory_repository_factory
        self._id_factory = id_factory

    async def run_reconcile_job(self, job: ReconcileJob) -> None:
        session = job.session
        self.config.validate_session(session)
        prompt = self.prompt_composer.compose(session)
        live_world = await self.world_state_provider.get_latest(session)
        captured_game_time = self.game_clock.current_time(session)
        seed = self.prompt_composer.initial_runtime_seed(
            session.active_character_id
        )
        previous = self.store.get_or_create_heroine_runtime(session, seed)
        checkpoint = self.store.load_reconcile_checkpoint(session)
        after_world_version = 0 if checkpoint is None else checkpoint.last_world_version
        event_deltas = self.store.list_world_event_deltas(
            session,
            after_version=min(after_world_version, live_world.version),
            through_version=live_world.version,
        )
        selected_memory_frame = SelectedMemoryFrame.empty()
        if self.memory_repository_factory is not None:
            repository = self.memory_repository_factory.open(session)
            selected_memory_frame = (
                await repository.recall(
                    self._recall_query(job, previous.living_mind.current_activity),
                    game_time=captured_game_time,
                )
            ).frame
        source_event_ids = ()
        if job.source_turn is not None:
            source_event_ids = (
                job.source_turn.user_event_id,
                job.source_turn.assistant_event_id,
            )
        snapshot = ReconcileWorldSnapshot(
            snapshot_id=self._id_factory(),
            job_id=job.job_id,
            session=session,
            captured_game_time=captured_game_time,
            live_world_version=live_world.version,
            mind_state_version=previous.version,
            world_state=self.projection.project(live_world),
            protagonist=live_world.protagonist,
            scene=live_world.scene,
            heroine_runtime=previous,
            event_deltas=event_deltas,
            source_event_ids=source_event_ids,
            selected_memory_frame=selected_memory_frame,
        )
        elapsed_game_seconds = self._elapsed_game_seconds(
            job,
            captured_game_time,
            checkpoint.last_game_time if checkpoint is not None else None,
        )
        feedback = None
        for semantic_attempt in range(2):
            request = ReconcileMindRequest(
                mode=job.mode,
                prompt=prompt,
                snapshot=snapshot,
                elapsed_game_seconds=elapsed_game_seconds,
                missed_intervals=job.missed_intervals,
                source_turn=job.source_turn,
                review_feedback=feedback,
            )
            result = await self.model.reconcile_mind(request)
            proposed = (
                previous
                if result.operation == "keep"
                else result.patch.apply(previous)
            )
            self.validator.validate_reconcile(
                snapshot,
                previous,
                proposed,
                result,
            )
            review = await self.model.review_reconciliation(
                ReconcileReviewRequest(
                    mode=job.mode,
                    prompt=prompt,
                    snapshot=snapshot,
                    previous_state=previous,
                    proposed_state=proposed,
                    reconcile_result=result,
                )
            )
            if review.decision == "reject" and semantic_attempt == 0:
                feedback = review.reason
                continue
            approved = previous if review.decision == "reject" else proposed
            if review.decision == "revise":
                if review.revision_patch is None:
                    raise ReconcileExecutionError(
                        "reconcile_revision_missing_patch",
                        retryable=True,
                    )
                approved = review.revision_patch.revise(proposed)
            self.validator.validate_reconcile_review(
                snapshot,
                previous,
                proposed,
                approved,
                result,
                review,
            )
            async with self.coordinator.mind_commit(session.save_id):
                self.store.commit_reconcile(
                    job,
                    snapshot,
                    previous,
                    approved,
                    result,
                    review,
                    _model_identity(self.model),
                )
            return
        raise ReconcileExecutionError("reconcile_semantic_retry_exhausted", retryable=False)

    @staticmethod
    def _elapsed_game_seconds(job, captured_game_time, checkpoint_game_time) -> float:
        baseline = checkpoint_game_time or job.created_game_time
        elapsed = max(0.0, (captured_game_time - baseline).total_seconds())
        return max(elapsed, job.elapsed_runtime_seconds)

    @staticmethod
    def _recall_query(job: ReconcileJob, current_activity: str) -> str:
        if job.source_turn is not None:
            return (
                f"男主说：{job.source_turn.protagonist_utterance}\n"
                f"女主回复：{job.source_turn.heroine_reply}"
            )
        return f"整理最近世界变化、当前活动和未完成意图：{current_activity}"


def _model_identity(model: object) -> dict[str, object]:
    identity = getattr(model, "identity", None)
    if identity is None:
        return {"model_type": type(model).__name__, "mode": "test_or_unbound"}
    return {
        name: getattr(identity, name)
        for name in (
            "model_id",
            "revision",
            "artifact_sha256",
            "character_id",
            "world_id",
            "protagonist_id",
        )
        if hasattr(identity, name)
    }
