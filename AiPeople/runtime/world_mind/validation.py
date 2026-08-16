from __future__ import annotations

from .model_gateway import (
    ContinuityReviewResult,
    GameReplyResult,
    MindAdvanceResult,
    ReconcileMindResult,
)
from .state import (
    HeroineRuntime,
    ReconcileWorldSnapshot,
    ReplyFactAssertions,
    TurnWorldSnapshot,
)


class HardInvariantError(ValueError):
    pass


class HardInvariantValidator:
    def validate_mind_advance(
        self,
        snapshot: TurnWorldSnapshot,
        previous: HeroineRuntime,
        proposed: HeroineRuntime,
        result: MindAdvanceResult,
    ) -> None:
        if proposed.save_id != previous.save_id:
            raise HardInvariantError("mind advance cannot change save_id")
        if proposed.world_id != previous.world_id:
            raise HardInvariantError("mind advance cannot change world_id")
        if proposed.character_id != previous.character_id:
            raise HardInvariantError("mind advance cannot change character_id")
        if proposed.relationship.protagonist_id != snapshot.session.protagonist_id:
            raise HardInvariantError("mind advance cannot change protagonist identity")
        if proposed.version != previous.version + 1:
            raise HardInvariantError("mind state version must advance by one")
        if result.parent_mind_state_version != snapshot.mind_state_version:
            raise HardInvariantError("mind result parent version mismatch")
        if result.snapshot_id != snapshot.snapshot_id:
            raise HardInvariantError("mind result snapshot_id mismatch")
        allowed_refs = self._allowed_refs(snapshot)
        if not result.transition_basis:
            raise HardInvariantError("mind transition_basis cannot be empty")
        if not set(result.transition_basis) <= allowed_refs:
            raise HardInvariantError("mind transition_basis contains unknown IDs")
        if not set(result.patch.evidence_refs) <= allowed_refs:
            raise HardInvariantError("mind patch evidence contains unknown IDs")
        for action in result.heroine_diegetic_actions:
            if not set(action.evidence_refs) <= allowed_refs:
                raise HardInvariantError("heroine action evidence contains unknown IDs")
        self._validate_assertions(snapshot, result.fact_assertions)

    def validate_continuity_review(
        self,
        snapshot: TurnWorldSnapshot,
        previous: HeroineRuntime,
        proposed: HeroineRuntime,
        approved: HeroineRuntime,
        review: ContinuityReviewResult,
    ) -> None:
        if review.snapshot_id != snapshot.snapshot_id:
            raise HardInvariantError("continuity review snapshot_id mismatch")
        if review.decision == "reject":
            raise HardInvariantError("rejected review has no approved state")
        if approved.save_id != previous.save_id:
            raise HardInvariantError("continuity revision cannot change save_id")
        if approved.world_id != previous.world_id:
            raise HardInvariantError("continuity revision cannot change world_id")
        if approved.character_id != previous.character_id:
            raise HardInvariantError("continuity revision cannot change character_id")
        if approved.relationship.protagonist_id != snapshot.session.protagonist_id:
            raise HardInvariantError("continuity revision changed protagonist identity")
        if approved.version != previous.version + 1:
            raise HardInvariantError("continuity revision changed candidate version")
        if proposed.version != approved.version:
            raise HardInvariantError("continuity revision must preserve candidate version")
        if review.revision_patch is not None:
            if not set(review.revision_patch.evidence_refs) <= self._allowed_refs(snapshot):
                raise HardInvariantError("continuity revision evidence contains unknown IDs")

    def validate_reply(
        self,
        snapshot: TurnWorldSnapshot,
        approved_state: HeroineRuntime,
        result: GameReplyResult,
    ) -> None:
        if not result.text.strip():
            raise HardInvariantError("reply cannot be empty")
        if result.snapshot_id != snapshot.snapshot_id:
            raise HardInvariantError("reply snapshot_id mismatch")
        if result.approved_mind_state_version != approved_state.version:
            raise HardInvariantError("reply approved state version mismatch")
        self._validate_assertions(snapshot, result.fact_assertions)

    def validate_reconcile(
        self,
        snapshot: ReconcileWorldSnapshot,
        previous: HeroineRuntime,
        proposed: HeroineRuntime,
        result: ReconcileMindResult,
    ) -> None:
        if result.parent_mind_state_version != snapshot.mind_state_version:
            raise HardInvariantError("reconcile parent version mismatch")
        if result.snapshot_id != snapshot.snapshot_id:
            raise HardInvariantError("reconcile snapshot_id mismatch")
        if result.latest_world_version != snapshot.live_world_version:
            raise HardInvariantError("reconcile live world version mismatch")
        if proposed.save_id != previous.save_id:
            raise HardInvariantError("reconcile cannot change save_id")
        if proposed.world_id != previous.world_id:
            raise HardInvariantError("reconcile cannot change world_id")
        if proposed.character_id != previous.character_id:
            raise HardInvariantError("reconcile cannot change character_id")
        if proposed.relationship.protagonist_id != snapshot.session.protagonist_id:
            raise HardInvariantError("reconcile cannot change protagonist identity")
        expected_version = previous.version + (result.operation == "update")
        if proposed.version != expected_version:
            raise HardInvariantError("reconcile state version is invalid")
        allowed_refs = self._allowed_reconcile_refs(snapshot)
        if not set(result.transition_basis) <= allowed_refs:
            raise HardInvariantError("reconcile transition_basis contains unknown IDs")
        if result.patch is not None and not set(result.patch.evidence_refs) <= allowed_refs:
            raise HardInvariantError("reconcile patch evidence contains unknown IDs")

    def validate_reconcile_review(
        self,
        snapshot: ReconcileWorldSnapshot,
        previous: HeroineRuntime,
        proposed: HeroineRuntime,
        approved: HeroineRuntime,
        result: ReconcileMindResult,
        review: ContinuityReviewResult,
    ) -> None:
        if review.snapshot_id != snapshot.snapshot_id:
            raise HardInvariantError("reconcile review snapshot_id mismatch")
        if review.decision == "reject":
            if approved != previous:
                raise HardInvariantError("rejected reconcile must keep previous state")
            return
        if result.operation == "keep":
            if review.decision != "approve" or approved != previous:
                raise HardInvariantError("keep reconcile can only be approved unchanged")
            return
        if approved.save_id != previous.save_id:
            raise HardInvariantError("reconcile revision cannot change save_id")
        if approved.world_id != previous.world_id:
            raise HardInvariantError("reconcile revision cannot change world_id")
        if approved.character_id != previous.character_id:
            raise HardInvariantError("reconcile revision cannot change character_id")
        if approved.relationship.protagonist_id != snapshot.session.protagonist_id:
            raise HardInvariantError("reconcile revision changed protagonist identity")
        if approved.version != previous.version + 1:
            raise HardInvariantError("reconcile revision changed candidate version")
        if proposed.version != approved.version:
            raise HardInvariantError("reconcile revision must preserve candidate version")
        if review.revision_patch is not None and not set(
            review.revision_patch.evidence_refs
        ) <= self._allowed_reconcile_refs(snapshot):
            raise HardInvariantError("reconcile revision evidence contains unknown IDs")

    @staticmethod
    def _allowed_refs(snapshot: TurnWorldSnapshot) -> set[str]:
        return {
            snapshot.snapshot_id,
            *(
                ()
                if snapshot.protagonist_utterance_event_id is None
                else (snapshot.protagonist_utterance_event_id,)
            ),
            *snapshot.selected_memory_frame.source_memory_ids,
            *snapshot.selected_memory_frame.source_event_ids,
            *snapshot.heroine_runtime.evidence_refs,
        }

    @staticmethod
    def _allowed_reconcile_refs(snapshot: ReconcileWorldSnapshot) -> set[str]:
        return {
            snapshot.snapshot_id,
            *snapshot.source_event_ids,
            *(delta.event_id for delta in snapshot.event_deltas),
            *snapshot.selected_memory_frame.source_memory_ids,
            *snapshot.selected_memory_frame.source_event_ids,
            *snapshot.heroine_runtime.evidence_refs,
        }

    @staticmethod
    def _validate_assertions(
        snapshot: TurnWorldSnapshot,
        assertions: ReplyFactAssertions,
    ) -> None:
        if assertions.protagonist_location_id != snapshot.protagonist.location_id:
            raise HardInvariantError("reply location assertion conflicts with snapshot")
        if assertions.protagonist_activity != snapshot.protagonist.activity:
            raise HardInvariantError("reply activity assertion conflicts with snapshot")
        if assertions.live_world_version != snapshot.live_world_version:
            raise HardInvariantError("reply world version conflicts with snapshot")
        if assertions.captured_game_time != snapshot.captured_game_time:
            raise HardInvariantError("reply game time conflicts with snapshot")
